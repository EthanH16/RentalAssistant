#!/usr/bin/env python3
"""Inspect the local rental-agent SQLite database as readable JSON."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3


def _decode(row: sqlite3.Row) -> dict:
    result = dict(row)
    for key in ("fees_json", "concessions_json", "contact_json", "scan_history_json"):
        if key in result:
            try:
                result[key] = json.loads(result[key])
            except (TypeError, json.JSONDecodeError):
                pass
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=Path(__file__).resolve().parents[1] / "data" / "rentalassistant.sqlite3")
    parser.add_argument("--building", help="Filter to one building_id")
    parser.add_argument("--include-inactive", action="store_true")
    parser.add_argument("--history", action="store_true", help="Include unit scan_history_json")
    args = parser.parse_args()

    if not args.db.exists():
        parser.error(f"Database does not exist: {args.db}. Run scan_onni.py first.")

    connection = sqlite3.connect(args.db)
    connection.row_factory = sqlite3.Row
    building_query = "SELECT * FROM buildings"
    params: list[str] = []
    if args.building:
        building_query += " WHERE building_id = ?"
        params.append(args.building)
    building_query += " ORDER BY canonical_name"
    buildings = [_decode(row) for row in connection.execute(building_query, params)]

    inventory_query = "SELECT * FROM inventory WHERE 1=1"
    inventory_params: list[str] = []
    if args.building:
        inventory_query += " AND building_id = ?"
        inventory_params.append(args.building)
    if not args.include_inactive:
        inventory_query += " AND status != 'inactive'"
    inventory_query += " ORDER BY building_id, min_price IS NULL, min_price, unit_id"
    inventory = [_decode(row) for row in connection.execute(inventory_query, inventory_params)]
    if not args.history:
        for row in inventory:
            row.pop("scan_history_json", None)

    print(json.dumps({"database": str(args.db), "buildings": buildings, "inventory": inventory}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
