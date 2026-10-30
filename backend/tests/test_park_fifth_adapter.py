import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from adapters.bozzuto.park_fifth import (  # noqa: E402
    parse_fee_widget_html,
    parse_park_fifth_calculator_html,
    parse_sightmap_pricing_json,
)


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

    def test_calculator_parser_requires_explicit_pricing(self):
        result = parse_park_fifth_calculator_html(
            "<main><p>Base Rent $2,718.00</p><p>Total Monthly Leasing Price $2,727.58</p></main>",
            "https://sightmap.com/embed/test?unit_number=0705",
            "0705",
            "2026-12-01",
            15,
        )
        self.assertEqual(result["base_rent"], 2718.0)
        self.assertEqual(result["total_monthly_price"], 2727.58)
        self.assertEqual(result["lease_term_months"], 15)

    def test_calculator_parser_fails_closed_on_app_shell(self):
        with self.assertRaisesRegex(Exception, "no explicit quote"):
            parse_park_fifth_calculator_html(
                "<div id='app'><div>Loading</div></div>",
                "https://sightmap.com/embed/test",
                "0705",
                "2026-12-01",
                15,
            )

    def test_sightmap_api_fails_closed_when_only_advertised_price_is_exposed(self):
        with self.assertRaisesRegex(Exception, "only the current advertised price"):
            parse_sightmap_pricing_json(
                {"data": {"options": [{"lease_term": None, "price": 3222, "total_display_price": "$3,231.58"}]}},
                "0704",
                "2026-10-26",
                15,
                pricing_url="https://sightmap.test/pricing",
                calculator_url="https://sightmap.test/embed",
            )

    def test_sightmap_api_parses_exact_term_option(self):
        result = parse_sightmap_pricing_json(
            {
                "data": {
                    "options": [
                        {"lease_term": 12, "price": 3200, "total_display_price": "$3,209.58"},
                        {"lease_term": 15, "price": 3150, "total_display_price": "$3,159.58"},
                    ]
                }
            },
            "0704",
            "2026-10-26",
            15,
            pricing_url="https://sightmap.test/pricing",
            calculator_url="https://sightmap.test/embed",
        )
        self.assertEqual(result["base_rent"], 3150.0)
        self.assertEqual(result["total_monthly_price"], 3159.58)
        self.assertEqual(result["provider"], "bozzuto_sightmap_api")


if __name__ == "__main__":
    unittest.main()
