"""Require a completed green CI run and identical tested Git tree before publication."""

import argparse
import json
import os
import re
import subprocess
from pathlib import Path


def api(path):
    return json.loads(subprocess.check_output(["gh", "api", path], text=True))


def verify(run, jobs, proof, tree, tested_tree):
    assert run["repository"]["full_name"].lower() == "levtos/wuschel", "Wrong CI repository"
    assert run["head_repository"]["full_name"].lower() == "levtos/wuschel", (
        "Fork CI is not publishable"
    )
    assert run["path"] == ".github/workflows/ci.yml", "Wrong CI workflow"
    assert run["event"] in {"pull_request", "push"}, "Wrong CI event"
    assert run["status"] == "completed" and run["conclusion"] == "success", "CI must be fully green"
    assert proof["run_id"] == str(run["id"]) and proof["run_attempt"] == str(run["run_attempt"]), (
        "CI attempt mismatch"
    )
    assert re.fullmatch(r"[0-9a-f]{40}", proof["revision"]), "Unpinned tested source"
    assert proof["tree"] == tested_tree == tree, "Tested source differs from selected main"
    required = {"herd-assets", "platform", "frontend", "workbench-e2e", "ha-bridge", "source-proof"}
    names = {job["name"] for job in jobs}
    assert (
        required <= names and len([name for name in names if name.startswith("images (")]) == 2
    ), "Incomplete CI jobs"
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
    jobs = api(
        f"repos/levtos/wuschel/actions/runs/{args.run_id}/attempts/{run['run_attempt']}/jobs?per_page=100"
    )["jobs"]
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
