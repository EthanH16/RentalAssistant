"""Read-only adapter for The Eden's official Greystar property site."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import html as html_lib
import http.cookiejar
import json
import re
from typing import Any
from urllib.parse import quote, urljoin, urlparse
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen

from bs4 import BeautifulSoup

BUILDING_ID = "the-eden"
BUILDING_NAME = "The Eden"
OFFICIAL_URL = "https://edendtla.com/"
FLOORPLANS_URL = "https://edendtla.com/floorplans/"
PHONE = "877-680-8826"
RENDERABLE_ENDPOINT = "_fp-renderable"


class EdenAdapterError(RuntimeError):
    """Raised when The Eden's official inventory cannot be trusted."""


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _money(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"-?\$?\s*([0-9][0-9,]*(?:\.\d{1,2})?)", str(value))
    return float(match.group(1).replace(",", "")) if match else None


def _integer(value: Any) -> int | None:
    try:
        return int(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _beds(value: Any) -> int | None:
    text = _clean(value).lower()
    if "studio" in text:
        return 0
    match = re.search(r"(\d+)", text)
    return int(match.group(1)) if match else None


def _date_from_timestamp(value: Any) -> str | None:
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return None
    if timestamp <= 0:
        return None
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).date().isoformat()


def _as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _iso_date(value: str) -> str:
    """Normalize the user-facing date formats accepted by the quote CLI."""

    for pattern in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(value.strip(), pattern).date().isoformat()
        except ValueError:
            continue
    raise EdenAdapterError("Move-in date must be YYYY-MM-DD or MM/DD/YYYY")


def _provider_date(iso_date: str) -> str:
    return datetime.strptime(iso_date, "%Y-%m-%d").strftime("%-m/%-d/%Y")


def _hidden_value(soup: BeautifulSoup, element_id: str) -> str | None:
    element = soup.select_one(f"#{element_id}")
    return element.get("value") if element else None


def _pricing_data(html: str) -> dict[str, Any]:
    match = re.search(r"var\s+PricingData\s*=\s*(\{.*?\});", html, re.S)
    if not match:
        raise EdenAdapterError("The Eden quote page has no structured PricingData")
    try:
        value = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise EdenAdapterError("The Eden quote PricingData is invalid JSON") from exc
    if not isinstance(value, dict):
        raise EdenAdapterError("The Eden quote PricingData has an unexpected shape")
    return value


def parse_unit_detail_html(detail_html: str, source_url: str) -> dict[str, Any]:
    """Parse Eden's renderable unit-detail fragment without persistence."""

    soup = BeautifulSoup(detail_html, "html.parser")
    # The response normally contains two unit JSON blocks. The resource block
    # is the server-selected unit; the UI selector block can be a stale
    # preloaded card when the provider's cache has rotated inventory.
    script = soup.select_one("script#jd-fp-data-script-resource") or soup.select_one(
        'script[data-jd-fp-selector="unit-data"]'
    )
    if not script:
        raise EdenAdapterError("The Eden unit-detail response has no unit-data JSON")
    try:
        unit = json.loads(script.string or script.get_text())
    except json.JSONDecodeError as exc:
        raise EdenAdapterError("The Eden unit-detail unit-data is invalid JSON") from exc
    if not isinstance(unit, dict) or not unit.get("lease_link_data"):
        raise EdenAdapterError("The Eden unit-detail response has no lease-link data")
    apartment = _clean(unit.get("apartment_number"))
    if apartment.startswith("#"):
        apartment = apartment[1:].strip()
    return {
        "provider": "greystar",
        "building_id": BUILDING_ID,
        "building_name": BUILDING_NAME,
        "source_url": source_url,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "status": "ok",
        "unit_id": apartment,
        "provider_unit_id": str(unit.get("id_value") or unit.get("id") or ""),
        "floor_plan": _clean(unit.get("floorplan_title") or unit.get("title")) or None,
        "beds": _beds(unit.get("bedrooms")),
        "baths": _money(unit.get("bathrooms")),
        "sqft": _integer(unit.get("square_feet")),
        "lease_link_data": unit["lease_link_data"],
        "lease_terms": [_lease_term(term) for term in (unit.get("lease_terms") or []) if isinstance(term, dict)],
        "provider_payload": unit,
    }


