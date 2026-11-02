"""Generate b22's address-free English interface bundle; never modifies released v1 files."""

import json
from pathlib import Path

from core_contracts.interface_upgrade import upgrade_catalog, upgrade_definition

ROOT = Path(__file__).parents[1]


def definitions():
    latest = {}
    for path in sorted((ROOT / "examples").glob("*-templates-v*.json")):
        for row in json.loads(path.read_text(encoding="utf-8")):
            old = latest.get(row["type_id"])
            if old is None or old["type_version"] < row["type_version"]:
                latest[row["type_id"]] = row
    # day.context is deliberately replaced by the date-specific day.context_at.
    return [
        new
        for key, row in sorted(latest.items())
        if key != "day.context" and (new := upgrade_definition(row)) != row
    ]


if __name__ == "__main__":
    for name, rows in (
        ("interface-templates-b22.json", definitions()),
        (
            "interface-catalogs-b22.json",
            [
                upgrade_catalog(row)
                for row in json.loads(
                    (ROOT / "examples/media-catalogs-v1.json").read_text(encoding="utf-8")
                )
            ],
        ),
    ):
        (ROOT / "examples" / name).write_text(
            json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
