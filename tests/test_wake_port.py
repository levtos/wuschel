"""Compare the real port against independently generated pinned-original results."""

import gzip
import json
from datetime import date
from pathlib import Path

from core_contracts.wake import wake_config, wake_planning

from .wake_reference_cases import VARIANTS, scenario


def test_three_years_have_zero_original_port_differences():
    path = Path(__file__).parent / "fixtures/wake-original-2032-2034.json.gz"
    fixture = json.loads(gzip.decompress(path.read_bytes()))
    assert fixture["source_commit"] == "c9408fa33f2914b1c5f7316738cb38b43add33b5"
    assert len(fixture["rows"]) == 1096 * len(VARIANTS)
    for day, variant, expected in fixture["rows"]:
        inputs = scenario(wake_planning, wake_config, date.fromisoformat(day), variant)
        actual = wake_planning.plan_wake(inputs).as_attributes()
        assert actual == expected, (day, variant, actual, expected)
    assert wake_config.default_config().to_dict() == fixture["default_config"]
