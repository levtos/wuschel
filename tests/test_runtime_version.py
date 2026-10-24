"""The API must report the installed distribution version, not a stale source literal."""

from importlib.metadata import version
from uuid import uuid4

from core_contracts.api import API
from core_contracts.security import Tokens


async def test_api_program_version_matches_installed_package(runtime, tmp_path):
    api = API(runtime, Tokens(tmp_path), str(uuid4()))
    assert api.info()["version"] == version("wuschel")
