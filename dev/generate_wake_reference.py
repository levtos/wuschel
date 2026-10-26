"""Regenerate synthetic expected results from an independently pinned original checkout."""

import argparse
import gzip
import hashlib
import json
import runpy
import sys
from datetime import date, timedelta
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("original", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    from tests.wake_reference_cases import VARIANTS, scenario

    loader = runpy.run_path(root / "docs/beta/bereich-4/probe_reference.py")["load_original"]
    wp, wc, digests = loader(args.original)
    rows = []
    day = date(2032, 1, 1)
    while day < date(2035, 1, 1):
        for variant in VARIANTS:
            rows.append(
                [
                    day.isoformat(),
                    variant,
                    wp.plan_wake(scenario(wp, wc, day, variant)).as_attributes(),
                ]
            )
        day += timedelta(days=1)
    data = dict(
        source_commit="c9408fa33f2914b1c5f7316738cb38b43add33b5",
        source_sha256=digests,
        default_config=wc.default_config().to_dict(),
        rows=rows,
    )
    raw = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    path = root / "tests/fixtures/wake-original-2032-2034.json.gz"
    path.write_bytes(gzip.compress(raw, mtime=0))
    print(
        json.dumps(
            dict(
                rows=len(rows),
                variants=len(VARIANTS),
                days=1096,
                raw_sha256=hashlib.sha256(raw).hexdigest(),
            )
        )
    )


if __name__ == "__main__":
    main()
