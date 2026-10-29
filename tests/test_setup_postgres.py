"""Real PostgreSQL provisioning, interrupted resume, migration and identity gates."""

import asyncio
import base64
import json
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import asyncpg
import pytest

from core_contracts import provision
from core_contracts.identity import write_private
from core_contracts.persistence import PostgresStore
from core_contracts.setup import Connection, Setup, probe

pytest_plugins = ["tests.test_postgres"]

pytestmark = pytest.mark.postgres


async def test_provision_resume_conflict_import_migrate_and_upgrade(
    database, tmp_path, monkeypatch
):
    admin = await asyncpg.connect(database, ssl=False)
    parsed = urlsplit(database)
    name = "cc_setup_" + uuid4().hex
    role = name + "_role"
    config = {
        "host": parsed.hostname,
        "port": parsed.port or 5432,
        "database": name,
        "user": role,
        "sslmode": "disable",
        "ca": "",
    }
    request = {"connection": config, "request_id": str(uuid4())}
    loop = asyncio.get_running_loop()
    interrupted = False

    async def execute(statement, selected="postgres"):
        nonlocal interrupted
        client = (
            admin
            if selected == "postgres"
            else await asyncpg.connect(parsed._replace(path="/" + selected).geturl(), ssl=False)
        )
        try:
            if statement.startswith(("SELECT", "SHOW")):
                result = await client.fetchval(statement)
                return (
                    "t"
                    if result is True
                    else "f"
                    if result is False
                    else ""
                    if result is None
                    else str(result)
                )
            if statement.startswith("COMMENT ON DATABASE") and not interrupted:
                interrupted = True
                raise RuntimeError("simulated interruption after CREATE DATABASE")
            await client.execute(statement)
            return ""
        finally:
            if client is not admin:
                await client.close()

    def sql(statement, selected="postgres"):
        return asyncio.run_coroutine_threadsafe(execute(statement, selected), loop).result(
            timeout=15
        )

    monkeypatch.setattr(provision, "sql", sql)
    try:
        with pytest.raises(RuntimeError, match="simulated interruption"):
            await asyncio.to_thread(provision.provision, request, tmp_path / "lxc")
        code = await asyncio.to_thread(provision.provision, request, tmp_path / "lxc")
        assert await asyncio.to_thread(provision.provision, request, tmp_path / "lxc") == code
        decoded = json.loads(base64.b64decode(code))
        connection = Connection.model_validate(decoded["connection"])
        result = await probe(connection)
        assert result["empty"] and not result["database_id"]
        restricted = await admin.fetchrow(
            "SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls FROM pg_roles WHERE rolname=$1",
            role,
        )
        assert not any(restricted.values())
        setup = Setup(tmp_path / "app", None)
        await setup.dispatch("provision", "POST", config)
        # The generated script and import code must share the persisted request.
        write_private(setup.directory / "provision.json", json.dumps(request))
        await setup.dispatch(
            "import",
            "POST",
            {
                "code": code,
                "confirm": True,
                "allow_insecure": True,
                "initialize_empty_database": True,
            },
        )
        identity = setup.pending["prepared_identity"]
        store = PostgresStore(
            connection.dsn(), Path(__file__).resolve().parents[1] / "migrations", tls=False
        )
        await store.open()
        await store.put_metadata("installation_id", identity)
        assert await store.writer.fetchval("SELECT count(*) FROM cc_schema_migrations") == 9
        await store.close()
        write_private(setup.data / "installation.json", json.dumps({"installation_id": identity}))
        setup.initialized()
        restarted = Setup(setup.data, None)
        assert restarted.pending["prepared_identity"] == identity
        assert not restarted.pending["initialize_empty_database"]
        assert (await probe(restarted.connection))["database_id"] == identity
        foreign = {**request, "request_id": str(uuid4())}
        with pytest.raises(RuntimeError, match="existing_role_conflict"):
            await asyncio.to_thread(provision.provision, foreign, tmp_path / "foreign")
        assert await admin.fetchval("SELECT rolname FROM pg_roles WHERE rolname=$1", role) == role
    finally:
        await admin.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=$1", name
        )
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}"')
        await admin.execute(f'DROP ROLE IF EXISTS "{role}"')
        await admin.close()


async def test_foreign_schema_and_admin_role_rejected_before_migration(database):
    parsed = urlsplit(database)
    connection = Connection(
        host=parsed.hostname,
        port=parsed.port or 5432,
        database=parsed.path[1:],
        user=parsed.username,
        password=parsed.password,
        sslmode="disable",
    )
    with pytest.raises(ValueError, match="application_role_too_privileged"):
        await probe(connection)
