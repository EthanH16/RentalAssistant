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


def _print_table(title: str, rows: list[dict], columns: list[tuple[str, str]]) -> None:
    print(f"\n{title}")
    if not rows:
        print("(none)")
        return
    values = [[str(row.get(key) if row.get(key) is not None else "") for key, _ in columns] for row in rows]
    widths = [max(len(label), *(len(value[index]) for value in values)) for index, (_, label) in enumerate(columns)]
    header = "  ".join(label.ljust(widths[index]) for index, (_, label) in enumerate(columns))
    divider = "  ".join("-" * width for width in widths)
    print(header)
    print(divider)
    for value in values:
        print("  ".join(value[index].ljust(widths[index]) for index in range(len(columns))))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=Path(__file__).resolve().parents[1] / "data" / "rentalassistant.sqlite3")
    parser.add_argument("--building", help="Filter to one building_id")
    parser.add_argument("--include-inactive", action="store_true")
    parser.add_argument("--history", action="store_true", help="Include unit scan_history_json")
    parser.add_argument("--format", choices=["table", "json"], default="table")
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

    payload = {"database": str(args.db), "buildings": buildings, "inventory": inventory}
    if args.format == "json":
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    print(f"Database: {args.db}")
    _print_table(
        "Buildings",
        buildings,
        [("building_id", "BUILDING"), ("canonical_name", "NAME"), ("provider", "PROVIDER"), ("updated_at", "UPDATED")],
    )
    _print_table(
        "Current inventory",
        inventory,
        [
            ("building_id", "BUILDING"),
            ("unit_id", "UNIT"),
            ("floor_plan", "PLAN"),
            ("beds", "BED"),
            ("sqft", "SQFT"),
            ("min_price", "MIN"),
            ("max_price", "MAX"),
            ("status", "STATUS"),
            ("available_date", "AVAILABLE"),
        ],
    )
    if args.history:
        print("\nScan history")
        for row in inventory:
            history = row.get("scan_history_json") or []
            if history:
                print(f"{row['building_id']} / {row['unit_id']}: {json.dumps(history, ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
