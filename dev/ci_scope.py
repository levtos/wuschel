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
    "tests/test_replay.py",
    "tests/conftest.py",
}
ROOT = Path(__file__).resolve().parents[1]


def area_plan(paths: list[str], areas: dict) -> dict | None:
    """Only a wholly mapped backend diff may narrow the backend test gate."""
    names = set()
    for path in paths:
        if not path.startswith(("src/core_contracts/", "tests/test_")):
            return None
        matched = {
            name
            for name, area in areas.items()
            if path in area["tests"]
            or any(
                path.startswith(prefix) if prefix.endswith("/") else path == prefix
                for prefix in area["paths"]
            )
        }
        if not matched or matched & {"release", "store", "load", "traber"}:
            return None
        names.update(matched)
    tests = sorted({test for name in names for test in areas[name]["tests"]})
    if not tests:
        return None
    # Invalid/stale mapping must never silently run an empty or partial set.
    if any(not (ROOT / test).is_file() for test in tests):
        return None
    return {"areas": sorted(names), "pytest_files": sorted(set(tests) | {"tests/test_replay.py"})}


def select(paths: list[str], *, full: bool = False, cache_probe: bool = False) -> dict:
    selected = {name: False for name in JOBS}
    full = full or any(
        p in RELEASE_FILES
        or p.startswith((".github/", "dev/", "wuschel/", "tests/fixtures/replay/"))
        for p in paths
    )
    probe = cache_probe or any(
        p in {"wuschel/Dockerfile", ".github/workflows/ci.yml"} for p in paths
    )
    area = None
    if not full and not cache_probe:
        try:
            areas = json.loads((ROOT / "tests/areas.json").read_text(encoding="utf-8"))
            area = area_plan(paths, areas)
        except (OSError, ValueError, KeyError, TypeError):
            full = True
    if area:
        return {
            "full": False,
            "cache_probe": False,
            **selected,
            "platform": True,
            "area_only": True,
            **area,
        }
    for p in paths:
        if p in {"AGENTS.md", "CLAUDE.md", "README.md", "CHANGELOG.md", "LICENSE"}:
            continue
        if p.startswith("docs/") and Path(p).suffix in {".md", ".png", ".svg", ".webp"}:
            continue
        if p.startswith("custom_components/traber/") or p == "tests/test_bridge_kernel.py":
            selected["ha-bridge"] = True
        elif p.startswith("src/"):
            full = True  # An unmapped backend path is shared core.
        elif p.startswith("client/"):
            for name in ("platform", "ha-bridge", "workbench-e2e"):
                selected[name] = True
        elif p.startswith("frontend/"):
            for name in ("frontend", "workbench-e2e", "images"):
                selected[name] = True
            selected["herd-assets"] |= p.startswith("frontend/public/")
        elif p.startswith("tests/"):
            full = True  # New/unmapped tests and shared fixtures are core too.
        elif p in RELEASE_FILES or p.startswith((".github/", "dev/", "wuschel/")):
            pass  # Already forces the full set above.
        else:
            full = True  # Unknown dependencies fail closed.
    if full:
        selected = dict.fromkeys(JOBS, True)
    if cache_probe:
        selected["images"] = True
    return {
        "full": full,
        "cache_probe": probe,
        **selected,
        "area_only": False,
        "areas": [],
        "pytest_files": [],
    }


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
        full = event["pull_request"]["base"].get("ref") == "main"
    elif args.event == "push":
        base = event.get("before", "")
        full = event.get("ref") == "refs/heads/main" or event.get("ref", "").startswith(
            "refs/tags/"
        )
    else:
        base = inputs.get("base_sha", "")
        full = str(inputs.get("full", "true")).lower() == "true"
    if not re.fullmatch(r"[0-9a-f]{40}", base) or base == "0" * 40:
        full, paths = True, []
    else:
        subprocess.run(["git", "merge-base", "--is-ancestor", base, args.head], check=True)
        paths = (
            subprocess.check_output(
                ["git", "diff", "--name-only", "--no-renames", "-z", base, args.head]
            )
            .decode()
            .rstrip("\0")
            .split("\0")
        )
        paths = [p for p in paths if p]
    plan = select(
        paths, full=full, cache_probe=str(inputs.get("cache_probe", "false")).lower() == "true"
    )
    measure = (
        args.event == "workflow_dispatch" and str(inputs.get("area_map", "false")).lower() == "true"
    )
    if measure:
        plan = select([], full=False)
        plan.update(dict.fromkeys(JOBS, False))
        plan.update(full=False, cache_probe=False)
    plan["area_map"] = measure
    with open(os.environ["GITHUB_OUTPUT"], "a") as out:
        for key, value in plan.items():
            out.write(f"{key}={json.dumps(value, separators=(',', ':'))}\n")
        out.write("plan=" + json.dumps(plan, separators=(",", ":")) + "\n")
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as out:
        out.write(
            "### CI selection\n\n```json\n"
            + json.dumps({"paths": paths, **plan}, indent=2)
            + "\n```\n"
        )


if __name__ == "__main__":
    main()
