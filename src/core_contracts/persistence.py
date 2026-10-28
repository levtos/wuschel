"""Dedicated, locked PostgreSQL writer and atomic platform changesets."""

import asyncio
import copy
import hashlib
import json
import logging
import ssl
from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

import asyncpg
from pydantic import BaseModel, Field

from .history import CONTRACT_SQL, HISTORY_TABLES, TIME_SQL, HistoryQuery, page, timestamp
from .model import canonical
from .retention import (
    EvidenceOutsideRetention,
    RetentionPolicy,
    maintain_postgres,
    pinned_references,
    reference_scope,
    summaries,
)

TABLES = (
    "runtime_epoch",
    "registry_revision",
    "registry_activation",
    "registry_draft",
    "activation_receipt",
    "draft_continuation",
    "registry_draft_version",
    "contract_lifecycle",
    "contract_state_current",
    "contract_state_history",
    "source_observation_current",
    "source_observation_history",
    "node_state",
    "sm_instance",
    "sm_episode",
    "sm_transition",
    "deadline",
    "command_log",
    "history_gap",
    "diagnostic_event",
    "service_job",
    "service_state",
    "manual_request",
    "editorial_current",
    "editorial_draft",
    "catalog_content_revision",
    "catalog_entry_revision",
    "catalog_head",
    "catalog_sighting",
    "catalog_request",
    "catalog_import",
    "title_sync_mapping",
)
IMMUTABLE = {
    "registry_draft_version",
    "activation_receipt",
    "catalog_content_revision",
    "catalog_entry_revision",
    "catalog_request",
    "catalog_import",
    "registry_revision",
    "registry_activation",
    "contract_state_history",
    "source_observation_history",
    "sm_transition",
    "history_gap",
    "diagnostic_event",
    "manual_request",
}
LOCK_ID = 1128484913
LOGGER = logging.getLogger(__name__)
# Historical rows are transaction append/upsert buffers, never a runtime cache.
ARCHIVE = IMMUTABLE | {"command_log", "sm_episode", "deadline", "runtime_epoch"}


class PersistenceUnavailable(RuntimeError):
    pass


def history_predicates(
    query: HistoryQuery, scope: str, time_column: str, key_column: str, source_column: str
) -> tuple[str, list[Any]]:
    predicates: list[str] = []
    values: list[Any] = []
    for value, expression in (
        (query.source_id, source_column + "="),
        (query.since, time_column + ">="),
        (query.until, time_column + "<="),
    ):
        if value is not None:
            values.append(value)
            predicates.append(expression + "$" + str(len(values)))
    if position := query.position(scope):
        values.extend(position)
        predicates.append(f"({time_column},{key_column})<(${len(values) - 1},${len(values)})")
    return ("WHERE " + " AND ".join(predicates) if predicates else ""), values


def rollup_rows(
    records: list[dict[str, Any]], query: HistoryQuery, position: tuple[datetime, str] | None
) -> list[tuple[str, dict[str, Any]]]:
    rows: list[tuple[str, dict[str, Any]]] = []
    for item in records:
        key = item.get(
            "rollup_key", f"{item['source_id']}:{item['binding_id']}:{item['registry_revision']}"
        )
        at = item["hour"]
        if (
            (query.source_id is not None and item["source_id"] != query.source_id)
            or (query.since and at < query.since)
            or (query.until and at > query.until)
            or (position and (at, key) >= position)
        ):
            continue
        payload = {
            k: (
                v.isoformat()
                if isinstance(v, datetime)
                else str(v)
                if isinstance(v, Decimal)
                else v
            )
            for k, v in item.items()
            if k != "rollup_key"
        }
        payload["at"] = at.isoformat()
        payload["average"] = (
            float(item["total"] / item["numeric_count"]) if item["numeric_count"] else None
        )
        rows.append((key, payload))
    return sorted(rows, key=lambda pair: (timestamp(pair[1]), pair[0]), reverse=True)[
        : query.limit + 1
    ]


def empty_tables() -> dict[str, dict[str, dict[str, Any]]]:
    return {table: {} for table in TABLES}


