"""Prepare a portable import off the truth lane and publish one draft atomically."""

from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

from . import slots
from .authoring import guard, open_count
from .model import Model, checksum
from .persistence import State
from .portable import request_package, stage
from .registry import RegistryConfig

if TYPE_CHECKING:
    from .runtime import Runtime


class PreparedPortableStage(Model):
    request_id: UUID
    kind: Literal["stage", "title-stage"] = "stage"
    request_digest: str
    expected_active_revision: int | None
    target_draft_id: str | None
    target_draft_version: int | None
    catalog_heads: dict[str, int]
    editorial_versions: dict[str, int]
    draft: dict[str, Any]


async def prepare_stage(
    runtime: Runtime, payload: dict[str, Any]
) -> tuple[dict[str, Any], PreparedPortableStage | None]:
    request_id = UUID(payload["request_id"])
    digest = checksum(payload)
    if previous := await runtime.store.get("catalog_import", f"stage:{request_id}"):
        if previous["digest"] != digest:
            raise ValueError("import_plan_conflict")
        return previous["result"], None

    state = runtime.state.catalog_clone()
    package = request_package(payload)
    draft = stage(
        runtime,
        state,
        payload,
        payload.get("_actor"),
        runtime.clock.now_utc(),
        restored=await runtime._portable_numbers(payload, package),
        verified_package=package,
    )
    return draft, seal_stage(request_id, digest, draft)


def seal_stage(
    request_id: UUID,
    digest: str,
    draft: dict[str, Any],
    *,
    kind: Literal["stage", "title-stage"] = "stage",
) -> PreparedPortableStage:
    details = draft["portable"]
    return PreparedPortableStage(
        request_id=request_id,
        kind=kind,
        request_digest=digest,
        expected_active_revision=details["expected_active_revision"],
        target_draft_id=details["target_draft_id"],
        target_draft_version=details["target_draft_version"],
        catalog_heads=details["catalog_heads"],
        editorial_versions=details["editorial_versions"],
        draft=draft,
    )


async def publish_stage(
    runtime: Runtime,
    state: State,
    operation: str,
    payload: PreparedPortableStage,
    now: datetime,
    config: RegistryConfig | None,
) -> dict[str, Any]:
    record = f"{payload.kind}:{payload.request_id}"
    if previous := await runtime.store.get("catalog_import", record):
        if previous["digest"] != payload.request_digest:
            raise ValueError("import_plan_conflict")
        result: dict[str, Any] = previous["result"]
        return result

    if payload.kind == "title-stage":
        import_id = payload.draft["portable"]["title_import"]["import_id"]
        if applied := await runtime.store.get("catalog_import", f"title:{import_id}"):
            return {**applied["result"], "already_applied": True}
    if state.active_revision != payload.expected_active_revision:
        raise ValueError("import_plan_conflict")
    if any(
        state.tables["catalog_head"].get(key, {}).get("revision", 0) != revision
        for key, revision in payload.catalog_heads.items()
    ) or any(
        state.tables["editorial_current"].get(key, {}).get("editorial_version", 0) != version
        for key, version in payload.editorial_versions.items()
    ):
        raise ValueError("import_plan_conflict")

    drafts = state.tables["registry_draft"]
    key = payload.draft["draft_id"]
    if payload.target_draft_id is not None:
        target = drafts.get(payload.target_draft_id)
        if (
            not target
            or target["draft_version"] != payload.target_draft_version
            or key != payload.target_draft_id
        ):
            raise ValueError("import_plan_conflict")
        guard(state, target)
    else:
        if key in drafts:
            raise ValueError("import_plan_conflict")
        if open_count(state) >= 100:
            raise ValueError("draft_limit")

    drafts[key] = payload.draft
    state.tables["catalog_import"][record] = {
        "digest": payload.request_digest,
        "result": payload.draft,
    }
    await runtime._commit(state)
    return payload.draft


slots.OPERATIONS["portable_stage_prepared"] = slots.Operation(
    publish_stage, state="catalog", early=False
)
