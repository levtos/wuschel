"""The API must report the installed distribution version, not a stale source literal."""

import re
from importlib.metadata import version
from pathlib import Path
from uuid import uuid4

import yaml

from core_contracts.api import API
from core_contracts.security import Tokens


async def test_api_program_version_matches_installed_package(runtime, tmp_path):
    api = API(runtime, Tokens(tmp_path), str(uuid4()))
    assert api.info()["version"] == version("wuschel")


def test_store_description_has_no_phase_or_version():
    root = Path(__file__).resolve().parents[1]
    description = yaml.safe_load((root / "wuschel/config.yaml").read_text(encoding="utf-8"))[
        "description"
    ]
    assert not re.search(r"alpha|beta|\d+\.\d+", description, flags=re.IGNORECASE)
