"""Version ordering belongs to the Supervisor slug read from each historical tag."""

import argparse
import json
import re
import subprocess
import tomllib
from importlib.metadata import version
from pathlib import Path
from typing import Literal

import yaml
from awesomeversion import AwesomeVersion

PIN = "25.8.0"
ROOT = Path(__file__).resolve().parents[1]


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True, encoding="utf-8")


def tagged_app(root: Path, tag: str) -> dict[str, str]:
    """Identify the tagged app from its version, then read its actual slug."""
    configs = []
    for path in git(root, "ls-tree", "-r", "--name-only", tag).splitlines():
        if re.fullmatch(r"[^/]+/config.yaml", path):
            config = yaml.safe_load(git(root, "show", f"{tag}:{path}"))
            if isinstance(config, dict) and str(config.get("version")) == tag.removeprefix("v"):
                if not isinstance(config.get("slug"), str) or not config["slug"]:
                    raise ValueError(f"{tag}: slug fehlt in {path}")
                configs.append({"slug": config["slug"], "path": path})
    if len(configs) != 1:
        raise ValueError(f"{tag}: App-Konfiguration fehlt oder ist mehrdeutig")
    return configs[0]


def compare_tags(
    root: Path,
    candidate: str,
    slug: str,
    tags: list[str],
    *,
    mode: Literal["ci", "release"] = "release",
) -> dict:
    errors, comparisons = [], []
    new = AwesomeVersion(candidate)
    if not new.valid or re.search(r"(?:a|b|rc)\d+.*(?:\.post|\.dev)", candidate):
        errors.append("Ungültige Vorabversion oder verbotenes post/dev-Suffix")
    if not tags:
        errors.append("Tag-Historie fehlt")
    for tag in sorted(tags):
        if not re.match(r"v?\d", tag):
            comparisons.append({"tag": tag, "classification": "kein Versionstag"})
            continue
        old = AwesomeVersion(tag.removeprefix("v"))
        if not old.valid:
            errors.append(f"{tag}: ungültiger Versionstag")
            continue
        try:
            app = tagged_app(root, tag)
            newer = bool(new > old)
            same = app["slug"] == slug
            comparisons.append(
                {
                    "tag": tag,
                    "version": str(old),
                    "newer": newer,
                    "slug_lineage": app["slug"],
                    "config_path": app["path"],
                    "classification": "gleiche App" if same else "andere App",
                }
            )
            if same and (not newer if mode == "release" else new < old):
                relation = "nicht neuer als" if mode == "release" else "kleiner als"
                errors.append(f"{candidate} ist {relation} {tag} in Linie {slug}")
        except (ValueError, subprocess.CalledProcessError, yaml.YAMLError) as error:
            errors.append(str(error))
    return {
        "candidate": candidate,
        "mode": mode,
        "slug": slug,
        "awesomeversion": PIN,
        "tag_snapshot": sorted(tags),
        "comparisons": comparisons,
        "errors": errors,
        "passed": not errors,
    }


def bridge_version_errors(root: Path, candidate: str, bridge_version: str) -> list[str]:
    if bridge_version == candidate:
        return []
    if not AwesomeVersion(bridge_version).valid or AwesomeVersion(bridge_version) > AwesomeVersion(
        candidate
    ):
        return ["Ungültige oder neuere Bridge-Version"]
    tag = f"v{bridge_version}"
    try:
        git(root, "rev-parse", f"{tag}:custom_components/traber")
        changed = git(root, "diff", "--name-only", tag, "--", "custom_components/traber")
        if not changed:
            return []
    except subprocess.CalledProcessError:
        pass
    return [
        "Abweichende Bridge-Version nur bei unverändertem Traber gegenüber seinem Versionstag erlaubt"
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("ci", "release"), default="release")
    args = parser.parse_args()
    if version("awesomeversion") != PIN:
        raise SystemExit(f"awesomeversion=={PIN} erforderlich")
    if git(ROOT, "rev-parse", "--is-shallow-repository").strip() != "false":
        raise SystemExit("Vollständige Git-Historie erforderlich")
    git(ROOT, "fetch", "origin", "--tags")
    remote = git(ROOT, "ls-remote", "--tags", "origin")
    tags = [
        line.split("refs/tags/", 1)[1]
        for line in remote.splitlines()
        if "refs/tags/" in line and not line.endswith("^{}")
    ]
    for line in remote.splitlines():
        sha, ref = line.split()
        if git(ROOT, "rev-parse", ref).strip() != sha:
            raise SystemExit(f"Tag-Snapshot stimmt nicht mit origin überein: {ref}")
    app = yaml.safe_load((ROOT / "wuschel/config.yaml").read_text(encoding="utf-8"))
    try:
        from .release_version import candidate as release_candidate
    except ImportError:
        from release_version import candidate as release_candidate
    candidate = release_candidate(ROOT, str(app["version"]))
    report = compare_tags(ROOT, candidate, app["slug"], tags, mode=args.mode)
    versions = [
        tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"],
        json.loads((ROOT / "frontend/package.json").read_text(encoding="utf-8"))["version"],
    ]
    if any(value != candidate for value in versions):
        report["errors"].append("App-, Paket- und Frontend-Version müssen übereinstimmen")
    bridge_version = json.loads(
        (ROOT / "custom_components/traber/manifest.json").read_text(encoding="utf-8")
    )["version"]
    report["bridge_version"] = bridge_version
    report["errors"].extend(bridge_version_errors(ROOT, candidate, bridge_version))
    report["passed"] = not report["errors"]
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["passed"] else 1)
