"""Human organization is versioned separately from the contract configuration."""

import re
from typing import TYPE_CHECKING, Any, Literal, Self

from pydantic import Field, model_validator

from .model import Model, canonical
from .persistence import State

if TYPE_CHECKING:
    from .runtime import Runtime

Category = Literal[
    "climate",
    "light_cover",
    "presence",
    "sleep_wake",
    "media",
    "other",
    "openings",
    "time",
    "devices",
]
CATEGORIES = {
    "devices": "mdi:devices",
    "climate": "mdi:thermometer",
    "light_cover": "mdi:lightbulb",
    "presence": "mdi:account-check",
    "sleep_wake": "mdi:sleep",
    "media": "mdi:play-circle",
    "other": "mdi:shape",
    "openings": "mdi:window-open-variant",
    "time": "mdi:weather-sunset",
}


class ObjectRef(Model):
    kind: Literal["contract", "template"]
    id: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_.-]{0,127}$")

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.id}"


class Room(Model):
    mode: Literal["suggested", "manual", "none"] = "none"
    area_id: str | None = Field(default=None, max_length=256)
    name: str = Field(default="", max_length=128)
    icon: str = Field(default="mdi:home", pattern=r"^mdi:[a-z0-9-]{1,64}$")
    missing: bool = False

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if (
            self.mode == "none"
            and (self.area_id or self.name)
            or self.mode != "none"
            and not self.name.strip()
            or self.mode == "suggested"
            and not self.area_id
        ):
            raise ValueError("invalid_room")
        return self


class EditorialWrite(Model):
    object: ObjectRef
    expected_editorial_version: int = Field(ge=0, strict=True)
    category: Category
    room: Room = Field(default_factory=Room)
    draft_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
    draft_version: int | None = Field(default=None, ge=1, strict=True)

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if (self.draft_id is None) != (self.draft_version is None):
            raise ValueError("draft_identity_required")
        if self.object.kind == "template" and self.room.mode != "none":
            raise ValueError("contract_room_required")
        return self


def contains(document: dict[str, Any] | None, ref: ObjectRef, runtime: Runtime) -> bool:
    if ref.kind == "contract":
        return any(
            item.get("contract_id") == ref.id for item in (document or {}).get("contracts", ())
        )
    return any(
        item.get("type_id") == ref.id for item in (document or {}).get("definitions", ())
    ) or any(key[0] == ref.id for key in runtime.types.types)


def put(
    runtime: Runtime, state: State, request: EditorialWrite, actor: dict[str, Any]
) -> dict[str, Any]:
    active = runtime._config(state)
    active_doc = active.model_dump(mode="json") if active else None
    existing = contains(active_doc, request.object, runtime)
    pending = request.draft_id is not None
    if pending and existing:
        raise ValueError("editorial_scope_conflict")
    draft = state.tables["registry_draft"].get(request.draft_id or "")
    if request.draft_id is not None and (
        draft is None or request.draft_version != draft["draft_version"]
    ):
        raise ValueError("draft_version_conflict")
    if draft is not None:
        from .authoring import guard

        guard(state, draft)
    if not existing and (draft is None or not contains(draft["config"], request.object, runtime)):
        raise ValueError("editorial_object_not_found")
    if pending and len(state.tables["editorial_draft"]) >= 10000:
        raise ValueError("editorial_draft_limit")
    key = request.object.key
    current = state.tables["editorial_current"].get(key)
    table = "editorial_draft" if pending else "editorial_current"
    target_key = f"{request.draft_id}:{key}" if pending else key
    previous = state.tables[table].get(target_key) or current
    version = previous["editorial_version"] if previous else 0
    if request.expected_editorial_version != version:
        raise ValueError("editorial_version_conflict")
    result = {
        "object": request.object.model_dump(),
        "category": request.category,
        "room": request.room.model_dump(),
        "editorial_version": version + 1,
        "updated_at": runtime.clock.now_utc().isoformat(),
    }
    if pending:
        result.update(
            draft_id=request.draft_id,
            draft_version=request.draft_version,
            base_editorial_version=previous.get(
                "base_editorial_version", current["editorial_version"] if current else 0
            )
            if previous
            else 0,
        )
    state.tables[table][target_key] = result
    runtime._audit(
        state,
        "editorial_saved",
        {"_actor": actor},
        object=request.object.model_dump(),
        before=previous,
        after=result,
        pending=pending,
    )
    return result


def carry_draft(state: State, draft: dict[str, Any], runtime: Runtime) -> None:
    for key, row in list(state.tables["editorial_draft"].items()):
        if row["draft_id"] != draft["draft_id"]:
            continue
        if not contains(draft["config"], ObjectRef.model_validate(row["object"]), runtime):
            del state.tables["editorial_draft"][key]
        else:
            row["draft_version"] = draft["draft_version"]


def apply_draft(state: State, draft: dict[str, Any], runtime: Runtime) -> None:
    for key, row in list(state.tables["editorial_draft"].items()):
        if row["draft_id"] != draft["draft_id"]:
            continue
        ref = ObjectRef.model_validate(row["object"])
        if row["draft_version"] != draft["draft_version"]:
            raise ValueError("draft_version_conflict")
        current = state.tables["editorial_current"].get(ref.key)
        if row["base_editorial_version"] != (current["editorial_version"] if current else 0):
            raise ValueError("editorial_version_conflict")
        if contains(draft["config"], ref, runtime):
            state.tables["editorial_current"][ref.key] = {
                name: value
                for name, value in row.items()
                if name not in {"draft_id", "draft_version", "base_editorial_version"}
            }
        del state.tables["editorial_draft"][key]


def keep_newer(current: dict[str, dict[str, Any]], restored: dict[str, dict[str, Any]]) -> None:
    for key, row in current.items():
        older = restored.get(key)
        if older is None or row["editorial_version"] > older["editorial_version"]:
            restored[key] = row
        elif row["editorial_version"] == older["editorial_version"] and canonical(row) != canonical(
            older
        ):
            raise ValueError("editorial_restore_conflict")


def refresh_areas(runtime: Runtime, state: State, entities: list[dict[str, Any]]) -> bool:
    areas = {
        entity["area_id"]: entity
        for entity in entities
        if entity.get("area_id") and entity.get("area_name")
    }
    changed = False
    for row in state.tables["editorial_current"].values():
        room = Room.model_validate(row["room"])
        if room.mode != "suggested":
            continue
        area = areas.get(room.area_id)
        candidate = room.model_copy(update={"missing": area is None})
        if area:
            icon = area.get("area_icon") or room.icon
            candidate = Room(
                mode="suggested",
                area_id=room.area_id,
                name=area["area_name"][:128],
                icon=icon
                if isinstance(icon, str) and re.fullmatch(r"mdi:[a-z0-9-]{1,64}", icon)
                else "mdi:home",
            )
        if candidate != room:
            before = dict(row)
            row.update(
                room=candidate.model_dump(),
                editorial_version=row["editorial_version"] + 1,
                updated_at=runtime.clock.now_utc().isoformat(),
            )
            runtime._audit(state, "editorial_area_refreshed", {}, before=before, after=dict(row))
            changed = True
    return changed
