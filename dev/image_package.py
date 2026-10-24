"""Supervisor image metadata, content and publication checks; no HA connection."""

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

import yaml

try:
    from .release_version import candidate
except ImportError:
    from release_version import candidate

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "levtos/wuschel"
SOURCE = "https://github.com/" + REPOSITORY
ARCHITECTURES = {"amd64": "amd64", "aarch64": "arm64"}


def metadata(arch, revision, root=ROOT):
    config = yaml.safe_load((root / "wuschel/config.yaml").read_text())
    version = candidate(root, str(config["version"]))
    assert set(config["arch"]) == set(ARCHITECTURES), "Unsupported Supervisor architectures"
    assert config["image"] == "ghcr.io/levtos/{arch}-wuschel", "Unexpected image namespace"
    assert arch in ARCHITECTURES, "Use Supervisor arch names: amd64 or aarch64"
    assert re.fullmatch(r"[0-9a-f]{40}", revision), "A full tested commit SHA is required"
    assert re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", version), "Invalid image tag"
    project = tomllib.loads((root / "pyproject.toml").read_text())
    assert project["project"]["version"] == version, "Supervisor and Python versions differ"
    return {
        "image": config["image"].format(arch=arch) + ":" + version,
        "version": version,
        "arch": arch,
        "docker_arch": ARCHITECTURES[arch],
        "revision": revision,
    }


def run(*args):
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"{args[0]} failed: {result.stderr.strip()}")
    return result.stdout


def inspect(image):
    return json.loads(run("docker", "image", "inspect", image))[0]


def check_metadata(info, meta):
    assert (info["Os"], info["Architecture"]) == ("linux", meta["docker_arch"]), (
        "Wrong image platform"
    )
    labels = info["Config"].get("Labels") or {}
    for key, value in {
        "io.hass.type": "app",
        "io.hass.version": meta["version"],
        "io.hass.arch": meta["arch"],
        "org.opencontainers.image.source": SOURCE,
        "org.opencontainers.image.version": meta["version"],
        "org.opencontainers.image.revision": meta["revision"],
    }.items():
        assert labels.get(key) == value, f"Incorrect image label: {key}"
    assert info["Config"]["Entrypoint"] == ["/entrypoint.sh"], "Unexpected entrypoint"
    assert set(info["Config"]["ExposedPorts"]) == {"8787/tcp", "8099/tcp"}
    assert "health/live" in " ".join(info["Config"]["Healthcheck"]["Test"])


def check_content(image, meta):
    expected = {
        "/app/" + path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted((ROOT / "src").rglob("*.py"))
        + sorted((ROOT / "migrations").glob("*.sql"))
    }
    expected["/entrypoint.sh"] = hashlib.sha256(
        (ROOT / "wuschel/rootfs/entrypoint.sh").read_bytes()
    ).hexdigest()
    probe = (
        "import hashlib,importlib.metadata,json,sys; from pathlib import Path; "
        "expected=json.loads(sys.argv[1]); "
        "actual={p:hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in expected}; "
        "assert actual==expected, 'Image source/migrations/entrypoint differ from checkout'; "
        "assert Path('/entrypoint.sh').stat().st_mode & 0o111; "
        "assert Path('/app/frontend/index.html').is_file(); "
        "assert any(Path('/app/frontend/assets').glob('*')); "
        "assert importlib.metadata.version('wuschel')==sys.argv[2]; "
        "from core_contracts.contract_types import type_registry; "
        "types=type_registry(); "
        "assert all(not types.get(name,1).schema.fixture for name in "
        "('opening.dual_contact','opening.single_contact','opening_zone.facts')); "
        "print('PASS: packaged source, migrations, entrypoint, frontend and installed version')"
    )
    print(
        run(
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--platform",
            "linux/" + meta["docker_arch"],
            "--entrypoint",
            "python",
            image,
            "-c",
            probe,
            json.dumps(expected),
            meta["version"],
        ).strip()
    )


