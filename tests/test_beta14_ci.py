"""Affected-path selection must never become a partial release gate."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from dev.ci_scope import JOBS, select


def active(plan):
    return {name for name in JOBS if plan[name]}


def test_docs_only_do_not_start_build_or_application_tests():
    plan = select(["docs/publishing.md", "AGENTS.md", "CLAUDE.md"])
    assert not active(plan) and plan["full"] is False


@pytest.mark.parametrize(
    "path",
    [
        "pyproject.toml",
        "wuschel/PENDING_RELEASE.json",
        "wuschel/config.yaml",
        ".github/workflows/ci.yml",
        "dev/release_ci.py",
        "new-unknown-file",
    ],
)
def test_release_build_policy_and_unknown_dependencies_require_every_job(path):
    assert active(select([path])) == set(JOBS)


def test_backend_frontend_bridge_and_test_boundaries():
    assert active(select(["src/core_contracts/runtime.py"])) == {
        "platform",
        "workbench-e2e",
        "images",
    }
    assert active(select(["frontend/src/features/contracts/presentation.ts"])) == {
        "frontend",
        "workbench-e2e",
        "images",
    }
    assert active(select(["custom_components/traber/__init__.py"])) == {"ha-bridge"}
    assert active(select(["tests/test_bridge_kernel.py"])) == {"ha-bridge"}
    assert active(select(["tests/test_runtime.py"])) == {"platform"}
    assert active(select(["docs/alpha4/example-registry.json"])) == set(JOBS)


def test_manual_full_gate_and_cache_probe_are_explicit():
    assert active(select(["docs/publishing.md"], full=True)) == set(JOBS)
    plan = select(["docs/publishing.md"], cache_probe=True)
    assert active(plan) == {"images"} and plan["full"] is False and plan["cache_probe"]


def test_dispatch_selects_the_real_documentation_diff_and_records_outputs(tmp_path):
    def git(*args):
        return subprocess.check_output(
            ["git", "-c", "user.name=Synthetic", "-c", "user.email=synthetic@example.test", *args],
            cwd=tmp_path,
            text=True,
        ).strip()

    git("init")
    (tmp_path / "README.md").write_text("before\n")
    git("add", "README.md")
    git("commit", "-m", "baseline")
    base = git("rev-parse", "HEAD")
    (tmp_path / "README.md").write_text("after\n")
    git("commit", "-am", "documentation")
    head = git("rev-parse", "HEAD")
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"inputs": {"full": "false", "base_sha": base}}))
    output = tmp_path / "output.txt"
    summary = tmp_path / "summary.md"
    script = Path(__file__).parents[1] / "dev/ci_scope.py"
    subprocess.run(
        [sys.executable, str(script), "--event", "workflow_dispatch", "--head", head],
        cwd=tmp_path,
        env={
            **os.environ,
            "GITHUB_EVENT_PATH": str(event),
            "GITHUB_OUTPUT": str(output),
            "GITHUB_STEP_SUMMARY": str(summary),
        },
        check=True,
    )
    plan = json.loads(
        next(line[5:] for line in output.read_text().splitlines() if line.startswith("plan="))
    )
    assert plan["full"] is False and not active(plan)
    assert '"README.md"' in summary.read_text()
