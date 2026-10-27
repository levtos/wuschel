"""Require a completed green CI run and identical tested Git tree before publication."""

import argparse
import json
import os
import re
import subprocess
from pathlib import Path

try:
    from .ci_scope import JOBS
except ImportError:
    from ci_scope import JOBS


def api(path):
    return json.loads(subprocess.check_output(["gh", "api", path], text=True))


def verify(run, jobs, proof, tree, tested_tree):
    assert run["repository"]["full_name"].lower() == "levtos/wuschel", "Wrong CI repository"
    assert run["head_repository"]["full_name"].lower() == "levtos/wuschel", (
        "Fork CI is not publishable"
    )
    assert run["path"] == ".github/workflows/ci.yml", "Wrong CI workflow"
    assert run["event"] in {"pull_request", "push", "workflow_dispatch"}, "Wrong CI event"
    assert run["status"] == "completed" and run["conclusion"] == "success", "CI must be fully green"
    assert proof["run_id"] == str(run["id"]), "CI run mismatch"
    assert re.fullmatch(r"[1-9][0-9]*", str(proof["run_attempt"])) and 1 <= int(
        proof["run_attempt"]
    ) <= int(run["run_attempt"]), "CI attempt mismatch"
    scope = proof.get("scope", {})
    assert scope.get("full") is True and all(scope.get(name) is True for name in JOBS), (
        "Publication requires the full release gate"
    )
    assert re.fullmatch(r"[0-9a-f]{40}", proof["revision"]), "Unpinned tested source"
    assert proof["tree"] == tested_tree == tree, "Tested source differs from selected main"
    required = {
        "scope",
        "herd-assets",
        "platform",
        "frontend",
        "workbench-e2e",
        "ha-bridge",
        "source-proof",
        "images (amd64, linux/amd64)",
        "CI result",
    }
    names = {job["name"] for job in jobs}
    assert names == required and len(jobs) == len(required), "Incomplete CI jobs"
    assert jobs and all(
        job["status"] == "completed" and job["conclusion"] == "success" for job in jobs
    ), "A CI job is not green"
    return proof["revision"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True, type=int)
    parser.add_argument("--proof", required=True, type=Path)
    args = parser.parse_args()
    run = api(f"repos/levtos/wuschel/actions/runs/{args.run_id}")
    proof = json.loads(args.proof.read_text())
    jobs = api(f"repos/levtos/wuschel/actions/runs/{args.run_id}/jobs?filter=latest&per_page=100")[
        "jobs"
    ]
    # A PR run's head_sha can differ from GITHUB_SHA (the tested synthetic merge).
    subprocess.run(["git", "fetch", "origin", proof["revision"]], check=True)
    tree = subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], text=True).strip()
    tested = subprocess.check_output(
        ["git", "rev-parse", proof["revision"] + "^{tree}"], text=True
    ).strip()
    revision = verify(run, jobs, proof, tree, tested)
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        output.write(f"revision={revision}\n")


if __name__ == "__main__":
    main()
