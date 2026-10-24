"""Prepare the two-file store promotion only after a green distribution run."""

import argparse
import json
import subprocess
from pathlib import Path

import yaml


def validate_run(run, jobs, revision):
    assert run["repository"]["full_name"].lower() == "levtos/wuschel"
    assert run["path"] == ".github/workflows/publish-images.yml"
    assert run["event"] == "workflow_dispatch"
    assert run["head_sha"] == revision and run["head_branch"] == "main"
    assert run["status"] == "completed" and run["conclusion"] == "success"
    names = {job["name"] for job in jobs}
    assert "guard" in names
    assert {
        f"{kind} ({arch})"
        for kind in ("publish", "anonymous-pull")
        for arch in ("amd64", "aarch64")
    } <= names
    assert all(job["status"] == "completed" and job["conclusion"] == "success" for job in jobs)


def promote(root):
    marker = root / "wuschel/PENDING_RELEASE.json"
    data = json.loads(marker.read_text())
    path = root / "wuschel/config.yaml"
    text = path.read_text(encoding="utf-8")
    assert str(yaml.safe_load(text)["version"]) == data["previous_version"]
    old = "version: " + data["previous_version"]
    assert text.count(old) == 1
    path.write_text(
        text.replace(old, "version: " + data["version"]), encoding="utf-8", newline="\n"
    )
    marker.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=int, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    assert not subprocess.check_output(["git", "status", "--porcelain"], cwd=root), (
        "Clean checkout required"
    )
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    run = json.loads(
        subprocess.check_output(
            ["gh", "api", f"repos/levtos/wuschel/actions/runs/{args.run_id}"], text=True
        )
    )
    jobs = json.loads(
        subprocess.check_output(
            [
                "gh",
                "api",
                f"repos/levtos/wuschel/actions/runs/{args.run_id}/attempts/{run['run_attempt']}/jobs?per_page=100",
            ],
            text=True,
        )
    )["jobs"]
    validate_run(run, jobs, revision)
    promote(root)
    print(
        "Store promotion prepared; review the two-file diff, then use the normal PR/CI/merge flow."
    )


if __name__ == "__main__":
    main()
