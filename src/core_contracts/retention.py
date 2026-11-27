"""Bounded, reference-aware raw observation retention and hourly numeric summaries."""

from datetime import datetime, timedelta
from decimal import Decimal
from math import isfinite
from typing import Any, Self

from pydantic import Field, model_validator

from .evidence import Observation
from .model import Model


class EvidenceOutsideRetention(LookupError):
    def __init__(self, reference: str, cutoff: str) -> None:
        self.reference, self.cutoff = reference, cutoff
        super().__init__("evidence_outside_retention")


class RetentionPolicy(Model):
    raw_days: int = Field(ge=1, le=3650)
    rollup_days: int = Field(ge=1, le=36500)
    batch_size: int = Field(default=1000, ge=1, le=10000)
    interval_s: int = Field(default=3600, ge=60, le=86400)

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.rollup_days < self.raw_days:
            raise ValueError("rollup_retention_must_cover_raw_retention")
        return self


def reference_scope(reference: str) -> tuple[str, int] | None:
    parts = reference.split(":")
    try:
        if len(parts) == 6 and parts[0] == "v2":
            return ":".join((*parts[:4], parts[5])), int(parts[4])
        if len(parts) == 3:
            return "v1:" + parts[0] + ":" + parts[2], int(parts[1])
    except ValueError:
        pass
    return None


def pinned_references(state: Any) -> set[str]:
    references: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key == "evidence" and isinstance(child, (list, tuple)):
                    references.update(item for item in child if isinstance(item, str))
                else:
                    visit(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                visit(child)

    for table in ("contract_state_current", "node_state", "sm_instance", "service_state"):
        visit(state.tables[table])
    references.update(
        Observation.model_validate(row).reference
        for row in state.tables["source_observation_current"].values()
    )
    return references


def summaries(
    rows: list[tuple[str, dict[str, Any]]],
) -> tuple[dict[tuple[Any, ...], dict[str, Any]], dict[str, int]]:
    groups: dict[tuple[Any, ...], dict[str, Any]] = {}
    watermarks: dict[str, int] = {}
    for reference, row in rows:
        at = datetime.fromisoformat(row["received_at"])
        hour = at.replace(minute=0, second=0, microsecond=0)
        key = (row["source_id"], row["binding_id"], row.get("registry_revision") or 0, hour)
        item = groups.setdefault(
            key,
            {
                "count": 0,
                "numeric_count": 0,
                "minimum": None,
                "maximum": None,
                "total": Decimal(0),
                "first_at": at,
                "last_at": at,
            },
        )
        item["count"] += 1
        item["first_at"], item["last_at"] = min(item["first_at"], at), max(item["last_at"], at)
        value = row.get("value_normalized") if row.get("normalized") else row.get("value_raw")
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and isfinite(value)
            and row.get("availability", "available") == "available"
            and not any(
                row.get(flag)
                for flag in ("ha_restored", "mqtt_retained", "invalid_timestamp", "transform_error")
            )
        ):
            item["numeric_count"] += 1
            item["minimum"] = value if item["minimum"] is None else min(value, item["minimum"])
            item["maximum"] = value if item["maximum"] is None else max(value, item["maximum"])
            item["total"] += Decimal(str(value))
        scope = reference_scope(reference)
        if scope:
            watermarks[scope[0]] = max(watermarks.get(scope[0], -1), scope[1])
    return groups, watermarks


class RetentionBatch(Model):
    raw_before: datetime
    rollup_before: datetime
    batch_size: int
    pinned_count: int
    references: tuple[str, ...]
    watermarks: dict[str, int]
    rollups: tuple[
        tuple[
            str,
            str,
            int,
            datetime,
            int,
            int,
            Decimal | None,
            Decimal | None,
            Decimal,
            datetime,
            datetime,
        ],
        ...,
    ]

    def receipt(self, *, dry_run: bool) -> dict[str, Any]:
        return {
            "raw_before": self.raw_before.isoformat(),
            "rollup_before": self.rollup_before.isoformat(),
            "selected": len(self.references),
            "rollup_groups": len(self.rollups),
            "pinned_references": self.pinned_count,
            "dry_run": dry_run,
        }


