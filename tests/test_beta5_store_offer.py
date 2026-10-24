"""A premature Supervisor version must fail CI even if edited without the promoter."""

import hashlib
import json

import pytest
import yaml

from dev import store_offer as offer


@pytest.mark.parametrize("arch,docker_arch", [("amd64", "amd64"), ("aarch64", "arm64")])
@pytest.mark.parametrize("failure", [None, "version", "platform", "digest", "missing", "index"])
def test_anonymous_offered_image_metadata(arch, docker_arch, failure):
    version = "1.0.0b5"
    blob = json.dumps(
        {
            "architecture": "other" if failure == "platform" else docker_arch,
            "os": "linux",
            "config": {
                "Labels": {
                    "io.hass.version": version,
                    "org.opencontainers.image.version": "1.0.0b4"
                    if failure == "version"
                    else version,
                    "io.hass.arch": arch,
                    "io.hass.type": "app",
                }
            },
        }
    ).encode()
    digest = "sha256:" + hashlib.sha256(blob).hexdigest()
    calls = []

    def get(url, headers):
        calls.append((url, headers))
        if "/token?" in url:
            assert headers == {}
            return b'{"token":"synthetic-anonymous-pull"}'
        assert headers["Authorization"] == "Bearer synthetic-anonymous-pull"
        if failure == "missing":
            raise RuntimeError("Offered image is not publicly readable: HTTP 404")
        if "/blobs/" in url:
            return blob + b" " if failure == "digest" else blob
        if failure == "index" and url.endswith(version):
            return json.dumps(
                {
                    "manifests": [
                        {
                            "platform": {"architecture": docker_arch, "os": "linux"},
                            "digest": "sha256:" + "a" * 64,
                        }
                    ]
                }
            ).encode()
        return json.dumps({"config": {"digest": digest}}).encode()

    if failure in {None, "index"}:
        offer.verify(arch, version, get)
    else:
        with pytest.raises((AssertionError, RuntimeError)):
            offer.verify(arch, version, get)
    assert calls[0][1] == {}
    assert any(url.endswith("/manifests/" + version) for url, _ in calls)


def test_ci_checks_the_offered_version_unconditionally(monkeypatch):
    root = offer.Path(offer.__file__).resolve().parents[1]
    config = yaml.safe_load((root / "wuschel/config.yaml").read_text())
    calls = []
    monkeypatch.setattr(offer, "verify", lambda arch, version: calls.append((arch, version)))
    offer.main()
    assert calls == [(arch, str(config["version"])) for arch in config["arch"]]
    workflow = yaml.load((root / ".github/workflows/ci.yml").read_text(), Loader=yaml.BaseLoader)
    (step,) = [
        step
        for step in workflow["jobs"]["platform"]["steps"]
        if step.get("run") == "uv run python dev/store_offer.py"
    ]
    assert "if" not in step and "continue-on-error" not in step
