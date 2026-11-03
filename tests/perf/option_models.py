"""Small synthetic option probes; no live services or private data.

Run with: uv run python -m tests.perf.option_models
These probes measure isolated costs, not end-to-end speedups or safety.
"""

import copy
import json
import platform
from concurrent.futures import ProcessPoolExecutor
from statistics import median
from time import perf_counter

from core_contracts.model import checksum
from core_contracts.registry import RegistryConfig
from tests.perf.benchmark import synthetic_config


def parse_many(document, count):
    for _ in range(count):
        RegistryConfig.model_validate(document)


def timed(fn):
    started = perf_counter()
    fn()
    return (perf_counter() - started) * 1000


def main():
    document = synthetic_config()
    state = {
        "catalogs": [
            {str(i): {"value": i, "master": i // 2} for i in range(1600)} for _ in range(7)
        ],
        "config": document,
    }
    results = {}
    for name, action in (
        ("digest", lambda: checksum(document)),
        ("full_snapshot_copy", lambda: copy.deepcopy(state)),
        ("one_catalog_copy", lambda: copy.deepcopy(state["catalogs"][0])),
        ("ten_separate_copies", lambda: [copy.deepcopy(state) for _ in range(10)]),
        ("one_group_copy", lambda: copy.deepcopy(state)),
    ):
        results[name] = median(timed(action) for _ in range(3))
    with ProcessPoolExecutor(max_workers=2) as pool:
        # Warm process creation separately. Serialization remains in the timed work.
        pool.submit(parse_many, document, 1).result()
        serial = []
        parallel = []
        for _ in range(3):
            serial.append(timed(lambda: parse_many(document, 40)))

            def run_parallel():
                jobs = [pool.submit(parse_many, document, 20) for _ in range(2)]
                for job in jobs:
                    job.result()

            parallel.append(timed(run_parallel))
        results["parse_40_serial_ms"] = median(serial)
        results["parse_40_two_processes_ms"] = median(parallel)
    groups = {}
    for contract in document["contracts"]:
        source = contract["inputs"][0]["ref"]
        groups[source] = groups.get(source, 0) + 1
    # This graph models only source -> contract edges. It is not a proof of
    # independence: catalogs, timers and publication are deliberately absent.
    graph_model = {"components": len(groups), "largest_contract_group": max(groups.values())}
    phase_count = 10000
    results["progress_10000_timestamps_ms"] = median(
        timed(lambda: [perf_counter() for _ in range(phase_count)]) for _ in range(3)
    )
    print(
        json.dumps(
            {
                "synthetic_only": True,
                "python": platform.python_version(),
                "isolated_costs_ms": results,
                "partial_graph_model": graph_model,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
