import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from adapters.brookfield.beaudry import parse_fee_guide_html  # noqa: E402


class BeaudryAdapterTests(unittest.TestCase):
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