class State(BaseModel):
    generation: int = 0
    active_revision: int = 0
    publication_seq: int = 0
    epoch_id: str = ""
    tables: dict[str, dict[str, dict[str, Any]]] = Field(default_factory=empty_tables)
    publications: dict[int, dict[str, Any]] = Field(default_factory=dict)

    def clone(self) -> State:
        return State(
            generation=self.generation,
            active_revision=self.active_revision,
            publication_seq=self.publication_seq,
            epoch_id=self.epoch_id,
            tables={name: copy.deepcopy(rows) for name, rows in self.current_tables().items()},
        )

    def current_tables(self) -> dict[str, dict[str, dict[str, Any]]]:
        result = {}
        for name, rows in self.tables.items():
            key = str(self.active_revision) if name == "registry_revision" else self.epoch_id
            if name in {"registry_revision", "runtime_epoch"}:
                result[name] = {key: rows[key]} if key in rows else {}
            else:
                result[name] = {} if name in ARCHIVE else rows
        return result

    def compact(self) -> None:
        self.tables = self.current_tables()
        self.publications = {}


class Store(Protocol):
    async def maintain_history(
        self, state: State, now: datetime, policy: RetentionPolicy, *, dry_run: bool = False
    ) -> dict[str, Any]: ...
    async def rollups(self, query: HistoryQuery) -> dict[str, Any]: ...
    async def catalog(self, catalog_id: str, version: int) -> dict[str, Any] | None: ...
    async def managed_entries(self, catalog: str, revision: int) -> dict[str, Any]: ...
    async def catalog_revisions(
        self, catalog: str, before: int, limit: int = 100
    ) -> list[dict[str, Any]]: ...
    async def load(self) -> State: ...
    async def commit(self, state: State) -> None: ...
    async def probe(self) -> None: ...
    async def close(self) -> None: ...
    async def get(self, table: str, key: str) -> dict[str, Any] | None: ...
    async def rows(
        self, table: str, *, contract_id: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]: ...
    async def history(self, table: str, query: HistoryQuery) -> dict[str, Any]: ...
    async def definition(self, type_id: str, version: int) -> dict[str, Any] | None: ...
    async def sensor_profile(self, profile_id: str, version: int) -> dict[str, Any] | None: ...
    async def source_history(
        self, source_id: str, *, include_revision: bool = False
    ) -> list[dict[str, Any]]: ...
    async def evidence(self, reference: str) -> dict[str, Any] | None: ...


