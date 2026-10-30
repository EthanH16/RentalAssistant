"""Read-only Beaudry inventory adapter.

Beaudry's current public site is operated on LyvWell's WordPress front end.
The page loads inventory from LyvWell's public middleware endpoint.  The
endpoint includes resident-facing apartment numbers, floor plans, total
monthly leasing prices, base-rent references, specials, and fee metadata.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
import json
import re
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup


class BeaudryAdapterError(RuntimeError):
    """Raised when Beaudry's public inventory response is not trustworthy."""


BUILDING_ID = "beaudry"
AVAILABILITY_URL = "https://lyvwellapartments.com/our-communities/beaudry/availabilities/"
FEE_GUIDE_URL = "https://lyvwellapartments.com/our-communities/beaudry/fee-guide/"
API_URL = "https://lyvwellapartments.com/wp-json/middleware/v1/getUnits/"
PROPERTY_ID = "1782290"
APPLICATION_URL = (
    "https://beaudrydtla.securecafe.com/onlineleasing/beaudry/"
    "guestlogin.aspx?ssid=5d3d8eaaa53a26ee950dd6a5627ead26"
)


def _money(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        match = re.search(r"[0-9][0-9,]*(?:\.\d+)?", str(value))
        return float(match.group(0).replace(",", "")) if match else None


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _date(value: Any) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def _iso_date(value: str) -> str:
    """Normalize the date formats accepted by the Beaudry quote page."""

    normalized = _date(value)
    if not normalized:
        raise BeaudryAdapterError("Move-in date must be YYYY-MM-DD or MM/DD/YYYY")
    return normalized


def _display_date(value: str) -> str:
    return datetime.strptime(_iso_date(value), "%Y-%m-%d").strftime("%-m/%-d/%Y")


def _hidden_value(soup: BeautifulSoup, element_id: str) -> str | None:
    element = soup.select_one(f"#{element_id}")
    return element.get("value") if element else None


def _query_value(url: str, key: str) -> str | None:
    values = parse_qs(urlparse(url).query).get(key)
    return values[0] if values else None


def _pricing_data(html: str) -> dict[str, Any]:
    match = re.search(r"var\s+PricingData\s*=\s*(\{.*?\});", html, re.S)
    if not match:
        raise BeaudryAdapterError("Beaudry quote page has no structured PricingData")
    try:
        value = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise BeaudryAdapterError("Beaudry quote PricingData is invalid JSON") from exc
    if not isinstance(value, dict):
        raise BeaudryAdapterError("Beaudry quote PricingData has an unexpected shape")
    return value


def _as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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


def parse_beaudry_quote_html(
    html: str,
    source_url: str,
    requested_move_in_date: str,
    requested_lease_term: int,
) -> dict[str, Any]:
    """Parse one official SecureCafe date/term quote without submitting an application."""

    if requested_lease_term <= 0:
        raise BeaudryAdapterError("Lease term must be a positive number of months")
    soup = BeautifulSoup(html, "html.parser")
    pricing = _pricing_data(html)
    requested_iso = _iso_date(requested_move_in_date)
    requested_provider_date = _display_date(requested_iso)

    # SecureCafe sometimes preserves a zero-padded date key; compare by date
    # rather than assuming one exact formatting variant.
    selected_key = next(
        (key for key in pricing if _date(key) == requested_iso),
        _hidden_value(soup, "dtMIDateSel") or _hidden_value(soup, "MoveInDate") or requested_provider_date,
    )
    if selected_key not in pricing:
        raise BeaudryAdapterError(f"Beaudry returned no pricing for move-in date {requested_iso}")
    date_prices = pricing[selected_key]
    term_text = str(requested_lease_term)
    if term_text not in date_prices or not isinstance(date_prices[term_text], dict):
        raise BeaudryAdapterError(
            f"Beaudry returned no {requested_lease_term}-month quote for {selected_key}"
        )
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
    selected_date = _date(_hidden_value(soup, "dtMIDateSel") or selected_key) or requested_iso
    specials = sorted({
        _clean(value)
        for value in re.findall(r"(?:\d+\s+Months?\s+Free[^<.]*|Special Offer!?[^<]*)", html, re.I)
        if _clean(value)
    })
    total = total_hidden if total_hidden is not None else ((base_rent or 0.0) + recurring_addons)
    return {
        "provider": "brookfield_securecafe",
        "building_id": BUILDING_ID,
        "building_name": "Beaudry",
        "source_url": source_url,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "status": "ok",
        "unit_id": _hidden_value(soup, "ApartmentNumber") or _hidden_value(soup, "UnitNumber") or _query_value(source_url, "UnitID"),
        "provider_unit_id": _hidden_value(soup, "UnitId") or _query_value(source_url, "UnitID"),
        "floor_plan_id": _hidden_value(soup, "FloorplanId") or _query_value(source_url, "FloorPlanID"),
        "move_in_date": selected_date,
        "lease_term_months": int(term_text),
        "base_rent": base_rent,
        "recurring_addons": round(recurring_addons, 2),
        "total_monthly_price": round(total, 2),
        "application_cost": _as_float(_hidden_value(soup, "ApplicationFee")),
        "fee_rows": _quote_fee_rows(soup),
        "concessions": specials,
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
            "Total Monthly Leasing Price includes mandatory monthly fees exposed by SecureCafe; optional pet/parking charges and variable utilities may be excluded.",
            "Concession text is preserved separately from rent and must be checked for move-in-date, lease-term, and unit eligibility.",
        ],
    }


