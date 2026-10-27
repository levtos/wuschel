import ast
import json
import re
from pathlib import Path

import yaml

from core_contracts.registry import validate
from core_contracts.testing.contract_types import type_registry

ROOT = Path(__file__).resolve().parents[1]


def test_only_approved_opening_domain_registration_outside_testing():
    domain_terms = {
        "opening",
        "window",
        "fenster",
        "door",
        "presence",
        "bio",
        "sleep",
        "activity",
        "gaming",
        "media",
        "climate",
        "heating",
        "blind",
        "light",
        "day_phase",
        "wake",
    }
    for path in (ROOT / "src").rglob("*.py"):
        if "testing" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "ContractType"
            ):
                # Alpha 4's generic adapter constructs types from validated data.
                # Alpha 8 accepts data-defined identities; no domain code is added.
                if path.name == "declarative.py" and any(
                    keyword.arg == "type_id"
                    and isinstance(keyword.value, ast.Attribute)
                    and isinstance(keyword.value.value, ast.Name)
                    and keyword.value.value.id == "definition"
                    and keyword.value.attr == "type_id"
                    for keyword in node.keywords
                ):
                    continue
                if path == ROOT / "src/core_contracts/openings.py":
                    from core_contracts.openings import register
                    from core_contracts.schema import TypeRegistry

                    production = TypeRegistry()
                    register(production)
                    assert set(production.types) == {
                        ("opening.dual_contact", 1),
                        ("opening.single_contact", 1),
                        ("opening_zone.facts", 1),
                    }
                    assert all(not entry.schema.fixture for entry in production.types.values())
                    continue
                raise AssertionError(f"Unapproved domain registration: {path.name}")
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert not any(node.value.startswith(term + ".v") for term in domain_terms)


def test_declarative_identities_allow_nonfixtures_but_remain_closed():
    import pytest
    from pydantic import ValidationError

    from core_contracts.declarative_models import DeclarativeDefinition

    from .test_declarative import definition

    for identity in ("example.level", "example.state", "other.test"):
        data = definition()
        data["type_id"] = identity
        data["schema"]["fixture"] = False
        result = DeclarativeDefinition.model_validate(data)
        assert not result.output_schema.fixture
    for identity in ("test", "", ".bad", "x;eval(1)"):
        data = definition()
        data["type_id"] = identity
        with pytest.raises(ValidationError):
            DeclarativeDefinition.model_validate(data)


def test_example_registry_and_no_forbidden_context():
    config = validate(
        json.loads((ROOT / "docs/platform-alpha1/example-registry.json").read_text()),
        type_registry(),
    )
    assert len(config.contracts) == 5
    assert all(c.type_id.startswith("test.") for c in config.contracts)
    for forbidden in (
        "profile",
        "profile_id",
        "consumer_ids",
        "shadow",
        "published",
        "safe_default",
        "hold_last",
        "set_state",
    ):
        assert forbidden not in type(config).model_fields


def test_watchdog_security_and_container():
    config = yaml.safe_load((ROOT / "wuschel/config.yaml").read_text())
    assert config["watchdog"].endswith("/health/live")
    assert config["hassio_api"] is False and config["homeassistant_api"] is True
    assert config["ports"]["8787/tcp"] is None
    assert config["arch"] == ["amd64"]
    assert not any(config.get(key) for key in ("privileged", "docker_api", "host_network"))
    docker = (ROOT / "wuschel/Dockerfile").read_text()
    assert "BUILD_FROM" not in docker and "uv sync --frozen" in docker
    assert not (ROOT / "wuschel/build.yaml").exists()
    entry = (ROOT / "wuschel/rootfs/entrypoint.sh").read_text()
    assert "setpriv --reuid=10001 --regid=10001" in entry


def test_bridge_has_no_domain_or_await_in_subscription():
    path = ROOT / "custom_components/traber/__init__.py"
    tree = ast.parse(path.read_text())
    subscribe = next(
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "subscribe"
    )
    assert not any(isinstance(n, ast.Await) for n in ast.walk(subscribe))
    imports = [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
    assert not any(name.startswith("core_contracts.") for name in imports)
    text = path.read_text()
    assert "async_track_state_report_event" in text and "require_admin" in text


def test_client_supports_declared_python_minimum():
    for path in (ROOT / "client/core_contracts_client").glob("*.py"):
        ast.parse(path.read_text(), feature_version=(3, 12))


def test_frontend_example_fixture_matches_documented_registry():
    """The Docker frontend stage only contains frontend/; its tests use this verbatim copy."""
    documented = (ROOT / "docs/alpha4/example-registry.json").read_bytes()
    assert (ROOT / "frontend/test/fixtures/example-registry.json").read_bytes() == documented
    for path in (ROOT / "frontend/src").rglob("*.ts*"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"from '[./]+/docs/", text), path
