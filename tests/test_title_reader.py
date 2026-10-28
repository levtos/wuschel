from contextlib import asynccontextmanager
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import SecretStr

from core_contracts.setup import Connection
from core_contracts.title_reader import (
    CATALOG_SQL,
    CONTEXT_SQL,
    Credentials,
    connection_settings,
    read,
)


def base():
    return Connection(
        host="database.example.invalid",
        database="synthetic_core",
        user="synthetic_core",
        password=SecretStr(""),
        sslmode="verify-full",
    )


def credentials():
    return Credentials(
        database="synthetic_titles",
        user="synthetic_reader",
        password=SecretStr("synthetic-test-only"),
    )


def test_reuses_host_port_and_verified_tls_without_second_ca():
    original = base()
    settings = connection_settings(original, credentials())
    assert settings["host"] == original.host and settings["port"] == original.port
    assert settings["ssl"].check_hostname
    assert settings["database"] == "synthetic_titles"
    assert set(Credentials.model_fields) == {"database", "user", "password"}
    with pytest.raises(ValueError, match="tls_required"):
        connection_settings(original.model_copy(update={"sslmode": "require"}), credentials())
    assert "synthetic-test-only" not in repr(credentials())


class Database:
    def __init__(self):
        self.entered = False
        self.closed = False
        self.queries = []
        self.allowed = True
        self.failure = None
        self.ident = str(uuid4())

    @asynccontextmanager
    async def transaction(self, **settings):
        assert settings == {"isolation": "repeatable_read", "readonly": True}
        self.entered = True
        try:
            yield
        finally:
            self.entered = False

    async def fetchval(self, sql):
        assert self.entered and sql.lstrip().startswith("SELECT")
        return self.allowed

    async def cursor(self, sql, **kwargs):
        assert self.entered
        self.queries.append(sql)
        if self.failure:
            raise self.failure
        if sql == CATALOG_SQL:
            yield dict(
                id=self.ident,
                scope="synthetic",
                media_type="music",
                signal_type="title",
                normalized_key="synthetic",
                key="Synthetic",
                parent_id=None,
                enum=0,
                artist="Synthetic Artist",
                title="Synthetic",
                album=None,
                app_name=None,
                first_seen="2032-01-01T00:00:00Z",
                last_seen="2032-01-02T00:00:00Z",
                seen_count=7,
                hidden_at="2032-01-01T00:00:00Z",
                reviewed_at=None,
            )
        else:
            assert sql == CONTEXT_SQL
            yield dict(
                entry_id=self.ident,
                context="synthetic",
                source_app="",
                enum_override=0,
                first_seen="2032-01-01T00:00:00Z",
                last_seen="2032-01-02T00:00:00Z",
                seen_count=7,
            )

    async def close(self, **kwargs):
        self.closed = True


async def test_reader_single_readonly_snapshot_and_close(monkeypatch):
    db = Database()
    connect = AsyncMock(return_value=db)
    monkeypatch.setattr("core_contracts.title_reader.asyncpg.connect", connect)
    result = await read(base(), credentials())
    assert db.closed and not db.entered
    assert db.queries == [CATALOG_SQL, CONTEXT_SQL]
    assert result["entries"][0]["contexts"][0]["enum_override"] == 0
    assert result["entries"][0]["telemetry"]["seen_count"] == 7


@pytest.mark.parametrize("failure", ["rights", "timeout", "budget"])
async def test_reader_failure_never_returns_partial_rows_or_raw_errors(monkeypatch, failure):
    db = Database()
    if failure == "rights":
        db.allowed = False
    elif failure == "timeout":
        db.failure = TimeoutError("synthetic sensitive driver text")
    else:
        monkeypatch.setattr("core_contracts.title_reader.MAX_BYTES", 1)
    monkeypatch.setattr("core_contracts.title_reader.asyncpg.connect", AsyncMock(return_value=db))
    with pytest.raises(ValueError) as error:
        await read(base(), credentials())
    assert "sensitive" not in str(error.value)
    assert db.closed
