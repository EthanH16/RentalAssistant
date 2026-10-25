import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from adapters.helio.adapter import (  # noqa: E402
    parse_page, parse_plans, parse_units, parse_rentcafe_price_response,
    parse_promotion, parse_securecafe_quote_html,
)


VENUE = """
<div id="nudgeTop"><div>Rent Specials</div><div>$1,000 Look &amp; Lease Special on select residences!</div></div>
<div id="floorplans-container">
 <div class="fp-container" id="fp-container-4313">
  <h2>A</h2><ul class="list-inline"><li>1 Bed</li><li>1 Bath</li><li>840 Sq. Ft.</li></ul>
  <span class="fp-availability">2 Available</span><span class="text-md">$3,950.00</span>
  <a class="track-apply" href="/floorplans/a" data-floorplan-price="3950 -4650">Availability</a>
 </div>
</div>
"""

UNITS = """
<div class="floorplan-section" data-id="4313" data-name="A">
 <table><tr class="unit-container" id="unit-container-123"><td class="td-card-name">#3688-203</td>
 <td class="td-card-sqft">840</td><td class="td-card-rent">$3,950.00</td>
 <td><a href="https://venue-residences.securecafe.com/onlineleasing/x?UnitID=123">Apply Now</a></td></tr></table>
</div>
"""

ARYA = """
<div class="floorplan-section" data-id="1" data-name="S2"><h2>S2</h2><div>Studio | 1 Bathroom</div>
<table><tr class="unit-container" id="unit-container-99"><td class="td-card-name">#404</td>
<td class="td-card-sqft">504</td><td class="td-card-rent">$3,250.00</td>
<td><a href="https://live-arya.rentcafe.com/applicant/x">Apply Now</a></td></tr></table></div>
"""

QUOTE = """
<div id="LeaseInfoSummary"><table>
<tr><th>Lease Term</th><td>12 months</td></tr>
<tr><th>Move-In Date</th><td>12/1/2026</td></tr>
<tr><th>Rent</th><td>$3,795.00</td></tr>
<tr><th>Internet Fee</th><td>$84.00</td></tr>
<tr><th>Net Rent</th><td>$3,879.00</td></tr>
</table></div>
"""


class HelioAdapterTests(unittest.TestCase):
    def test_parses_plan_card_and_promotion(self):
        result = parse_page(VENUE, "https://venue.example/floorplans", {"mode": "plan_pages"})
        self.assertEqual(result["plans"][0]["plan_name"], "A")
        self.assertEqual(result["plans"][0]["base_rent_min"], 3950.0)
        self.assertEqual(result["plans"][0]["base_rent_max"], 4650.0)
        self.assertIn("$1,000 Look & Lease", result["concessions"][0])

    def test_parses_homepage_look_and_lease_banner(self):
        html = """
        <div class="top-banner">Receive a $1k look and lease special for select residences. Contact us today!</div>
        """
        result = parse_promotion(html)
        self.assertEqual(
            result,
            ["Receive a $1k look and lease special for select residences. Contact us today!"],
        )

    def test_parses_resident_facing_unit(self):
        units = parse_units(UNITS, "https://venue.example/floorplans/a")
        self.assertEqual(units[0]["unit_id"], "3688-203")
        self.assertEqual(units[0]["provider_unit_id"], "123")
        self.assertEqual(units[0]["floor_plan"], "A")

    def test_arya_direct_unit_uses_plan_context(self):
        plans = parse_plans(ARYA, "https://arya.example/availableunits")
        self.assertEqual(plans[0]["plan_name"], "S2")
        units = parse_units(ARYA, "https://arya.example/availableunits", plans[0])
        self.assertEqual(units[0]["unit_id"], "404")
        self.assertEqual(units[0]["floor_plan"], "S2")

    def test_parses_securecafe_quote_summary(self):
        quote = parse_securecafe_quote_html(QUOTE, "https://example.com/quote")
        self.assertEqual(quote["base_rent"], 3795.0)
        self.assertEqual(quote["net_rent"], 3879.0)
        self.assertEqual(quote["lease_term"], "12 months")
        self.assertEqual(quote["move_in_date"], "2026-12-01")
        self.assertEqual(len(quote["fee_rows"]), 5)

    def test_parses_price_endpoint(self):
        self.assertEqual(parse_rentcafe_price_response("<span>$4,095.00</span>"), 4095.0)


if __name__ == "__main__":
    unittest.main()
