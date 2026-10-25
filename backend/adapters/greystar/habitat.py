"""Read-only adapter for Habitat Residences' official property site.

Habitat uses Jonah Digital's structured floor-plan payload, Engrain's public
expense guide, and SecureCafe rental-options pages for date/term quotes. The
provider shape is shared with Greystar-style sites, but Habitat has its own
building identity and URLs, so it is kept as a separate adapter.
"""

from __future__ import annotations

from datetime import datetime, timezone
import html as html_lib
import http.cookiejar
import json
import re
from typing import Any
from urllib.parse import quote, urljoin, urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen

from bs4 import BeautifulSoup
from sqids import Sqids

from .eden import (
    EXPENSE_GUIDE_API,
    EXPENSE_GUIDE_HOST,
    EXPENSE_GUIDE_SQIDS_ALPHABET,
    EdenAdapterError,
    _clean,
    _extract_json_script,
    _fee_guide_reference,
    _iso_date,
    _marketing_concessions,
    _unit,
    parse_fee_guide_payload,
    parse_quote_html,
    parse_unit_detail_html,
)

BUILDING_ID = "habitat"
BUILDING_NAME = "Habitat Residences"
OFFICIAL_URL = "https://habitat-residences.com/"
FLOORPLANS_URL = "https://habitat-residences.com/floorplans/"
PHONE = "323-709-0584"


def _provider_date(iso_date: str) -> str:
    return datetime.strptime(iso_date, "%Y-%m-%d").strftime("%-m/%-d/%Y")


def _rewrite_unit(unit: dict[str, Any], raw: dict[str, Any]) -> dict[str, Any]:
    """Rebind the shared parser's Eden identity fields to Habitat."""

    unit["detail_url"] = urljoin(FLOORPLANS_URL, raw.get("permalink") or "")
    unit["application_url"] = unit["detail_url"]
    unit["provider_payload"]["provider"] = "jonah_digital"
    unit["provider_payload"]["property_id"] = raw.get("property_id")
    return unit


def _jsonld_concessions(page_html: str) -> list[str]:
    """Read Jonah's official JSON-LD announcement objects."""

    values: list[str] = []
    soup = BeautifulSoup(page_html, "html.parser")
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            payload = json.loads(script.string or script.get_text())
        except (TypeError, json.JSONDecodeError):
            continue

        def walk(value: Any) -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    if str(key).lower().endswith("name") and isinstance(child, str):
                        if re.search(r"(?:free|special|offer|concession|look\s*(?:&|and)\s*lease)", child, re.I):
                            values.append(_clean(child))
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)

        walk(payload)
    return list(dict.fromkeys(value for value in values if value))


def parse_habitat_floorplans_html(page_html: str, source_url: str = FLOORPLANS_URL) -> dict[str, Any]:
    payload = _extract_json_script(page_html)
    raw_units = [item for item in payload["units"] if isinstance(item, dict)]
    if not raw_units:
        raise EdenAdapterError("Habitat inventory JSON returned zero units; refusing an empty scan")

    units = [_rewrite_unit(_unit(raw, None), raw) for raw in raw_units]
    concessions = list(dict.fromkeys(
        _marketing_concessions(page_html, payload) + _jsonld_concessions(page_html)
    ))
    fee_guide = _fee_guide_reference(page_html, source_url)
    if fee_guide is None:
        match = re.search(
            r"EngrainExpenseEmbed\s*\(\s*\{.*?assetId\s*:\s*['\"]([^'\"]+)['\"].*?\}\s*\)",
            page_html,
            re.I | re.S,
        )
        if match:
            fee_guide = {
                "provider": "engrain_expense_guide",
                "source_url": source_url,
                "widget_url": EXPENSE_GUIDE_HOST,
                "section_title": "Easy-to-Use Guide",
                "fees": [],
                "status": "widget_reference_only",
                "asset_id": match.group(1),
            }

    return {
        "provider": "greystar_spaces",
        "building_id": BUILDING_ID,
        "building_name": BUILDING_NAME,
        "source_url": source_url,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "status": "ok",
        "units": units,
        "policies": {
            "fees": [],
            "concessions": concessions,
            "fee_guide": fee_guide,
        },
        "contact": {"phone": PHONE, "website": OFFICIAL_URL},
        "floorplans_count": len(payload.get("floorplans") or []),
        "units_count": len(units),
        "warnings": [
            "Habitat prices are the official Total Monthly Leasing Price with a base-rent reference preserved per unit.",
            "Prices, availability, fees, and promotions can change without notice.",
            "Concessions are preserved as official marketing text and must be checked for eligibility before quoting a client.",
        ],
    }