def verify(image, meta):
    info = inspect(image)
    check_metadata(info, meta)
    check_content(image, meta)
    return info


def package_api(path):
    request = urllib.request.Request(
        "https://api.github.com/orgs/levtos/packages/container/" + path,
        headers={
            "Authorization": "Bearer " + os.environ["GH_TOKEN"],
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise RuntimeError(
            f"Package API HTTP {error.code}; check Actions package permissions"
        ) from None


def existing_version(name, version):
    page = 1
    while versions := package_api(f"{name}/versions?per_page=100&page={page}"):
        if any(version in v["metadata"]["container"]["tags"] for v in versions):
            return True
        page += 1
    return False


def pull(meta):
    print(run("docker", "pull", "--platform", "linux/" + meta["docker_arch"], meta["image"]))


def publish(meta, receipt):
    local = verify("core-contracts:smoke", meta)
    name = meta["arch"] + "-wuschel"
    before = package_api(name)
    print(
        json.dumps(
            {
                "package_before": before
                and {
                    key: before.get(key)
                    for key in ("name", "visibility", "created_at", "version_count")
                }
            }
        )
    )
    if existing_version(name, meta["version"]):
        pull(meta)
        assert inspect(meta["image"])["Id"] == local["Id"], (
            "Refusing to replace an existing version with different image bytes; use a new version"
        )
    else:
        run("docker", "tag", "core-contracts:smoke", meta["image"])
        print(run("docker", "push", meta["image"]))
        pull(meta)
    pushed = inspect(meta["image"])
    assert pushed["Id"] == local["Id"], "Published image differs from tested CI artifact"
    digest = next(
        value.split("@", 1)[1]
        for value in pushed["RepoDigests"]
        if value.startswith(meta["image"].rsplit(":", 1)[0] + "@")
    )
    package = package_api(name)
    assert package and (package.get("repository") or {}).get("full_name", "").lower() == REPOSITORY
    record = {
        **meta,
        "image_id": local["Id"],
        "digest": digest,
        "package_url": package["html_url"],
        "visibility_at_publish": package["visibility"],
        "package_created_at": package["created_at"],
        "workflow_run": os.environ["GITHUB_RUN_ID"],
    }
    receipt.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(record, indent=2))


def anonymous_verify(meta, receipt):
    expected = json.loads(receipt.read_text())
    assert all(expected[key] == value for key, value in meta.items()), "Receipt/source mismatch"
    # A fresh Docker config has no auths or credential helper. Never alter the user's config.
    original_config = os.environ.get("DOCKER_CONFIG")
    try:
        with tempfile.TemporaryDirectory(prefix="cc-anonymous-") as directory:
            os.environ["DOCKER_CONFIG"] = directory
            Path(directory, "config.json").write_text('{"auths": {}}')
            pull(meta)
            info = verify(meta["image"], meta)
            assert info["Id"] == expected["image_id"], "Anonymous pull differs from tested artifact"
            assert meta["image"].rsplit(":", 1)[0] + "@" + expected["digest"] in info["RepoDigests"]
    finally:
        if original_config is None:
            os.environ.pop("DOCKER_CONFIG", None)
        else:
            os.environ["DOCKER_CONFIG"] = original_config
    print(json.dumps({**expected, "anonymous_pull": True}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("metadata", "check", "publish", "anonymous"))
    parser.add_argument("--arch", required=True, choices=ARCHITECTURES)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args()
    meta = metadata(args.arch, args.revision)
    if args.operation == "metadata":
        for key, value in meta.items():
            print(f"{key}={value}")
    elif args.operation == "check":
        verify("core-contracts:smoke", meta)
    else:
        assert args.receipt is not None, "--receipt is required"
        if args.operation == "publish":
            publish(meta, args.receipt)
        else:
            anonymous_verify(meta, args.receipt)


if __name__ == "__main__":
    main()
