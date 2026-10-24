"""The offered Supervisor version must already have public images on both architectures."""

import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import yaml

ACCEPT = "application/vnd.oci.image.manifest.v1+json,application/vnd.docker.distribution.manifest.v2+json,application/vnd.oci.image.index.v1+json,application/vnd.docker.distribution.manifest.list.v2+json"


def fetch(url, headers):
    try:
        with urllib.request.urlopen(
            urllib.request.Request(url, headers=headers), timeout=30
        ) as response:
            data = response.read(1_000_001)
            assert len(data) <= 1_000_000, "Registry metadata resource limit"
            return data
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"Offered image is not publicly readable: HTTP {error.code}") from None


def verify(arch, version, get=fetch):
    repository = f"levtos/{arch}-wuschel"
    query = urllib.parse.urlencode({"service": "ghcr.io", "scope": f"repository:{repository}:pull"})
    # Anonymous pull capability only, kept in memory; no account or saved credentials.
    token = json.loads(get("https://ghcr.io/token?" + query, {}))["token"]
    headers = {"Authorization": "Bearer " + token, "Accept": ACCEPT}
    base = "https://ghcr.io/v2/" + repository
    manifest = json.loads(get(base + "/manifests/" + urllib.parse.quote(version, safe=""), headers))
    docker_arch = {"amd64": "amd64", "aarch64": "arm64"}[arch]
    if "manifests" in manifest:
        choices = [
            row
            for row in manifest["manifests"]
            if row.get("platform", {}).get("architecture") == docker_arch
            and row["platform"].get("os") == "linux"
        ]
        assert len(choices) == 1, "Offered architecture missing or ambiguous"
        manifest = json.loads(get(base + "/manifests/" + choices[0]["digest"], headers))
    digest = manifest["config"]["digest"]
    assert digest.startswith("sha256:") and len(digest) == 71, "Invalid config digest"
    raw = get(base + "/blobs/" + digest, headers)
    assert "sha256:" + hashlib.sha256(raw).hexdigest() == digest, (
        "Offered image config digest mismatch"
    )
    config = json.loads(raw)
    assert config["architecture"] == docker_arch and config["os"] == "linux", (
        "Offered image architecture mismatch"
    )
    labels = config["config"]["Labels"]
    assert labels["io.hass.version"] == labels["org.opencontainers.image.version"] == version, (
        "Offered image version mismatch"
    )
    assert labels["io.hass.arch"] == arch and labels["io.hass.type"] == "app"


def main():
    config = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / "wuschel/config.yaml").read_text()
    )
    assert config["image"] == "ghcr.io/levtos/{arch}-wuschel"
    assert set(config["arch"]) == {"amd64", "aarch64"}
    for arch in config["arch"]:
        verify(arch, str(config["version"]))
        print(f"Offered {arch} image is public with matching version and platform")


if __name__ == "__main__":
    main()
