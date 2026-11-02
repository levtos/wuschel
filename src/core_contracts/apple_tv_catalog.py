"""Configured Apple TV title parsing; no live addresses, examples or network I/O."""

from typing import Annotated, Any, Literal, Self

from pydantic import Field, field_validator, model_validator

from .model import Model, checksum
from .quality import FieldValue
from .stash_catalog import text
from .text_rules import normalize_key

Seconds = Annotated[float, Field(ge=0.1, le=3600, allow_inf_nan=False)]
TitleMode = Literal[
    "episode_in_second", "parent_in_second", "content_prefix", "standalone", "ignore"
]


class AppTitleRule(Model):
    app_id: str | None = Field(default=None, min_length=1, max_length=256)
    app_name: str | None = Field(default=None, min_length=1, max_length=256)
    mode: TitleMode
    stability_s: Seconds | None = None
    live_stability_s: Seconds | None = None

    @field_validator("app_id", "app_name")
    @classmethod
    def nonblank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("apple_tv_app_required")
        return value.strip() if value is not None else None

    @model_validator(mode="after")
    def identified(self) -> Self:
        if not (self.app_id or "").strip() and not (self.app_name or "").strip():
            raise ValueError("apple_tv_app_required")
        return self


class AppleTitleConfig(Model):
    stability_s: Seconds = 2
    live_stability_s: Seconds = 60
    default_mode: Literal["standalone", "ignore"] = "standalone"
    apps: tuple[AppTitleRule, ...] = Field(default=(), max_length=64)

    @model_validator(mode="after")
    def unique(self) -> Self:
        keys = [
            ("id", row.app_id) if row.app_id else ("name", normalize_key(row.app_name or ""))
            for row in self.apps
        ]
        if len(set(keys)) != len(keys):
            raise ValueError("apple_tv_app_duplicate")
        return self


def parse(attributes: dict[str, Any], settings: AppleTitleConfig) -> dict[str, Any]:
    title, second = text(attributes.get("media_title")), text(attributes.get("media_artist"))
    app_id, app_name = text(attributes.get("app_id")), text(attributes.get("app_name"))
    content_id = text(attributes.get("media_content_id"))
    rule = next((row for row in settings.apps if row.app_id and row.app_id == app_id), None)
    rule = rule or next(
        (
            row
            for row in settings.apps
            if not row.app_id and normalize_key(row.app_name or "") == normalize_key(app_name)
        ),
        None,
    )
    mode = rule.mode if rule else settings.default_mode
    ignored = mode == "ignore" or mode == "content_prefix" and content_id.startswith("GN_SEASON")
    result: dict[str, Any] = {
        "catalog_profile": "apple_tv_title",
        "title_ignored": ignored,
        "title_candidate": None,
    }
    if ignored or not app_id or not title:
        return result
    parent, child, live, sender = "", title, False, ""
    if mode == "episode_in_second":
        if not second:
            return result
        parent, child = title, second
    elif mode == "parent_in_second":
        if not second:
            return result
        parent = second
    elif mode == "content_prefix":
        if content_id.startswith("GN_EP"):
            before, separator, after = title.rpartition(" - ")
            if separator and before.strip() and after.strip():
                parent, child = before.strip(), after.strip()
        elif not content_id and second:
            # Live programme is a root title. The sender is a filter, never
            # another hierarchy level (Benni's two-level decision in #97).
            live, sender = True, second
    # App identity is part of both keys. Labels never collide with episode keys.
    namespace = app_id
    key = "item:" + checksum([namespace, normalize_key(parent), normalize_key(child)])
    parent_key = "master:" + checksum([namespace, normalize_key(parent)]) if parent else None
    stability = (rule.stability_s if rule else None) or settings.stability_s
    if live:
        stability = (rule.live_stability_s if rule else None) or settings.live_stability_s
    metadata = {"app_id": app_id, "app_name": app_name, "live_tv": live, "sender": sender}
    candidate = {
        "key": key,
        "master_key": parent_key,
        "master_display_name": parent,
        "display_metadata": {"title": child},
        "catalog_metadata": metadata,
        "profile": "apple_tv_title",
        "stability_s": stability,
        "transition_pair": [app_id, normalize_key(parent), normalize_key(child)]
        if parent
        else None,
    }
    candidate["fingerprint"] = checksum([key, parent_key, live, sender, stability])
    result["title_candidate"] = candidate
    return result


def lookup(ctx: Any, rule: Any, args: list[FieldValue]) -> FieldValue:
    from .catalog_hierarchy import lookup as hierarchy_lookup
    from .catalog_hierarchy import source_frame
    from .quality import FieldValue, ReasonCode, derive, unknown

    frame = source_frame(ctx, args, "apple_tv_title")
    if not frame:
        return unknown(ReasonCode.INPUT_UNKNOWN, "apple_tv_title", ctx.now)
    if frame.get("title_ignored"):
        return FieldValue(
            status="not_applicable",
            quality="healthy",
            positively_not_applicable=True,
            evidence=tuple(ref for arg in args for ref in arg.evidence),
        )
    if any(not value.usable(ctx.now) for value in args):
        return derive(None, args, ctx.now)
    candidate = frame.get("title_candidate")
    if not candidate or frame.get("title") != str(args[-1].value):
        return unknown(ReasonCode.INPUT_UNKNOWN, "apple_tv_title", ctx.now)
    return hierarchy_lookup(ctx, rule, args, candidate["key"], candidate)
