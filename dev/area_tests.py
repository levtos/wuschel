"""Print pytest arguments for an area or changed paths (ALL means full CI)."""

import argparse
import json
import shlex
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def changed_paths(base):
    # Compare the entire tracked working tree with base: commits, index and edits.
    # Keep both paths of renames so a move cannot hide the old area's impact.
    tracked = subprocess.check_output(
        ["git", "diff", "--name-only", "--no-renames", "-z", base, "--"], cwd=ROOT
    )
    untracked = subprocess.check_output(
        ["git", "ls-files", "--others", "--exclude-standard", "-z"], cwd=ROOT
    )
    return sorted(set((tracked + untracked).decode("utf-8").rstrip("\0").split("\0")) - {""})


def affected_tests(areas, paths):
    selected = set()
    for path in paths:
        matched = False
        for area in areas.values():
            if path in area["tests"] or any(
                path.startswith(prefix) if prefix.endswith("/") else path == prefix
                for prefix in area["paths"]
            ):
                matched = True
                selected.update(area["tests"])
        if not matched and (
            path.startswith(("src/", "tests/", "dev/", ".github/"))
            or path in {"pyproject.toml", "uv.lock", "AGENTS.md"}
        ):
            return "ALL"
    return shlex.join(sorted(selected))


def main():
    areas = json.loads((ROOT / "tests/areas.json").read_text(encoding="utf-8"))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("area", nargs="?", choices=areas)
    parser.add_argument("--changed", nargs="?", const="origin/b25", metavar="BASE")
    args = parser.parse_args()
    if (args.area is None) == (args.changed is None):
        parser.error("choose an area or --changed [BASE]")
    if args.changed is None:
        print(shlex.join(areas[args.area]["tests"]))
    else:
        try:
            paths = changed_paths(args.changed)
        except subprocess.CalledProcessError as error:
            parser.exit(1, f"cannot compare changed paths with {args.changed}: {error}\n")
        print(affected_tests(areas, paths))


if __name__ == "__main__":
    main()
