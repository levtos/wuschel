import json
import shlex
import subprocess
import sys

from dev.area_tests import ROOT


def test_area_files_exist_and_katalog_arguments_are_exact():
    areas = json.loads((ROOT / "tests/areas.json").read_text())
    expected = [
        "test_managed_catalog",
        "test_catalog_playback",
        "test_catalog_scope",
        "test_catalog_scope_conflicts",
        "test_catalog_cleanup",
        "test_catalog_care",
        "test_catalog_title_keys",
        "test_title_sync",
        "test_title_sync_pages",
        "test_title_import",
        "test_title_reader",
    ]
    assert areas["katalog"] == [f"tests/{name}.py" for name in expected]
    assert areas["traber"] == [
        "tests/test_bridge_kernel.py",
        "tests/test_beta5_projection.py",
        "tests/test_ha_projection_reconnect.py",
        "tests/test_b23_projection.py",
    ]
    for name, paths in areas.items():
        assert all((ROOT / path).is_file() for path in paths)
        result = subprocess.run(
            [sys.executable, str(ROOT / "dev/area_tests.py"), name],
            capture_output=True,
            text=True,
            check=True,
        )
        assert shlex.split(result.stdout) == paths


def test_unknown_area_fails():
    result = subprocess.run(
        [sys.executable, str(ROOT / "dev/area_tests.py"), "missing"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "invalid choice" in result.stderr
