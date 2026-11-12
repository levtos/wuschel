import json
import shlex
import subprocess
import sys

import pytest

from dev.area_tests import ROOT, affected_tests


def test_area_files_exist_and_arguments_match_reviewed_mapping():
    areas = json.loads((ROOT / "tests/areas.json").read_text())
    material = (ROOT / "docs/architecture/b25/testbereiche.md").read_text(encoding="utf-8")
    expected = json.loads(material.split("```json\n", 1)[1].split("```", 1)[0])
    assert areas == expected
    assert len(areas) == 14
    assert len(areas["wake"]["tests"]) == 7
    # The old two-area format is explicitly superseded by #116; retain its
    # existence/CLI checks for every entry in the complete reviewed mapping.
    assert areas["traber"]["tests"] == [
        "tests/test_b23_projection.py",
        "tests/test_beta5_projection.py",
        "tests/test_bridge_kernel.py",
        "tests/test_ha_projection_reconnect.py",
    ]
    for name, area in areas.items():
        assert all((ROOT / path).is_file() for path in area["tests"])
        result = subprocess.run(
            [sys.executable, str(ROOT / "dev/area_tests.py"), name],
            capture_output=True,
            text=True,
            check=True,
        )
        assert shlex.split(result.stdout) == area["tests"]


def test_unknown_area_fails():
    result = subprocess.run(
        [sys.executable, str(ROOT / "dev/area_tests.py"), "missing"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "invalid choice" in result.stderr


def test_changed_selection_combines_areas_and_fails_closed_for_core():
    areas = json.loads((ROOT / "tests/areas.json").read_text())
    selected = affected_tests(areas, ["src/core_contracts/wake/operator.py"])
    assert shlex.split(selected) == sorted(areas["wake"]["tests"])
    assert affected_tests(areas, ["src/core_contracts/quality.py"]) == "ALL"
    assert affected_tests(areas, ["src/core_contracts/wakefulness.py"]) == "ALL"
    assert affected_tests(areas, ["tests/test_replay.py"]) == "ALL"
    combined = affected_tests(
        areas, ["src/core_contracts/wake/operator.py", "src/core_contracts/calendar_cache.py"]
    )
    assert shlex.split(combined) == sorted(set(areas["wake"]["tests"] + areas["calendar"]["tests"]))
    assert affected_tests(areas, []) == ""


@pytest.mark.parametrize(
    "change", ["worktree", "staged", "commit", "untracked", "delete", "rename"]
)
def test_changed_cli_uses_real_git_and_default_or_explicit_base(tmp_path, change):
    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=tmp_path, capture_output=True, text=True, check=True
        )

    (tmp_path / "dev").mkdir()
    (tmp_path / "tests").mkdir()
    script = tmp_path / "dev/area_tests.py"
    script.write_bytes((ROOT / "dev/area_tests.py").read_bytes())
    (tmp_path / "tests/areas.json").write_bytes((ROOT / "tests/areas.json").read_bytes())
    operator = tmp_path / "src/core_contracts/wake/operator.py"
    operator.parent.mkdir(parents=True)
    operator.write_text("# baseline\n")
    git("init", "--quiet")
    git("add", ".")
    git(
        "-c",
        "user.name=Synthetic",
        "-c",
        "user.email=synthetic@example.invalid",
        "commit",
        "-qm",
        "base",
    )
    git("update-ref", "refs/remotes/origin/b25", "HEAD")
    if change == "delete":
        operator.unlink()
    elif change == "rename":
        operator.rename(operator.with_name("renamed.py"))
        git("add", ".")
    elif change == "untracked":
        operator.with_name("new.py").write_text("# new\n")
    else:
        operator.write_text("# changed\n")
        if change in {"staged", "commit"}:
            git("add", ".")
        if change == "commit":
            git(
                "-c",
                "user.name=Synthetic",
                "-c",
                "user.email=synthetic@example.invalid",
                "commit",
                "-qm",
                "change",
            )
    areas = json.loads((ROOT / "tests/areas.json").read_text())
    for args in (["--changed"], ["--changed", "origin/b25"]):
        result = subprocess.run(
            [sys.executable, str(script), *args],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=True,
        )
        assert shlex.split(result.stdout) == sorted(areas["wake"]["tests"])
    # An unmapped core path dominates the previously selected area.
    (operator.parent.parent / "quality.py").write_text("# core\n")
    result = subprocess.run(
        [sys.executable, str(script), "--changed"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "ALL"


def test_missing_or_conflicting_modes_and_invalid_base_fail(tmp_path):
    for args in ([], ["wake", "--changed"], ["--changed", "nonexistent-replay-base"]):
        result = subprocess.run(
            [sys.executable, str(ROOT / "dev/area_tests.py"), *args], capture_output=True, text=True
        )
        assert result.returncode != 0
