"""Distribution contract: names, metadata, immutable tags and workflow gates."""

import json
import os
import urllib.error
from pathlib import Path

import pytest
import yaml

from dev import image_package as package

REVISION = "a" * 40


@pytest.fixture
def meta():
    return package.metadata("amd64", REVISION)


@pytest.fixture
def image_info(meta):
    return {
        "Os": "linux",
        "Architecture": "amd64",
        "Config": {
            "Entrypoint": ["/entrypoint.sh"],
            "ExposedPorts": {"8787/tcp": {}, "8099/tcp": {}},
            "Healthcheck": {"Test": ["CMD", "health/live"]},
            "Labels": {
                "io.hass.type": "app",
                "io.hass.arch": "amd64",
                "io.hass.version": meta["version"],
                "org.opencontainers.image.source": package.SOURCE,
                "org.opencontainers.image.version": meta["version"],
                "org.opencontainers.image.revision": REVISION,
            },
        },
    }


@pytest.mark.parametrize("arch,docker_arch", [("amd64", "amd64"), ("aarch64", "arm64")])
def test_supervisor_reference_and_platform(arch, docker_arch):
    meta = package.metadata(arch, REVISION)
    assert meta["image"] == f"ghcr.io/levtos/{arch}-wuschel:1.0.0b5"
    assert meta["docker_arch"] == docker_arch


@pytest.mark.parametrize("arch,revision", [("arm64", REVISION), ("amd64", "main")])
def test_distribution_rejects_wrong_arch_name_or_unpinned_source(arch, revision):
    with pytest.raises(AssertionError):
        package.metadata(arch, revision)


@pytest.mark.parametrize("change", ["namespace", "version"])
def test_distribution_rejects_config_drift(tmp_path, change):
    (tmp_path / "wuschel").mkdir()
    config = yaml.safe_load((package.ROOT / "wuschel/config.yaml").read_text())
    config["image" if change == "namespace" else "version"] = (
        "ghcr.io/levtos/arm64-core-contracts" if change == "namespace" else "0.0.0-invalid"
    )
    (tmp_path / "wuschel/config.yaml").write_text(yaml.safe_dump(config))
    (tmp_path / "pyproject.toml").write_text((package.ROOT / "pyproject.toml").read_text())
    with pytest.raises(AssertionError):
        package.metadata("amd64", REVISION, tmp_path)


def test_expected_image_metadata_is_valid(image_info, meta):
    package.check_metadata(image_info, meta)


@pytest.mark.parametrize(
    "label",
    [
        "io.hass.type",
        "io.hass.arch",
        "io.hass.version",
        "org.opencontainers.image.source",
        "org.opencontainers.image.version",
        "org.opencontainers.image.revision",
    ],
)
def test_incorrect_supervisor_or_provenance_label_is_rejected(image_info, meta, label):
    image_info["Config"]["Labels"][label] = "wrong"
    with pytest.raises(AssertionError, match="Incorrect image label"):
        package.check_metadata(image_info, meta)


def test_architecture_is_checked_independently_of_labels(image_info, meta):
    image_info["Architecture"] = "arm64"
    with pytest.raises(AssertionError, match="Wrong image platform"):
        package.check_metadata(image_info, meta)


def test_existing_version_with_other_bytes_is_never_overwritten(monkeypatch, meta, tmp_path):
    monkeypatch.setattr(package, "verify", lambda *_: {"Id": "sha256:tested"})
    monkeypatch.setattr(package, "package_api", lambda *_: {})
    monkeypatch.setattr(package, "existing_version", lambda *_: True)
    monkeypatch.setattr(package, "pull", lambda *_: None)
    monkeypatch.setattr(package, "inspect", lambda *_: {"Id": "sha256:different"})
    monkeypatch.setattr(package, "run", lambda *_: pytest.fail("Must not tag or push"))
    with pytest.raises(AssertionError, match="Refusing to replace"):
        package.publish(meta, tmp_path / "receipt.json")
    assert not (tmp_path / "receipt.json").exists()


def test_existing_version_search_includes_later_pages(monkeypatch):
    responses = iter(
        [
            [{"metadata": {"container": {"tags": ["older"]}}}],
            [{"metadata": {"container": {"tags": ["1.0.0a1"]}}}],
        ]
    )
    monkeypatch.setattr(package, "package_api", lambda *_: next(responses))
    assert package.existing_version("amd64-core-contracts", "1.0.0a1")