async def collect_retention_batch(
    connection: Any, now: datetime, policy: RetentionPolicy, pins: set[str], *, lock: bool = False
) -> RetentionBatch:
    import json

    raw_before = now - timedelta(days=policy.raw_days)
    rows = await connection.fetch(
        "SELECT h.key,h.payload FROM cc_observation_index i JOIN source_observation_history h ON h.key=i.reference "
        "WHERE i.received_at<$1 AND NOT(h.key=ANY($2::text[])) ORDER BY i.received_at,i.reference LIMIT $3"
        + (" FOR UPDATE OF h" if lock else ""),
        raw_before,
        list(pins),
        policy.batch_size,
    )
    values = [(row["key"], json.loads(row["payload"])) for row in rows]
    groups, watermarks = summaries(values)
    return RetentionBatch(
        raw_before=raw_before,
        rollup_before=now - timedelta(days=policy.rollup_days),
        batch_size=policy.batch_size,
        pinned_count=len(pins),
        references=tuple(reference for reference, _ in values),
        watermarks=watermarks,
        rollups=tuple(
            (
                *key,
                item["count"],
                item["numeric_count"],
                item["minimum"],
                item["maximum"],
                item["total"],
                item["first_at"],
                item["last_at"],
            )
            for key, item in groups.items()
        ),
    )


async def verify_retention_batch(connection: Any, batch: RetentionBatch, pins: set[str]) -> None:
    if pins.intersection(batch.references):
        raise ValueError("retention_plan_conflict")
    current = await connection.fetch(
        "SELECT h.key FROM cc_observation_index i JOIN source_observation_history h ON h.key=i.reference "
        "WHERE i.received_at<$1 AND h.key=ANY($2::text[]) FOR UPDATE OF h",
        batch.raw_before,
        batch.references,
    )
    if {row["key"] for row in current} != set(batch.references):
        raise ValueError("retention_plan_conflict")


async def apply_retention_batch(connection: Any, batch: RetentionBatch) -> None:
    await connection.executemany(
        "INSERT INTO cc_observation_rollup(source_id,binding_id,registry_revision,hour,count,numeric_count,minimum,maximum,total,first_at,last_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11) "
        "ON CONFLICT(source_id,binding_id,registry_revision,hour) DO UPDATE SET "
        "count=cc_observation_rollup.count+EXCLUDED.count,numeric_count=cc_observation_rollup.numeric_count+EXCLUDED.numeric_count,"
        "minimum=LEAST(cc_observation_rollup.minimum,EXCLUDED.minimum),maximum=GREATEST(cc_observation_rollup.maximum,EXCLUDED.maximum),"
        "total=cc_observation_rollup.total+EXCLUDED.total,first_at=LEAST(cc_observation_rollup.first_at,EXCLUDED.first_at),last_at=GREATEST(cc_observation_rollup.last_at,EXCLUDED.last_at)",
        batch.rollups,
    )
    await connection.executemany(
        "INSERT INTO cc_evidence_watermark VALUES($1,$2,$3) ON CONFLICT(scope) DO UPDATE SET max_sequence=GREATEST(cc_evidence_watermark.max_sequence,EXCLUDED.max_sequence),retained_after=GREATEST(cc_evidence_watermark.retained_after,EXCLUDED.retained_after)",
        [(scope, sequence, batch.raw_before) for scope, sequence in batch.watermarks.items()],
    )
    await connection.execute(
        "DELETE FROM source_observation_history WHERE key=ANY($1::text[])",
        batch.references,
    )
    await connection.execute(
        "DELETE FROM cc_observation_rollup WHERE ctid IN (SELECT ctid FROM cc_observation_rollup WHERE hour<$1 ORDER BY hour LIMIT $2)",
        batch.rollup_before,
        batch.batch_size,
    )


async def maintain_postgres(
    connection: Any,
    now: datetime,
    policy: RetentionPolicy,
    pins: set[str],
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    async with connection.transaction():
        batch = await collect_retention_batch(connection, now, policy, pins, lock=not dry_run)
        if not dry_run:
            await apply_retention_batch(connection, batch)
        return batch.receipt(dry_run=dry_run)