def _quote_fee_rows(soup: BeautifulSoup) -> list[dict[str, str]]:
    summary = soup.select_one("#LeaseInfoSummary")
    if not summary:
        return []
    rows: list[dict[str, str]] = []
    for row in summary.select("tr"):
        cells = [_clean(cell.get_text(" ")) for cell in row.select("td")]
        if len(cells) >= 2:
            rows.append({"label": cells[0], "value": cells[1]})
    return rows


def parse_quote_html(
    html: str,
    source_url: str,
    requested_move_in_date: str | None = None,
    requested_lease_term: int | None = None,
) -> dict[str, Any]:
    """Parse a selected Eden SecureCafe date/term quote."""

    soup = BeautifulSoup(html, "html.parser")
    pricing = _pricing_data(html)
    selected_iso = _iso_date(requested_move_in_date) if requested_move_in_date else None
    selected_key = _provider_date(selected_iso) if selected_iso else _hidden_value(soup, "dtMIDateSel")
    if not selected_key:
        selected_key = next(iter(pricing), None)
    if selected_key not in pricing:
        raise EdenAdapterError(f"The Eden quote page returned no pricing for {selected_key}")
    date_prices = pricing[selected_key]
    term_text = str(requested_lease_term or _hidden_value(soup, "sLeaseTerm") or "")
    if term_text not in date_prices:
        raise EdenAdapterError(f"The Eden quote page returned no {term_text}-month quote for {selected_key}")
    selected = date_prices[term_text]
    base_rent = _as_float(selected.get("price"))
    recurring_addons = sum(
        value or 0.0
        for value in (
            _as_float(_hidden_value(soup, "RentableItemTypesRent")),
            _as_float(_hidden_value(soup, "MonthlyOtherRent")),
            _as_float(selected.get("Tax")),
        )
    )
    total_hidden = _as_float(_hidden_value(soup, "TotalRent"))
    selected_date_iso = _iso_date(_hidden_value(soup, "dtMIDateSel") or selected_key)
    return {
        "provider": "greystar",
        "building_id": BUILDING_ID,
        "building_name": BUILDING_NAME,
        "source_url": source_url,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "status": "ok",
        "unit_id": _hidden_value(soup, "ApartmentNumber") or _hidden_value(soup, "UnitNumber"),
        "provider_unit_id": _hidden_value(soup, "UnitId"),
        "floor_plan_id": _hidden_value(soup, "FloorplanId"),
        "move_in_date": selected_date_iso,
        "lease_term_months": int(term_text),
        "base_rent": base_rent,
        "recurring_addons": round(recurring_addons, 2),
        "total_monthly_price": round(total_hidden if total_hidden is not None else (base_rent or 0) + recurring_addons, 2),
        "application_cost": _as_float(_hidden_value(soup, "ApplicationFee")),
        "fee_rows": _quote_fee_rows(soup),
        "term_quotes": [
            {
                "lease_term_months": int(key),
                "base_rent": _as_float(value.get("price")),
                "emc_base_rent": _as_float(value.get("EMCBasePrice")),
                "special_amount": _as_float(value.get("SpecialAmount")),
                "tax": _as_float(value.get("Tax")),
            }
            for key, value in date_prices.items()
            if str(key).isdigit() and isinstance(value, dict)
        ],
        "raw_pricing_data": selected,
        "warnings": [
            "Quote is read-only and does not submit an application.",
            "Total Monthly Leasing Price includes fees exposed by SecureCafe; optional selections and required move-in/move-out charges may be excluded.",
        ],
    }


