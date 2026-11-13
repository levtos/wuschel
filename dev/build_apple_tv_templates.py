"""Address-free P7 catalog and portable definition; no private media examples."""

import copy
import json
from pathlib import Path

from core_contracts.rules.apple_title import AppleTitleConfig, AppTitleRule, TitleMode
from dev.build_media_templates import (
    definition,
    inp,
    literal,
    mapping,
    negate,
    not_applicable,
    schema,
    select,
)


def settings():
    groups: dict[TitleMode, list[str]] = {
        "episode_in_second": ["Disney+", "Prime Video", "Amazon Prime Video"],
        "parent_in_second": ["YouTube", "Podcasts", "Apple Podcasts"],
        "content_prefix": ["MagentaTV"],
        "ignore": ["Netflix", "Fitness", "Fitness+", "Apple Fitness+", "stashyTV"],
    }
    return AppleTitleConfig(
        apps=tuple(
            AppTitleRule(app_name=name, mode=mode)
            for mode, names in groups.items()
            for name in names
        )
    )


def build():
    classes = ["normal", "private"]
    active = mapping(
        inp("state"),
        [
            (state, state in {"playing", "paused", "buffering"})
            for state in ("playing", "paused", "buffering", "idle", "off", "standby")
        ],
    )
    lookup = {
        "op": "managed_catalog_lookup",
        "profile": "apple_tv_title",
        "catalog_id": "media.appletv_titles",
        "definition_version": 1,
        "scope": literal("appletv"),
        "media_type": literal("video"),
        "signal_type": literal("title"),
        "key": inp("title"),
        "schema": schema("enum", classes),
    }
    result = definition(
        "media.appletv_title_class",
        {"title": schema("text"), "state": schema("text")},
        {"value": select((negate(active), not_applicable("enum", classes)), (active, lookup))},
        {"value": schema("enum", classes)},
    )
    result.update(language_version=8, display_name="Apple-TV-Titelklasse")
    return [result]


def catalog():
    definitions = json.loads(
        (Path(__file__).parents[1] / "examples/interface-catalogs-b22.json").read_text(
            encoding="utf-8"
        )
    )
    result = copy.deepcopy(next(row for row in definitions if row["catalog_id"] == "media.appletv"))
    result.update(catalog_id="media.appletv_titles", version=1)
    result["classification_schema"]["legend"] = {
        "normal": {
            "display_name": "Normal",
            "description": "Dieser Titel ist nicht als privat eingeordnet.",
        },
        "private": {
            "display_name": "Privat",
            "description": "Dieser Titel gehört zum privaten Medienkontext.",
        },
    }
    return result


if __name__ == "__main__":
    target = Path(__file__).parents[1] / "examples/apple-tv-templates-b22.json"
    target.write_text(
        json.dumps(
            {
                "catalog": catalog(),
                "definitions": build(),
                "title_catalog": settings().model_dump(mode="json"),
                "settings_schema": AppleTitleConfig.model_json_schema(),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
