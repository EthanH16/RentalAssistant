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
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class BeaudryAdapterError(RuntimeError):
    """Raised when Beaudry's public inventory response is not trustworthy."""


BUILDING_ID = "beaudry"
AVAILABILITY_URL = "https://lyvwellapartments.com/our-communities/beaudry/availabilities/"
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

        fees = _dedupe_records([fee for unit in units for fee in unit.pop("fees", [])])
        concessions = sorted({u.pop("special") for u in units if u.get("special")})
        required_monthly = sorted({u.pop("required_monthly_fee_reference") for u in units if u.get("required_monthly_fee_reference") is not None})
        base_references = {
            "min": min((u.pop("base_rent_reference_min") for u in units if u.get("base_rent_reference_min") is not None), default=None),
            "max": max((u.pop("base_rent_reference_max") for u in units if u.get("base_rent_reference_max") is not None), default=None),
        }
        for unit in units:
            unit["detail_checked"] = False

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
            "warnings": [
                "Availability, pricing, fees, and specials can change without notice.",
                "The official site states that optional pet/parking charges and variable utilities are not included in Total Monthly Leasing Price.",
            ],
        }