class PostgresStore:
    async def sensor_profile(self, profile_id: str, version: int) -> dict[str, Any] | None:
        if self.pool is None:
            raise PersistenceUnavailable("persistence_unavailable")
        value = await self.pool.fetchval(
            "SELECT source->'sensor_profile' FROM registry_revision, LATERAL jsonb_array_elements(payload->'config'->'sources') AS entries(source) WHERE source->'sensor_profile'->>'id'=$1 AND source->'sensor_profile'->>'version'=$2 ORDER BY (payload->>'revision')::bigint DESC LIMIT 1",
            profile_id,
            str(version),
        )
        return json.loads(value) if value is not None else None

    async def observation_history(self, query: HistoryQuery) -> dict[str, Any]:
        if self.pool is None:
            raise PersistenceUnavailable("persistence_unavailable")
        if query.contract_id is not None:
            return page([], query, "source_observation_history")
        where, values = history_predicates(
            query, "source_observation_history", "i.received_at", "i.reference", "i.source_id"
        )
        records = await self.pool.fetch(
            "SELECT h.key,h.payload FROM cc_observation_index i JOIN source_observation_history h ON h.key=i.reference "
            + where
            + " ORDER BY i.received_at DESC,i.reference DESC LIMIT $"
            + str(len(values) + 1),
            *values,
            query.limit + 1,
        )
        return page(
            [(row["key"], json.loads(row["payload"])) for row in records],
            query,
            "source_observation_history",
        )

    async def maintain_history(
        self, state: State, now: datetime, policy: RetentionPolicy, *, dry_run: bool = False
    ) -> dict[str, Any]:
        async with self._mutex:
            await self.probe()
            return await maintain_postgres(
                self._writer(), now, policy, pinned_references(state), dry_run=dry_run
            )

    async def rollups(self, query: HistoryQuery) -> dict[str, Any]:
        if self.pool is None:
            raise PersistenceUnavailable("persistence_unavailable")
        position = query.position("observation_rollups")
        where, values = history_predicates(
            query, "observation_rollups", "hour", "rollup_key", "source_id"
        )
        records = await self.pool.fetch(
            "SELECT * FROM cc_observation_rollup "
            + where
            + " ORDER BY hour DESC,rollup_key DESC LIMIT $"
            + str(len(values) + 1),
            *values,
            query.limit + 1,
        )
        rows = rollup_rows([dict(row) for row in records], query, position)
        return page(rows, query, "observation_rollups")

    def __init__(self, dsn: str, migrations: Path, *, tls: ssl.SSLContext | bool = True) -> None:
        self.dsn = dsn
        self.migrations = migrations
        self.tls = tls
        self.writer: asyncpg.Connection[Any] | None = None
        self.pool: asyncpg.Pool[Any] | None = None
        self._committed = State()
        self.before_commit: Callable[[], None] | None = None

        self._mutex = asyncio.Lock()

    async def open(self) -> None:
        connection = await asyncpg.connect(
            self.dsn,
            ssl=self.tls,
            timeout=10,
            command_timeout=10,
            server_settings={
                "tcp_keepalives_idle": "15",
                "tcp_keepalives_interval": "5",
                "tcp_keepalives_count": "3",
                "tcp_user_timeout": "30000",
                "idle_session_timeout": "60000",
                "idle_in_transaction_session_timeout": "15000",
                "application_name": "core_contracts_writer",
            },
        )
        try:
            if not await connection.fetchval("SELECT pg_try_advisory_lock($1)", LOCK_ID):
                owner = await connection.fetchval(
                    "SELECT pid FROM pg_locks WHERE locktype='advisory' AND objid=$1 AND classid=0 AND granted LIMIT 1",
                    LOCK_ID,
                )
                LOGGER.warning("writer_lock_unavailable", extra={"writer_pid": owner})
                raise PersistenceUnavailable("writer_lock_unavailable")
            self.writer = connection
            await self.migrate()
            self.pool = await asyncpg.create_pool(
                self.dsn, ssl=self.tls, min_size=1, max_size=2, timeout=10, command_timeout=10
            )
            LOGGER.info("writer_lock_acquired")
        except BaseException:
            await connection.close()
            self.writer = None
            raise

    async def migrate(self) -> None:
        connection = self._writer()
        await connection.execute(
            "CREATE TABLE IF NOT EXISTS cc_schema_migrations "
            "(version integer PRIMARY KEY, checksum text NOT NULL)"
        )
        known = {int(p.name.split("_")[0]): p for p in self.migrations.glob("[0-9]*.sql")}
        if not known or sorted(known) != list(range(1, max(known) + 1)):
            raise ValueError("invalid migration sequence")
        applied = {
            row["version"]: row["checksum"]
            for row in await connection.fetch("SELECT version, checksum FROM cc_schema_migrations")
        }
        if applied.keys() - known.keys():
            raise ValueError("newer_database_schema")
        for version, path in sorted(known.items()):
            sql = path.read_text(encoding="utf-8").replace("\r\n", "\n")
            digest = hashlib.sha256(sql.encode()).hexdigest()
            if version in applied:
                if digest != applied[version]:
                    raise ValueError("migration_checksum_mismatch")
                continue
            async with connection.transaction():
                await connection.execute(sql)
                await connection.execute(
                    "INSERT INTO cc_schema_migrations VALUES ($1, $2)", version, digest
                )
            LOGGER.info("migration_applied", extra={"revision": version})

    def _writer(self) -> asyncpg.Connection[Any]:
        if self.writer is None or self.writer.is_closed():
            raise PersistenceUnavailable("persistence_unavailable")
        return self.writer

    async def probe(self) -> None:
        connection = self._writer()
        held = await connection.fetchval(
            "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE locktype='advisory' "
            "AND pid=pg_backend_pid() AND objid=$1 AND classid=0 AND objsubid=1 AND granted)",
            LOCK_ID,
        )
        if not held:
            raise PersistenceUnavailable("writer_lock_unavailable")

    async def metadata(self, key: str) -> Any:
        value = await self._writer().fetchval("SELECT value FROM cc_meta WHERE key=$1", key)
        return json.loads(value) if value is not None else None

    async def put_metadata(self, key: str, value: Any) -> None:
        await self.probe()
        await self._writer().execute(
            "INSERT INTO cc_meta VALUES ($1,$2::jsonb) "
            "ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value",
            key,
            canonical(value),
        )

    async def load(self) -> State:
        await self.probe()
        connection = self._writer()
        async with connection.transaction(isolation="repeatable_read", readonly=True):
            state = State.model_validate(await self.metadata("runtime") or {})
            for table in TABLES:
                if table == "registry_revision":
                    rows = await connection.fetch(
                        f"SELECT key,payload FROM {table} WHERE key=$1", str(state.active_revision)
                    )
                elif table == "runtime_epoch":
                    rows = await connection.fetch(
                        f"SELECT key,payload FROM {table} WHERE key=$1", state.epoch_id
                    )
                elif table in ARCHIVE:
                    continue
                else:
                    rows = await connection.fetch(f"SELECT key,payload FROM {table}")
                state.tables[table] = {row["key"]: json.loads(row["payload"]) for row in rows}
        self._committed = state.clone()
        return state

    async def commit(self, state: State) -> None:
        async with self._mutex:
            await self.probe()
            connection = self._writer()
            previous = self._committed
            if state.generation != previous.generation:
                raise ValueError("stale changeset")
            async with connection.transaction():
                for table in TABLES:
                    old, new = previous.tables[table], state.tables[table]
                    for key in old.keys() - new.keys():
                        if table in ARCHIVE:
                            continue
                        await connection.execute(f"DELETE FROM {table} WHERE key=$1", key)
                    for key, value in new.items():
                        if old.get(key) == value:
                            continue
                        if key in old and table in IMMUTABLE:
                            raise ValueError("immutable history")
                        suffix = (
                            ""
                            if table in IMMUTABLE
                            else " ON CONFLICT(key) DO UPDATE SET payload=EXCLUDED.payload"
                        )
                        await connection.execute(
                            f"INSERT INTO {table} VALUES ($1,$2::jsonb)" + suffix,
                            key,
                            canonical(value),
                        )
                for seq in state.publications.keys() - previous.publications.keys():
                    payload = state.publications[seq]
                    await connection.execute(
                        "INSERT INTO publication VALUES ($1,$2,$3::jsonb)",
                        seq,
                        datetime.fromisoformat(payload["created_at"]),
                        canonical(payload),
                    )
                metadata = state.model_dump(exclude={"tables", "publications"})
                metadata["generation"] += 1
                await self.put_metadata("runtime", metadata)
                if self.before_commit:
                    self.before_commit()
            state.generation += 1
            state.compact()
            self._committed = state.clone()

    async def get(self, table: str, key: str) -> dict[str, Any] | None:
        if table not in TABLES or self.pool is None:
            raise PersistenceUnavailable("persistence_unavailable")
        value = await self.pool.fetchval(f"SELECT payload FROM {table} WHERE key=$1", key)
        return json.loads(value) if value is not None else None

    async def managed_entries(self, catalog: str, revision: int) -> dict[str, Any]:
        if self.pool is None:
            raise PersistenceUnavailable("persistence_unavailable")
        rows = await self.pool.fetch(
            "SELECT DISTINCT ON (payload->>'identity') payload FROM catalog_entry_revision "
            "WHERE payload->>'catalog'=$1 AND (payload->>'revision')::bigint <= $2 "
            "ORDER BY payload->>'identity', (payload->>'revision')::bigint DESC",
            catalog,
            revision,
        )
        entries = [json.loads(row["payload"]) for row in rows]
        return {row["identity"]: row["entry"] for row in entries}

    async def catalog_revisions(
        self, catalog: str, before: int, limit: int = 100
    ) -> list[dict[str, Any]]:
        if self.pool is None:
            raise PersistenceUnavailable("persistence_unavailable")
        rows = await self.pool.fetch(
            "SELECT payload FROM catalog_content_revision WHERE payload->>'catalog'=$1 "
            "AND (payload->>'revision')::bigint < $2 ORDER BY (payload->>'revision')::bigint DESC LIMIT $3",
            catalog,
            before,
            min(max(limit, 1), 100),
        )
        return [json.loads(row["payload"]) for row in rows]

    async def rows(
        self, table: str, *, contract_id: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        if table not in TABLES or self.pool is None:
            raise PersistenceUnavailable("persistence_unavailable")
        order = (
            "(payload->>'publication_seq')::bigint"
            if table == "contract_state_history"
            else "(payload->>'revision')::bigint"
            if table == "registry_revision"
            else "COALESCE(payload->>'at',payload->>'created_at',payload->>'updated_at','')"
        )
        records = await self.pool.fetch(
            f"SELECT payload FROM {table} WHERE ($1::text IS NULL OR payload->>'contract_id'=$1) ORDER BY {order} DESC, key DESC LIMIT $2",
            contract_id,
            min(max(limit, 1), 1000),
        )
        return [json.loads(row["payload"]) for row in records]

    async def history(self, table: str, query: HistoryQuery) -> dict[str, Any]:
        if table not in HISTORY_TABLES.values() or self.pool is None:
            raise ValueError("invalid_history_table")
        if table == "source_observation_history":
            return await self.observation_history(query)
        position = query.position(table)
        contract_match = f"{CONTRACT_SQL}=$1"
        if table in {"sm_episode", "deadline"}:
            # Alpha-3 episode/deadline rows omitted contract_id. Their durable
            # transition owns the episode; resolve it without rewriting history.
            contract_match += (
                " OR (payload->>'contract_id' IS NULL AND EXISTS "
                "(SELECT 1 FROM sm_transition AS transition WHERE "
                "transition.payload->>'contract_id'=$1 AND "
                f"transition.payload->>'episode_id'={table}.payload->>'episode_id'))"
            )
        records = await self.pool.fetch(
            f"SELECT key,payload FROM {table} WHERE "
            f"($1::text IS NULL OR {contract_match}) "
            "AND ($2::text IS NULL OR payload->>'source_id'=$2) "
            f"AND ($3::timestamptz IS NULL OR {TIME_SQL}>=$3) "
            f"AND ($4::timestamptz IS NULL OR {TIME_SQL}<=$4) "
            f"AND ($5::timestamptz IS NULL OR ({TIME_SQL},key)<($5,$6)) "
            f"ORDER BY {TIME_SQL} DESC,key DESC LIMIT $7",
            query.contract_id,
            query.source_id,
            query.since,
            query.until,
            position[0] if position else None,
            position[1] if position else None,
            query.limit + 1,
        )
        return page([(row["key"], json.loads(row["payload"])) for row in records], query, table)

    async def definition(self, type_id: str, version: int) -> dict[str, Any] | None:
        if self.pool is None:
            raise PersistenceUnavailable("persistence_unavailable")
        value = await self.pool.fetchval(
            "SELECT definition FROM registry_revision, "
            "LATERAL jsonb_array_elements(COALESCE(payload->'config'->'definitions','[]'::jsonb)) AS entries(definition) "
            "WHERE definition->>'type_id'=$1 AND definition->>'type_version'=$2 ORDER BY (payload->>'revision')::bigint DESC LIMIT 1",
            type_id,
            str(version),
        )
        return json.loads(value) if value is not None else None

    async def catalog(self, catalog_id: str, version: int) -> dict[str, Any] | None:
        if self.pool is None:
            raise PersistenceUnavailable("persistence_unavailable")
        value = await self.pool.fetchval(
            "SELECT catalog FROM registry_revision, LATERAL jsonb_array_elements(COALESCE(payload->'config'->'catalogs','[]'::jsonb)) AS entries(catalog) "
            "WHERE catalog->>'catalog_id'=$1 AND catalog->>'version'=$2 ORDER BY (payload->>'revision')::bigint DESC LIMIT 1",
            catalog_id,
            str(version),
        )
        return json.loads(value) if value is not None else None

    async def source_history(
        self, source_id: str, *, include_revision: bool = False
    ) -> list[dict[str, Any]]:
        if self.pool is None:
            raise PersistenceUnavailable("persistence_unavailable")
        records = await self.pool.fetch(
            "SELECT source, MIN((payload->>'revision')::integer) AS revision FROM registry_revision, "
            "LATERAL jsonb_array_elements(payload->'config'->'sources') AS entries(source) "
            "WHERE source->>'source_id'=$1 GROUP BY source ORDER BY revision",
            source_id,
        )
        return [
            {
                **json.loads(row["source"]),
                **({"_registry_revision": str(row["revision"])} if include_revision else {}),
            }
            for row in records
        ]

    async def evidence(self, reference: str) -> dict[str, Any] | None:
        if self.pool is None:
            raise PersistenceUnavailable("persistence_unavailable")
        # Current evidence identities are primary keys. Do not turn this indexed
        # lookup into a scan via the legacy computed-reference OR predicate.
        direct = await self.get("source_observation_history", reference)
        if direct is None and (scope := reference_scope(reference)):
            watermark = await self.pool.fetchrow(
                "SELECT max_sequence,retained_after FROM cc_evidence_watermark WHERE scope=$1",
                scope[0],
            )
            if watermark and scope[1] <= watermark["max_sequence"]:
                raise EvidenceOutsideRetention(reference, watermark["retained_after"].isoformat())
        if direct is not None or reference.startswith("v2:"):
            return direct
        value = await self.pool.fetchval(
            "SELECT payload FROM source_observation_history WHERE "
            "(COALESCE(payload->>'reference_version','1')='1' AND "
            "(payload->>'epoch_id') || ':' || (payload->>'ingest_seq') || ':' || "
            "(payload->>'binding_id')=$1) "
            "UNION ALL SELECT payload FROM source_observation_current "
            "WHERE COALESCE(payload->>'reference_version','1')='1' AND "
            "(payload->>'epoch_id') || ':' || (payload->>'ingest_seq') || ':' || "
            "(payload->>'binding_id')=$1 LIMIT 1",
            reference,
        )
        return json.loads(value) if value is not None else None

    async def close(self) -> None:
        if self.pool:
            try:
                async with asyncio.timeout(3):
                    await self.pool.close()
            except TimeoutError:
                LOGGER.warning("reader_pool_close_timeout")
                self.pool.terminate()
            self.pool = None
        if self.writer and not self.writer.is_closed():
            await self.writer.close(timeout=3)  # Session lock is released by PostgreSQL.
        self.writer = None


class MemoryStore:
    async def sensor_profile(self, profile_id: str, version: int) -> dict[str, Any] | None:
        await self.probe()
        for _, revision in sorted(
            self.state.tables["registry_revision"].items(),
            key=lambda row: int(row[0]),
            reverse=True,
        ):
            for source in revision["config"]["sources"]:
                profile = source.get("sensor_profile")
                if profile and (profile["id"], profile["version"]) == (profile_id, version):
                    return dict(copy.deepcopy(profile))
        return None

    """Transactional test double only; the application always constructs PostgresStore."""

    def __init__(self) -> None:
        self.state = State()
        self.available = True
        self.locked = True
        self.before_commit: Callable[[], None] | None = None
        self.observation_rollups: dict[tuple[Any, ...], dict[str, Any]] = {}
        self.evidence_watermarks: dict[str, tuple[int, str]] = {}

    async def maintain_history(
        self, state: State, now: datetime, policy: RetentionPolicy, *, dry_run: bool = False
    ) -> dict[str, Any]:
        await self.probe()
        pins = pinned_references(state)
        before = now - timedelta(days=policy.raw_days)
        rows = sorted(
            (
                (key, row)
                for key, row in self.state.tables["source_observation_history"].items()
                if key not in pins and timestamp(row) < before
            ),
            key=lambda pair: (timestamp(pair[1]), pair[0]),
        )[: policy.batch_size]
        groups, watermarks = summaries(rows)
        receipt = {
            "selected": len(rows),
            "rollup_groups": len(groups),
            "pinned_references": len(pins),
            "dry_run": dry_run,
        }
        if dry_run:
            return receipt
        updated = copy.deepcopy(self.observation_rollups)
        for key, item in groups.items():
            if key in updated:
                old = updated[key]
                item["count"] += old["count"]
                item["numeric_count"] += old["numeric_count"]
                item["total"] += old["total"]
                item["minimum"] = (
                    min(v for v in (item["minimum"], old["minimum"]) if v is not None)
                    if item["numeric_count"]
                    else None
                )
                item["maximum"] = (
                    max(v for v in (item["maximum"], old["maximum"]) if v is not None)
                    if item["numeric_count"]
                    else None
                )
                item["first_at"], item["last_at"] = (
                    min(item["first_at"], old["first_at"]),
                    max(item["last_at"], old["last_at"]),
                )
            updated[key] = item
        if self.before_commit:
            self.before_commit()
        self.observation_rollups = {
            key: item
            for key, item in updated.items()
            if key[3] >= now - timedelta(days=policy.rollup_days)
        }
        for scope, sequence in watermarks.items():
            self.evidence_watermarks[scope] = (
                max(sequence, self.evidence_watermarks.get(scope, (-1, ""))[0]),
                before.isoformat(),
            )
        for reference, _ in rows:
            self.state.tables["source_observation_history"].pop(reference)
        return receipt

    async def rollups(self, query: HistoryQuery) -> dict[str, Any]:
        await self.probe()
        records = [
            {
                "source_id": key[0],
                "binding_id": key[1],
                "registry_revision": key[2],
                "hour": key[3],
                **item,
            }
            for key, item in self.observation_rollups.items()
        ]
        return page(
            rollup_rows(records, query, query.position("observation_rollups")),
            query,
            "observation_rollups",
        )

    async def catalog(self, catalog_id: str, version: int) -> dict[str, Any] | None:
        await self.probe()
        return next(
            (
                copy.deepcopy(catalog)
                for _, revision in sorted(
                    self.state.tables["registry_revision"].items(),
                    key=lambda row: int(row[0]),
                    reverse=True,
                )
                for catalog in revision["config"].get("catalogs", [])
                if (catalog["catalog_id"], catalog["version"]) == (catalog_id, version)
            ),
            None,
        )

    async def probe(self) -> None:
        if not self.available or not self.locked:
            raise PersistenceUnavailable("persistence_unavailable")

    async def load(self) -> State:
        await self.probe()
        return self.state.clone()

    async def commit(self, state: State) -> None:
        await self.probe()
        if state.generation != self.state.generation:
            raise ValueError("stale changeset")
        if self.before_commit:
            self.before_commit()
        for table, rows in state.tables.items():
            if table not in ARCHIVE:
                self.state.tables[table] = copy.deepcopy(rows)
            else:
                self.state.tables[table].update(copy.deepcopy(rows))
        self.state.publications.update(copy.deepcopy(state.publications))
        state.generation += 1
        for name in ("generation", "active_revision", "publication_seq", "epoch_id"):
            setattr(self.state, name, getattr(state, name))
        state.compact()

    async def get(self, table: str, key: str) -> dict[str, Any] | None:
        await self.probe()
        return copy.deepcopy(self.state.tables[table].get(key))

    async def managed_entries(self, catalog: str, revision: int) -> dict[str, Any]:
        await self.probe()
        rows = sorted(
            (
                row
                for row in self.state.tables["catalog_entry_revision"].values()
                if row["catalog"] == catalog and row["revision"] <= revision
            ),
            key=lambda row: row["revision"],
        )
        return copy.deepcopy({row["identity"]: row["entry"] for row in rows})

    async def catalog_revisions(
        self, catalog: str, before: int, limit: int = 100
    ) -> list[dict[str, Any]]:
        await self.probe()
        rows = [
            row
            for row in self.state.tables["catalog_content_revision"].values()
            if row["catalog"] == catalog and row["revision"] < before
        ]
        return copy.deepcopy(
            sorted(rows, key=lambda row: row["revision"], reverse=True)[: min(max(limit, 1), 100)]
        )

    async def rows(
        self, table: str, *, contract_id: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        await self.probe()
        records = [
            v
            for v in self.state.tables[table].values()
            if contract_id is None or v.get("contract_id") == contract_id
        ]
        key = (
            "publication_seq"
            if table == "contract_state_history"
            else "revision"
            if table == "registry_revision"
            else "at"
        )
        return copy.deepcopy(
            sorted(
                records,
                key=lambda row: row.get(key, row.get("created_at", row.get("updated_at", ""))),
                reverse=True,
            )[:limit]
        )

    async def history(self, table: str, query: HistoryQuery) -> dict[str, Any]:
        await self.probe()
        if table not in HISTORY_TABLES.values():
            raise ValueError("invalid_history_table")
        position = query.position(table)
        legacy_episodes = (
            {
                row.get("episode_id")
                for row in self.state.tables["sm_transition"].values()
                if row.get("contract_id") == query.contract_id
            }
            if query.contract_id and table in {"sm_episode", "deadline"}
            else set()
        )
        records = [
            (key, value)
            for key, value in self.state.tables[table].items()
            if (
                query.contract_id is None
                or value.get("contract_id", value.get("command", {}).get("contract_id"))
                == query.contract_id
                or (value.get("contract_id") is None and value.get("episode_id") in legacy_episodes)
            )
            and (query.source_id is None or value.get("source_id") == query.source_id)
            and (query.since is None or timestamp(value) >= query.since)
            and (query.until is None or timestamp(value) <= query.until)
            and (position is None or (timestamp(value), key) < position)
        ]
        records.sort(key=lambda row: (timestamp(row[1]), row[0]), reverse=True)
        return page(copy.deepcopy(records[: query.limit + 1]), query, table)

    async def definition(self, type_id: str, version: int) -> dict[str, Any] | None:
        await self.probe()
        return next(
            (
                copy.deepcopy(definition)
                for _, revision in sorted(
                    self.state.tables["registry_revision"].items(),
                    key=lambda row: int(row[0]),
                    reverse=True,
                )
                for definition in revision["config"].get("definitions", [])
                if (definition["type_id"], definition["type_version"]) == (type_id, version)
            ),
            None,
        )

    async def source_history(
        self, source_id: str, *, include_revision: bool = False
    ) -> list[dict[str, Any]]:
        await self.probe()
        return [
            {
                **copy.deepcopy(source),
                **(
                    {"_registry_revision": str(revision.get("revision", number))}
                    if include_revision
                    else {}
                ),
            }
            for number, revision in self.state.tables["registry_revision"].items()
            for source in revision["config"]["sources"]
            if source["source_id"] == source_id
        ]

    async def evidence(self, reference: str) -> dict[str, Any] | None:
        await self.probe()
        direct = self.state.tables["source_observation_history"].get(reference)
        if (
            direct is None
            and (scope := reference_scope(reference))
            and scope[0] in self.evidence_watermarks
        ):
            maximum, cutoff = self.evidence_watermarks[scope[0]]
            if scope[1] <= maximum:
                raise EvidenceOutsideRetention(reference, cutoff)
        if direct is not None or reference.startswith("v2:"):
            return copy.deepcopy(direct)
        for table in ("source_observation_history", "source_observation_current"):
            for key, value in self.state.tables[table].items():
                if key == reference or (
                    value.get("reference_version", 1) == 1
                    and f"{value['epoch_id']}:{value['ingest_seq']}:{value['binding_id']}"
                    == reference
                ):
                    return copy.deepcopy(value)
        return None

    async def close(self) -> None:
        self.locked = False
