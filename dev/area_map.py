"""Measure function-line coverage per test and regenerate tests/areas.json.

Run the full measurement only in the manually dispatched PostgreSQL CI job.
"""

import argparse
import ast
import json
import os
import sys
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


def measurement_files(root: Path, areas: dict) -> list[str]:
    # Unmapped kernel lines cannot belong to an area. Avoid tracing their hot
    # loops; Runtime.start is the only extra source needed for the denominator.
    files = {root / "src/core_contracts/runtime.py"}
    for area in areas.values():
        for prefix in area["paths"]:
            path = root / prefix
            if prefix.endswith("/"):
                files.update(path.rglob("*.py"))
            elif path.suffix == ".py":
                files.add(path)
    return sorted(str(path.resolve()) for path in files)


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
        "collector": "sys.monitoring LINE per test (CoverageData contexts)",
        "runtime_tests": len(runtime_tests),
        "observed_test_files": len(observed),
        "noise_lines": noise,
        "minimum_function_lines": MIN_LINES,
        "retained_unmeasured_or_environment_tests": retained,
    }


class TestContexts:
    def __init__(self, cov) -> None:
        self.cov = cov

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_protocol(self, item):
        # Include setup and teardown, where async Runtime fixtures do their work.
        self.cov.switch_context(item.nodeid)
        try:
            return (yield)
        finally:
            self.cov.switch_context("")


class LineCoverage:
    """Record each executed line once per test using Python's native monitoring.

    C tracing adds work on every loop iteration and distorts the unchanged
    latency gates. LINE/DISABLE records the same sets with bounded overhead.
    """

    def __init__(self, files: list[str], data_file: str | None = None) -> None:
        self.files = {os.path.normcase(path): path for path in files}
        self.paths: dict[str, str | None] = {}
        self.data = coverage.CoverageData(basename=data_file, no_disk=data_file is None)
        self.context = ""
        self.lines: dict[str, set[int]] = defaultdict(set)
        self.tool = next(
            (i for i in (1, 5, 4, 3, 2, 0) if sys.monitoring.get_tool(i) is None), None
        )
        if self.tool is None:
            raise RuntimeError("No free Python monitoring tool ID")

    def line(self, code, line):
        filename = code.co_filename
        if filename not in self.paths:
            self.paths[filename] = self.files.get(os.path.normcase(filename))
        if self.context and (path := self.paths[filename]) is not None:
            self.lines[path].add(line)
        return sys.monitoring.DISABLE

    def start(self) -> None:
        sys.monitoring.use_tool_id(self.tool, "wuschel-area-map")
        sys.monitoring.register_callback(self.tool, sys.monitoring.events.LINE, self.line)
        sys.monitoring.set_events(self.tool, sys.monitoring.events.LINE)

    def switch_context(self, context: str) -> None:
        if self.context:
            self.data.set_context(self.context)
            self.data.add_lines(dict(self.lines))
        self.lines.clear()
        self.context = context
        # A line executed in an earlier test must be recorded again for this one.
        sys.monitoring.restart_events()

    def stop(self) -> None:
        sys.monitoring.set_events(self.tool, 0)
        sys.monitoring.register_callback(self.tool, sys.monitoring.events.LINE, None)
        sys.monitoring.free_tool_id(self.tool)

    def save(self) -> None:
        self.data.write()

    def get_data(self) -> coverage.CoverageData:
        return self.data


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "tests/areas.json")
    args = parser.parse_args()
    areas = json.loads((ROOT / "tests/areas.json").read_text(encoding="utf-8"))
    cov = LineCoverage(measurement_files(ROOT, areas), str(ROOT / ".coverage.areas"))
    cov.start()
    try:
        status = pytest.main(["-q", str(ROOT / "tests")], plugins=[TestContexts(cov)])
    finally:
        cov.stop()
        cov.save()
    if status != pytest.ExitCode.OK:
        raise SystemExit(status)
    result, report = measured_areas(areas, cov.get_data(), ROOT)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    args.output.with_suffix(".measurement.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