def _extract_json_script(page_html: str) -> dict[str, Any]:
    pattern = re.compile(
        r'<script[^>]*id=["\']jd-fp-data-script-app["\'][^>]*>(.*?)</script>',
        re.IGNORECASE | re.DOTALL,
    )
    match = pattern.search(page_html)
    if not match:
        raise EdenAdapterError("The Eden floorplans page has no embedded inventory JSON")
    try:
        payload = json.loads(html_lib.unescape(match.group(1).strip()))
    except json.JSONDecodeError as exc:
        raise EdenAdapterError("The Eden embedded inventory JSON is invalid") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("units"), list):
        raise EdenAdapterError("The Eden inventory JSON has no unit list")
    return payload


def _term_base_rent(term: dict[str, Any]) -> float | None:
    for key in ("termOptionDisplay", "priceDisplay", "priceDisplayNoFees"):
        match = re.search(
            r"\(\s*\$?\s*([0-9][0-9,]*(?:\.\d{1,2})?)\s+Base Rent",
            _clean(term.get(key)), re.I,
        )
        if match:
            return _money(match.group(1))
    return None


def _lease_term(term: dict[str, Any]) -> dict[str, Any]:
    return {
        "months": _integer(term.get("term")),
        "price_low": _money(term.get("priceLow")),
        "price_high": _money(term.get("priceHigh")),
        "price_display": term.get("priceDisplay"),
        "price_display_no_fees": term.get("priceDisplayNoFees"),
        "base_rent_reference": _term_base_rent(term),
        "pricing_reflects_fees": term.get("pricingReflectFees"),
        "available_date": term.get("date"),
    }


def _selected_term(unit: dict[str, Any], terms: list[dict[str, Any]]) -> dict[str, Any] | None:
    origin = str(unit.get("price_term_origin") or "").strip()
    for term in terms:
        if origin and str(term.get("term")) == origin:
            return term
    return terms[0] if terms else None


def _unit(raw: dict[str, Any], application_url: str | None) -> dict[str, Any]:
    apartment = _clean(raw.get("apartment_number") or raw.get("unit_number"))
    apartment = apartment[1:].strip() if apartment.startswith("#") else apartment
    if not apartment:
        raise EdenAdapterError("The Eden inventory unit has no resident-facing apartment number")
    total_min = _money(raw.get("rent_min") or raw.get("price"))
    total_max = _money(raw.get("rent_max") or raw.get("price"))
    raw_terms = [term for term in (raw.get("lease_terms") or []) if isinstance(term, dict)]
    terms = [_lease_term(term) for term in raw_terms]
    selected = _selected_term(raw, raw_terms)
    base_reference = _term_base_rent(selected or {})
    if base_reference is None and selected:
        base_reference = _money(selected.get("priceLow"))
    beds = _beds(raw.get("bedrooms"))
    available_now = _clean(raw.get("available_display")).lower() in {"available now", "now"}
    available_date = _date_from_timestamp(raw.get("available_date"))
    floor_plan = _clean(raw.get("floorplan_title") or raw.get("title")) or None
    return {
        "unit_id": apartment,
        "provider_unit_id": str(raw.get("id_value") or raw.get("id") or apartment),
        "resident_facing_unit": apartment,
        "floor_plan": floor_plan,
        "category": "Studio" if beds == 0 else f"{beds} Bedroom",
        "beds": beds,
        "baths": _money(raw.get("bathrooms")),
        "sqft": _integer(raw.get("square_feet")),
        # Existing MVP price columns hold the selected/base-rent reference;
        # retain the official Total Monthly Leasing Price separately.
        "base_rent_min": base_reference if base_reference is not None else total_min,
        "base_rent_max": base_reference if base_reference is not None else total_max,
        "base_rent_reference_min": base_reference,
        "base_rent_reference_max": base_reference,
        "total_monthly_leasing_price_min": total_min,
        "total_monthly_leasing_price_max": total_max,
        "pricing_status": "priced" if total_min is not None else "call_for_pricing",
        "pricing_basis": "official_total_monthly_leasing_price_with_base_rent_reference",
        "lease_term_months": _integer(raw.get("price_term_origin")),
        "lease_terms": terms,
        "available_date": available_date,
        "available_now": available_now,
        "available_display": raw.get("available_display"),
        "status": "available" if available_now else "future",
        "application_url": application_url,
        "detail_url": urljoin(FLOORPLANS_URL, raw.get("permalink") or ""),
        "provider_payload": {
            "unit_record_id": raw.get("id"),
            "unit_record_id_value": raw.get("id_value"),
            "property_id": raw.get("property_id"),
            "floorplan_id": raw.get("floorplan_id"),
            "floorplan_slug": raw.get("slug"),
            "permalink": raw.get("permalink"),
            "specials": raw.get("specials") or [],
            "price_itemized": raw.get("price_itemized") or {},
        },
    }


