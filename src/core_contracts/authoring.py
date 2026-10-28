"""Draft authoring invariants; all writes run in the existing single writer transaction."""

from __future__ import annotations

import logging
from copy import deepcopy
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from .model import checksum
from .numeric_equivalence import equivalent, preserve_config, preserve_numbers
from .persistence import PersistenceUnavailable
from .registry import RegistryConfig, storage_document, validate

LOGGER = logging.getLogger(__name__)


class AuthoringError(ValueError):
    """A safe, structured conflict (never include document contents in logs)."""

    def __init__(self, code: str, **details: Any) -> None:
        super().__init__(code)
        self.code = code
        self.details = details


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
    from .catalog_models import EntryChange
    from .editorial import ObjectRef, Room
    from .managed_catalog import definition, definition_digest, head, identity

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
    for content in package.get("catalog_contents", []):
        if config is None:
            return False
        try:
            catalog = definition(config, content["catalog_id"], content["definition_version"])
        except ValueError:
            return False
        if definition_digest(catalog) != content["definition_digest"]:
            return False
        current_head = head(state, catalog)
        for row in content["entries"]:
            entry = EntryChange.model_validate(
                {k: v for k, v in row.items() if k not in {"master_identity", "normalized_key"}}
            )
            key = identity(entry.scope, entry.media_type, entry.signal_type, entry.key)
            current = current_head.get("entries", {}).get(key)
            if not current:
                return False
            desired = entry.model_dump(mode="json")
            if any(current.get(k) != v for k, v in desired.items() if k not in {"operation"}):
                return False
    return True


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
    from .catalog_models import EntryChange
    from .editorial import ObjectRef
    from .managed_catalog import definition, definition_digest, head, head_key, identity
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
    for content in plan["package"]["catalog_contents"]:
        catalog = definition(config, content["catalog_id"], content["definition_version"])
        key = head_key(catalog)
        current = head(state, catalog)
        if definition_digest(catalog) != content["definition_digest"]:
            raise AuthoringError("catalog_definition_conflict", catalog_id=catalog.catalog_id)
        plan["catalog_heads"][key] = current["revision"]
        original_entries = await runtime.store.managed_entries(
            key, old["catalog_heads"].get(key, 0)
        )
        kept_entries = []
        for row in content["entries"]:
            wanted_entry = EntryChange.model_validate(
                {k: v for k, v in row.items() if k not in {"master_identity", "normalized_key"}}
            )
            entry_key = identity(
                wanted_entry.scope,
                wanted_entry.media_type,
                wanted_entry.signal_type,
                wanted_entry.key,
            )
            actual = current["entries"].get(entry_key, MISSING)
            old_entry = original_entries.get(entry_key, MISSING)
            wanted = wanted_entry.model_dump(mode="json")
            actual_plain = {k: actual.get(k) for k in wanted} if actual != MISSING else MISSING
            old_plain = {k: old_entry.get(k) for k in wanted} if old_entry != MISSING else MISSING
            path = f"/catalog_contents/{key}/{entry_key}"
            # An explicit keep in the original plan carries no overwrite intent.
            if old.get("resolutions", {}).get(f"catalog-entry:{key}:{entry_key}") == "keep":
                continue
            if actual_plain == wanted or wanted == old_plain:
                continue
            if actual_plain == old_plain:
                kept_entries.append(row)
                continue
            entry_conflict = conflict_row(path, old_plain, wanted, actual_plain, "catalog")
            digests = entry_conflict["digests"]
            choice = decisions.get(path, {})
            if choice.get("digests") != digests or choice.get("choice") not in {"draft", "current"}:
                conflicts.append(entry_conflict)
            elif choice["choice"] == "draft":
                kept_entries.append(row)
        content["entries"] = kept_entries
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
    for key in list(state.tables["registry_draft"]):
        original = state.tables["registry_draft"][key]
        try:
            # Preserve earlier drafts' uncommitted archive appends in this transaction.
            probe = state.model_copy(deep=True)
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
            for table in (
                "registry_draft",
                "draft_continuation",
                "editorial_draft",
                "registry_draft_version",
            ):
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
