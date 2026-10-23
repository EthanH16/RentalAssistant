import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT))

from storage.sqlite import InventoryDatabase  # noqa: E402


class InventoryDatabaseTests(unittest.TestCase):
    def test_upsert_updates_current_state_and_keeps_history(self):
        with tempfile.TemporaryDirectory() as directory:
            database = InventoryDatabase(Path(directory) / "inventory.sqlite3")
            first = {
                "provider": "onni",
                "building_id": "825-south-hill",
                "building_name": "825 South Hill",
                "source_url": "https://825southhilldtla.com/availability",
                "checked_at": "2026-10-23T10:00:00+00:00",
                "units": [
                    {
                        "unit_id": "0522",
                        "floor_plan": "D",
                        "beds": 2,
                        "baths": None,
                        "sqft": 1030,
                        "base_rent_min": 4060,
                        "base_rent_max": 5246,
                        "available_date": None,
                        "available_now": True,
                        "application_url": "https://example.test/app/0522",
                    }
                ],
                "policies": {"fees": [{"label": "Application Fee", "amount": "$65.86"}], "concessions": []},
            }
            first_scan = database.upsert_scan(first)
            self.assertEqual((first_scan.units_added, first_scan.units_changed), (1, 0))

            second = {**first, "checked_at": "2026-10-24T10:00:00+00:00", "units": [{**first["units"][0], "base_rent_min": 4090}]}
            second_scan = database.upsert_scan(second)
            self.assertEqual((second_scan.units_added, second_scan.units_changed), (0, 1))
            row = database.list_inventory("825-south-hill")[0]
            self.assertEqual(row["min_price"], 4090)
            self.assertEqual(json.loads(row["scan_history_json"])[0]["state"]["min_price"], 4060)

    def test_successful_scan_marks_missing_units_inactive(self):
        with tempfile.TemporaryDirectory() as directory:
            database = InventoryDatabase(Path(directory) / "inventory.sqlite3")
            base = {
                "provider": "onni",
                "building_id": "hope-flower",
                "building_name": "Hope + Flower",
                "units": [
                    {"unit_id": "A", "base_rent_min": 2000, "available_now": True},
                    {"unit_id": "B", "base_rent_min": 2100, "available_now": True},
                ],
                "policies": {},
            }
            database.upsert_scan(base)
            database.upsert_scan({**base, "units": [base["units"][0]]})
            self.assertEqual(len(database.list_inventory("hope-flower")), 1)
            all_rows = database.list_inventory("hope-flower", include_inactive=True)
            self.assertEqual({row["status"] for row in all_rows}, {"available", "inactive"})


if __name__ == "__main__":
    unittest.main()
