"""Bounded TC reads only; connection topology is owned by Wuschel's setup."""

import asyncio
import ssl
from collections import defaultdict
from datetime import date, datetime
from typing import Any
from uuid import UUID

import asyncpg
from pydantic import Field, SecretStr

from .model import Model, canonical
from .setup import Connection
from .title_import import MAX_BYTES, MAX_ENTRIES


class Credentials(Model):
    database: str = Field(default="", max_length=63)
    user: str = Field(default="", max_length=63)
    password: SecretStr = Field(default_factory=lambda: SecretStr(""), max_length=4096)


CATALOG_SQL = (
    "SELECT id, scope, media_type, signal_type, normalized_key, key, parent_id, enum, "
    "artist, title, album, app_name, first_seen, last_seen, seen_count, hidden_at, reviewed_at "
    "FROM public.tc_v3_catalog ORDER BY id LIMIT 100001"
)
CONTEXT_SQL = (
    "SELECT entry_id, context, source_app, enum_override, first_seen, last_seen, seen_count "
    "FROM public.tc_v3_entry_context ORDER BY entry_id, context, source_app LIMIT 6400001"
)
# Permission checks return only a boolean. No role names or driver errors reach callers.
READER_SQL = """
SELECT NOT (r.rolsuper OR r.rolcreaterole OR r.rolcreatedb OR r.rolreplication
 OR r.rolbypassrls OR has_database_privilege(current_database(), 'CREATE')
 OR has_schema_privilege('public', 'CREATE')
 OR EXISTS (
   SELECT 1 FROM unnest(ARRAY['public.tc_v3_catalog','public.tc_v3_entry_context']) t,
     unnest(ARRAY['INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER','MAINTAIN']) p
   WHERE has_table_privilege(t,p)
 )
 OR has_any_column_privilege('public.tc_v3_catalog','INSERT,UPDATE,REFERENCES')
 OR has_any_column_privilege('public.tc_v3_entry_context','INSERT,UPDATE,REFERENCES'))
FROM pg_roles r WHERE r.rolname = current_user
"""


def scalar(value: Any) -> Any:
    return (
        value.isoformat()
        if isinstance(value, (datetime, date))
        else str(value)
        if isinstance(value, UUID)
        else value
    )


def connection_settings(base: Connection | None, credentials: Credentials) -> dict[str, Any]:
    if (
        base is None
        or not credentials.database
        or not credentials.user
        or not credentials.password.get_secret_value()
    ):
        raise ValueError("title_sync_not_configured")
    tls = base.tls()
    if (
        not isinstance(tls, ssl.SSLContext)
        or not tls.check_hostname
        or tls.verify_mode != ssl.CERT_REQUIRED
    ):
        raise ValueError("title_sync_tls_required")
    return dict(
        host=base.host,
        port=base.port,
        database=credentials.database,
        user=credentials.user,
        password=credentials.password.get_secret_value(),
        ssl=tls,
        timeout=5,
        command_timeout=5,
    )


async def read(base: Connection | None, credentials: Credentials) -> dict[str, Any]:
    """One snapshot, closed before preview. Never retry or write to TC."""
    connection = None
    try:
        settings = connection_settings(base, credentials)
        async with asyncio.timeout(30):
            connection = await asyncpg.connect(**settings)
            async with connection.transaction(isolation="repeatable_read", readonly=True):
                if not await connection.fetchval(READER_SQL):
                    raise ValueError("title_sync_reader_required")
                size = 0
                entries: list[dict[str, Any]] = []
                contexts: dict[str, list[dict[str, Any]]] = defaultdict(list)
                for sql, target in ((CATALOG_SQL, entries), (CONTEXT_SQL, None)):
                    async for record in connection.cursor(sql, prefetch=100):
                        row = {key: scalar(value) for key, value in dict(record).items()}
                        size += len(canonical(row).encode("utf-8"))
                        if size > MAX_BYTES:
                            raise ValueError("title_export_limit")
                        if target is not None:
                            if len(target) >= MAX_ENTRIES:
                                raise ValueError("title_export_limit")
                            target.append(row)
                        else:
                            source = row.pop("entry_id")
                            contexts[source].append(row)
                            if len(contexts[source]) > 64:
                                raise ValueError("title_export_limit")
                by_id = {row["id"]: row for row in entries}
                if contexts.keys() - by_id.keys():
                    raise ValueError("title_sync_source_invalid")
                for row in entries:
                    parent = by_id.get(row["parent_id"])
                    if row["parent_id"] and parent is None:
                        raise ValueError("title_sync_source_invalid")
                    row["parent_key"] = parent["normalized_key"] if parent else None
                    row["telemetry"] = {
                        k: row.pop(k) for k in ("first_seen", "last_seen", "seen_count")
                    }
                    row["contexts"] = [
                        {
                            **ctx,
                            "telemetry": {
                                k: ctx[k] for k in ("first_seen", "last_seen", "seen_count")
                            },
                        }
                        for ctx in contexts.get(row["id"], [])
                    ]
                    for ctx in row["contexts"]:
                        for key in ("first_seen", "last_seen", "seen_count"):
                            del ctx[key]
                return {"version": 3, "entries": entries}
    except ValueError as error:
        if str(error) in {
            "title_sync_not_configured",
            "title_sync_tls_required",
            "title_sync_reader_required",
            "title_export_limit",
            "title_sync_source_invalid",
        }:
            raise
        raise ValueError("title_sync_source_invalid") from None
    except OSError, TimeoutError, asyncpg.PostgresError, asyncpg.InterfaceError:
        raise ValueError("title_sync_read_failed") from None
    finally:
        if connection is not None:
            try:
                await connection.close(timeout=5)
            except OSError, TimeoutError, asyncpg.PostgresError, asyncpg.InterfaceError:
                connection.terminate()