class HabitatAdapter:
    """Fetch current Habitat inventory and read-only unit quotes."""

    def __init__(self, timeout_seconds: int = 30, user_agent: str | None = None):
        self.timeout_seconds = timeout_seconds
        self.user_agent = user_agent or (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 Chrome/141.0 Safari/537.36"
        )

    def _request_html(self, url: str, *, referer: str | None = None, session: Any = None) -> str:
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        if referer:
            headers["Referer"] = referer
        request = Request(url, headers=headers)
        opener = session or build_opener()
        with opener.open(request, timeout=self.timeout_seconds) as response:
            if response.status != 200:
                raise EdenAdapterError(f"Habitat official page returned HTTP {response.status}")
            return response.read().decode(response.headers.get_content_charset() or "utf-8", "replace")

    def fetch_html(self, url: str = FLOORPLANS_URL) -> str:
        return self._request_html(url)

    def fetch_fee_guide(self, fee_guide: dict[str, Any]) -> dict[str, Any]:
        asset_id = _clean(fee_guide.get("asset_id"))
        if not asset_id.isdigit():
            raise EdenAdapterError("Habitat fee widget reference has no numeric asset ID")
        encoded = Sqids(alphabet=EXPENSE_GUIDE_SQIDS_ALPHABET, min_length=11).encode([int(asset_id)])
        api_url = EXPENSE_GUIDE_API.format(asset_id=encoded)
        request = Request(api_url, headers={
            "User-Agent": self.user_agent,
            "Referer": FLOORPLANS_URL,
            "Origin": "https://habitat-residences.com",
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Sec-Fetch-Site": "cross-site",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
        })
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                if response.status != 200:
                    raise EdenAdapterError(f"Habitat fee widget returned HTTP {response.status}")
                payload = json.loads(response.read().decode(response.headers.get_content_charset() or "utf-8", "replace"))
        except EdenAdapterError:
            raise
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise EdenAdapterError(f"Habitat fee widget could not be fetched: {exc}") from exc
        return parse_fee_guide_payload(payload, asset_id, FLOORPLANS_URL, api_url)

    def fetch_unit_detail(self, unit_slug: str) -> dict[str, Any]:
        session = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self._request_html(FLOORPLANS_URL, session=session)
        detail_url = urljoin(FLOORPLANS_URL, f"unit-{unit_slug}/")
        html = self._request_html(detail_url, referer=FLOORPLANS_URL, session=session)
        result = parse_unit_detail_html(html, detail_url)
        result.update({"provider": "greystar_spaces", "building_id": BUILDING_ID, "building_name": BUILDING_NAME})
        return result

    def fetch_unit_quote(self, unit_slug: str, move_in_date: str, lease_term_months: int) -> dict[str, Any]:
        if lease_term_months <= 0:
            raise EdenAdapterError("Lease term must be a positive number of months")
        iso_date = _iso_date(move_in_date)
        detail = self.fetch_unit_detail(unit_slug)
        lease_link = detail.get("lease_link_data", {}).get("lease_link")
        if not lease_link:
            raise EdenAdapterError("Habitat unit has no SecureCafe lease-link template")
        quote_url = lease_link.replace("{date}", _provider_date(iso_date)).replace("{term}", str(lease_term_months))
        session = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self._request_html(FLOORPLANS_URL, session=session)
        html = self._request_html(quote_url, referer=detail["source_url"], session=session)
        result = parse_quote_html(html, quote_url, iso_date, lease_term_months)
        result.update({
            "provider": "greystar_spaces",
            "building_id": BUILDING_ID,
            "building_name": BUILDING_NAME,
            "unit_id": result.get("unit_id") or detail.get("unit_id"),
            "provider_unit_id": result.get("provider_unit_id") or detail.get("provider_unit_id"),
            "floor_plan": detail.get("floor_plan"),
            "detail_url": detail["source_url"],
            "requested_unit_slug": unit_slug,
            "lease_link_data": detail["lease_link_data"],
        })
        return result

    def fetch(self) -> dict[str, Any]:
        page_html = self.fetch_html()
        result = parse_habitat_floorplans_html(page_html)
        fee_guide = result["policies"].get("fee_guide")
        if fee_guide and fee_guide.get("asset_id"):
            try:
                fetched = self.fetch_fee_guide(fee_guide)
            except EdenAdapterError as exc:
                fee_guide["status"] = "widget_fetch_failed"
                fee_guide["fetch_error"] = str(exc)
                result["warnings"].append("The official Habitat fee widget could not be fetched during this scan; retry is required.")
            else:
                result["policies"]["fee_guide"] = fetched
                result["policies"]["fees"] = fetched["fees"]
        return result

