import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from adapters.onni import (  # noqa: E402
    OnniAdapterError,
    parse_building_policies,
    parse_825_south_hill_html,
    parse_fixture,
    parse_olympic_hill_floorplans_html,
    parse_olympic_hill_plan_html,
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

    def test_parses_olympic_hill_plan_rows(self):
        html = '''
        <div class="floorplan-section" data-name="Plan A" data-id="5579466">
          <div class="row"><h2>Plan A</h2><div><span>1 Bedroom</span> | <span>1 Bathroom</span></div></div>
          <table><tbody><tr class="unit-container" id="unit-container-43089391">
            <td>#3113</td><td>577</td><td>$2,551.00 to -$3,394.00</td><td>Available</td>
            <td><a href="https://rentolympicandhill.securecafe.com/onlineleasing/olympic-hill/oleapplication.aspx?FloorPlanID=5579466&UnitID=43089391">Apply</a></td>
          </tr>
          <tr class="unit-container d-none" id="unit-container-43090715">
            <td><p>Apartment:</p> #0712</td><td><p>Sq. Ft.:</p> 584</td>
            <td><p>Rent:</p>$2,499.00 to -$3,325.00</td><td><p>Date:</p>Available</td>
            <td><a href="https://rentolympicandhill.securecafe.com/onlineleasing/olympic-hill/oleapplication.aspx?FloorPlanID=5579466&UnitID=43090715">Apply</a></td>
          </tr></tbody></table>
        </div>
        <h3>Essentials</h3><table><thead><tr><th>Fee Label</th><th>Amount</th><th>Applied</th><th>Frequency / Due At</th><th>Type</th></tr></thead><tbody><tr><td>Utility</td><td>Allocated</td><td>Per Unit</td><td>Monthly</td><td>REQUIRED</td></tr></tbody></table>
        '''
        result = parse_olympic_hill_plan_html(html, "https://www.rentolympicandhill.com/floorplans/plan-a")
        self.assertEqual(result["units"][0]["unit_id"], "43089391")
        self.assertEqual(result["units"][0]["base_rent_min"], 2551)
        self.assertEqual(result["units"][0]["base_rent_max"], 3394)
        self.assertEqual(result["policies"]["fees"][0]["requirement"], "REQUIRED")
        self.assertEqual(result["units"][1]["unit_id"], "43090715")
        self.assertEqual(result["units"][1]["sqft"], 584)
        self.assertTrue(result["units"][1]["available_now"])

    def test_parses_all_olympic_plan_links_without_provider_specific_class(self):
        html = '''
        <div class="floorplan-card"><h2>Plan A</h2><span>1 Bed</span><span>1 Bath</span>
          <span>577 Sq. Ft.</span><span>5 Available</span><span>Starting at $2,499.00</span>
          <a href="/floorplans/plan-a">Availability for Plan A</a></div>
        <div class="floorplan-card"><h2>Plan D</h2><span>2 Bed</span><span>2 Bath</span>
          <span>862 Sq. Ft.</span><span>5 Available</span><span>Starting at $3,500.00</span>
          <a href="/floorplans/plan-d">Availability for Plan D</a></div>
        '''
        result = parse_olympic_hill_floorplans_html(html, "https://www.rentolympicandhill.com/floorplans")
        self.assertEqual([p["floor_plan"] for p in result["floorplans"]], ["Plan A", "Plan D"])
        self.assertEqual(result["floorplans"][1]["beds"], 2)
        self.assertEqual(result["floorplans"][1]["base_rent_min"], 3500)

    def test_selects_requested_olympic_plan_not_first_similar_plan(self):
        html = '''
        <h1>Plan D</h1>
        <div class="floorplan-section" data-name="Plan A" data-id="a">
          <div class="row"><h2>Plan A</h2><span>1 Bedroom</span> | <span>1 Bathroom</span></div>
          <table><tr class="unit-container"><td>#1001</td><td>577</td><td>$2,500</td><td>Available</td><td><a href="/apply?UnitID=1001">Apply</a></td></tr></table>
        </div>
        <div class="floorplan-section" data-name="Plan D" data-id="d">
          <div class="row"><h2>Plan D</h2><span>2 Bedroom</span> | <span>2 Bathroom</span></div>
          <table><tr class="unit-container"><td>#2001</td><td>862</td><td>$3,500</td><td>Available</td><td><a href="/apply?UnitID=2001">Apply</a></td></tr></table>
        </div>
        '''
        result = parse_olympic_hill_plan_html(html, "https://www.rentolympicandhill.com/floorplans/plan-d")
        self.assertEqual(result["floor_plan"], "Plan D")
        self.assertEqual(result["units"][0]["unit_id"], "2001")
        self.assertEqual(result["units"][0]["beds"], 2)

    def test_parses_825_south_hill_shared_availability_rows(self):
        html = '''
        <div class="floorplan__availability__panel js-plan-group" data-cat="2-bed-den">
          <div class="floorplan__availability__intro"><h2>Plan A2</h2><p>2 Bed + Den</p></div>
          <div class="floorplan__availability__row js-plan-row" data-unit="0522" data-rent-min="4060" data-rent-max="5246">
            <ul><li class="floorplan__availability__li--data">#522</li><li class="floorplan__availability__li--data">2 Bed + Den</li><li class="floorplan__availability__li--data">1030 sqft</li><li class="floorplan__availability__li--data">Available</li></ul>
            <a href="https://825southhilldtla.securecafe.com/onlineleasing/825-south-hill/oleapplication.aspx?FloorPlanID=2413008&UnitID=29417540">Apply</a>
          </div>
        </div>
        '''
        result = parse_825_south_hill_html(html, "https://825southhilldtla.com/availability")
        self.assertEqual(result["building_id"], "825-south-hill")
        self.assertEqual(result["building_name"], "825 South Hill")
        self.assertEqual(result["units"][0]["unit_id"], "0522")
        self.assertEqual(result["units"][0]["base_rent_min"], 4060)
        self.assertTrue(result["units"][0]["available_now"])

    def test_ignores_provider_placeholder_rent(self):
        html = '''
        <div class="floorplan__availability__panel js-plan-group" data-cat="1-bed">
          <div class="floorplan__availability__intro">PLAN O | 1 bed</div>
          <div class="floorplan__availability__row js-plan-row" data-unit="4602" data-rent-min="$-1" data-rent-max="$-1">
            <ul><li class="floorplan__availability__li--data">#4602</li><li class="floorplan__availability__li--data">$-1</li><li class="floorplan__availability__li--data">538 sqft</li><li class="floorplan__availability__li--data">11/22/2026</li></ul>
          </div>
        </div>
        '''
        result = parse_825_south_hill_html(html, "https://825southhilldtla.com/availability")
        self.assertIsNone(result["units"][0]["base_rent_min"])
        self.assertIsNone(result["units"][0]["base_rent_max"])


if __name__ == "__main__":
    unittest.main()
