"""Measure function-line coverage per test and regenerate tests/areas.json.

Run the full measurement only in the manually dispatched PostgreSQL CI job.
"""

import argparse
import ast
import json
from collections import defaultdict
from pathlib import Path

import coverage
import pytest

ROOT = Path(__file__).resolve().parents[1]
MIN_LINES = 5


def function_lines(source: str) -> set[int]:
    return {
        line
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        for statement in node.body
        for line in range(statement.lineno, (statement.end_lineno or statement.lineno) + 1)
    }


def matches(path: str, prefixes: list[str]) -> bool:
    return any(path.startswith(p) if p.endswith("/") else path == p for p in prefixes)


def measured_areas(areas: dict, data: coverage.CoverageData, root: Path) -> tuple[dict, dict]:
    hits: dict[tuple[str, int], set[str]] = {}
    runtime_tests: set[str] = set()
    observed: set[str] = set()
    runtime = ast.parse((root / "src/core_contracts/runtime.py").read_text(encoding="utf-8"))
    start_lines = {
        line
        for cls in runtime.body
        if isinstance(cls, ast.ClassDef) and cls.name == "Runtime"
        for method in cls.body
        if isinstance(method, ast.AsyncFunctionDef) and method.name == "start"
        for stmt in method.body
        for line in range(stmt.lineno, (stmt.end_lineno or stmt.lineno) + 1)
    }
    for filename in sorted(data.measured_files()):
        file = Path(filename)
        path = file.resolve().relative_to(root.resolve()).as_posix()
        lines = function_lines(file.read_text(encoding="utf-8"))
        for line, contexts in data.contexts_by_lineno(filename).items():
            tests = {c for c in contexts if c.startswith("tests/") and "::" in c}
            if line not in lines or not tests:
                continue
            hits[path, line] = tests
            observed.update(c.split("::", 1)[0] for c in tests)
            if path == "src/core_contracts/runtime.py" and line in start_lines:
                runtime_tests.update(tests)
    if not runtime_tests:
        raise ValueError("No tests started Runtime; refusing to replace the area map")
    counts: dict[str, dict[str, set[tuple[str, int]]]] = defaultdict(lambda: defaultdict(set))
    noise = 0
    for (path, line), tests in hits.items():
        if len(tests & runtime_tests) * 2 >= len(runtime_tests):
            noise += 1
            continue
        for name, area in areas.items():
            if matches(path, area["paths"]):
                for test in tests:
                    counts[name][test.split("::", 1)[0]].add((path, line))
    result = {}
    retained = {}
    for name, area in areas.items():
        # Empty-path groups describe an execution environment, not source ownership.
        manual = {test for test in area["tests"] if not area["paths"] or test not in observed}
        measured = {test for test, lines in counts[name].items() if len(lines) >= MIN_LINES}
        result[name] = {"paths": area["paths"], "tests": sorted(manual | measured)}
        if manual:
            retained[name] = sorted(manual)
    return result, {
        "runtime_tests": len(runtime_tests),
        "observed_test_files": len(observed),
        "noise_lines": noise,
        "minimum_function_lines": MIN_LINES,
        "retained_unmeasured_or_environment_tests": retained,
    }


class TestContexts:
    def __init__(self, cov: coverage.Coverage) -> None:
        self.cov = cov

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_protocol(self, item):
        # Include setup and teardown, where async Runtime fixtures do their work.
        self.cov.switch_context(item.nodeid)
        try:
            return (yield)
        finally:
            self.cov.switch_context("")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "tests/areas.json")
    args = parser.parse_args()
    cov = coverage.Coverage(
        data_file=str(ROOT / ".coverage.areas"),
        source=[str(ROOT / p) for p in ("src", "client", "dev", "custom_components")],
        config_file=False,
    )
    cov.set_option("run:core", "ctrace")
    cov.start()
    try:
        status = pytest.main(["-q", str(ROOT / "tests")], plugins=[TestContexts(cov)])
    finally:
        cov.stop()
        cov.save()
    if status != pytest.ExitCode.OK:
        raise SystemExit(status)
    areas = json.loads((ROOT / "tests/areas.json").read_text(encoding="utf-8"))
    result, report = measured_areas(areas, cov.get_data(), ROOT)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    args.output.with_suffix(".measurement.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
