import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from adapters.greystar.eden import (  # noqa: E402
    EdenAdapter,
    parse_floorplans_html,
    parse_quote_html,
    parse_unit_detail_html,
)


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

    def test_builds_official_renderable_detail_url(self):
        url = EdenAdapter._detail_url("unit-slug")
        self.assertIn("_fp-renderable/params%3A", url)
        self.assertIn("id%3Dunit-slug", url)

    def test_parses_unit_detail_lease_link(self):
        unit = {
            "apartment_number": "#330",
            "id": 123,
            "id_value": "456",
            "floorplan_title": "A1",
            "bedrooms": "1",
            "bathrooms": "1",
            "square_feet": 700,
            "lease_link_data": {
                "lease_link": "https://secure.example/ole?MoveInDate={date}&sLeaseTerm={term}"
            },
            "lease_terms": [{"term": "12", "priceLow": 2500, "priceHigh": 2500}],
        }
        html = '<script data-jd-fp-selector="unit-data" type="application/json">' + json.dumps(unit) + "</script>"
        result = parse_unit_detail_html(html, "https://edendtla.com/detail")
        self.assertEqual(result["unit_id"], "330")
        self.assertIn("{date}", result["lease_link_data"]["lease_link"])

    def test_parses_selected_eden_quote_and_term_quotes(self):
        html = '''
        <div id="LeaseInfoSummary"><table><tr><td>Move-in Date</td><td>11/4/2026</td></tr>
        <tr><td>Lease Term</td><td>12 months</td></tr><tr><td>Rent</td><td>$2,500.00</td></tr>
        <tr><td>Cable</td><td>$95.00</td></tr><tr><td>SCEP Inspection Fee</td><td>$2.83</td></tr></table></div>
        <input id="UnitId" value="456"><input id="FloorplanId" value="789">
        <input id="dtMIDateSel" value="11/4/2026"><input id="sLeaseTerm" value="12">
        <input id="MonthlyOtherRent" value="97.83"><input id="TotalRent" value="2597.83">
        <script>var PricingData = {
          "11/4/2026": {
            "12": {"price": 2500.0, "Tax": 0.0, "EMCBasePrice": 2500.0, "SpecialAmount": 0.0},
            "18": {"price": 2300.0, "Tax": 0.0, "EMCBasePrice": 2300.0, "SpecialAmount": 0.0}
          }
        };</script>
        '''
        result = parse_quote_html(html, "https://secure.example/quote", "2026-11-04", 12)
        self.assertEqual(result["move_in_date"], "2026-11-04")
        self.assertEqual(result["base_rent"], 2500.0)
        self.assertEqual(result["total_monthly_price"], 2597.83)
        self.assertEqual(len(result["term_quotes"]), 2)
        self.assertEqual(result["fee_rows"][2]["label"], "Rent")


if __name__ == "__main__":
    unittest.main()