def parse_floorplans_html(page_html: str, source_url: str = FLOORPLANS_URL) -> dict[str, Any]:
    payload = _extract_json_script(page_html)
    settings = payload.get("config", {}).get("settings", {})
    application_url = settings.get("lease_url") or settings.get("apply_url")
    units = [_unit(raw, application_url) for raw in payload["units"] if isinstance(raw, dict)]
    if not units:
        raise EdenAdapterError("The Eden inventory JSON returned zero units; refusing an empty scan")

    pet_policy = None
    pet_match = re.search(r"we welcome both cats and dogs.*?(?=</p>|\n|$)", page_html, re.I | re.S)
    if pet_match:
        pet_policy = _clean(re.sub(r"<[^>]+>", " ", pet_match.group(0)))
    concessions: list[str] = []
    for match in re.finditer(r"[^<]{0,120}(?:weeks?\s+free|look\s*&?\s*lease)[^<]{0,240}", page_html, re.I):
        value = _clean(re.sub(r"<[^>]+>", " ", match.group(0)))
        if value and value not in concessions:
            concessions.append(value)
    fees: list[dict[str, Any]] = []
    if pet_policy:
        fees.append({"type": "pet_policy", "text": pet_policy, "source": source_url})
    fees.append({
        "type": "pricing_disclaimer",
        "text": "Total Monthly Leasing Price includes base rent, mandatory monthly fees and selected optional fees; variable/usage-based fees and required move-in or move-out charges may be excluded.",
        "source": source_url,
    })
    return {
        "provider": "greystar",
        "building_id": BUILDING_ID,
        "building_name": BUILDING_NAME,
        "source_url": source_url,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "status": "ok",
        "units": units,
        "policies": {"fees": fees, "concessions": concessions},
        "contact": {"phone": PHONE, "website": OFFICIAL_URL},
        "floorplans_count": len(payload.get("floorplans") or []),
        "units_count": len(units),
        "warnings": [
            "Eden card prices are the official Total Monthly Leasing Price; base-rent references and all lease-term quotes are preserved per unit.",
            "Prices, availability, fees, and promotions can change without notice.",
            "No current public concession banner was detected on the official floorplans page during this scan." if not concessions else "Concessions are preserved as page text and should be rechecked before quoting a client.",
        ],
    }


