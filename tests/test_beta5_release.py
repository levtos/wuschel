"""Publication still requires every CI job and the exact tested source tree."""

import copy
import json

import pytest
import yaml

from dev.promote_store import promote, validate_run
from dev.release_ci import verify
from dev.release_version import candidate


def ci():
    run = {
        "id": 9,
        "run_attempt": 1,
        "repository": {"full_name": "levtos/wuschel"},
        "head_repository": {"full_name": "levtos/wuschel"},
        "path": ".github/workflows/ci.yml",
        "event": "pull_request",
        "status": "completed",
        "conclusion": "success",
    }
    proof = {"run_id": "9", "run_attempt": "1", "revision": "a" * 40, "tree": "b" * 40}
    jobs = [
        {"name": name, "status": "completed", "conclusion": "success"}
        for name in (
            "herd-assets",
            "platform",
            "frontend",
            "workbench-e2e",
            "ha-bridge",
            "source-proof",
            "images (amd64)",
            "images (aarch64)",
        )
    ]
    return run, jobs, proof


def test_green_exact_merge_tree_is_reusable():
    run, jobs, proof = ci()
    assert verify(run, jobs, proof, "b" * 40, "b" * 40) == "a" * 40


@pytest.mark.parametrize(
    "failure",
    ["job", "missing", "workflow", "repo", "fork", "attempt", "tree", "revision", "running"],
)
def test_incomplete_or_different_ci_cannot_publish(failure):
    run, jobs, proof = ci()
    tree = "b" * 40
    if failure == "job":
        jobs[0]["conclusion"] = "skipped"
    if failure == "missing":
        jobs.pop()
    if failure == "workflow":
        run["path"] = "other.yml"
    if failure == "repo":
        run["repository"]["full_name"] = "other/repo"
    if failure == "fork":
        run["head_repository"]["full_name"] = "fork/wuschel"
    if failure == "attempt":
        proof["run_attempt"] = "2"
    if failure == "tree":
        tree = "c" * 40
    if failure == "revision":
        proof["revision"] = "main"
    if failure == "running":
        run["status"] = "in_progress"
    with pytest.raises(AssertionError):
        verify(run, jobs, proof, tree, "b" * 40)


def test_store_stays_old_until_verified_promotion(tmp_path):
    (tmp_path / "wuschel").mkdir()
    marker = tmp_path / "wuschel/PENDING_RELEASE.json"
    marker.write_text(json.dumps({"version": "1.0.0b5", "previous_version": "1.0.0b4"}))
    config = tmp_path / "wuschel/config.yaml"
    config.write_text("name: Wuschel\nversion: 1.0.0b4\n")
    assert candidate(tmp_path, "1.0.0b4") == "1.0.0b5"
    with pytest.raises(AssertionError):
        candidate(tmp_path, "1.0.0b3")
    assert yaml.safe_load(config.read_text())["version"] == "1.0.0b4"
    promote(tmp_path)
    assert not marker.exists() and candidate(tmp_path, "1.0.0b5") == "1.0.0b5"
    assert config.read_text() == "name: Wuschel\nversion: 1.0.0b5\n"


def test_promotion_requires_both_anonymous_architecture_gates():
    run = {
        "repository": {"full_name": "levtos/wuschel"},
        "path": ".github/workflows/publish-images.yml",
        "event": "workflow_dispatch",
        "head_sha": "a" * 40,
        "head_branch": "main",
        "status": "completed",
        "conclusion": "success",
    }
    jobs = [
        {"name": name, "status": "completed", "conclusion": "success"}
        for name in (
            "guard",
            "publish (amd64)",
            "publish (aarch64)",
            "anonymous-pull (amd64)",
            "anonymous-pull (aarch64)",
        )
    ]
    validate_run(run, jobs, "a" * 40)
    bad = copy.deepcopy(jobs)
    bad[-1]["conclusion"] = "failure"
    with pytest.raises(AssertionError):
        validate_run(run, bad, "a" * 40)
    with pytest.raises(AssertionError):
        validate_run(run, jobs[:-1], "a" * 40)