def _available_now(available_date: str | None) -> bool:
    if not available_date:
        return False
    try:
        return date.fromisoformat(available_date) <= date.today()
    except ValueError:
        return False


def _fee_records(raw: Any) -> list[dict[str, Any]]:
    if not raw:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    if not isinstance(raw, list):
        return []
    records: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        records.append({
            "name": item.get("feeName") or item.get("feeDescription"),
            "amount": item.get("feeCost"),
            "amount_max": item.get("feeCostMax") or None,
            "type": item.get("feeFrequency") or item.get("feeTiming"),
            "timing": item.get("feeTiming"),
            "required": bool(item.get("isRequired")),
            "charge_type": item.get("feeChargeType"),
            "refundable": item.get("isRefundable"),
            "per_applicant": item.get("isAppliedPerApplicant"),
        })
    return records


def _dedupe_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for record in records:
        key = json.dumps(record, sort_keys=True, ensure_ascii=False)
        if key not in seen:
            seen.add(key)
            result.append(record)
    return result


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def parse_fee_guide_html(html: str, source_url: str = FEE_GUIDE_URL) -> dict[str, Any]:
    """Parse Beaudry's official fee guide, including collapsed sections.

    The page renders both desktop and mobile copies of each table.  The
    desktop tables are used as the canonical copy so fees are not duplicated.
    """

    soup = BeautifulSoup(html, "html.parser")
    wrapper = None
    for candidate in soup.select(".fee-guide-wrapper"):
        heading = _clean(candidate.find(["h2", "h3", "h4", "h5"]).get_text(" ") if candidate.find(["h2", "h3", "h4", "h5"]) else "")
        if "beaudry" in heading.lower():
            wrapper = candidate
            break
    if wrapper is None:
        raise BeaudryAdapterError("Beaudry fee guide section was not found")

    fees: list[dict[str, Any]] = []
    sections: list[str] = []
    for table in wrapper.select("table.desktop-fee-table"):
        section_item = table.find_parent(class_="fee-item")
        section = _clean(section_item.find(["h2", "h3", "h4", "h5"]).get_text(" ") if section_item and section_item.find(["h2", "h3", "h4", "h5"]) else "")
        if section and section not in sections:
            sections.append(section)
        for row in table.select("tbody tr"):
            cells = row.select("td")
            if len(cells) < 3:
                continue
            raw_name = _clean(cells[0].get_text(" "))
            if not raw_name:
                continue
            has_footnote = raw_name.endswith("*")
            name = raw_name.rstrip("*").strip()
            frequency = _clean(cells[1].get_text(" "))
            amount = _clean(cells[2].get_text(" "))
            included = bool(cells[3].select_one(".check-circle")) if len(cells) >= 4 else False
            fees.append({
                "name": name,
                "amount": amount,
                "type": frequency,
                "timing": frequency,
                "category": section or None,
                "required": section != "Optional Fees",
                "included_in_total_monthly_leasing_price": included,
                "has_footnote": has_footnote,
                "source": "official_fee_guide",
            })

    if not fees:
        raise BeaudryAdapterError("Beaudry fee guide contained no fee rows")
    description = _clean(wrapper.select_one(".fee-guide-description").get_text(" ") if wrapper.select_one(".fee-guide-description") else "")
    return {
        "status": "parsed",
        "source_url": source_url,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "sections": sections,
        "fees": _dedupe_records(fees),
        "description": description,
        "ground_truth": True,
    }


