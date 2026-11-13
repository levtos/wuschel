"""Decided b25 area gate and mandatory full main/release gate."""

import json
import os
import subprocess
import sys

import pytest
import yaml

from dev.ci_scope import JOBS, ROOT, select
from dev.release_ci import verify
from tests.test_beta5_release import ci


def test_catalog_only_runs_its_measured_tests_replay_and_tools():
    areas = json.loads((ROOT / "tests/areas.json").read_text())
    plan = select(["src/core_contracts/extensions/catalog/catalog_care.py"])
    assert {name for name in JOBS if plan[name]} == {"platform"}
    assert plan["area_only"] is True and plan["full"] is False
    assert plan["areas"] == ["catalog"]
    assert plan["pytest_files"] == sorted(set(areas["catalog"]["tests"] + ["tests/test_replay.py"]))
    combined = select(
        [
            "src/core_contracts/extensions/catalog/catalog_care.py",
            "src/core_contracts/extensions/wake/__init__.py",
        ]
    )
    assert combined["areas"] == ["catalog", "wake"]
    assert set(combined["pytest_files"]) == set(
        areas["catalog"]["tests"] + areas["wake"]["tests"] + ["tests/test_replay.py"]
    )


@pytest.mark.parametrize(
    "path",
    [
        "src/core_contracts/quality.py",
        "src/core_contracts/rules/catalog_models.py",
        "src/core_contracts/extensions/new/feature.py",
        "dev/area_map.py",
        "uv.lock",
    ],
)
def test_core_unknown_and_mixed_changes_keep_all_jobs(path):
    plan = select(["src/core_contracts/extensions/catalog/catalog_care.py", path])
    assert plan["full"] and all(plan[name] for name in JOBS)
    assert not plan["area_only"] and not plan["pytest_files"]


def test_main_push_and_pr_and_manual_measurement_use_real_event_contract(tmp_path):
    def git(*args):
        return subprocess.check_output(
            [
                "git",
                "-c",
                "user.name=Synthetic",
                "-c",
                "user.email=synthetic@example.invalid",
                *args,
            ],
            cwd=tmp_path,
            text=True,
        ).strip()

    git("init", "-q")
    file = tmp_path / "src/core_contracts/extensions/catalog/catalog_care.py"
    file.parent.mkdir(parents=True)
    file.write_text("# before\n")
    git("add", ".")
    git("commit", "-qm", "baseline")
    base = git("rev-parse", "HEAD")
    file.write_text("# after\n")
    git("commit", "-qam", "catalog")
    head = git("rev-parse", "HEAD")
    cases = [
        ("pull_request", {"pull_request": {"base": {"sha": base, "ref": "b25"}}}, False, False),
        ("pull_request", {"pull_request": {"base": {"sha": base, "ref": "main"}}}, True, False),
        ("push", {"before": base, "ref": "refs/heads/main"}, True, False),
        ("push", {"before": base, "ref": "refs/tags/v1.0.0b25"}, True, False),
        ("workflow_dispatch", {"inputs": {"area_map": "true"}}, False, True),
        ("workflow_call", {}, True, False),
    ]
    for number, (kind, event, full, measurement) in enumerate(cases):
        event_file, output = tmp_path / "event.json", tmp_path / f"output-{number}.txt"
        event_file.write_text(json.dumps(event))
        subprocess.run(
            [sys.executable, str(ROOT / "dev/ci_scope.py"), "--event", kind, "--head", head],
            cwd=tmp_path,
            check=True,
            env={
                **os.environ,
                "GITHUB_EVENT_PATH": str(event_file),
                "GITHUB_OUTPUT": str(output),
                "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md"),
            },
        )
        plan = json.loads(
            next(line[5:] for line in output.read_text().splitlines() if line.startswith("plan="))
        )
        assert plan["full"] is full and plan["area_map"] is measurement
        assert plan["area_only"] is (not full and not measurement)
        assert all(plan[name] for name in JOBS) is full


def test_measurement_job_is_manual_and_area_steps_keep_every_check():
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    job = workflow["jobs"]["area-map"]
    assert job["if"] == "github.event_name == 'workflow_dispatch' && inputs.area_map"
    assert job["services"]["postgres"]["image"].startswith("postgres:18")
    assert "area-map" in workflow["jobs"]["ci-result"]["needs"]
    steps = workflow["jobs"]["platform"]["steps"]
    for command in (
        "uv run ruff check .",
        "uv run ruff format --check .",
        "uv run mypy --strict src client/core_contracts_client",
        "uv run lint-imports",
    ):
        assert next(s for s in steps if s.get("run") == command).get("if") is None
    assert (
        next(s for s in steps if s.get("run") == "uv run pytest -q")["if"]
        == "needs.scope.outputs.area_only != 'true'"
    )
    assert (
        next(s for s in steps if s.get("name") == "Area tests and replay")["if"]
        == "needs.scope.outputs.area_only == 'true'"
    )


def test_measurement_is_never_a_release_gate():
    run, jobs, proof = ci()
    jobs.append({"name": "area-map", "status": "completed", "conclusion": "skipped"})
    assert verify(run, jobs, proof, "b" * 40, "b" * 40) == "a" * 40
    jobs[-1]["conclusion"] = "success"
    with pytest.raises(AssertionError, match="measurement"):
        verify(run, jobs, proof, "b" * 40, "b" * 40)
