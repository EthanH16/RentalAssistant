import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from adapters.greystar.eden import parse_floorplans_html  # noqa: E402


class EdenAdapterTests(unittest.TestCase):
    def test_parses_embedded_unit_inventory_and_term_quotes(self):
        payload = {
            "config": {"settings": {"lease_url": "https://apply.example.test/"}},
            "floorplans": [{"title": "S1"}],
            "units": [{
                "id": "unit-abc", "id_value": "12345", "apartment_number": "#619",
                "floorplan_title": "B5", "bedrooms": "2", "bathrooms": "2",
                "square_feet": "1091", "rent_min": "3816.48", "rent_max": "3816.48",
                "available_display": "Available Now", "available_date": "1787806800",
                "price_term_origin": "18", "permalink": "/floorplans/unit-abc/",
                "lease_terms": [
                    {"term": "12", "priceLow": 4284, "priceHigh": 4284,
                     "termOptionDisplay": "12 Months ($4,284 Base Rent)"},
                    {"term": "18", "priceLow": 3805, "priceHigh": 3805,
                     "termOptionDisplay": "18 Months ($3,805 Base Rent)"},
                ],
            }],
        }
        result = parse_floorplans_html(
            '<p>We welcome both cats and dogs. With up to 2 pets allowed per residence, $50 per month pet rent and a $500 deposit per pet.</p>'
            '<script id="jd-fp-data-script-app" type="application/json">'
            + json.dumps(payload) + "</script>"
        )
        unit = result["units"][0]
        self.assertEqual(result["building_id"], "the-eden")
        self.assertEqual(unit["unit_id"], "619")
        self.assertEqual(unit["floor_plan"], "B5")
        self.assertEqual(unit["base_rent_min"], 3805.0)
        self.assertEqual(unit["total_monthly_leasing_price_min"], 3816.48)
        self.assertTrue(unit["available_now"])
        self.assertEqual(unit["application_url"], "https://apply.example.test/")

    def test_fails_closed_without_embedded_payload(self):
        with self.assertRaises(Exception):
            parse_floorplans_html("<html></html>")


if __name__ == "__main__":
    unittest.main()