def _unit(raw: dict[str, Any]) -> dict[str, Any]:
    apartment = str(raw.get("apartmentName") or raw.get("apartmentId") or "").strip()
    if not apartment:
        raise BeaudryAdapterError("Beaudry unit is missing its apartment number")
    available_date = _date(raw.get("availableDate"))
    floor_plan = str(raw.get("floorplanName") or raw.get("floorplanCode") or "").strip() or None
    minimum = _money(raw.get("minimumRent") or raw.get("min_price"))
    maximum = _money(raw.get("maximumRent") or raw.get("max_price"))
    if minimum is None:
        raise BeaudryAdapterError(f"Beaudry unit {apartment} is missing price")
    detail_url = (
        f"https://lyvwellapartments.com/our-communities/beaudry/availabilities/unit/?"
        f"propertyId%5B%5D={PROPERTY_ID}&floorplanId={raw.get('floorplanId')}&unitId={raw.get('apartmentId')}"
    )
    return {
        # Resident-facing apartment number, not RentCafe's internal id.
        "unit_id": apartment,
        "provider_unit_id": str(raw.get("apartmentId") or apartment),
        "resident_facing_unit": apartment,
        "floor_plan": floor_plan,
        "category": "Studio" if str(raw.get("beds")) == "0" else f"{raw.get('beds')} Bedroom",
        "beds": _int(raw.get("beds")),
        "baths": _money(raw.get("baths")),
        "sqft": _int(raw.get("sqft")),
        # LyvWell's minimumRent/maximumRent are the displayed total monthly
        # leasing prices.  originalMinRent/originalMaxRent are base-rent refs.
        "base_rent_min": minimum,
        "base_rent_max": maximum,
        "base_rent_reference_min": _money(raw.get("originalMinRent")),
        "base_rent_reference_max": _money(raw.get("originalMaxRent")),
        "available_date": available_date,
        "available_now": _available_now(available_date),
        "status": "available",
        "application_url": raw.get("applyOnlineURL") or APPLICATION_URL,
        "detail_url": detail_url,
        "amenities": raw.get("amenities") or [],
        "special": raw.get("unit_specials"),
        "fees": _fee_records(raw.get("unitFeeDetails")),
        "required_monthly_fee_reference": _money(raw.get("unitAdditionalFee")),
        "provider_payload": {
            "property_id": raw.get("propertyId"),
            "floorplan_id": raw.get("floorplanId"),
            "floorplan_code": raw.get("floorplanCode"),
            "concession_amount": raw.get("concession_amount"),
            "concession_details": raw.get("concession_details"),
        },
    }


