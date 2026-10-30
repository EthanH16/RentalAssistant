import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from adapters.brookfield.beaudry import parse_beaudry_quote_html, parse_fee_guide_html  # noqa: E402


class BeaudryAdapterTests(unittest.TestCase):
    def test_parses_selected_date_and_term_quote(self):
        html = """
        <input id='dtMIDateSel' value='12/1/2026'>
        <input id='MoveInDate' value='12/1/2026'>
        <input id='sLeaseTerm' value='15'>
        <input id='RentableItemTypesRent' value='0'>
        <input id='MonthlyOtherRent' value='12.57'>
        <script>var PricingData = {
          "12/1/2026": {
            "15": {"price": 3243.0, "Tax": 3.82, "EMCBasePrice": 3243.0, "SpecialAmount": 0.0},
            "16": {"price": 2868.0, "Tax": 3.82}
          }
        };</script>
        <div id='LeaseInfoSummary'><table><tr><td>Base rent</td><td>$3,243.00</td></tr></table></div>
        <div>2 Months Free!* Move in by 31-Dec-26</div>
        """
        result = parse_beaudry_quote_html(
            html,
            "https://example.test/rentaloptions.aspx?UnitID=42767727",
            "2026-12-01",
            15,
        )
        self.assertEqual(result["move_in_date"], "2026-12-01")
        self.assertEqual(result["lease_term_months"], 15)
        self.assertEqual(result["base_rent"], 3243.0)
        self.assertEqual(result["total_monthly_price"], 3259.39)
        self.assertEqual(result["fee_rows"][0]["label"], "Base rent")
        self.assertTrue(result["concessions"])

    def test_parses_collapsed_official_fee_sections(self):
        html = """
        <div class='fee-guide-wrapper'>
          <h3>Beaudry Fee Guide</h3>
          <div class='fee-guide-description'>Total Monthly Leasing Price includes fixed fees.</div>
          <div class='fee-item'><h4>Required Fees</h4><div class='fee-content'>
            <table class='desktop-fee-table'><tbody>
              <tr><td><h6>Application Fee*</h6></td><td>One-Time</td><td>$50.00</td><td></td></tr>
              <tr><td><h6>Pest Control*</h6></td><td>Per Month</td><td>$0.99</td><td><span class='check-circle'></span></td></tr>
            </tbody></table>
          </div></div>
          <div class='fee-item'><h4>Optional Fees</h4><div class='fee-content'>
            <table class='desktop-fee-table'><tbody>
              <tr><td><h6>Monthly Pet Rent*</h6></td><td>Per Month</td><td>$50.00</td><td></td></tr>
            </tbody></table>
          </div></div>
          <table class='mobile-fee-table'><tbody><tr><td>Duplicate</td></tr></tbody></table>
        </div>
        """
        result = parse_fee_guide_html(html, "https://example.test/beaudry/fee-guide/")
        self.assertEqual(result["status"], "parsed")
        self.assertTrue(result["ground_truth"])
        self.assertEqual(len(result["fees"]), 3)
        self.assertEqual(result["fees"][0]["name"], "Application Fee")
        self.assertTrue(result["fees"][0]["has_footnote"])
        self.assertFalse(result["fees"][0]["included_in_total_monthly_leasing_price"])
        self.assertTrue(result["fees"][1]["included_in_total_monthly_leasing_price"])
        self.assertFalse(result["fees"][2]["required"])


if __name__ == "__main__":
    unittest.main()
