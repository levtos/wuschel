"""Configured slot metadata and studio inheritance; no addresses or network I/O."""

from typing import Any

from .model import checksum
from .quality import ConfigurationHint, ConfigurationStatus, FieldValue, ReasonCode, derive, unknown
from .text_rules import normalize_key


def text(value: Any) -> str:
    return value.strip()[:1024] if isinstance(value, str) else ""


def studio_key(name: str) -> str:
    return "studio:" + checksum(normalize_key(name))


def metadata(attributes: dict[str, Any]) -> dict[str, Any]:
    studio = attributes.get("studio")
    if isinstance(studio, dict):
        studio = studio.get("name")
    studio = text(studio) or text(attributes.get("media_album_name"))
    scene = attributes.get("scene_id")
    scene = str(scene) if type(scene) is int else text(scene)
    return {
        "catalog_profile": "stash_slot",
        "scene_id": scene,
        "studio": studio,
        "display_metadata": {
            "title": text(attributes.get("title")) or text(attributes.get("media_title")),
            "artist": text(attributes.get("media_artist")),
        },
    }


def lookup(ctx: Any, rule: Any, args: list[FieldValue]) -> FieldValue:
    from .catalog_playback import offer
    from .evidence import Observation
    from .managed_catalog import definition, effective, head, head_key, identity, semantic, token
    from .registry import RegistryConfig

    scope, media, signal, scene = (str(value.value) for value in args)
    references = {ref for arg in args for ref in arg.evidence if not ref.startswith("catalog:")}
    frames = [
        frame
        for row in ctx.state.tables["source_observation_current"].values()
        if (observation := Observation.model_validate(row)).reference in references
        and (frame := observation.catalog_frame)
        and frame.get("catalog_profile") == "stash_slot"
    ]
    if not frames or len({item["event"] for item in frames}) != 1:
        return unknown(ReasonCode.INPUT_UNKNOWN, "stash_slot", ctx.now)
    frame = frames[0]
    if frame["scene_id"] != scene or not scene.strip() or not frame["studio"]:
        return unknown(ReasonCode.INPUT_UNKNOWN, "stash_slot", ctx.now)
    config = RegistryConfig.model_validate(
        ctx.state.tables["registry_revision"][str(ctx.state.active_revision)]["config"]
    )
    catalog = definition(config, rule.catalog_id, rule.definition_version)
    current = head(ctx.state, catalog)
    parent_raw = studio_key(frame["studio"])
    scene_key, parent_key = (identity(scope, media, signal, raw) for raw in (scene, parent_raw))
    if scene_key == parent_key:
        return unknown(ReasonCode.INVALID_VALUE, "stash_slot", ctx.now)
    # A saved assignment/detachment always wins over automatically discovered
    # parentage. For new scenes the configured studio is the classification key.
    selected_key = scene_key if scene_key in current["entries"] else parent_key
    value, complete, master = effective(current["entries"], selected_key, catalog)
    result = derive(value, args, ctx.now)
    tokens = []
    for key in dict.fromkeys([scene_key, selected_key]):
        digest = semantic(current["entries"], key, catalog)
        tokens.append(token(catalog, key, digest))
        ctx.node.setdefault("catalog_lookups", {})[head_key(catalog) + ":" + key] = {
            "catalog": head_key(catalog),
            "key": key,
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
        scene,
        tuple(references),
        min((arg.valid_until for arg in args if arg.valid_until), default=None),
        hierarchy={
            "master_key": parent_raw,
            "master_display_name": frame["studio"],
            "display_metadata": frame["display_metadata"],
        },
    )
    return result
