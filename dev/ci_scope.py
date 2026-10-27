"""Conservative CI selection; a version or build-policy change requires every job."""

import argparse
import json
import os
import re
import subprocess
from pathlib import Path

JOBS = ("platform", "frontend", "workbench-e2e", "ha-bridge", "images", "herd-assets")
RELEASE_FILES = {
    "pyproject.toml",
    "uv.lock",
    "frontend/package.json",
    "frontend/package-lock.json",
    "wuschel/config.yaml",
    "wuschel/PENDING_RELEASE.json",
}


def select(paths: list[str], *, full: bool = False, cache_probe: bool = False) -> dict:
    selected = {name: False for name in JOBS}
    full = full or any(
        p in RELEASE_FILES or p.startswith((".github/", "dev/", "wuschel/")) for p in paths
    )
    probe = cache_probe or any(
        p in {"wuschel/Dockerfile", ".github/workflows/ci.yml"} for p in paths
    )
    for p in paths:
        if p in {"AGENTS.md", "CLAUDE.md", "README.md", "CHANGELOG.md", "LICENSE"}:
            continue
        if p.startswith("docs/") and Path(p).suffix in {".md", ".png", ".svg", ".webp"}:
            continue
        if p.startswith("custom_components/traber/") or p == "tests/test_bridge_kernel.py":
            selected["ha-bridge"] = True
        elif p.startswith("src/"):
            for name in ("platform", "workbench-e2e", "images"):
                selected[name] = True
        elif p.startswith("client/"):
            for name in ("platform", "ha-bridge", "workbench-e2e"):
                selected[name] = True
        elif p.startswith("frontend/"):
            for name in ("frontend", "workbench-e2e", "images"):
                selected[name] = True
            selected["herd-assets"] |= p.startswith("frontend/public/")
        elif p.startswith("tests/"):
            selected["platform"] = True
        elif p in RELEASE_FILES or p.startswith((".github/", "dev/", "wuschel/")):
            pass  # Already forces the full set above.
        else:
            full = True  # Unknown dependencies fail closed.
    if full:
        selected = dict.fromkeys(JOBS, True)
    if cache_probe:
        selected["images"] = True
    return {"full": full, "cache_probe": probe, **selected}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event", required=True)
    parser.add_argument("--head", required=True)
    args = parser.parse_args()
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    inputs = event.get("inputs", {})
    full = False
    if args.event == "pull_request":
        base = event["pull_request"]["base"]["sha"]
    elif args.event == "push":
        base = event.get("before", "")
    else:
        base = inputs.get("base_sha", "")
        full = str(inputs.get("full", "true")).lower() == "true"
    if not re.fullmatch(r"[0-9a-f]{40}", base) or base == "0" * 40:
        full, paths = True, []
    else:
        subprocess.run(["git", "merge-base", "--is-ancestor", base, args.head], check=True)
        paths = (
            subprocess.check_output(["git", "diff", "--name-only", "-z", base, args.head])
            .decode()
            .rstrip("\0")
            .split("\0")
        )
        paths = [p for p in paths if p]
    plan = select(
        paths, full=full, cache_probe=str(inputs.get("cache_probe", "false")).lower() == "true"
    )
    with open(os.environ["GITHUB_OUTPUT"], "a") as out:
        for key, value in plan.items():
            out.write(f"{key}={str(value).lower()}\n")
        out.write("plan=" + json.dumps(plan, separators=(",", ":")) + "\n")
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as out:
        out.write(
            "### CI selection\n\n```json\n"
            + json.dumps({"paths": paths, **plan}, indent=2)
            + "\n```\n"
        )


if __name__ == "__main__":
    main()