class EdenAdapter:
    """Fetch current The Eden inventory from its official property site."""

    def __init__(self, timeout_seconds: int = 30, user_agent: str | None = None):
        self.timeout_seconds = timeout_seconds
        self.user_agent = user_agent or (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 Chrome/141.0 Safari/537.36"
        )

    def fetch_html(self, url: str = FLOORPLANS_URL) -> str:
        request = Request(url, headers={"User-Agent": self.user_agent, "Accept": "text/html,application/xhtml+xml"})
        with urlopen(request, timeout=self.timeout_seconds) as response:
            if response.status != 200:
                raise EdenAdapterError(f"The Eden official page returned HTTP {response.status}")
            if response.headers.get_content_type() not in {"text/html", "application/xhtml+xml"}:
                raise EdenAdapterError("The Eden official page did not return HTML")
            return response.read().decode(response.headers.get_content_charset() or "utf-8", "replace")

    def _request_html(
        self,
        url: str,
        *,
        referer: str | None = None,
        origin: str | None = None,
        session: Any = None,
    ) -> str:
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "X-Requested-With": "XMLHttpRequest",
        }
        if referer:
            headers["Referer"] = referer
        if origin:
            headers["Origin"] = origin
        request = Request(url, headers=headers)
        opener = session or build_opener()
        with opener.open(request, timeout=self.timeout_seconds) as response:
            if response.status != 200:
                raise EdenAdapterError(f"The Eden official page returned HTTP {response.status}")
            return response.read().decode(response.headers.get_content_charset() or "utf-8", "replace")

    @staticmethod
    def _detail_url(unit_slug: str) -> str:
        instance = hashlib.md5("/floorplans/".encode()).hexdigest()
        route = (
            f"params:instance={instance}&action=render&type=details&resource=unit"
            f"&id={unit_slug}&omitTourFormRender=1"
        )
        return urljoin(FLOORPLANS_URL, f"{RENDERABLE_ENDPOINT}/{quote(route, safe='')}/?forcecache=1")

    def fetch_unit_detail(self, unit_slug: str) -> dict[str, Any]:
        """Fetch one renderable unit detail fragment; this never writes SQLite."""

        session = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
        # Establish the first-party session before following the AJAX-like detail request.
        self._request_html(FLOORPLANS_URL, session=session)
        # The public unit route contains the same structured unit-data block as
        # the modal request, and is more reliable for binding the quote to the
        # requested apartment when the provider's modal cache is stale.
        detail_page_url = urljoin(FLOORPLANS_URL, f"unit-{unit_slug}/")
        html = self._request_html(detail_page_url, referer=FLOORPLANS_URL, session=session)
        try:
            return parse_unit_detail_html(html, detail_page_url)
        except EdenAdapterError:
            # Keep the browser-equivalent modal request as a provider fallback.
            detail_url = self._detail_url(unit_slug)
            fragment = self._request_html(detail_url, referer=detail_page_url, session=session)
            return parse_unit_detail_html(fragment, detail_url)

    def fetch_unit_quote(
        self,
        unit_slug: str,
        move_in_date: str,
        lease_term_months: int,
    ) -> dict[str, Any]:
        """Fetch one official Eden date/term quote without persistence."""

        if lease_term_months <= 0:
            raise EdenAdapterError("Lease term must be a positive number of months")
        iso_date = _iso_date(move_in_date)
        detail = self.fetch_unit_detail(unit_slug)
        lease_link = detail["lease_link_data"].get("lease_link")
        if not lease_link:
            raise EdenAdapterError("The Eden unit has no SecureCafe lease-link template")
        provider_date = _provider_date(iso_date)
        quote_url = lease_link.replace("{date}", provider_date).replace("{term}", str(lease_term_months))
        parsed = urlparse(quote_url)
        session = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self._request_html(FLOORPLANS_URL, session=session)
        # A fresh session is intentional: the SecureCafe handoff is a separate
        # first-party host, and the referer identifies the official Eden flow.
        try:
            html = self._request_html(
                quote_url,
                referer=detail["source_url"],
                origin="https://edendtla.com",
                session=session,
            )
        except Exception as exc:
            raise EdenAdapterError(f"The Eden SecureCafe quote page could not be fetched: {exc}") from exc
        result = parse_quote_html(html, quote_url, iso_date, lease_term_months)
        result["unit_id"] = result.get("unit_id") or detail.get("unit_id")
        result["provider_unit_id"] = result.get("provider_unit_id") or detail.get("provider_unit_id")
        result["floor_plan"] = detail.get("floor_plan")
        result["detail_url"] = detail["source_url"]
        result["requested_unit_slug"] = unit_slug
        result["lease_link_data"] = detail["lease_link_data"]
        return result

    def fetch(self) -> dict[str, Any]:
        return parse_floorplans_html(self.fetch_html(), FLOORPLANS_URL)
