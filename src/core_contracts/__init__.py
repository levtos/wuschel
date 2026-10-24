"""Generic Core Contracts platform."""

import tomllib
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

try:
    __version__ = version("wuschel")
except PackageNotFoundError:
    # Source-only tools (including the isolated HA kernel) have no installed distribution.
    __version__ = tomllib.loads(
        (Path(__file__).resolve().parents[2] / "pyproject.toml").read_text(encoding="utf-8")
    )["project"]["version"]
