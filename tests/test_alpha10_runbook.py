import ast
import re
from pathlib import Path

import pytest
import yaml

from dev.db_preloads import parse_preloads

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "value,expected",
    [("", ()), ("timescaledb", ("timescaledb",)), (" timescaledb ", ("timescaledb",))],
)
def test_preload_allowlist(value, expected):
    assert parse_preloads(value) == expected


@pytest.mark.parametrize(
    "value",
    ["timescaledb,pg_stat_statements", "pgaudit", "auto_explain", "unknown", "timescaledb,"],
)
def test_other_preloads_are_rejected_before_password(value):
    with pytest.raises(ValueError, match="preload_module_not_allowed"):
        parse_preloads(value)


def test_embedded_parser_is_exactly_the_tested_parser():
    import inspect

    text = (ROOT / "docs/operations/wuschel-new-database.md").read_text(encoding="utf-8")
    block = re.findall(r"```bash\n(.*?)\n```", text, re.S)[0]
    program = block.split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
    tree = ast.parse(program)
    parser = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "parse_preloads"
    )
    expected = ast.parse(inspect.getsource(parse_preloads)).body[0]
    assert ast.dump(parser) == ast.dump(expected)
    assert program.index('parse_preloads(sql("SHOW shared_preload_libraries;"))') < program.index(
        "password = secrets."
    )
    assert "WHERE extname='pgaudit'" in program
    assert "template0" in program


def test_all_app_secrets_use_password_schema():
    app = yaml.safe_load((ROOT / "wuschel/config.yaml").read_text(encoding="utf-8"))
    assert app["schema"]["postgres_password"] == app["schema"]["mqtt_password"] == "password"
    assert app["options"]["postgres_password"] == app["options"]["mqtt_password"] == ""


def test_all_twenty_options_have_names_and_descriptions_in_both_languages():
    app = yaml.safe_load((ROOT / "wuschel/config.yaml").read_text(encoding="utf-8"))
    assert len(app["options"]) == 20
    assert set(app["options"]["title_classifier"]) == {"database", "user", "password"}
    assert app["schema"]["title_classifier"]["password"] == "password"
    for language in ("de", "en"):
        text = yaml.safe_load(
            (ROOT / f"wuschel/translations/{language}.yaml").read_text(encoding="utf-8")
        )["configuration"]
        assert set(text) == set(app["options"])
        assert all(
            set(item) == {"name", "description"} and all(item.values()) for item in text.values()
        )
        assert all(
            word in text["mqtt_mode"]["description"]
            for word in ("disabled", "supervisor", "external")
        )


def test_mqtt_status_exposes_presence_without_credentials():
    from pydantic import SecretStr

    from core_contracts.app import diagnostic_options, mqtt_status

    options = diagnostic_options()
    assert mqtt_status(options) == ("disabled", False)
    with_data = options.model_copy(
        update={"mqtt_username": "synthetic-user", "mqtt_password": SecretStr("synthetic-only")}
    )
    assert mqtt_status(with_data) == ("disabled", True)
    assert mqtt_status(with_data.model_copy(update={"mqtt_mode": "external"})) == ("external", True)
