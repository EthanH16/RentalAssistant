#!/usr/bin/env python3
"""Scan The Eden and upsert its current inventory into SQLite."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from adapters.greystar import EdenAdapter  # noqa: E402
from adapters.greystar.eden import parse_floorplans_html  # noqa: E402
from storage.sqlite import InventoryDatabase  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "rentalassistant.sqlite3")
    parser.add_argument("--fixture", type=Path)
    args = parser.parse_args()
    result = parse_floorplans_html(args.fixture.read_text(encoding="utf-8")) if args.fixture else EdenAdapter().fetch()
    summary = InventoryDatabase(args.db).upsert_scan(result, official_url=result["source_url"], contact=result["contact"])
    print({
        "database": str(args.db),
        "building_id": summary.building_id,
        "units_seen": summary.units_seen,
        "units_added": summary.units_added,
        "units_changed": summary.units_changed,
        "units_marked_inactive": summary.units_marked_inactive,
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
