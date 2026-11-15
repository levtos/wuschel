"""Every platform validation code has reviewed German guidance in the UI."""

import ast
import inspect
import json
from pathlib import Path
from textwrap import dedent
from typing import get_args

from pydantic_core import ErrorType

from core_contracts import declarative, slots, validation


def test_every_validation_code_has_a_german_text():
    root = Path(__file__).parents[1]
    texts = json.loads((root / "frontend/src/i18n/de.json").read_text(encoding="utf-8"))
    codes = set()
    for node in ast.walk(ast.parse(Path(validation.__file__).read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        value = None
        if node.func.id == "add" and node.args:
            value = node.args[0]
        if node.func.id == "ValidationIssue":
            value = next((kw.value for kw in node.keywords if kw.arg == "code"), None)
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            codes.add(value.value)
    assert len(codes) >= 35  # Fail if extraction stops finding the backend contract.
    # validation.py forwards Pydantic codes and declarative rule errors.
    for code in get_args(ErrorType):
        codes.update({f"schema.{code}", f"parameter.{code}"})
    checks = [
        declarative.check_definition,
        declarative.all_rules,
        declarative.joined,
        declarative.map_key,
    ]
    for rule in slots.RULES.values():
        checks.extend(check for check in (rule.check, rule.validate) if check is not None)
        if rule.minimum_language:
            codes.add(f"definition.language_version_{rule.minimum_language}_required")
    for check in checks:
        for node in ast.walk(ast.parse(dedent(inspect.getsource(check)))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in {"ValueError", "RuleValidationError", "error"}
                and node.args
                and isinstance(node.args[0], ast.Constant)
            ):
                codes.add(f"definition.{node.args[0].value}")
    for check in slots.VALIDATION.values():
        for node in ast.walk(ast.parse(dedent(inspect.getsource(check)))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "add"
                and node.args
                and isinstance(node.args[0], ast.Constant)
            ):
                codes.add(node.args[0].value)
    missing = sorted(code for code in codes if not texts.get(f"validation.{code}"))
    assert not missing, f"German validation guidance missing: {missing}"
    assert all(texts[f"validation.{code}"] != texts["validation.unknown"] for code in codes)
