"""Coverage selection uses function bodies, real test contexts and the decided thresholds."""

import json
import subprocess
import sys

import coverage
import pytest

from dev.area_map import ROOT, LineCoverage, function_lines, measured_areas, measurement_files


def test_native_monitoring_records_exactly_the_ctracer_context_line_sets(tmp_path):
    file = tmp_path / "example.py"
    file.write_text(
        "def choose(flag):\n    for i in range(100):\n        if flag:\n            seen = True\n        else:\n            seen = False\n    return seen\n"
    )
    namespace = {}
    exec(compile(file.read_text(), str(file), "exec"), namespace)
    reference = coverage.Coverage(data_file=None, include=[str(file)], config_file=False)
    reference.set_option("run:core", "ctrace")
    native = LineCoverage([str(file)])
    for recorder in (reference, native):
        recorder.start()
        try:
            for name, flag in (("first", True), ("second", False), ("third", True)):
                recorder.switch_context(f"tests/test_example.py::test_{name}")
                namespace["choose"](flag)
            recorder.switch_context("")
        finally:
            recorder.stop()
    actual = native.get_data().contexts_by_lineno(str(file))
    expected = reference.get_data().contexts_by_lineno(str(file))
    assert {line: set(contexts) for line, contexts in actual.items()} == {
        line: set(contexts) for line, contexts in expected.items()
    }
    assert set(actual[4]) == {
        "tests/test_example.py::test_first",
        "tests/test_example.py::test_third",
    }
    assert actual[6] == ["tests/test_example.py::test_second"]


def test_measurement_includes_all_area_python_files_and_runtime_only(tmp_path):
    for name in ("feature.py", "nested/other.py", "metadata.json"):
        file = tmp_path / "src/feature" / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("")
    areas = {"feature": {"paths": ["src/feature/", "src/single.py"], "tests": []}}
    assert measurement_files(tmp_path, areas) == sorted(
        str((tmp_path / p).resolve())
        for p in (
            "src/core_contracts/runtime.py",
            "src/single.py",
            "src/feature/feature.py",
            "src/feature/nested/other.py",
        )
    )


def test_function_lines_exclude_imports_class_attributes_and_function_headers():
    assert function_lines(
        "import os\nCONSTANT = 1\nclass Example:\n    attribute = 2\n"
        "    def value(self):\n        return 3\n"
        "async def other():\n    return 4\n"
    ) == {6, 8}


def test_mapping_removes_half_runtime_noise_and_aggregates_five_lines_per_file(tmp_path):
    source = tmp_path / "src/core_contracts"
    source.mkdir(parents=True)
    runtime = source / "runtime.py"
    runtime.write_text("class Runtime:\n    async def start(self):\n        return None\n")
    module = source / "feature.py"
    module.write_text("IMPORT_ONLY = 1\ndef feature():\n" + "    value = 1\n" * 10)
    data = coverage.CoverageData(no_disk=True)
    for number in range(8):
        data.set_context(f"tests/test_runtime_{number}.py::test_start")
        data.add_lines({str(runtime): {3}, str(module): {3} if number < 4 else set()})
    for case, lines in (("first", {1, 2, 4, 5, 6}), ("second", {7, 8})):
        data.set_context(f"tests/test_feature.py::test_{case}")
        data.add_lines({str(module): lines})
    data.set_context("tests/test_four.py::test_four")
    data.add_lines({str(module): {9, 10, 11, 12}})
    data.set_context("")  # Collection/import noise never contributes.
    data.add_lines({str(module): {4, 5, 6, 7, 8, 9, 10, 11, 12}})
    areas = {
        "feature": {
            "paths": ["src/core_contracts/feature.py"],
            "tests": ["tests/test_four.py", "tests/test_external.py"],
        },
        "load": {"paths": [], "tests": ["tests/test_four.py"]},
    }
    result, report = measured_areas(areas, data, tmp_path)
    assert result["feature"]["tests"] == ["tests/test_external.py", "tests/test_feature.py"]
    assert result["load"] == areas["load"]
    assert report["runtime_tests"] == 8 and report["noise_lines"] == 2
    assert report["minimum_function_lines"] == 5
    assert report["retained_unmeasured_or_environment_tests"]["feature"] == [
        "tests/test_external.py"
    ]


def test_empty_runtime_measurement_cannot_replace_map(tmp_path):
    (tmp_path / "src/core_contracts").mkdir(parents=True)
    (tmp_path / "src/core_contracts/runtime.py").write_text("class Runtime: pass\n")
    with pytest.raises(ValueError, match="No tests started Runtime"):
        measured_areas({}, coverage.CoverageData(no_disk=True), tmp_path)


@pytest.mark.parametrize("backend", ["ctrace", "native"])
def test_real_pytest_context_includes_async_fixture_setup_and_teardown(tmp_path, backend):
    (tmp_path / "test_context.py").write_text(
        "import pytest\n"
        "@pytest.fixture\n"
        "async def resource():\n"
        "    setup = 1\n"
        "    yield setup\n"
        "    teardown = 2\n"
        "async def test_resource(resource):\n"
        "    assert resource == 1\n"
    )
    constructor = (
        f"coverage.Coverage(data_file=None,source=[{str(tmp_path)!r}],config_file=False)"
        if backend == "ctrace"
        else f"LineCoverage([{str(tmp_path / 'test_context.py')!r}])"
    )
    configure = "c.set_option('run:core','ctrace'); " if backend == "ctrace" else ""
    script = (
        "import coverage,pytest,json; from dev.area_map import TestContexts,LineCoverage; "
        f"c={constructor}; " + configure + "c.start(); "
        f"code=pytest.main(['-q','--asyncio-mode=auto',{str(tmp_path / 'test_context.py')!r}],"
        "plugins=[TestContexts(c)]); c.stop(); assert code==0; "
        f"print('CONTEXTS='+json.dumps(c.get_data().contexts_by_lineno({str(tmp_path / 'test_context.py')!r})))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script], cwd=ROOT, text=True, capture_output=True, check=True
    )
    contexts = json.loads(completed.stdout.split("CONTEXTS=", 1)[1])
    assert contexts["4"] == contexts["6"] == contexts["8"]
    assert any("test_context.py::test_resource" in c for c in contexts["4"])
