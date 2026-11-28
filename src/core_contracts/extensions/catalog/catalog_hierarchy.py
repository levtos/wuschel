"""Explicit hierarchical lookup shared by configured media profiles."""

from typing import Any

from core_contracts.quality import ConfigurationHint, ConfigurationStatus, FieldValue, derive


def source_frame(ctx: Any, args: list[FieldValue], profile: str) -> dict[str, Any] | None:
    references = (ref for arg in args for ref in arg.evidence if not ref.startswith("catalog:"))
    frames = [
        frame
        for observation in ctx.evidence_observations(references)
        if observation.availability == "available"
        and not observation.transform_error
        and (frame := observation.catalog_frame)
        and frame.get("catalog_profile") == profile
    ]
    return frames[0] if frames and len({item["event"] for item in frames}) == 1 else None


def lookup(
    ctx: Any, rule: Any, args: list[FieldValue], raw: str, hierarchy: dict[str, Any]
) -> FieldValue:
    from core_contracts.extensions.catalog.catalog_playback import offer
    from core_contracts.extensions.catalog.managed_catalog import (
        definition,
        effective,
        head,
        head_key,
        identity,
        semantic,
        token,
        trace_key,
    )

    scope, media, signal = (str(value.value) for value in args[:3])
    catalog = definition(ctx.registry, rule.catalog_id, rule.definition_version)
    current = head(ctx.state, catalog)
    key = identity(scope, media, signal, raw)
    parent = (
        identity(scope, media, signal, hierarchy["master_key"])
        if hierarchy.get("master_key")
        else key
    )
    selected = key if key in current["entries"] else parent
    class_key = getattr(rule, "class_key", None)
    value, complete, master = effective(current["entries"], selected, catalog, class_key)
    result = derive(value, args, ctx.now)
    tokens = []
    for dependency in dict.fromkeys([key, selected]):
        # An unsaved child already uses the parent's effective truth. Persisting
        # that same relation must not manufacture a later basis change.
        digest = semantic(current["entries"], selected, catalog, class_key)
        trace = trace_key(dependency, class_key)
        tokens.append(token(catalog, trace, digest))
        ctx.node.setdefault("catalog_lookups", {})[head_key(catalog) + ":" + trace] = {
            "catalog": head_key(catalog),
            "key": dependency,
            **({"class_key": class_key} if class_key else {}),
            "content_revision": current["revision"],
            "master": master,
            "semantic_digest": digest,
            "complete": complete,
        }
    result = result.model_copy(update={"evidence": (*result.evidence, *tokens)})
    if not complete:
        hint = ConfigurationHint(code="catalog_assignment_missing")
        result = result.model_copy(
            update={
                "configuration": ConfigurationStatus(
                    complete=False, hints=tuple(dict.fromkeys((*result.configuration.hints, hint)))
                )
            }
        )
    offer(
        ctx,
        catalog,
        scope,
        media,
        signal,
        raw,
        tuple(ref for arg in args for ref in arg.evidence if not ref.startswith("catalog:")),
        min((arg.valid_until for arg in args if arg.valid_until), default=None),
        hierarchy=hierarchy,
    )
    return result
