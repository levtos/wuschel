"""Durable job state. This connection may write only to the two work tables."""

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg

from .model import canonical
from .work_artifacts import ARTIFACT_CHUNK, PLAN_KINDS

_ARTIFACT_QUERY = "SELECT content FROM cc_work_artifact WHERE job_id=$1 AND kind=$2 AND part=$3"


class JobConflict(ValueError):
    pass


class WorkJournal:
    def __init__(self, connection: asyncpg.Connection[Any]) -> None:
        self.connection = connection
        self.lock = asyncio.Lock()

    async def recover(self) -> None:
        async with self.lock, self.connection.transaction():
            await self.connection.execute("DELETE FROM cc_work_job WHERE phase='receiving'")
            await self.connection.execute(
                "UPDATE cc_work_job SET phase='waiting', updated_at=clock_timestamp() "
                "WHERE phase='running'"
            )
            await self.connection.execute(
                "UPDATE cc_work_job SET phase='prepared', updated_at=clock_timestamp() "
                "WHERE phase='committing'"
            )
            await self.connection.execute(
                "DELETE FROM cc_work_artifact a USING cc_work_job j "
                "WHERE a.job_id=j.id AND j.phase='waiting' AND a.kind!='request'"
            )

    @asynccontextmanager
    async def admission(
        self, job_id: UUID, owner: str, operation: str, metadata: dict[str, Any]
    ) -> AsyncIterator[bool]:
        """An incomplete upload is invisible. Its key is reserved during transfer."""
        async with self.lock, self.connection.transaction():
            exists = await self.connection.fetchrow(
                "SELECT owner, operation, phase FROM cc_work_job WHERE id=$1",
                job_id,
            )
            if exists is not None and (
                exists["owner"] != owner
                or exists["operation"] != operation
                or exists["phase"] == "receiving"
            ):
                raise JobConflict("job_identity_conflict")
            if exists is None:
                await self.connection.execute(
                    "INSERT INTO cc_work_job (id, owner, operation, request_hash, metadata, phase) "
                    "VALUES ($1, $2, $3, '', $4::jsonb, 'receiving')",
                    job_id,
                    owner,
                    operation,
                    canonical(metadata),
                )
        try:
            yield exists is None
        finally:
            if exists is None:
                async with self.lock:
                    await self.connection.execute(
                        "DELETE FROM cc_work_job WHERE id=$1 AND phase='receiving'", job_id
                    )

    async def admit(
        self,
        job_id: UUID,
        owner: str,
        operation: str,
        metadata: dict[str, Any],
        chunks: AsyncIterator[bytes],
    ) -> dict[str, Any]:
        digest = hashlib.sha256(canonical(metadata).encode())
        async with self.admission(job_id, owner, operation, metadata) as new:
            part = 0
            async for piece in chunks:
                digest.update(piece)
                if new:
                    await self.append(job_id, "request", part, piece)
                part += 1
            async with self.lock:
                if new:
                    await self.connection.execute(
                        "UPDATE cc_work_job SET phase='waiting', request_hash=$2, "
                        "updated_at=clock_timestamp() WHERE id=$1 AND phase='receiving'",
                        job_id,
                        digest.hexdigest(),
                    )
                row = await self.connection.fetchrow(
                    "SELECT * FROM cc_work_job WHERE id=$1", job_id
                )
                assert row is not None
                if row["request_hash"] != digest.hexdigest():
                    raise JobConflict("job_identity_conflict")
                return public_job(row)

    async def claim(self, *, short_only: bool) -> asyncpg.Record | None:
        async with self.lock:
            return await self.connection.fetchrow(
                "UPDATE cc_work_job SET phase='running', updated_at=clock_timestamp() "
                "WHERE id=(SELECT id FROM cc_work_job WHERE phase='waiting' "
                "AND (NOT $1 OR metadata->>'long'='false') "
                "ORDER BY (owner='internal:system' AND created_at > clock_timestamp() - interval '60 seconds'), "
                "created_at, id LIMIT 1) RETURNING *",
                short_only,
            )

    async def waiting(self) -> bool:
        async with self.lock:
            return bool(
                await self.connection.fetchval(
                    "SELECT EXISTS (SELECT 1 FROM cc_work_job WHERE phase='waiting')"
                )
            )

    async def append(self, job_id: UUID, kind: str, part: int, content: bytes) -> None:
        if len(content) > ARTIFACT_CHUNK:
            raise ValueError("work_chunk_too_large")
        async with self.lock:
            await self.connection.execute(
                "INSERT INTO cc_work_artifact (job_id, kind, part, content) VALUES ($1, $2, $3, $4)",
                job_id,
                kind,
                part,
                content,
            )

    async def artifact(self, job_id: UUID, kind: str) -> AsyncIterator[bytes]:
        part = 0
        while True:
            async with self.lock:
                value = await self.connection.fetchval(_ARTIFACT_QUERY, job_id, kind, part)
            if value is None:
                return
            yield value
            part += 1

    async def complete(
        self,
        job_id: UUID,
        phase: str,
        *,
        error: str | None = None,
        result_status: int | None = None,
        plan: tuple[str, str, str] | None = None,
    ) -> None:
        async with self.lock, self.connection.transaction():
            updated = await self.connection.fetchval(
                "UPDATE cc_work_job SET phase=$2, error_code=$3, result_status=$4, "
                "updated_at=clock_timestamp() WHERE id=$1 AND phase='running' RETURNING id",
                job_id,
                phase,
                error,
                result_status,
            )
            if updated is not None and plan is not None:
                kind, key, owner = plan
                if kind not in PLAN_KINDS or not key or len(key) > 64:
                    raise ValueError("work_plan_identity_invalid")
                await self.connection.execute(
                    "INSERT INTO cc_work_plan (kind, plan_key, owner, job_id) VALUES ($1,$2,$3,$4) "
                    "ON CONFLICT (kind, plan_key, owner) DO UPDATE SET job_id=EXCLUDED.job_id",
                    kind,
                    key,
                    owner,
                    job_id,
                )
            await self.connection.execute(
                "DELETE FROM cc_work_artifact WHERE job_id=$1 AND "
                "(kind='request' OR ($2 AND kind!='request'))",
                job_id,
                updated is None or result_status is None,
            )

    async def prepare_commit(self, job_id: UUID, operation: str) -> None:
        async with self.lock, self.connection.transaction():
            await self.connection.execute(
                "UPDATE cc_work_job SET phase='prepared', prepared_operation=$2, "
                "updated_at=clock_timestamp() WHERE id=$1 AND phase='running'",
                job_id,
                operation,
            )
            await self.connection.execute(
                "DELETE FROM cc_work_artifact WHERE job_id=$1 AND kind='request'", job_id
            )

    async def claim_commit(self) -> asyncpg.Record | None:
        async with self.lock:
            return await self.connection.fetchrow(
                "UPDATE cc_work_job SET phase='committing', updated_at=clock_timestamp() "
                "WHERE id=(SELECT id FROM cc_work_job WHERE phase IN ('prepared','committing') "
                "ORDER BY phase!='committing',created_at,id LIMIT 1) RETURNING id,prepared_operation"
            )

    async def seal_receipt(self, job_id: UUID, status: int, replacement: bytes | None) -> None:
        async with self.lock, self.connection.transaction():
            updated = await self.connection.fetchval(
                "UPDATE cc_work_job SET phase=$2, result_status=$3, updated_at=clock_timestamp() "
                "WHERE id=$1 AND phase='committing' RETURNING id",
                job_id,
                "finished" if status < 400 else "failed",
                status,
            )
            if updated is None:
                return
            await self.connection.execute(
                "DELETE FROM cc_work_artifact WHERE job_id=$1 AND kind='plan'", job_id
            )
            if replacement is not None:
                await self.connection.execute(
                    "DELETE FROM cc_work_artifact WHERE job_id=$1 AND kind='result'", job_id
                )
                await self.connection.executemany(
                    "INSERT INTO cc_work_artifact VALUES ($1, 'result', $2, $3)",
                    (
                        (job_id, part, replacement[offset : offset + ARTIFACT_CHUNK])
                        for part, offset in enumerate(range(0, len(replacement), ARTIFACT_CHUNK))
                    ),
                )

    async def requeue(self, job_id: UUID) -> None:
        async with self.lock, self.connection.transaction():
            await self.connection.execute(
                "DELETE FROM cc_work_artifact WHERE job_id=$1 AND kind!='request'", job_id
            )
            await self.connection.execute(
                "UPDATE cc_work_job SET phase='waiting', updated_at=clock_timestamp() "
                "WHERE id=$1 AND phase='running'",
                job_id,
            )

    async def list(self, owner: str) -> list[dict[str, Any]]:
        async with self.lock:
            rows = await self.connection.fetch(
                "SELECT * FROM cc_work_job WHERE owner=$1 AND phase!='receiving' "
                "ORDER BY phase NOT IN ('waiting','running','prepared','committing'), created_at DESC LIMIT 100",
                owner,
            )
        return [public_job(row) for row in rows]

    async def get(self, job_id: UUID, owner: str) -> dict[str, Any] | None:
        async with self.lock:
            row = await self.connection.fetchrow(
                "SELECT * FROM cc_work_job WHERE id=$1 AND owner=$2 AND phase!='receiving'",
                job_id,
                owner,
            )
        return public_job(row) if row else None

    async def cancel(self, job_id: UUID, owner: str) -> bool:
        async with self.lock:
            return bool(
                await self.connection.fetchval(
                    "UPDATE cc_work_job SET phase='cancelled', updated_at=clock_timestamp() "
                    "WHERE id=$1 AND owner=$2 AND phase IN ('waiting','running','prepared') RETURNING id",
                    job_id,
                    owner,
                )
            )

    async def cancel_plan(self, kind: str, key: str, owner: str) -> None:
        if kind not in PLAN_KINDS or len(key) > 64 or not key:
            return
        async with self.lock:
            await self.connection.execute(
                "DELETE FROM cc_work_plan WHERE kind=$1 AND plan_key=$2 AND owner=$3",
                kind,
                key,
                owner,
            )

    async def prune(self) -> None:
        async with self.lock:
            await self.connection.execute(
                "DELETE FROM cc_work_job WHERE id IN (SELECT id FROM cc_work_job "
                "WHERE phase IN ('finished','failed','cancelled') "
                "AND updated_at < clock_timestamp() - interval '24 hours' LIMIT 100)"
            )


def public_job(row: asyncpg.Record) -> dict[str, Any]:
    created_at: datetime = row["created_at"]
    updated_at: datetime = row["updated_at"]
    metadata = json.loads(row["metadata"])
    return {
        "id": str(row["id"]),
        "operation": row["operation"],
        "phase": {"prepared": "waiting", "committing": "running"}.get(row["phase"], row["phase"]),
        "cancellable": row["phase"] in {"waiting", "running", "prepared"},
        "created_at": created_at.isoformat(),
        "updated_at": updated_at.isoformat(),
        "error": row["error_code"],
        "status": row["result_status"],
        "path": metadata["path"],
    }
