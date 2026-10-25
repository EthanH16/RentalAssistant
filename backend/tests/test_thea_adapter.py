import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from adapters.greystar.thea import parse_plan_cards, parse_unit_cards, parse_thea_inventory, parse_thea_quote_html  # noqa: E402


class TheaAdapterTests(unittest.TestCase):
    def test_parses_aggregated_plan_cards(self):
        html = '''
        <article data-spaces-plan="570665" data-spaces-plan-name="1-A3" data-spaces-bed-count="1" data-spaces-area="837">
          <h3>1-A3</h3><p>2 Units Available</p><p>1 Bed 1 Bath 837 Sq. Ft. From $3,562 /mo $3,548 Base Rent</p><span>Has Special</span>
        </article>'''
        result = parse_plan_cards(html)
        self.assertEqual(result[0]["plan_id"], "570665")
        self.assertEqual(result[0]["available_count"], 2)
        self.assertEqual(result[0]["total_monthly_price"], 3562.0)
        self.assertEqual(result[0]["base_rent"], 3548.0)
        self.assertTrue(result[0]["has_special"])

    def test_parses_real_units_using_apartment_number_not_provider_id(self):
        html = '''
        <article data-spaces-unit-id="10342778" data-spaces-unit="3906" data-spaces-plan-name="1-A3"
                 data-spaces-bed-count="1" data-spaces-area="837" data-spaces-date="2026-11-21"
                 data-spaces-href="?spaces_tab=unit-detail&detail=10342778">
          <a href="https://theadtla.securecafe.com/onlineleasing/thea-at-metropolis-new/rentaloptions.aspx?UnitID=34515195">Apply</a>
          <p>1 Bed 1 Bath 837 Sq. Ft. $3,682 /mo $3,668 Base Rent</p>
        </article>'''
        result = parse_unit_cards(html)
        self.assertEqual(result[0]["unit_id"], "3906")
        self.assertEqual(result[0]["provider_unit_id"], "10342778")
        self.assertEqual(result[0]["base_rent_min"], 3668.0)
        self.assertEqual(result[0]["total_monthly_leasing_price_min"], 3682.0)
        self.assertEqual(result[0]["available_date"], "2026-11-21")
        self.assertIn("UnitID=34515195", result[0]["application_url"])

    def test_inventory_requires_actual_unit_cards_and_preserves_banner(self):
        page = '<div>Up to 8 Weeks Free + Up to $1,000 Look &amp; Lease Bonus! Includes a $500 Look &amp; Lease Bonus when you apply within 24 hours of your tour.</div>'
        plans = '<article data-spaces-plan="1" data-spaces-plan-name="S1"><p>1 Unit Available From $2,500 /mo $2,486 Base Rent</p><span>Has Special</span></article>'
        units = '<article data-spaces-unit-id="u1" data-spaces-unit="1201" data-spaces-plan-name="S1"><p>$2,500 /mo $2,486 Base Rent</p></article>'
        result = parse_thea_inventory(page, plans, [("1", units)])
        self.assertEqual(result["units"][0]["unit_id"], "1201")
        self.assertTrue(any("8 Weeks Free" in item for item in result["policies"]["concessions"]))
        self.assertTrue(any("Has Special" in item for item in result["policies"]["concessions"]))

    def test_parses_rentcafe_quote_response(self):
        html = '''
        <div class="breakdown-row"><div class="col-xs-5">Rent</div><div class="col-xs-2">$3,668</div></div>
        <div id="UnitSpecials"></div>
        <input id="BaseMaxRent" value="3668.00"><input id="MonthlyOtherRent" value="11">
        <input id="TotalRent" value="3679.00"><input id="Tax" value="0">
        <input id="sLeaseTerm" value="12"><input id="dtMIDateSel" value="11/21/2026">
        <input id="UnitId" value="34515195"><input id="FloorplanId" value="4623215">
        '''
        result = parse_thea_quote_html(html, "https://secure.example/quote", "2026-11-21", 12)
        self.assertEqual(result["base_rent"], 3668.0)
        self.assertEqual(result["total_monthly_price"], 3679.0)
        self.assertEqual(result["recurring_addons"], 11.0)
        self.assertEqual(result["move_in_date"], "2026-11-21")


if __name__ == "__main__":
    unittest.main()
