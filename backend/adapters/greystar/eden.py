"""Read-only adapter for The Eden's official Greystar property site."""

from __future__ import annotations

from datetime import datetime, timezone
import html as html_lib
import json
import re
from typing import Any
from urllib.parse import urljoin
from urllib.request import Request, urlopen

BUILDING_ID = "the-eden"
BUILDING_NAME = "The Eden"
OFFICIAL_URL = "https://edendtla.com/"
FLOORPLANS_URL = "https://edendtla.com/floorplans/"
PHONE = "877-680-8826"


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

    def fetch(self) -> dict[str, Any]:
        return parse_floorplans_html(self.fetch_html(), FLOORPLANS_URL)
