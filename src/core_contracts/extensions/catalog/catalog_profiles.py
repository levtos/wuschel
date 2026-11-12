"""Catalog source profiles used by the generic HA adapter."""

from typing import Any

from core_contracts import slots
from core_contracts.extensions.catalog.apple_tv_catalog import parse
from core_contracts.extensions.catalog.stash_catalog import metadata
from core_contracts.model import checksum


def stash_frame(frame: dict[str, Any], attributes: Any, adapter: Any, state: Any) -> dict[str, Any]:
    frame.update(metadata(attributes))
    frame["event"] = checksum([frame, (state or {}).get("last_updated")])
    return frame


def title_frame(frame: dict[str, Any], attributes: Any, adapter: Any, state: Any) -> dict[str, Any]:
    if adapter.title_catalog is not None:
        frame["source"] = checksum([adapter.ha_entity_id, "apple_tv_title"])
        frame.update(parse(attributes, adapter.title_catalog))
        frame["event"] = checksum([frame, (state or {}).get("last_updated")])
    return frame


slots.SOURCE_PROFILES["stash_slot"] = slots.SourceProfile(read=metadata, metadata=stash_frame)
slots.SOURCE_PROFILES["apple_tv_title"] = slots.SourceProfile(read=parse, metadata=title_frame)
