"""Shared read interface for sealed work plans.

Read-only workers and the truth writer use the same owner and plan lookup.
Only the coordinator inserts the private parts or publishes a plan key.
"""

import json
from typing import Any, cast

import asyncpg

ARTIFACT_CHUNK = 128 * 1024

PLAN_KINDS = frozenset({"catalog_cleanup", "title_sync"})


async def load_plan(
    reader: asyncpg.Connection[Any] | asyncpg.Pool[Any], kind: str, key: str, owner: str
) -> dict[str, Any] | None:
    if kind not in PLAN_KINDS or not key or len(key) > 64:
        return None
    parts = await reader.fetch(
        "SELECT a.content FROM cc_work_plan p JOIN cc_work_artifact a ON a.job_id=p.job_id "
        "WHERE p.kind=$1 AND p.plan_key=$2 AND p.owner=$3 AND a.kind='plan' ORDER BY a.part",
        kind,
        key,
        owner,
    )
    if not parts:
        return None
    plan = cast(dict[str, Any], json.loads(b"".join(row["content"] for row in parts)))
    if kind == "title_sync" and (progress := await title_progress(reader, key, owner)):
        plan["_work_progress"] = progress
    return plan


async def title_progress(
    reader: asyncpg.Connection[Any] | asyncpg.Pool[Any], key: str, owner: str
) -> dict[str, Any] | None:
    row = await reader.fetchval(
        "SELECT COALESCE((SELECT payload->'title_sync_progress' FROM catalog_request "
        "WHERE payload ? 'title_sync_progress' AND payload->'title_sync_progress'->>'plan_id'=$1 "
        "AND payload->'title_sync_progress'->>'owner'=$2 "
        "ORDER BY (payload->'title_sync_progress'->>'applied_page')::integer DESC LIMIT 1), '{}'::jsonb) "
        "FROM cc_work_plan WHERE kind='title_sync' AND plan_key=$1 AND owner=$2",
        key,
        owner,
    )
    return cast(dict[str, Any], json.loads(row)) if row is not None else None
