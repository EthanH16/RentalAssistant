import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from adapters.bozzuto.park_fifth import parse_fee_widget_html  # noqa: E402


class ParkFifthAdapterTests(unittest.TestCase):
    def test_parses_official_fee_widget(self):
        html = """
        <div class="rfwa-expense-guide-container">
          <h2>The Fine Print, Simplified: Your Fee Breakdown</h2>
          <p>Review all potential fees.</p>
          <div class="rfwa-expense-accordion">
            <div class="rfwa-accordion-item">
              <h3 class="rfwa-group-title">Figures to Help You Figure It Out Required</h3>
              <p class="rfwa-group-description">Essential fees.</p>
              <table class="rfwa-expense-list"><tbody>
                <tr><td><div>Amenity Fee</div></td><td><span data-tooltip="One-time charge">$20.00</span></td><td>One - Time</td><td>Required</td></tr>
              </tbody></table>
            </div>
          </div>
          <div class="rfwa-expense-footer">Prices may change.</div>
        </div>
        """
        result = parse_fee_widget_html(html, "https://fees.example.test/widget")
        self.assertEqual(result["status"], "parsed")
        self.assertEqual(result["site_id"], "7C65F7")
        self.assertEqual(result["fees"][0]["name"], "Amenity Fee")
        self.assertEqual(result["fees"][0]["cost"], "$20.00")
        self.assertEqual(result["fees"][0]["type"], "Required")
        self.assertEqual(result["fees"][0]["tooltip"], "One-time charge")


if __name__ == "__main__":
    unittest.main()