class BeaudryAdapter:
    """Fetch current Beaudry inventory from the official LyvWell endpoint."""

    def __init__(self, timeout_seconds: int = 30, user_agent: str | None = None):
        self.timeout_seconds = timeout_seconds
        self.user_agent = user_agent or (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 Chrome/141.0 Safari/537.36"
        )

    def fetch_json(self, url: str) -> Any:
        request = Request(
            url,
            headers={"User-Agent": self.user_agent, "Accept": "application/json,text/plain,*/*"},
        )
        with urlopen(request, timeout=self.timeout_seconds) as response:
            if response.status != 200:
                raise BeaudryAdapterError(f"Beaudry inventory endpoint returned HTTP {response.status}")
            try:
                return json.loads(response.read().decode(response.headers.get_content_charset() or "utf-8"))
            except json.JSONDecodeError as exc:
                raise BeaudryAdapterError("Beaudry inventory endpoint did not return JSON") from exc

    def fetch_html(self, url: str) -> str:
        request = Request(
            url,
            headers={"User-Agent": self.user_agent, "Accept": "text/html,application/xhtml+xml,*/*"},
        )
        with urlopen(request, timeout=self.timeout_seconds) as response:
            if response.status != 200:
                raise BeaudryAdapterError(f"Beaudry fee guide returned HTTP {response.status}")
            return response.read().decode(response.headers.get_content_charset() or "utf-8", errors="replace")

    def fetch_fee_guide(self) -> dict[str, Any]:
        return parse_fee_guide_html(self.fetch_html(FEE_GUIDE_URL))

    def fetch_unit_quote(
        self,
        unit_number: str,
        move_in_date: str,
        lease_term_months: int,
    ) -> dict[str, Any]:
        """Fetch one official Beaudry date/term quote without persistence."""

        if lease_term_months <= 0:
            raise BeaudryAdapterError("Lease term must be a positive number of months")
        requested_iso = _iso_date(move_in_date)
        inventory = self.fetch()
        unit = next((item for item in inventory["units"] if item["unit_id"] == str(unit_number).strip()), None)
        if unit is None:
            raise BeaudryAdapterError(f"Beaudry unit {unit_number} is not in the current official inventory")
        application_url = unit.get("application_url")
        if not application_url:
            raise BeaudryAdapterError(f"Beaudry unit {unit_number} has no official quote URL")
        parsed = urlparse(application_url)
        query = parse_qs(parsed.query)
        # Beaudry/SecureCafe uses MoveInDate for the selected card and
        # sLeaseTerm for the slider. sMoveInDate alone does not update the
        # current selection.
        query["MoveInDate"] = [_display_date(requested_iso)]
        query["sLeaseTerm"] = [str(lease_term_months)]
        quote_url = urlunparse(parsed._replace(query=urlencode(query, doseq=True)))
        html = self.fetch_html(quote_url)
        result = parse_beaudry_quote_html(html, quote_url, requested_iso, lease_term_months)
        result["unit_id"] = result.get("unit_id") or unit["unit_id"]
        result["provider_unit_id"] = result.get("provider_unit_id") or unit.get("provider_unit_id")
        result["floor_plan"] = unit.get("floor_plan")
        result["category"] = unit.get("category")
        result["sqft"] = unit.get("sqft")
        result["detail_url"] = unit.get("detail_url")
        result["advertised_unit"] = unit
        result["official_fee_guide_url"] = FEE_GUIDE_URL
        return result

    def fetch(self) -> dict[str, Any]:
        query = urlencode([
            ("propertyId[]", PROPERTY_ID),
            ("has_availability", "true"),
            ("order", "ASC"),
        ])
        endpoint = f"{API_URL}?{query}"
        payload = self.fetch_json(endpoint)
        if not isinstance(payload, list) or not payload:
            code = payload.get("code") if isinstance(payload, dict) else None
            raise BeaudryAdapterError(f"Beaudry returned no usable inventory ({code or 'empty response'})")

        units = [_unit(item) for item in payload if isinstance(item, dict)]
        if not units:
            raise BeaudryAdapterError("Beaudry returned no recognizable units")
        if len({u["unit_id"] for u in units}) != len(units):
            raise BeaudryAdapterError("Beaudry returned duplicate resident-facing apartment numbers")

        inventory_fees = _dedupe_records([fee for unit in units for fee in unit.pop("fees", [])])
        concessions = sorted({u.pop("special") for u in units if u.get("special")})
        required_monthly = sorted({u.pop("required_monthly_fee_reference") for u in units if u.get("required_monthly_fee_reference") is not None})
        base_references = {
            "min": min((u.pop("base_rent_reference_min") for u in units if u.get("base_rent_reference_min") is not None), default=None),
            "max": max((u.pop("base_rent_reference_max") for u in units if u.get("base_rent_reference_max") is not None), default=None),
        }
        for unit in units:
            unit["detail_checked"] = False

        fee_guide_error = None
        try:
            fee_guide = self.fetch_fee_guide()
            fees = fee_guide["fees"]
        except Exception as exc:
            # Do not silently downgrade to the inventory API's partial fee
            # metadata.  The official fee guide is Beaudry's fee ground truth.
            fee_guide = {
                "status": "fetch_failed",
                "source_url": FEE_GUIDE_URL,
                "ground_truth": True,
                "fees": [],
                "error": str(exc),
            }
            fee_guide_error = str(exc)
            fees = []

        return {
            "provider": "brookfield",
            "site_brand": "LyvWell Apartments",
            "building_id": BUILDING_ID,
            "building_name": "Beaudry",
            "source_url": AVAILABILITY_URL,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "status": "ok",
            "units": units,
            "policies": {
                "fees": fees,
                "inventory_api_fees": inventory_fees,
                "fee_guide": fee_guide,
                "concessions": concessions,
                "required_monthly_fee_reference": required_monthly,
                "base_rent_reference": base_references,
                "pricing_note": "minimumRent/maximumRent are the displayed Total Monthly Leasing Price; originalMinRent/originalMaxRent are base-rent references.",
            },
            "contact": {
                "address": "960 W. 7th Street, Los Angeles, CA 90017-6042",
                "phone": "213-583-0709",
                "office_hours": "Monday-Sunday 9:00 AM-6:00 PM",
                "application_url": APPLICATION_URL,
            },
            "endpoint": endpoint,
            "warnings": ([f"Official Beaudry fee guide unavailable: {fee_guide_error}"] if fee_guide_error else []) + [
                "Availability, pricing, fees, and specials can change without notice.",
                "The official site states that optional pet/parking charges and variable utilities are not included in Total Monthly Leasing Price.",
            ],
        }