@pytest.mark.parametrize("status", [403, 404])
def test_package_api_denied_is_not_confused_with_not_found(monkeypatch, status):
    monkeypatch.setenv("GH_TOKEN", "synthetic-test-token")

    def fail(*args, **kwargs):
        raise urllib.error.HTTPError("https://api.github.com", status, "test", {}, None)

    monkeypatch.setattr(package.urllib.request, "urlopen", fail)
    if status == 404:
        assert package.package_api("amd64-core-contracts") is None
    else:
        with pytest.raises(RuntimeError, match="HTTP 403"):
            package.package_api("amd64-core-contracts")


def test_anonymous_verification_rejects_receipt_for_other_commit(monkeypatch, meta, tmp_path):
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps({**meta, "revision": "b" * 40}))
    monkeypatch.setattr(package, "pull", lambda *_: pytest.fail("Must check receipt first"))
    with pytest.raises(AssertionError, match="Receipt/source mismatch"):
        package.anonymous_verify(meta, receipt)


def test_anonymous_pull_cannot_use_existing_registry_credentials(monkeypatch, meta, tmp_path):
    original = tmp_path / "original"
    original.mkdir()
    credentials = {"auths": {"ghcr.io": {"auth": "synthetic"}}, "credsStore": "test"}
    (original / "config.json").write_text(json.dumps(credentials))
    monkeypatch.setenv("DOCKER_CONFIG", str(original))
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps({**meta, "image_id": "tested", "digest": "sha256:test"}))
    pulled = []

    def pull(_):
        active = Path(os.environ["DOCKER_CONFIG"])
        assert active != original
        assert json.loads((active / "config.json").read_text()) == {"auths": {}}
        pulled.append(True)

    monkeypatch.setattr(package, "pull", pull)
    monkeypatch.setattr(
        package,
        "verify",
        lambda *_: {
            "Id": "tested",
            "RepoDigests": [meta["image"].rsplit(":", 1)[0] + "@sha256:test"],
        },
    )
    package.anonymous_verify(meta, receipt)
    assert pulled == [True]
    assert os.environ["DOCKER_CONFIG"] == str(original)
    assert json.loads((original / "config.json").read_text()) == credentials


def test_publication_is_manual_and_gated_by_full_ci():
    workflow = yaml.load(
        (package.ROOT / ".github/workflows/publish-images.yml").read_text(), Loader=yaml.BaseLoader
    )
    assert set(workflow["on"]) == {"workflow_dispatch"}
    assert workflow["permissions"] == {"contents": "read", "actions": "read"}
    jobs = workflow["jobs"]
    release_gate = next(
        step
        for step in jobs["guard"]["steps"]
        if step.get("name", "").startswith("Require a new release")
    )
    assert release_gate["run"] == "uv run python dev/verify_postrelease.py --mode release"
    assert "if" not in release_gate
    checkout = next(
        step
        for step in jobs["guard"]["steps"]
        if step.get("uses", "").startswith("actions/checkout@")
    )
    assert checkout["with"]["fetch-depth"] == "0"
    assert "ci" not in jobs
    proof = next(step for step in jobs["guard"]["steps"] if step.get("id") == "tested")
    assert "dev/release_ci.py" in proof["run"] and "if" not in proof
    download = next(
        step
        for step in jobs["publish"]["steps"]
        if step.get("uses", "").startswith("actions/download-artifact@")
    )
    assert download["with"]["run-id"] == "${{ inputs.ci_run_id }}"
    assert jobs["publish"]["needs"] == "guard"
    assert jobs["publish"]["permissions"] == {
        "contents": "read",
        "actions": "read",
        "packages": "write",
    }
    assert jobs["anonymous-pull"]["needs"] == ["guard", "publish"]
    assert jobs["anonymous-pull"]["permissions"] == {"contents": "read"}
    assert "docker login" not in str(jobs["anonymous-pull"])
    assert set(jobs["publish"]["strategy"]["matrix"]["arch"]) == set(package.ARCHITECTURES)
    ci = yaml.load((package.ROOT / ".github/workflows/ci.yml").read_text(), Loader=yaml.BaseLoader)
    assert any(
        step.get("run") == "uv run python dev/verify_postrelease.py --mode ci" and "if" not in step
        for step in ci["jobs"]["platform"]["steps"]
    )
    assert "workflow_call" in ci["on"]
    matrix = ci["jobs"]["images"]["strategy"]["matrix"]["include"]
    assert {row["arch"]: row["platform"] for row in matrix} == {
        "amd64": "linux/amd64",
        "aarch64": "linux/arm64",
    }
    builds = [
        step
        for step in ci["jobs"]["images"]["steps"]
        if step.get("uses", "").startswith("docker/build-push-action@")
    ]
    assert len(builds) == 1 and builds[0]["with"]["push"] == "false"
