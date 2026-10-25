import json
from pathlib import Path
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from adapters.greystar.eden import (  # noqa: E402
    EdenAdapter,
    parse_floorplans_html,
    parse_fee_guide_payload,
    parse_quote_html,
    parse_unit_detail_html,
)


class EdenAdapterTests(unittest.TestCase):
    def test_normalizes_engrain_fee_widget_payload(self):
        payload = {
            "dateOfIssuance": "2026-10-01",
            "marketingHeadline": "Community Fees",
            "data": [{
                "category_header": "Required",
                "category_rows": [
                    {"label": "Pest Control", "value_type": "amount", "amount": "1.62",
                     "provider_method": "Paid to Property", "frequency": "Monthly",
                     "due_at_timing": "With Rent", "is_required": True},
                    {"label": "Application Fee", "value_type": "text", "text_amount": "See Application",
                     "frequency": "One-time", "is_required": True},
                    {"label": "Income Verification", "value_type": "percentage", "percentage_amount": "2.5",
                     "frequency": "One-time", "is_situational": True},
                ],
            }],
        }
        result = parse_fee_guide_payload(payload, "27664", "https://edendtla.com/floorplans/", "https://example.test/api")
        self.assertEqual(result["status"], "parsed")
        self.assertEqual(result["fees"][0]["amount"], "$1.62")
        self.assertEqual(result["fees"][0]["frequency_due_at"], "Monthly / With Rent")
        self.assertEqual(result["fees"][1]["amount"], "See Application")
        self.assertEqual(result["fees"][2]["amount"], "2.5% of Base Rent")
        self.assertEqual(result["fees"][2]["required"], "Situational")

    def test_fee_widget_uses_the_provider_asset_encoding(self):
        adapter = EdenAdapter()
        guide = {"asset_id": "27664", "source_url": "https://edendtla.com/floorplans/"}
        with mock.patch("adapters.greystar.eden.urlopen") as mocked:
            response = mock.MagicMock()
            response.status = 200
            response.headers.get_content_charset.return_value = "utf-8"
            response.read.return_value = json.dumps({"data": [{"category_header": "Required", "category_rows": [{
                "label": "Pest Control", "value_type": "amount", "amount": "1.62", "frequency": "Monthly",
            }]}]}).encode()
            response.__enter__.return_value = response
            mocked.return_value = response
            result = adapter.fetch_fee_guide(guide)
        request = mocked.call_args.args[0]
        self.assertIn("/assets/3g71zlkwosc/expenses", request.full_url)
        self.assertEqual(result["fees"][0]["label"], "Pest Control")
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

    def test_preserves_building_marketing_concession_text(self):
        payload = {
            "config": {"settings": {}},
            "floorplans": [],
            "units": [{
                "id": "unit-abc", "apartment_number": "#101", "bedrooms": "0",
                "rent_min": "2200", "rent_max": "2200",
                "specials": ["Up to 6 Weeks Free"],
            }],
        }
        html = (
            "<div>Up to 6 Weeks Free plus Free Parking for two months.</div>"
            '<script id="jd-fp-data-script-app" type="application/json">'
            + json.dumps(payload) + "</script>"
        )
        result = parse_floorplans_html(html)
        self.assertIn("Up to 6 Weeks Free", result["policies"]["concessions"])
        self.assertTrue(any("Free Parking for two months" in item for item in result["policies"]["concessions"]))

    def test_records_building_fee_guide_reference_separately(self):
        payload = {
            "config": {"settings": {}},
            "floorplans": [],
            "units": [{"id": "unit-abc", "apartment_number": "#101", "bedrooms": "0", "rent_min": "2200", "rent_max": "2200"}],
        }
        html = (
            "<div id='floorplans-fee-guide'>"
            "<script id='engrain-expense-embed'>"
            "JonahWidget.engrainExpenseEmbed({import: 'https://greystar.expenseguide.com/js/api-v1.min.js', assetId: '27664'})"
            "</script></div>"
            '<script id="jd-fp-data-script-app" type="application/json">'
            + json.dumps(payload) + "</script>"
        )
        result = parse_floorplans_html(html, "https://edendtla.com/floorplans/")
        guide = result["policies"]["fee_guide"]
        self.assertEqual(guide["asset_id"], "27664")
        self.assertEqual(guide["fees"], [])
        self.assertEqual(guide["status"], "widget_reference_only")

    def test_parses_rendered_building_fee_guide_rows(self):
        payload = {
            "config": {"settings": {}},
            "floorplans": [],
            "units": [{"id": "unit-abc", "apartment_number": "#101", "bedrooms": "0", "rent_min": "2200", "rent_max": "2200"}],
        }
        html = (
            "<div id='floorplans-fee-guide'><h5>Easy-to-Use Guide</h5><p>Potential fees.</p>"
            "<table><tr><th>Fee</th><th>Amount</th></tr>"
            "<tr><td>Pest Control</td><td>$1.62 / mo</td></tr>"
            "<tr><td>SCEP Fee</td><td>$2.83 / mo</td></tr></table></div>"
            '<script id="jd-fp-data-script-app" type="application/json">'
            + json.dumps(payload) + "</script>"
        )
        result = parse_floorplans_html(html, "https://edendtla.com/floorplans/")
        guide = result["policies"]["fee_guide"]
        self.assertEqual(guide["status"], "parsed")
        self.assertEqual(guide["fees"][0]["label"], "Pest Control")
        self.assertTrue(any(item.get("label") == "SCEP Fee" for item in result["policies"]["fees"]))

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

    def test_preserves_securecafe_lease_specials(self):
        html = '''
        <div id="LeaseInfoSummary"><table><tr><td>Rent</td><td>$2,500.00</td></tr></table></div>
        <input id="dtMIDateSel" value="11/4/2026"><input id="sLeaseTerm" value="15">
        <input id="MonthlyOtherRent" value="0"><input id="TotalRent" value="2500">
        <script>var PricingData = {
          "11/4/2026": {
            "15": {"price": 2500.0, "Tax": 0.0, "EMCBasePrice": 2500.0,
                    "bSpecial": true, "LeasingSpecials": ["Up to 6 Weeks Free"], "SpecialAmount": 0.0}
          }
        };</script>
        <p class="special-offer">Two months free parking</p>
        '''
        result = parse_quote_html(html, "https://secure.example/quote", "2026-11-04", 15)
        self.assertEqual(result["concessions"], ["Up to 6 Weeks Free", "Two months free parking"])
        self.assertEqual(result["concession_scope"], "unit_or_lease_quote")


if __name__ == "__main__":
    unittest.main()
