#!/usr/bin/env python3
"""Fetch Park Fifth and upsert its normalized inventory into SQLite."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from adapters.bozzuto import ParkFifthAdapter  # noqa: E402
from storage.sqlite import InventoryDatabase  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "rentalassistant.sqlite3")
    args = parser.parse_args()
    result = ParkFifthAdapter().fetch()
    summary = InventoryDatabase(args.db).upsert_scan(
        result,
        official_url="https://www.parkfifthtower.com/floor-plans/",
        contact={"leasing": "parkfifth@bozzutoliving.com", "phone": "877-540-0401"},
    )
    print(json.dumps({"database": str(args.db), "scan": summary.__dict__}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
