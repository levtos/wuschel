"""Apple title configuration format."""

from typing import Annotated, Literal, Self

from pydantic import Field, field_validator, model_validator

from ..model import Model
from ..text_rules import normalize_key

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
