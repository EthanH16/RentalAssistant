import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from adapters.onni import (  # noqa: E402
    OnniAdapterError,
    parse_building_policies,
    parse_fixture,
    parse_unit_detail_html,
)
from bs4 import BeautifulSoup


class OnniAdapterTests(unittest.TestCase):
    fixture = ROOT / "fixtures" / "onni" / "hope-flower-availability.sample.html"

    def test_parses_unit_level_rows(self):
        result = parse_fixture(self.fixture)
        self.assertEqual(result["building_id"], "hope-flower")
        self.assertEqual(len(result["units"]), 2)
        self.assertEqual(result["units"][0]["unit_id"], "1519")
        self.assertEqual(result["units"][0]["base_rent_min"], 2434)
        self.assertEqual(result["units"][0]["screening_base_rent"], 2434)
        self.assertFalse(result["units"][0]["available_now"])
        self.assertEqual(result["units"][0]["available_date"], "2026-11-08")
        self.assertEqual(result["units"][0]["floor_plan_id"], "3122756")
        self.assertEqual(result["units"][1]["status"], "available")
        self.assertTrue(result["units"][1]["available_now"])

    def test_parses_securecafe_term_quote(self):
        html = '''
        <input id="UnitId" value="30308695">
        <input id="FloorplanId" value="3122773">
        <input id="dtMIDateSel" value="12/13/2026">
        <input id="sLeaseTerm" value="12">
        <input id="RentableItemTypesRent" value="0">
        <input id="MonthlyOtherRent" value="17.99">
        <script>var PricingData = {
          "12/13/2026": {
            "12": {"price": 2846.0, "Tax": 0.0, "EMCBasePrice": 2846.0, "SpecialAmount": 0.0},
            "18": {"price": 2799.0, "Tax": 0.0, "EMCBasePrice": 2799.0, "SpecialAmount": 0.0}
          }
        };</script>
        '''
        result = parse_unit_detail_html(
            html,
            "https://example.test/unit?UnitID=30308695&FloorPlanID=3122773",
            requested_lease_term=12,
        )
        self.assertEqual(result["base_rent"], 2846.0)
        self.assertEqual(result["total_monthly_price"], 2863.99)
        self.assertEqual(len(result["term_quotes"]), 2)

    def test_parses_building_fees_and_concession_without_numeric_assumptions(self):
        soup = BeautifulSoup(
            '''
            <h3>Essentials</h3>
            <table><thead><tr><th>Fee Label</th><th>Amount</th><th>Applied</th><th>Frequency / Due At</th><th></th></tr></thead>
            <tbody><tr><td>Utility – Electric<sup>1</sup></td><td>Allocated (Utilities)</td><td>Per Unit</td><td>Monthly</td><td>Required</td></tr></tbody></table>
            <h2>Discover Special Savings on Our Most Elevated Residences</h2>
            <div class="content"><p>Now offering up to $1,000 Look &amp; Lease on select one + two bedroom homes.</p><p>*Must lease by October 31, 2026.</p></div>
            <p>Date of Issuance: 10/1/2026</p>
            ''',
            "html.parser",
        )
        result = parse_building_policies(soup, "https://example.test/availability")
        self.assertEqual(result["fee_guide_issued"], "2026-10-01")
        self.assertEqual(result["fees"][0]["label"], "Utility – Electric")
        self.assertEqual(result["fees"][0]["amount"], "Allocated (Utilities)")
        self.assertEqual(result["fees"][0]["requirement"], "Required")
        self.assertEqual(len(result["concessions"]), 1)
        self.assertIn("$1,000", result["concessions"][0]["text"])

    def test_fails_closed_when_markup_is_missing(self):
        try:
            parse_fixture(self.fixture.with_name("missing.html"))
        except FileNotFoundError:
            pass
        with self.assertRaises(OnniAdapterError):
            from adapters.onni import parse_hope_flower_html
            parse_hope_flower_html("<html></html>", "https://example.test")


if __name__ == "__main__":
    unittest.main()
