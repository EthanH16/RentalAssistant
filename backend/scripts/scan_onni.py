#!/usr/bin/env python3
"""Fetch one Onni building and persist its normalized inventory in SQLite."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from adapters.onni import BUILDINGS, OnniAdapter, OnniAdapterError, parse_fixture  # noqa: E402
from storage.sqlite import InventoryDatabase  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("building", choices=[*BUILDINGS.keys(), "all"])
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "rentalassistant.sqlite3")
    parser.add_argument("--fixture", type=Path)
    args = parser.parse_args()

    scan = InventoryDatabase(args.db)
    scan.initialize()
    adapter = OnniAdapter()
    building_ids = list(BUILDINGS) if args.building == "all" else [args.building]
    if args.fixture and args.building == "all":
        parser.error("--fixture can only be used with one building, not all")

    summaries: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for index, building_id in enumerate(building_ids, start=1):
        print(
            f"[{index}/{len(building_ids)}] Scanning {building_id}...",
            file=sys.stderr,
            flush=True,
        )
        try:
            result = parse_fixture(args.fixture, building_id) if args.fixture else adapter.fetch(building_id)
            summary = scan.upsert_scan(result)
            summaries.append(summary.__dict__)
            print(
                f"[{index}/{len(building_ids)}] Saved {building_id}: "
                f"{summary.units_seen} seen, {summary.units_added} added, "
                f"{summary.units_changed} changed, {summary.units_marked_inactive} inactive",
                file=sys.stderr,
                flush=True,
            )
        except (OnniAdapterError, OSError, ValueError) as exc:
            failures.append({"building_id": building_id, "error": str(exc)})
            print(
                f"[{index}/{len(building_ids)}] Failed {building_id}: {exc}",
                file=sys.stderr,
                flush=True,
            )

    print(json.dumps({"database": str(args.db), "scans": summaries, "failures": failures}, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
