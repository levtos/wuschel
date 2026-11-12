"""Configured slot metadata and studio inheritance; no addresses or network I/O."""

from typing import Any

from core_contracts.model import checksum
from core_contracts.quality import FieldValue, ReasonCode, unknown
from core_contracts.text_rules import normalize_key


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
    from core_contracts.extensions.catalog.catalog_hierarchy import lookup as hierarchy_lookup
    from core_contracts.extensions.catalog.catalog_hierarchy import source_frame

    scene = str(args[-1].value)
    frame = source_frame(ctx, args, "stash_slot")
    if not frame:
        return unknown(ReasonCode.INPUT_UNKNOWN, "stash_slot", ctx.now)
    if frame["scene_id"] != scene or not scene.strip() or not frame["studio"]:
        return unknown(ReasonCode.INPUT_UNKNOWN, "stash_slot", ctx.now)
    parent_raw = studio_key(frame["studio"])
    if normalize_key(scene) == normalize_key(parent_raw):
        return unknown(ReasonCode.INVALID_VALUE, "stash_slot", ctx.now)
    return hierarchy_lookup(
        ctx,
        rule,
        args,
        scene,
        {
            "master_key": parent_raw,
            "master_display_name": frame["studio"],
            "display_metadata": frame["display_metadata"],
        },
    )
