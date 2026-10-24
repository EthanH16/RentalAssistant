#!/usr/bin/env python3
"""Fetch one Related building and upsert its public inventory into SQLite."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from adapters.related import BUILDINGS, RelatedAdapter  # noqa: E402
from storage.sqlite import InventoryDatabase  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("building", choices=sorted(BUILDINGS))
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "rentalassistant.sqlite3")
    args = parser.parse_args()
    building = BUILDINGS[args.building]
    result = RelatedAdapter().fetch(args.building)
    summary = InventoryDatabase(args.db).upsert_scan(
        result,
        official_url=building.detail_url,
        contact=result.get("contact"),
    )
    print(json.dumps({"database": str(args.db), "scan": summary.__dict__}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
