"""Print pytest arguments for a named area; unlisted tests remain in full CI."""

import argparse
import json
import shlex
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    areas = json.loads((ROOT / "tests/areas.json").read_text(encoding="utf-8"))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("area", choices=areas)
    args = parser.parse_args()
    print(shlex.join(areas[args.area]))


if __name__ == "__main__":
    main()
