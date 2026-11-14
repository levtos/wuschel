"""Draft authoring invariants; all writes run in the existing single writer transaction."""

from __future__ import annotations

import copy
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from . import slots
from .errors import AuthoringError as AuthoringError
from .model import checksum
from .numeric_equivalence import preserve_config, preserve_numbers
from .persistence import State
from .registry import (
    RegistryConfig,
    storage_document,
)
from .validation import validate_document as validate

if TYPE_CHECKING:
    from .runtime import Runtime

import re
from typing import TYPE_CHECKING

from .model import canonical

if TYPE_CHECKING:
    from .runtime import Runtime

from copy import deepcopy
from enum import Enum
from uuid import UUID

from .numeric_equivalence import equivalent
from .persistence import PersistenceUnavailable

LOGGER = logging.getLogger(__name__)


def base_fields(state: Any) -> dict[str, Any]:
    row = state.tables["registry_revision"].get(str(state.active_revision), {})
    return {
        "base_revision": state.active_revision,
        "base_checksum": row.get("checksum"),
        "base_status": "known",
        "lifecycle": "open",
        "authoring_version": 2,
    }


def view(state: Any, draft: dict[str, Any]) -> dict[str, Any]:
    return {
        **draft,
        "base_current": draft.get("base_status", "needs_review") == "known"
        and draft.get("base_revision") == state.active_revision
        and draft.get("base_checksum") == base_fields(state)["base_checksum"],
        "active_revision": state.active_revision,
    }


def guard(state: Any, draft: dict[str, Any], *, current: bool = True) -> None:
    if draft.get("lifecycle", "open") != "open":
        raise AuthoringError("draft_closed", draft_id=draft["draft_id"])
    if current and draft.get("base_status", "needs_review") != "known":
        raise AuthoringError("draft_base_unknown", draft_id=draft["draft_id"])
    if current and not view(state, draft)["base_current"]:
        raise AuthoringError(
            "draft_base_conflict",
            draft_id=draft["draft_id"],
            base_revision=draft.get("base_revision"),
            active_revision=state.active_revision,
        )


def open_count(state: Any) -> int:
    return sum(
        d.get("lifecycle", "open") == "open" for d in state.tables["registry_draft"].values()
    )


def bind_plan(draft: dict[str, Any]) -> None:
    plan = deepcopy(draft["portable"])
    if plan.get("status") == "consumed":
        raise AuthoringError("draft_closed")
    plan.update(
        draft_id=draft["draft_id"],
        draft_version=draft["draft_version"],
        candidate_digest=checksum(draft["config"]),
    )
    plan["plan_digest"] = checksum({k: v for k, v in plan.items() if k != "plan_digest"})
    draft["portable"] = plan


def effects_match(runtime: Any, state: Any, plan: dict[str, Any]) -> bool:
    """Conservative proof of ALL package effects, including selected keep outcomes."""
    from .editorial import ObjectRef, Room

    package = plan.get("package", {})
    for row in package.get("editorial", []):
        ref = ObjectRef.model_validate(row["object"])
        current = state.tables["editorial_current"].get(ref.key)
        if (
            not current
            or current["category"] != row["category"]
            or current["room"] != Room.model_validate(row["room"]).model_dump()
        ):
            return False
    config = runtime._config(state)
    return bool(slots.AUTHORING["catalog_effects"](state, config, package))


def comparison(runtime: Any, state: Any, draft: dict[str, Any]) -> dict[str, Any]:
    guard(state, draft)
    active = runtime._config(state)
    # Validate submitted types before restoring their stored numeric spelling.
    config = validate(draft["config"], runtime.types)
    if active:
        config = validate(
            preserve_config(storage_document(config), storage_document(active)), runtime.types
        )
    from .editorial import apply_draft
    from .portable import apply
    from .reconfiguration import context_effects

    probe = state.clone()
    apply_draft(probe, draft, runtime)
    catalog_effects: list[dict[str, str]] = []
    latches = runtime.latches
    if draft.get("portable"):
        latches = apply(runtime, probe, draft, config, runtime.clock.now_utc(), catalog_effects)
    # RetentionPolicy supplies the effective defaults on both sides.
    before = (
        active.history_retention.model_dump(mode="json")
        if active and active.history_retention
        else None
    )
    after = config.history_retention.model_dump(mode="json") if config.history_retention else None
    shortening = [
        k
        for k, v in (after or {}).items()
        if k.endswith("days") and (before is None or v < before[k])
    ]
    same = active is not None and equivalent(storage_document(active), storage_document(config))
    pending_editorial = [
        r for r in state.tables["editorial_draft"].values() if r["draft_id"] == draft["draft_id"]
    ]

    def editorial_effects(rows: Any) -> dict[str, Any]:
        return {
            k: {field: row.get(field) for field in ("object", "category", "room")}
            for k, row in rows.items()
        }

    no_effect = (
        same
        and editorial_effects(probe.tables["editorial_current"])
        == editorial_effects(state.tables["editorial_current"])
        and {k: row["entries"] for k, row in probe.tables["catalog_head"].items()}
        == {k: row["entries"] for k, row in state.tables["catalog_head"].items()}
    )
    result = {
        "draft_id": draft["draft_id"],
        "draft_version": draft["draft_version"],
        "expected_active_revision": state.active_revision,
        "retention": {
            "before": before,
            "after": after,
            "changed": before != after,
            "shortening": shortening,
        },
        "no_effect": no_effect,
        "context_effects": catalog_effects
        + (context_effects(probe, active, config, runtime.types, latches) if active else []),
        "required_confirmations": ["impact", *(["retention"] if shortening else [])],
    }
    result["comparison_digest"] = checksum(
        {
            "result": result,
            "draft": draft,
            "heads": state.tables["catalog_head"],
            "editorial": state.tables["editorial_current"],
            "pending": pending_editorial,
        }
    )
    return result


