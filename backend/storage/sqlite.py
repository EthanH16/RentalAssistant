"""Small two-table SQLite store for current inventory and scan history.

The adapter layer remains provider-specific. This module only accepts the
shared normalized result shape, so adding another provider does not require a
schema change.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any


SCHEMA = """
CREATE TABLE IF NOT EXISTS buildings (
    building_id TEXT PRIMARY KEY,
    canonical_name TEXT NOT NULL,
    provider TEXT NOT NULL,
    official_url TEXT,
    fees_json TEXT NOT NULL DEFAULT '{}',
    concessions_json TEXT NOT NULL DEFAULT '[]',
    contact_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS inventory (
    building_id TEXT NOT NULL,
    unit_id TEXT NOT NULL,
    floor_plan TEXT,
    beds INTEGER,
    baths REAL,
    sqft INTEGER,
    min_price REAL,
    max_price REAL,
    available_date TEXT,
    status TEXT NOT NULL,
    application_url TEXT,
    last_seen_at TEXT NOT NULL,
    scan_history_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (building_id, unit_id),
    FOREIGN KEY (building_id) REFERENCES buildings(building_id)
);

CREATE INDEX IF NOT EXISTS idx_inventory_building_status
    ON inventory(building_id, status);
CREATE INDEX IF NOT EXISTS idx_inventory_floor_plan
    ON inventory(floor_plan);
CREATE INDEX IF NOT EXISTS idx_inventory_beds_price
    ON inventory(beds, min_price);
CREATE INDEX IF NOT EXISTS idx_inventory_available_date
    ON inventory(available_date);
"""


@dataclass(frozen=True)
class ScanResult:
    building_id: str
    units_seen: int
    units_added: int
    units_changed: int
    units_marked_inactive: int


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _state(unit: dict[str, Any], status: str) -> dict[str, Any]:
    return {
        "floor_plan": unit.get("floor_plan"),
        "beds": unit.get("beds"),
        "baths": unit.get("baths"),
        "sqft": unit.get("sqft"),
        "min_price": unit.get("base_rent_min"),
        "max_price": unit.get("base_rent_max"),
        "available_date": unit.get("available_date"),
        "status": status,
        "application_url": unit.get("application_url"),
        "pricing_status": unit.get("pricing_status"),
    }


def _status(unit: dict[str, Any]) -> str:
    if unit.get("pricing_status") == "call_for_pricing" or unit.get("base_rent_min") is None:
        return "needs_review"
    if unit.get("available_now"):
        return "available"
    if unit.get("available_date"):
        return "future"
    return "needs_review"


class InventoryDatabase:
    """SQLite repository implementing the accepted two-table MVP."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)

    def upsert_scan(
        self,
        result: dict[str, Any],
        *,
        official_url: str | None = None,
        contact: dict[str, Any] | None = None,
    ) -> ScanResult:
        """Upsert one validated adapter result in a single transaction.

        Missing units are marked inactive only after the result has passed the
        adapter's validation and the transaction has started. They are never
        deleted, preserving reappearance and price history.
        """

        self.initialize()
        building_id = result["building_id"]
        now = result.get("checked_at") or _now()
        policies = result.get("policies") or {}
        units = result.get("units") or []
        seen: set[str] = set()
        added = changed = 0

        with self.connect() as connection:
            connection.execute(
                """INSERT INTO buildings
                   (building_id, canonical_name, provider, official_url,
                    fees_json, concessions_json, contact_json, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(building_id) DO UPDATE SET
                    canonical_name=excluded.canonical_name,
                    provider=excluded.provider,
                    official_url=COALESCE(excluded.official_url, buildings.official_url),
                    fees_json=excluded.fees_json,
                    concessions_json=excluded.concessions_json,
                    contact_json=CASE WHEN excluded.contact_json = '{}' THEN buildings.contact_json
                                      ELSE excluded.contact_json END,
                    updated_at=excluded.updated_at""",
                (
                    building_id,
                    result.get("building_name") or building_id,
                    result.get("provider") or "unknown",
                    official_url or result.get("source_url"),
                    _dump(policies.get("fees") or []),
                    _dump(policies.get("concessions") or result.get("concessions") or []),
                    _dump(contact or {}),
                    now,
                    now,
                ),
            )

            for unit in units:
                unit_id = str(unit.get("unit_id") or "").strip()
                if not unit_id:
                    raise ValueError("Validated inventory unit is missing unit_id")
                seen.add(unit_id)
                current_status = _status(unit)
                current_state = _state(unit, current_status)
                existing = connection.execute(
                    "SELECT * FROM inventory WHERE building_id = ? AND unit_id = ?",
                    (building_id, unit_id),
                ).fetchone()
                if existing is None:
                    history = []
                    added += 1
                    created_at = now
                else:
                    previous_state = {
                        key: existing[column]
                        for key, column in (
                            ("floor_plan", "floor_plan"),
                            ("beds", "beds"),
                            ("baths", "baths"),
                            ("sqft", "sqft"),
                            ("min_price", "min_price"),
                            ("max_price", "max_price"),
                            ("available_date", "available_date"),
                            ("status", "status"),
                            ("application_url", "application_url"),
                        )
                    }
                    history = json.loads(existing["scan_history_json"] or "[]")
                    if previous_state != current_state:
                        history.append({"observed_at": now, "state": previous_state})
                        changed += 1
                    created_at = existing["created_at"]

                connection.execute(
                    """INSERT INTO inventory
                       (building_id, unit_id, floor_plan, beds, baths, sqft,
                        min_price, max_price, available_date, status,
                        application_url, last_seen_at, scan_history_json,
                        created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(building_id, unit_id) DO UPDATE SET
                        floor_plan=excluded.floor_plan,
                        beds=excluded.beds,
                        baths=excluded.baths,
                        sqft=excluded.sqft,
                        min_price=excluded.min_price,
                        max_price=excluded.max_price,
                        available_date=excluded.available_date,
                        status=excluded.status,
                        application_url=excluded.application_url,
                        last_seen_at=excluded.last_seen_at,
                        scan_history_json=excluded.scan_history_json,
                        updated_at=excluded.updated_at""",
                    (
                        building_id,
                        unit_id,
                        unit.get("floor_plan"),
                        unit.get("beds"),
                        unit.get("baths"),
                        unit.get("sqft"),
                        unit.get("base_rent_min"),
                        unit.get("base_rent_max"),
                        unit.get("available_date"),
                        current_status,
                        unit.get("application_url"),
                        now,
                        _dump(history),
                        created_at,
                        now,
                    ),
                )

            inactive = 0
            existing_rows = connection.execute(
                "SELECT unit_id, status, scan_history_json, created_at FROM inventory WHERE building_id = ?",
                (building_id,),
            ).fetchall()
            for existing in existing_rows:
                if existing["unit_id"] in seen or existing["status"] == "inactive":
                    continue
                history = json.loads(existing["scan_history_json"] or "[]")
                history.append(
                    {
                        "observed_at": now,
                        "state": {"status": existing["status"]},
                        "event": "not_seen_in_successful_scan",
                    }
                )
                connection.execute(
                    """UPDATE inventory
                       SET status='inactive', scan_history_json=?, updated_at=?
                       WHERE building_id=? AND unit_id=?""",
                    (_dump(history), now, building_id, existing["unit_id"]),
                )
                inactive += 1

        return ScanResult(building_id, len(units), added, changed, inactive)

    def list_inventory(self, building_id: str, *, include_inactive: bool = False) -> list[dict[str, Any]]:
        query = "SELECT * FROM inventory WHERE building_id = ?"
        params: list[Any] = [building_id]
        if not include_inactive:
            query += " AND status != 'inactive'"
        query += " ORDER BY min_price IS NULL, min_price, unit_id"
        with self.connect() as connection:
            return [dict(row) for row in connection.execute(query, params).fetchall()]
