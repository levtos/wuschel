"""Base-work keys for newly observed music; existing explicit care always wins."""

import json
import re
from typing import Any

from core_contracts.model import canonical

VERSION_PATTERN = r"\((?:[^()]*(?:remix|mix|edit|version)|live(?: at [^()]*)?)\)"


def music_hierarchy(raw: str) -> dict[str, Any] | None:
    try:
        pair = json.loads(raw) if raw.startswith("[") else raw.split(" - ", 1)
    except ValueError, RecursionError:
        return None
    if (
        not isinstance(pair, list)
        or len(pair) != 2
        or not all(isinstance(item, str) and item.strip() for item in pair)
    ):
        return None
    artist, title = pair
    base_artist = re.split(r"\s+(?:feat\.?|ft\.?|featuring)\s+", artist, flags=re.I)[0].strip()
    base_title = re.sub(r"\s+", " ", re.sub(VERSION_PATTERN, " ", title, flags=re.I)).strip()
    if not base_artist or not base_title:
        return None
    base_key = canonical([base_artist, base_title])
    if base_artist == artist.strip() and base_title == title.strip():
        return None
    return {
        "profile": "music",
        "master_key": base_key,
        "master_display_name": f"{base_artist} – {base_title}",
        "display_metadata": {"artist": artist, "title": title},
    }