def request_identity(payload: dict[str, Any]) -> tuple[str, str]:
    value = payload.get("request_id")
    if value is None:
        raise AuthoringError("request_id_required")
    try:
        key = str(UUID(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise AuthoringError("invalid_request_id") from exc
    # Only the command envelope is part of identity, never the cached draft response.
    digest = checksum(
        {
            k: payload.get(k)
            for k in (
                "draft_id",
                "draft_version",
                "expected_active_revision",
                "comparison_digest",
                "confirmations",
                "note",
                "_actor",
                "completion_kind",
            )
        }
    )
    return key, digest


async def prior_receipt(runtime: Any, state: Any, payload: dict[str, Any]) -> dict[str, Any] | None:
    key, digest = request_identity(payload)
    old = state.tables["activation_receipt"].get(key) or await runtime.store.get(
        "activation_receipt", key
    )
    if old:
        if old["request_digest"] != digest:
            raise AuthoringError("request_identity_conflict")
        return dict(old["result"])
    return None


def prepare_activation(
    runtime: Any, state: Any, draft: dict[str, Any], payload: dict[str, Any]
) -> None:
    guard(state, draft)
    if (
        type(payload.get("draft_version")) is not int
        or payload["draft_version"] != draft["draft_version"]
    ):
        raise AuthoringError("draft_version_conflict")
    previous = draft.get("activation_request_id")
    if previous:
        raise AuthoringError("draft_closed", request_id=previous)
    check = comparison(runtime, state, draft)
    if payload.get("comparison_digest") != check["comparison_digest"]:
        raise AuthoringError("comparison_expired")
    confirmations = payload.get("confirmations")
    if (
        not isinstance(confirmations, list)
        or len(confirmations) > 8
        or not all(isinstance(c, str) for c in confirmations)
        or not set(check["required_confirmations"]).issubset(confirmations)
    ):
        raise AuthoringError("confirmation_required")


def complete(
    state: Any,
    draft: dict[str, Any],
    payload: dict[str, Any],
    revision: dict[str, Any],
    now: Any,
    *,
    kind: str = "activated",
) -> dict[str, Any]:
    key, digest = request_identity(payload)
    draft.update(
        lifecycle="completed",
        completed_revision=revision["revision"],
        completed_at=now.isoformat(),
        completion_kind=kind,
        activation_request_id=key,
    )
    if draft.get("portable"):
        draft["portable"] = {
            **draft["portable"],
            "status": "consumed",
            "consumed_at": now.isoformat(),
            "result_revision": revision["revision"],
        }
    result = {**revision, "request_id": key, "draft_id": draft["draft_id"], "completion_kind": kind}
    state.tables["activation_receipt"][key] = {
        "request_id": key,
        "request_digest": digest,
        "draft_id": draft["draft_id"],
        "draft_version": draft["draft_version"],
        "plan_digest": draft.get("portable", {}).get("plan_digest"),
        "result_revision": revision["revision"],
        "result_checksum": revision["checksum"],
        "completed_at": now.isoformat(),
        "actor": payload.get("_actor"),
        "result": result,
    }
    return result


# Lists with identities have set semantics; arguments and other ordered lists do not.
IDENTITIES = {
    "sources": ("source_id",),
    "bindings": ("binding_id",),
    "contracts": ("contract_id",),
    "dependencies": ("dependency_id",),
    "catalogs": ("catalog_id", "version"),
    "definitions": ("type_id", "type_version"),
    "context_producers": ("producer_id",),
    "freshness_templates": ("template_id",),
    "inputs": ("name",),
}


class _Absent(Enum):
    MISSING = "missing"
    UNKNOWN = "unknown"


MISSING = _Absent.MISSING
UNKNOWN = _Absent.UNKNOWN


def pointer(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def comparison_value(value: Any) -> dict[str, Any]:
    return {
        "present": value is not MISSING,
        "known": value is not UNKNOWN,
        "value": None if isinstance(value, _Absent) else value,
    }


def conflict_row(path: str, base: Any, draft: Any, current: Any, kind: str) -> dict[str, Any]:
    values = [comparison_value(v) for v in (base, draft, current)]
    return {
        "path": path,
        "kind": kind,
        "digests": [checksum(v) for v in values],
        **{
            key: row["value"] for key, row in zip(("base", "draft", "current"), values, strict=True)
        },
        "presence": {
            key: {"present": row["present"], "known": row["known"]}
            for key, row in zip(("base", "draft", "current"), values, strict=True)
        },
    }


def merge(
    base: Any,
    draft: Any,
    current: Any,
    decisions: dict[str, Any] | None = None,
    *,
    prefix: str = "",
) -> tuple[Any, list[dict[str, Any]]]:
    if any(
        isinstance(value, dict) and value.get("config_schema_version") == 4
        for value in (base, draft, current)
    ):
        from .freshness_templates import upgrade_document

        def comparable(value: Any) -> Any:
            if not isinstance(value, dict) or value.get("config_schema_version") not in (
                1,
                2,
                3,
                4,
            ):
                return value
            upgraded = upgrade_document(value)
            try:
                return storage_document(RegistryConfig.model_validate(upgraded))
            except ValueError:
                # Keep unfinished/invalid draft content visible for explicit repair.
                return upgraded

        base, draft, current = (comparable(value) for value in (base, draft, current))
    conflicts: list[dict[str, Any]] = []
    decisions = decisions or {}
    forced: set[str] = set()
    if all(isinstance(v, dict) for v in (base, draft, current)):

        def refers(value: Any, identity: str) -> bool:
            if isinstance(value, dict):
                return any(
                    (
                        k
                        in {
                            "ref",
                            "source_id",
                            "dependency_id",
                            "catalog_id",
                            "template_id",
                            "comparison_template_id",
                        }
                        and isinstance(v, str)
                        and (v == identity or v.startswith(identity + "."))
                    )
                    or refers(v, identity)
                    for k, v in value.items()
                )
            return isinstance(value, list) and any(refers(v, identity) for v in value)

        for group, fields in IDENTITIES.items():
            if group == "inputs":
                continue

            def rows(document: Any, name: str) -> list[Any]:
                value = document.get(name, [])
                return (
                    value
                    if isinstance(value, list) and all(isinstance(row, dict) for row in value)
                    else []
                )

            draft_ids = {
                row.get(fields[0])
                for row in rows(draft, group)
                if isinstance(row.get(fields[0]), str)
            }
            for row in rows(base, group):
                identity = row.get(fields[0])
                if not isinstance(identity, str) or identity in draft_ids:
                    continue
                for other_group, other_fields in IDENTITIES.items():
                    if other_group == "inputs":
                        continue
                    for user in rows(current, other_group):
                        if refers(user, identity) and user not in rows(draft, other_group):
                            forced.add(f"/{group}/{'@'.join(str(row.get(k, '')) for k in fields)}")
                            forced.add(
                                f"/{other_group}/{'@'.join(str(user.get(k, '')) for k in other_fields)}"
                            )

    def walk(b: Any, d: Any, c: Any, path: str, field: str) -> Any:
        # Switching own/shared chooses one complete rule, never a mixed union.
        selection_switch = (
            field == "freshness_selection"
            and len({value.get("kind") for value in (b, d, c) if isinstance(value, dict)}) > 1
        )
        has_forced_child = any(p.startswith(path + "/") for p in forced)
        if path not in forced and not has_forced_child and (equivalent(d, c) or equivalent(b, c)):
            return (
                preserve_config(d, c)
                if not path and isinstance(d, dict) and isinstance(c, dict)
                else preserve_numbers(d, c)
            )
        if path not in forced and not has_forced_child and equivalent(b, d):
            return deepcopy(c)
        if b == UNKNOWN and not selection_switch and all(isinstance(v, dict) for v in (d, c)):
            return {
                k: value
                for k in sorted(d.keys() | c.keys())
                if (
                    value := walk(
                        UNKNOWN, d.get(k, MISSING), c.get(k, MISSING), f"{path}/{pointer(k)}", k
                    )
                )
                != MISSING
            }
        if (
            path not in forced
            and not selection_switch
            and all(isinstance(v, dict) for v in (b, d, c))
        ):
            return {
                k: value
                for k in sorted(b.keys() | d.keys() | c.keys())
                if (
                    value := walk(
                        b.get(k, MISSING),
                        d.get(k, MISSING),
                        c.get(k, MISSING),
                        f"{path}/{pointer(k)}",
                        k,
                    )
                )
                != MISSING
            }
        parts = path.split("/")
        identity_list = (
            len(parts) == 2
            or len(parts) == 4
            and parts[1] in {"contracts", "definitions"}
            and field == "inputs"
        )
        keys = IDENTITIES.get(field) if identity_list else None
        if (
            keys
            and (isinstance(b, list) or b == UNKNOWN)
            and all(
                isinstance(v, list)
                and all(isinstance(row, dict) and all(k in row for k in keys) for row in v)
                and len({checksum([row[k] for k in keys]) for row in v}) == len(v)
                for v in ((d, c) if b == UNKNOWN else (b, d, c))
            )
        ):

            def indexed(rows: list[Any]) -> dict[str, Any]:
                return {"@".join(str(row.get(k, "")) for k in keys): row for row in rows}

            bi, di, ci = indexed(b) if isinstance(b, list) else {}, indexed(d), indexed(c)
            return [
                value
                for k in sorted(bi.keys() | di.keys() | ci.keys())
                if (
                    value := walk(
                        bi.get(k, UNKNOWN if b == UNKNOWN else MISSING),
                        di.get(k, MISSING),
                        ci.get(k, MISSING),
                        f"{path}/{pointer(k)}",
                        "",
                    )
                )
                != MISSING
            ]
        row = conflict_row(
            path,
            b,
            d,
            c,
            "delete_needed"
            if path in forced
            else "delete_edit"
            if MISSING in (b, d, c)
            else "property",
        )
        digests = row["digests"]
        decision = decisions.get(path)
        if (
            isinstance(decision, dict)
            and decision.get("digests") == digests
            and decision.get("choice") in {"draft", "current"}
        ):
            return deepcopy(d if decision["choice"] == "draft" else c)
        conflicts.append(row)
        return deepcopy(c)

    return walk(base, draft, current, prefix, ""), conflicts


def archive_versions(before: Any, after: Any) -> None:
    """Append only saved input snapshots; lifecycle changes do not rewrite a version."""
    for key, draft in after.tables["registry_draft"].items():
        previous = before.tables["registry_draft"].get(key)
        if previous and previous.get("draft_version") == draft.get("draft_version"):
            continue
        version_key = f"{key}:{draft['draft_version']}"
        after.tables["registry_draft_version"][version_key] = version_snapshot(draft)


def version_snapshot(draft: dict[str, Any]) -> dict[str, Any]:
    return {
        key: deepcopy(draft.get(key))
        for key in ("draft_id", "draft_version", "config", "display_name")
    }


async def reconcile(runtime: Any, state: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Compare one browser's saved base/local input with the latest saved draft.

    This never saves, changes the active basis, or applies package effects.
    The following save still needs the returned draft version.
    """
    from .registry import safe_document

    draft = state.tables["registry_draft"][payload["draft_id"]]
    guard(state, draft, current=False)
    if (
        type(payload.get("expected_server_version")) is not int
        or payload["expected_server_version"] != draft["draft_version"]
    ):
        raise AuthoringError("draft_version_conflict")
    if type(payload.get("draft_version")) is not int:
        raise AuthoringError("draft_version_conflict")
    key = f"{draft['draft_id']}:{payload['draft_version']}"
    base = await runtime.store.get("registry_draft_version", key)
    if base is None:
        raise AuthoringError("draft_version_unknown")
    local = payload.get("config")
    if not isinstance(local, dict):
        raise ValueError("configuration object required")
    safe_document(local)
    decisions = payload.get("decisions", {})
    if not isinstance(decisions, dict) or len(decisions) > 1000:
        raise AuthoringError("continuation_unresolved")
    name = payload.get("display_name", base.get("display_name", ""))
    if not isinstance(name, str) or len(name) > 256:
        raise ValueError("invalid_draft_display_name")
    candidate, conflicts = merge(base["config"], local, draft["config"], decisions)
    names, name_conflicts = merge(
        {"display_name": base.get("display_name", "")},
        {"display_name": name},
        {"display_name": draft.get("display_name", "")},
        decisions,
    )
    return {
        "config": candidate,
        "display_name": names["display_name"],
        "conflicts": conflicts + name_conflicts,
        "draft_version": draft["draft_version"],
    }


async def continuation(
    runtime: Any, state: Any, operation: str, payload: dict[str, Any], now: Any
) -> dict[str, Any]:
    if operation == "continuation_start":
        if sum(s["status"] == "open" for s in state.tables["draft_continuation"].values()) >= 1000:
            raise AuthoringError("continuation_limit")
        draft = state.tables["registry_draft"][payload["draft_id"]]
        guard(state, draft, current=False)
        if (
            type(payload.get("draft_version")) is not int
            or draft["draft_version"] != payload["draft_version"]
        ):
            raise AuthoringError("draft_version_conflict")
        base = await runtime.store.get("registry_revision", str(draft.get("base_revision")))
        known = draft.get("base_status") == "known" and (
            (base and base["checksum"] == draft.get("base_checksum"))
            or draft.get("base_revision") == 0
        )
        if not known and payload.get("manual_comparison") is not True:
            raise AuthoringError("draft_base_unknown")
        session = {
            "session_id": str(uuid4()),
            "session_version": 0,
            "draft_id": draft["draft_id"],
            "draft_version": draft["draft_version"],
            "base": base["config"] if known and base else {},
            "draft": deepcopy(draft["config"]),
            "manual": not known,
            "decisions": {},
            "recomparisons": 0,
            "status": "open",
            "created_at": now.isoformat(),
        }
        state.tables["draft_continuation"][session["session_id"]] = session
        session["request_id"] = session["session_id"]
    else:
        session = state.tables["draft_continuation"][payload["session_id"]]
        finish_digest = checksum(
            {k: payload.get(k) for k in ("session_id", "session_version", "request_id", "_actor")}
        )
        if operation == "continuation_cancel":
            if session["status"] == "cancelled":
                return dict(session)
            if session["status"] == "completed":
                raise AuthoringError("continuation_closed")
            if (
                type(payload.get("session_version")) is not int
                or payload["session_version"] != session["session_version"]
            ):
                raise AuthoringError("continuation_version_conflict")
            session.update(status="cancelled", session_version=session["session_version"] + 1)
            return dict(session)
        if operation == "continuation_finish":
            try:
                UUID(payload.get("request_id", ""))
            except (ValueError, TypeError, AttributeError) as exc:
                raise AuthoringError("invalid_request_id") from exc
        if session["status"] != "open":
            if operation == "continuation_finish" and session["status"] == "completed":
                if session.get("finish_digest") != finish_digest:
                    raise AuthoringError("request_identity_conflict")
                return dict(session["result"])
            raise AuthoringError("continuation_closed")
        if (
            type(payload.get("session_version")) is not int
            or payload.get("session_version") != session["session_version"]
        ):
            raise AuthoringError("continuation_version_conflict")
        draft = state.tables["registry_draft"][session["draft_id"]]
        guard(state, draft, current=False)
        if draft["draft_version"] != session["draft_version"]:
            raise AuthoringError("draft_version_conflict")
    current = base_fields(state)
    generation = checksum(
        {
            "base": current,
            "heads": state.tables["catalog_head"],
            "editorial": state.tables["editorial_current"],
            "pending_editorial": [
                r
                for r in state.tables["editorial_draft"].values()
                if r["draft_id"] == draft["draft_id"]
            ],
        }
    )
    if session.get("generation") not in {None, generation}:
        if session["recomparisons"] >= 1:
            session.update(status="halted", cause="continuation_changed_again")
            return session
        session["recomparisons"] += 1
    changed = session.get("generation") not in {None, generation}
    session.update(generation=generation, expected_active_revision=state.active_revision)
    if operation == "continuation_decisions" and not changed:
        if not isinstance(payload.get("decisions"), dict) or len(payload["decisions"]) > 1000:
            raise AuthoringError("continuation_unresolved")
        valid = {c["path"]: c for c in session["conflicts"]}
        for path, decision in payload.get("decisions", {}).items():
            if (
                path not in valid
                or not isinstance(decision, dict)
                or decision.get("digests") != valid[path]["digests"]
                or decision.get("choice") not in {"draft", "current"}
            ):
                raise AuthoringError("comparison_expired")
            session["decisions"][path] = decision
    active = runtime._config(state)
    current_doc = storage_document(active) if active else {}
    # Unknown bases are explicitly a two-way choice, never claimed as historical proof.
    b = UNKNOWN if session["manual"] else session["base"]
    candidate, conflicts = merge(b, session["draft"], current_doc, session["decisions"])
    if not conflicts:
        from .validation import RegistryValidationError

        try:
            candidate = await runtime._restore_numbers(candidate)
        except RegistryValidationError:
            pass  # Keep invalid drafts editable; the finish path still validates them.
    plan = deepcopy(draft.get("portable"))
    if plan and plan.get("status") != "consumed" and not conflicts:
        plan, plan_conflicts = await rebuild_plan(
            runtime, state, plan, candidate, session["decisions"]
        )
        conflicts.extend(plan_conflicts)
    session.update(
        config=candidate,
        conflicts=conflicts,
        portable=plan,
        session_version=session["session_version"] + 1,
    )
    if operation != "continuation_finish" or changed:
        return session
    if conflicts:
        raise AuthoringError("continuation_unresolved")
    validate(candidate, runtime.types)
    await runtime._definition_versions(validate(candidate, runtime.types))
    await runtime._source_types(validate(candidate, runtime.types))
    target_id = str(uuid4())
    target = {
        **draft,
        **current,
        "draft_id": target_id,
        "draft_version": 1,
        "config": candidate,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "continued_from_draft_id": draft["draft_id"],
    }
    for field in (
        "completed_revision",
        "completed_at",
        "completion_kind",
        "activation_request_id",
        "continued_to_draft_id",
        "continuation_session_id",
    ):
        target.pop(field, None)
    if plan and plan.get("status") != "consumed":
        target["portable"] = {
            **plan,
            "status": "prepared",
            "plan_id": str(uuid4()),
            "draft_id": target_id,
            "draft_version": 1,
        }
        bind_plan(target)
    else:
        target.pop("portable", None)
    state.tables["registry_draft"][target_id] = target
    draft.update(
        lifecycle="continued", continued_to_draft_id=target_id, completed_at=now.isoformat()
    )
    from .editorial import carry_draft

    for row in list(state.tables["editorial_draft"].values()):
        if row["draft_id"] == draft["draft_id"]:
            item = {**row, "draft_id": target_id, "draft_version": 1}
            state.tables["editorial_draft"][
                f"{target_id}:{row['object']['kind']}:{row['object']['id']}"
            ] = item
    carry_draft(state, target, runtime)
    session.update(
        status="completed",
        result_draft_id=target_id,
        finish_digest=finish_digest,
        result=deepcopy(target),
    )
    return target


async def rebuild_plan(
    runtime: Any,
    state: Any,
    old: dict[str, Any],
    candidate: dict[str, Any],
    decisions: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Recompare every unconsumed effect and validate the newly bound executable plan."""
    from .editorial import ObjectRef
    from .portable import apply

    plan = deepcopy(old)
    plan.pop("candidate_digest", None)
    plan.update(
        expected_active_revision=state.active_revision,
        config=candidate,
        config_digest=checksum(candidate),
        origin_plan_digest=old["plan_digest"],
        catalog_heads={},
        editorial_versions={},
    )
    conflicts: list[dict[str, Any]] = []
    config = validate(candidate, runtime.types)
    for row in plan["package"]["editorial"]:
        key = ObjectRef.model_validate(row["object"]).key
        current = state.tables["editorial_current"].get(key, {})
        version = current.get("editorial_version", 0)
        plan["editorial_versions"][key] = version
        desired = {k: row[k] for k in ("category", "room")}
        actual = {k: current.get(k) for k in ("category", "room")}
        if old.get("editorial_choices", {}).get(key) == "keep":
            continue
        baseline = old.get("editorial_base", {}).get(key, UNKNOWN)
        merged, editorial_conflicts = merge(
            baseline, desired, actual, decisions, prefix=f"/editorial/{pointer(key)}"
        )
        conflicts.extend({**c, "kind": "editorial"} for c in editorial_conflicts)
        row.update(merged)
        plan.setdefault("editorial_base", {})[key] = deepcopy(actual)
        plan["editorial_choices"][key] = "file" if merged != actual else "keep"
    await slots.AUTHORING["catalog_rebuild"](
        runtime, state, old, plan, config, decisions, conflicts
    )
    plan["plan_digest"] = checksum({k: v for k, v in plan.items() if k != "plan_digest"})
    if not conflicts:
        apply(
            runtime,
            state.clone(),
            {"portable": plan, "config": candidate},
            config,
            runtime.clock.now_utc(),
        )
    return plan, conflicts


async def migrate(runtime: Any, state: Any, now: Any) -> None:
    tables = ("registry_draft", "draft_continuation", "editorial_draft", "registry_draft_version")
    for key in list(state.tables["registry_draft"]):
        original = state.tables["registry_draft"][key]
        try:
            if original.get("authoring_version") == 2:
                version_key = f"{key}:{original['draft_version']}"
                if version_key in state.tables["registry_draft_version"] or await runtime.store.get(
                    "registry_draft_version", version_key
                ):
                    continue
            # Only these four tables can change. The probe is never committed;
            # adoption below journals the isolated rows on the actual writer.
            # Include earlier drafts' uncommitted archive appends in every probe.
            probe = state.model_copy(
                update={
                    "tables": {**state.tables, **{t: deepcopy(state.tables[t]) for t in tables}}
                }
            )
            if original.get("authoring_version") != 2:
                await _migrate_rows(runtime, probe, now, key)
            # Legacy rows may predate the input archive. Backfill only their current
            # saved version; historical inputs cannot be reconstructed or guessed.
            draft_keys = {key} | (
                probe.tables["registry_draft"].keys() - state.tables["registry_draft"].keys()
            )
            for draft_key in draft_keys:
                draft = probe.tables["registry_draft"][draft_key]
                version_key = f"{draft_key}:{draft['draft_version']}"
                if version_key not in probe.tables[
                    "registry_draft_version"
                ] and not await runtime.store.get("registry_draft_version", version_key):
                    probe.tables["registry_draft_version"][version_key] = version_snapshot(draft)
        except PersistenceUnavailable:
            raise
        except Exception as error:
            # An incomplete legacy document must not prevent application startup.
            # No partial continuation or invented proof escapes the probe.
            original.update(authoring_version=2, lifecycle="open", base_status="needs_review")
            LOGGER.warning(
                "draft_migration_failed",
                extra={"draft_id": key, "error_type": type(error).__name__},
            )
        else:
            for table in tables:
                state.tables[table] = probe.tables[table]


async def _migrate_rows(runtime: Any, state: Any, now: Any, draft_id: str) -> None:
    """Startup migration, atomic with restore; synthetic stores exercise the same code."""
    for draft in list(state.tables["registry_draft"].values()):
        if draft["draft_id"] != draft_id or draft.get("authoring_version") == 2:
            continue
        draft.update(authoring_version=2, lifecycle="open", base_status="needs_review")
        base = await runtime.store.get("registry_revision", str(draft.get("base_revision")))
        plan = draft.get("portable")
        if not plan:
            if base or draft.get("base_revision") == 0:
                draft.update(base_status="known", base_checksum=base["checksum"] if base else None)
            continue
        imported = plan.get("config")
        if not isinstance(imported, dict) or checksum(imported) != plan.get("config_digest"):
            continue
        try:
            b = storage_document(validate(imported, runtime.types))
            valid_effects = effects_match(runtime, state, plan)
        except ValueError, KeyError:
            continue
        if not valid_effects:
            continue
        # Ascending exact lookups avoid a truncated history page as a false proof.
        matches = []
        for number in range(1, state.active_revision + 1):
            row = await runtime.store.get("registry_revision", str(number))
            activation = await runtime.store.get("registry_activation", str(number))
            if (
                row
                and activation
                and number > plan.get("expected_active_revision", state.active_revision)
                and row["checksum"] == checksum(b)
                and activation["at"] >= draft["created_at"]
            ):
                matches.append({**row, "activation_at": activation["at"]})
        if not matches:
            continue
        proven = matches[0]
        draft.update(
            base_revision=proven["revision"],
            completed_revision=proven["revision"],
            base_checksum=proven["checksum"],
            base_status="known",
            migration_evidence={
                "revision": proven["revision"],
                "checksum": proven["checksum"],
                "plan_effects": "verified",
                "matching_revisions": [row["revision"] for row in matches],
            },
        )
        draft["portable"] = {**plan, "status": "consumed", "result_revision": proven["revision"]}
        draft["completion_kind"] = "migrated"
        if storage_document(validate(draft["config"], runtime.types)) == b:
            draft.update(
                lifecycle="completed",
                completed_revision=proven["revision"],
                completed_at=proven["activation_at"],
                completion_kind="migrated",
            )
            continue
        # This base is the proven staged configuration, not the original pre-import base.
        session = await continuation(
            runtime,
            state,
            "continuation_start",
            {"draft_id": draft["draft_id"], "draft_version": draft["draft_version"]},
            now,
        )
        if not session["conflicts"]:
            await continuation(
                runtime,
                state,
                "continuation_finish",
                {
                    "session_id": session["session_id"],
                    "session_version": session["session_version"],
                    "request_id": session["session_id"],
                },
                now,
            )
        else:
            draft["continuation_session_id"] = session["session_id"]


RUNTIME_LOGGER = logging.getLogger("core_contracts.runtime")


async def operation_draft_preview(
    runtime: Runtime,
    state: State,
    operation: str,
    payload: Any,
    now: datetime,
    config: RegistryConfig | None,
) -> Any:
    from . import authoring

    if operation == "draft_reconcile":
        return await authoring.reconcile(runtime, state, payload)
    if operation.startswith("continuation_"):
        result = await authoring.continuation(runtime, state, operation, payload, now)
        await runtime._commit(state)
        return result
    if operation == "draft_complete":
        prior_activation = await authoring.prior_receipt(runtime, state, payload)
        if prior_activation is not None:
            return prior_activation
    draft = state.tables["registry_draft"][payload["draft_id"]]
    result = authoring.comparison(runtime, state, draft)
    if operation == "draft_preview":
        await runtime._checked_config(draft["config"])
        return result
    authoring.prepare_activation(runtime, state, draft, payload)
    if not result["no_effect"]:
        raise authoring.AuthoringError("draft_has_effects")
    revision_row = state.tables["registry_revision"][str(state.active_revision)]
    result = authoring.complete(state, draft, payload, revision_row, now, kind="already_active")
    runtime._audit(state, "draft_completed", payload, draft_id=draft["draft_id"])
    await runtime._commit(state)
    return result


slots.OPERATIONS["draft_preview"] = slots.Operation(
    operation_draft_preview, state="full", early=False, heavy=True
)
slots.OPERATIONS["draft_complete"] = slots.Operation(
    operation_draft_preview, state="full", early=False, heavy=True
)
slots.OPERATIONS["draft_reconcile"] = slots.Operation(
    operation_draft_preview, state="full", early=False, heavy=True
)
slots.OPERATION_PREFIXES["continuation_"] = slots.Operation(operation_draft_preview, heavy=True)


async def operation_draft_create(
    runtime: Runtime,
    state: State,
    operation: str,
    payload: Any,
    now: datetime,
    config: RegistryConfig | None,
) -> Any:
    document = payload["config"]
    # Drafts can be incomplete, but cannot carry credentials or invalid JSON.
    from .registry import safe_document

    if not isinstance(document, dict):
        raise ValueError("configuration object required")
    safe_document(document)
    if len(canonical(document).encode()) > 2_000_000:
        raise ValueError("document too large")
    key = payload.get("draft_id", str(uuid4()))
    if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", key):
        raise ValueError("invalid_draft_id")
    if operation == "draft_update" and (
        type(payload.get("draft_version")) is not int or payload["draft_version"] < 1
    ):
        raise ValueError("invalid_draft_version")
    old = state.tables["registry_draft"].get(key)
    if operation == "draft_create" and old is not None:
        raise ValueError("draft_identity_conflict")
    if operation == "draft_create" and await runtime.store.get(
        "registry_draft_version", f"{key}:1"
    ):
        raise ValueError("draft_identity_conflict")
    from .authoring import base_fields, guard, open_count

    if old:
        guard(state, old, current=not payload.get("preservation_save", False))
    if (
        operation == "draft_create"
        and payload.get("expected_active_revision", state.active_revision) != state.active_revision
    ):
        raise ValueError("active_revision_conflict")
    if operation == "draft_create" and open_count(state) >= 100:
        raise ValueError("draft_limit")
    if operation == "draft_update" and (
        old is None or payload["draft_version"] != old["draft_version"]
    ):
        raise ValueError("draft_version_conflict")
    from .validation import RegistryValidationError

    try:
        document = await runtime._restore_numbers(document, old["config"] if old else None)
    except RegistryValidationError:
        # Incomplete/invalid drafts remain editable, with their errors intact.
        pass
    result = {
        **(old or base_fields(state)),
        "draft_id": key,
        "draft_version": old["draft_version"] + 1 if old else 1,
        "config": document,
        "updated_at": now.isoformat(),
        "created_at": old.get("created_at", old["updated_at"]) if old else now.isoformat(),
        "base_revision": old.get("base_revision", state.active_revision)
        if old
        else state.active_revision,
        "display_name": payload.get("display_name", old.get("display_name", "") if old else ""),
        **({"portable": old["portable"]} if old and "portable" in old else {}),
    }
    if not isinstance(result["display_name"], str) or len(result["display_name"]) > 256:
        raise ValueError("invalid_draft_display_name")
    state.tables["registry_draft"][key] = result
    if result.get("portable"):
        from .authoring import bind_plan

        bind_plan(result)
    from .editorial import EditorialWrite, carry_draft, put

    carry_draft(state, result, runtime)
    editorial_entries = payload.get("editorial", [])
    if not isinstance(editorial_entries, list) or len(editorial_entries) > 128:
        raise ValueError("invalid_editorial_batch")
    for item in editorial_entries:
        put(
            runtime,
            state,
            EditorialWrite.model_validate(
                {**item, "draft_id": key, "draft_version": result["draft_version"]}
            ),
            payload.get("_actor", {"kind": "internal", "role": "system"}),
        )
    runtime._audit(state, operation, payload, draft_id=key, draft_version=result["draft_version"])
    await runtime._commit(state)
    return result


slots.OPERATIONS["draft_create"] = slots.Operation(
    operation_draft_create, state="full", early=False
)
slots.OPERATIONS["draft_update"] = slots.Operation(
    operation_draft_create, state="full", early=False
)


async def operation_draft_delete(
    runtime: Runtime,
    state: State,
    operation: str,
    payload: Any,
    now: datetime,
    config: RegistryConfig | None,
) -> Any:
    key = payload["draft_id"]
    if type(payload.get("draft_version")) is not int:
        raise ValueError("invalid_draft_version")
    old = state.tables["registry_draft"].get(key)
    if old is None or payload["draft_version"] != old["draft_version"]:
        raise ValueError("draft_version_conflict")
    from .authoring import guard

    guard(state, old, current=False)
    del state.tables["registry_draft"][key]
    for editorial_key, row in list(state.tables["editorial_draft"].items()):
        if row["draft_id"] == key:
            del state.tables["editorial_draft"][editorial_key]
    runtime._audit(state, operation, payload, draft_id=key, draft_version=old["draft_version"])
    await runtime._commit(state)
    return {"deleted": True, "draft_id": key}


slots.OPERATIONS["draft_delete"] = slots.Operation(
    operation_draft_delete, state="full", early=False
)


async def operation_activate(
    runtime: Runtime,
    state: State,
    operation: str,
    payload: Any,
    now: datetime,
    config: RegistryConfig | None,
) -> Any:
    from . import authoring

    if operation == "activate":
        # Every caller supplies a durable identity. Test/bootstrap helpers use
        # the same preview contract as HTTP clients.
        prior_activation = await authoring.prior_receipt(runtime, state, payload)
        if prior_activation is not None:
            return prior_activation
    note = payload.get("note", "")
    if not isinstance(note, str) or len(note) > 256:
        raise ValueError("invalid_revision_note")
    note = note.strip()
    if type(payload.get("expected_active_revision")) is not int:
        raise ValueError("invalid_expected_revision")
    if payload["expected_active_revision"] != state.active_revision:
        raise ValueError("active_revision_conflict")
    if operation == "activate":
        draft = state.tables["registry_draft"][payload["draft_id"]]
        if payload["draft_version"] != draft["draft_version"]:
            raise ValueError("draft_version_conflict")
        authoring.prepare_activation(runtime, state, draft, payload)
        document = draft["config"]
    else:
        historical = await runtime.store.get("registry_revision", str(payload["revision"]))
        if historical is None:
            raise ValueError("revision_not_found")
        document = historical["config"]
    new_config = await runtime._checked_config(document, restore=operation == "activate")
    if operation == "activate":
        from .editorial import apply_draft

        apply_draft(state, draft, runtime)
        from .portable import apply

        title_import = draft.get("portable", {}).get("title_import")
        if title_import and await runtime.store.get(
            "catalog_import", "title:" + title_import["import_id"]
        ):
            raise ValueError("import_plan_conflict")
        imported_latches = apply(runtime, state, draft, new_config, now)
        draft["config"] = storage_document(new_config)
    revision = max((int(r) for r in state.tables["registry_revision"]), default=0) + 1
    state.active_revision = revision
    state.tables["registry_revision"][str(revision)] = {
        "revision": revision,
        "config": storage_document(new_config),
        "checksum": checksum(storage_document(new_config)),
        "created_at": now.isoformat(),
        "actor": payload.get("_actor", {"kind": "internal", "role": "system"}),
        **({"note": note} if note else {}),
        **(
            {
                "draft_id": draft["draft_id"],
                "draft_version": draft["draft_version"],
                "activation_request_id": payload["request_id"],
            }
            if operation == "activate"
            else {}
        ),
    }
    state.tables["registry_activation"][str(revision)] = {
        "revision": revision,
        "at": now.isoformat(),
        "operation": operation,
        **(
            {
                "draft_id": draft["draft_id"],
                "draft_version": draft["draft_version"],
                "activation_request_id": payload["request_id"],
            }
            if operation == "activate"
            else {}
        ),
    }
    runtime._audit(state, operation, payload, revision=revision, **({"note": note} if note else {}))
    next_latches = imported_latches if operation == "activate" else runtime.latches
    if config:
        from .reconfiguration import preserve

        next_latches = copy.deepcopy(next_latches)
        compatible = preserve(state, config, new_config, runtime.types, next_latches)
        next_latches = {k: v for k, v in next_latches.items() if k.partition(":")[0] in compatible}
        if "ha" in runtime.dependency_connections and (
            runtime.bridge != "connected" or runtime.ha != "connected"
        ):
            # A revision must not carry live HA evidence across a known gap.
            # Ordinary disconnect ageing and stored calendar caches stay unchanged.
            for observation in state.tables["source_observation_current"].values():
                if observation["adapter"] in {"ha_state", "ha_attribute"}:
                    observation["observation_kind"] = "restore"
    activation_result = (
        authoring.complete(
            state, draft, payload, state.tables["registry_revision"][str(revision)], now
        )
        if operation == "activate"
        else state.tables["registry_revision"][str(revision)]
    )
    runtime._evaluate(
        state,
        new_config,
        origin="revision",
        reassessment=config is not None,
        initial_latches=next_latches,
    )
    await runtime._commit(state, publication=True)
    from .reconfiguration import same_ha_subscription

    if not same_ha_subscription(config, new_config):
        runtime.first_snapshot = False
    runtime._broadcast(runtime.service_state())
    RUNTIME_LOGGER.info(
        "registry_rollback" if operation == "rollback" else "registry_activated",
        extra={"revision": revision},
    )
    runtime.revision_changed.set()
    runtime.mqtt_revision_changed.set()
    return activation_result


slots.OPERATIONS["activate"] = slots.Operation(operation_activate, state="full", early=False)
slots.OPERATIONS["rollback"] = slots.Operation(operation_activate, state="full", early=False)


async def restore_authoring(runtime: Runtime, state: State, context: Any) -> None:
    await migrate(runtime, state, runtime.clock.now_utc())


slots.RESTORE[30] = slots.Hook(restore_authoring, "migration", "restore_authoring")
slots.BEFORE_COMMIT[10] = slots.Hook(archive_versions, "archive")
