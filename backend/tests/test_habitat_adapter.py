import json
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from adapters.greystar.habitat import parse_habitat_floorplans_html  # noqa: E402


class HabitatAdapterTests(unittest.TestCase):
    def test_parses_units_and_jsonld_concession(self):
        payload = {
            "units": [{
                "id": 1,
                "id_value": "12170552",
                "apartment_number": "0326",
                "floorplan_title": "AX",
                "bedrooms": "Studio",
                "bathrooms": "1",
                "square_feet": "507",
                "rent_min": "3426.21",
                "rent_max": "3426.21",
                "price_term_origin": "13",
                "available_date": "1778821200",
                "available_display": "Available Now",
                "permalink": "/floorplans/unit-cdab05d5c0267c6c0b366293fcc8e777/",
                "price_itemized": {"items": []},
                "lease_terms": [{
                    "term": "13",
                    "priceLow": 3417,
                    "priceHigh": 3417,
                    "priceDisplay": "$3,417 /mo*",
                    "termOptionDisplay": "13 Months ($3,417 Base Rent)",
                    "pricingReflectFees": True,
                    "date": "2026-10-24",
                }],
            }],
            "floorplans": [{"id": 1}],
        }
        page = (
            '<script id="jd-fp-data-script-app" type="application/json">'
            + json.dumps(payload)
            + '</script>'
            '<script type="application/ld+json">'
            + json.dumps({"jonah:announcements": [{"jonah:name": "UP TO 10 WEEKS FREE"}]})
            + '</script>'
            '<script id="engrain-expense-embed">'
            "JonahWidget.engrainExpenseEmbed({import: 'https://greystar.expenseguide.com/js/api-v1.min.js', assetId: '36317'})"
            '</script>'
        )
        result = parse_habitat_floorplans_html(page)
        self.assertEqual(result["building_id"], "habitat")
        self.assertEqual(result["units"][0]["unit_id"], "0326")
        self.assertEqual(result["units"][0]["base_rent_min"], 3417.0)
        self.assertTrue(any("10 WEEKS FREE" in item for item in result["policies"]["concessions"]))
        self.assertEqual(result["policies"]["fee_guide"]["asset_id"], "36317")


if __name__ == "__main__":
    unittest.main()
