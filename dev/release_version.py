"""Separate a tested candidate from the version offered by the Supervisor store."""

import json
import re


def candidate(root, store_version):
    marker = root / "wuschel/PENDING_RELEASE.json"
    if not marker.exists():
        return store_version
    data = json.loads(marker.read_text())
    assert set(data) == {"version", "previous_version"}, "Invalid release marker"
    assert data["previous_version"] == store_version, "Store version drift"
    assert re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", data["version"]), (
        "Invalid candidate version"
    )
    assert data["version"] != store_version, "Candidate must differ from store"
    return data["version"]
