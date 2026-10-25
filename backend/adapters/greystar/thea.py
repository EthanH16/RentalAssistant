"""Read-only adapter for THEA at Metropolis (Greystar Spaces/Engrain)."""

from __future__ import annotations

from datetime import date, datetime, timezone
import html as html_lib
import json
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup
from sqids import Sqids

from .eden import parse_fee_guide_payload


BUILDING_ID = "thea"
BUILDING_NAME = "Thea"
OFFICIAL_URL = "https://theadtla.com/"
FLOORPLANS_URL = "https://theadtla.com/floor-plan/"
AJAX_URL = "https://theadtla.com/wp-admin/admin-ajax.php"
SIGHTMAP_URL = "https://sightmap.com/embed/r5v51135wny"
SPACE_ASSET_ID = "4778"
ENGRAIN_ASSET_ID = "29033"
EXPENSE_GUIDE_API = "https://greystar.expenseguide.com/api/engrain/v1/assets/{asset_id}/expenses"
SQIDS_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"


class TheaAdapterError(RuntimeError):
    pass


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _money(value: Any) -> float | None:
    match = re.search(r"\$?\s*([0-9][0-9,]*(?:\.\d{1,2})?)", str(value or ""))
    return float(match.group(1).replace(",", "")) if match else None


def _int(value: Any) -> int | None:
    try:
        return int(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _beds(value: Any, text: str = "") -> int | None:
    value = _clean(value).lower()
    text = _clean(text).lower()
    if "studio" in value or "studio" in text:
        return 0
    match = re.search(r"(\d+)\s*(?:bed|br|bedroom)", value or text)
    return int(match.group(1)) if match else None


def _date(value: Any, text: str = "") -> str | None:
    candidate = _clean(value) or _clean(text)
    for pattern in (r"\d{4}-\d{2}-\d{2}", r"\d{1,2}/\d{1,2}/\d{4}", r"\b[A-Z][a-z]{2}\s+\d{1,2},\s+\d{4}"):
        match = re.search(pattern, candidate)
        if not match:
            continue
        raw = match.group(0)
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%B %d, %Y", "%b %d, %Y"):
            try:
                return datetime.strptime(raw, fmt).date().isoformat()
            except ValueError:
                pass
    return None


def _timestamp_date(value: Any) -> str | None:
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc).date().isoformat()
    except (TypeError, ValueError, OSError):
        return None


def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        value = _clean(value)
        if value and value.lower() not in seen:
            result.append(value)
            seen.add(value.lower())
    return result


def _parse_config(page_html: str) -> dict[str, Any]:
    def value(name: str) -> str | None:
        match = re.search(rf"\b{re.escape(name)}\s*:\s*([\"'])(.*?)\1", page_html, re.S)
        return match.group(2) if match else None

    return {
        "spaces_asset_id": value("spaces_asset_id") or SPACE_ASSET_ID,
        "engrain_asset_id": value("engrain_asset_id") or ENGRAIN_ASSET_ID,
        "apply_provider": value("apply_provider") or "rentcafe",
        "sightmap_url": value("sightmap_url") or SIGHTMAP_URL,
    }


def _promotion_text(page_html: str) -> list[str]:
    soup = BeautifulSoup(page_html, "html.parser")
    text = _clean(soup.get_text(" "))
    values: list[str] = []
    patterns = (
        r"Up to\s+8\s+Weeks\s+Free\s*\+\s*Up to\s*\$?1,?000\s*Look\s*(?:&|and)\s*Lease\s*Bonus[^.]*\.?",
        r"Includes\s+a\s*\$?500\s*Look\s*(?:&|and)\s*Lease\s*Bonus[^.]*\.?",
        r"Short-term leases\s*\([^)]*\)\s*available\.?",
    )
    for pattern in patterns:
        values.extend(match.group(0) for match in re.finditer(pattern, text, re.I))
    return _unique(values)


def _plan_price(text: str) -> tuple[float | None, float | None]:
    prices = [_money(match) for match in re.findall(r"\$\s*[0-9][0-9,]*(?:\.\d{1,2})?", text)]
    prices = [price for price in prices if price is not None]
    return (prices[0] if prices else None, prices[1] if len(prices) > 1 else prices[0] if prices else None)


def parse_plan_cards(fragment: str, source_url: str = FLOORPLANS_URL) -> list[dict[str, Any]]:
    soup = BeautifulSoup(fragment, "html.parser")
    cards = soup.select("[data-spaces-plan]")
    plans: list[dict[str, Any]] = []
    seen: set[str] = set()
    for card in cards:
        plan_id = _clean(card.get("data-spaces-plan"))
        if not plan_id or plan_id in seen:
            continue
        seen.add(plan_id)
        text = _clean(card.get_text(" "))
        plan_name = _clean(card.get("data-spaces-plan-name")) or _clean(
            (card.select_one("[data-spaces-plan-title], h2, h3, h4") or card).get_text(" ")
        )
        total, base = _plan_price(text)
        match = re.search(r"(\d+)\s+Units?\s+Available", text, re.I)
        plans.append({
            "plan_id": plan_id,
            "plan_name": plan_name,
            "beds": _beds(card.get("data-spaces-bed-count") or card.get("data-spaces-sort-bed"), text),
            "baths": _money(card.get("data-spaces-bath-count") or card.get("data-spaces-sort-bath")),
            "sqft": _int(card.get("data-spaces-area") or card.get("data-spaces-sort-area")),
            "available_count": int(match.group(1)) if match else None,
            "available_date": _date(card.get("data-spaces-date") or card.get("data-spaces-soonest"), text) or _timestamp_date(card.get("data-spaces-sort-date")),
            "total_monthly_price": total,
            "base_rent": base,
            "has_special": bool(re.search(r"has\s+special", text, re.I) or card.get("data-spaces-has-special")),
            "source_url": source_url,
            "provider_payload": {key: value for key, value in card.attrs.items() if str(key).startswith("data-spaces-")},
        })
    if not plans:
        raise TheaAdapterError("Thea plan response contained no plan cards")
    return plans


def parse_unit_cards(fragment: str, source_url: str = FLOORPLANS_URL) -> list[dict[str, Any]]:
    soup = BeautifulSoup(fragment, "html.parser")
    cards = soup.select("[data-spaces-unit-id], [data-spaces-id]")
    units: list[dict[str, Any]] = []
    seen: set[str] = set()
    for card in cards:
        provider_id = _clean(card.get("data-spaces-unit-id") or card.get("data-spaces-id"))
        apartment = _clean(card.get("data-spaces-unit"))
        text = _clean(card.get_text(" "))
        if not provider_id or not apartment or provider_id in seen:
            continue
        seen.add(provider_id)
        total, base = _plan_price(text)
        href = card.get("data-spaces-href") or ""
        application = next((a.get("href") for a in card.select("a[href]") if "securecafe" in (a.get("href") or "").lower()), None)
        units.append({
            "unit_id": apartment.lstrip("#").strip(),
            "resident_facing_unit": apartment.lstrip("#").strip(),
            "provider_unit_id": provider_id,
            "floor_plan": _clean(card.get("data-spaces-plan-name") or card.get("data-spaces-sort-plan-name") or card.get("data-spaces-plan")) or None,
            "beds": _beds(card.get("data-spaces-bed-count") or card.get("data-spaces-sort-bed"), text),
            "baths": _money(card.get("data-spaces-bath-count") or card.get("data-spaces-sort-bath")),
            "sqft": _int(card.get("data-spaces-area") or card.get("data-spaces-sort-area")),
            "base_rent_min": base,
            "base_rent_max": base,
            "total_monthly_leasing_price_min": total,
            "total_monthly_leasing_price_max": total,
            "pricing_status": "priced" if base is not None else "call_for_pricing",
            "available_date": _date(card.get("data-spaces-date") or card.get("data-spaces-soonest"), text) or _timestamp_date(card.get("data-spaces-sort-date")),
            "available_now": card.get("data-spaces-available", "").lower() == "true" or bool(re.search(r"available\s+now", text, re.I)),
            "application_url": urljoin(source_url, application) if application else None,
            "detail_url": urljoin(source_url, href) if href else None,
            "provider_payload": {key: value for key, value in card.attrs.items() if str(key).startswith("data-spaces-")},
        })
    return units


def parse_thea_inventory(page_html: str, plans_html: str, unit_fragments: list[tuple[str, str]], source_url: str = FLOORPLANS_URL) -> dict[str, Any]:
    config = _parse_config(page_html)
    plans = parse_plan_cards(plans_html, source_url)
    units: list[dict[str, Any]] = []
    for plan_id, fragment in unit_fragments:
        for unit in parse_unit_cards(fragment, source_url):
            unit["floor_plan_id"] = plan_id
            units.append(unit)
    if not units:
        raise TheaAdapterError("Thea returned no actual unit cards; refusing an empty scan")
    concessions = _promotion_text(page_html)
    if any(plan.get("has_special") for plan in plans):
        concessions.append("Some available floor plans are marked Has Special; verify unit, lease-term, move-in-date, and application eligibility.")
    return {
        "provider": "greystar_spaces",
        "building_id": BUILDING_ID,
        "building_name": BUILDING_NAME,
        "source_url": source_url,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "status": "ok",
        "units": units,
        "plans": plans,
        "policies": {
            "fees": [],
            "concessions": _unique(concessions),
            "fee_guide": {"asset_id": config["engrain_asset_id"], "source_url": source_url, "status": "pending_widget_fetch"},
        },
        "contact": {"email": "thealeasing@greystar.com", "phone": "213.488.THEA", "website": OFFICIAL_URL},
        "provider_config": config,
        "floorplans_count": len(plans),
        "units_count": len(units),
        "warnings": ["Thea plan cards are aggregates; actual inventory came from plan-detail unit cards.", "Concession text is preserved verbatim where available and is not automatically applied to rent."],
    }


def parse_thea_quote_html(html: str, source_url: str, move_in_date: str, lease_term_months: int) -> dict[str, Any]:
    """Parse THEA's read-only RentCafe rental-options response."""

    soup = BeautifulSoup(html, "html.parser")

    def hidden(element_id: str) -> str | None:
        node = soup.select_one(f"#{element_id}")
        return node.get("value") if node else None

    def text(element_id: str) -> str | None:
        node = soup.select_one(f"#{element_id}")
        return _clean(node.get_text(" ")) if node else None

    base = _money(hidden("BaseRent") or hidden("BaseMinRent") or hidden("BaseMaxRent"))
    total = _money(hidden("TotalRent") or text("MontlyTotalAmount"))
    monthly_other = _money(hidden("MonthlyOtherRent")) or 0.0
    tax = _money(hidden("Tax")) or 0.0
    if base is None or total is None:
        raise TheaAdapterError("Thea RentCafe quote returned no base or total rent")
    fee_rows: list[dict[str, str]] = []
    for row in soup.select(".breakdown-row, #LeaseInfoSummary tr"):
        cells = [_clean(cell.get_text(" ")) for cell in row.select("td, .col-xs-5, .col-xs-2")]
        if len(cells) >= 2:
            fee_rows.append({"label": cells[0], "value": cells[1]})
    specials = _unique([
        _clean(node.get_text(" "))
        for node in soup.select("#UnitSpecials, .special-offer")
        if _clean(node.get_text(" "))
    ])
    return {
        "provider": "greystar_spaces",
        "building_id": BUILDING_ID,
        "building_name": BUILDING_NAME,
        "source_url": source_url,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "status": "ok",
        "unit_id": hidden("ApartmentNumber") or hidden("UnitNumber"),
        "provider_unit_id": hidden("UnitId"),
        "floor_plan_id": hidden("FloorplanId"),
        "move_in_date": _date(hidden("dtMIDateSel") or move_in_date),
        "lease_term_months": _int(hidden("sLeaseTerm")) or lease_term_months,
        "base_rent": base,
        "recurring_addons": round(monthly_other + tax, 2),
        "total_monthly_price": total,
        "fee_rows": fee_rows,
        "concessions": specials,
        "raw_fields": {
            "base_rent": hidden("BaseRent") or hidden("BaseMinRent") or hidden("BaseMaxRent"),
            "monthly_other_rent": hidden("MonthlyOtherRent"),
            "total_rent": hidden("TotalRent"),
            "tax": hidden("Tax"),
        },
        "warnings": ["Read-only RentCafe quote; does not submit an application.", "Building-level concession eligibility must still be confirmed separately."],
    }


class TheaAdapter:
    """Fetch Thea's public Spaces inventory and read-only quote endpoints."""

    def __init__(self, timeout_seconds: int = 30, user_agent: str | None = None):
        self.timeout_seconds = timeout_seconds
        self.user_agent = user_agent or "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/141.0 Safari/537.36"

    def _request(self, url: str, data: bytes | None = None, headers: dict[str, str] | None = None) -> str:
        request_headers = {"User-Agent": self.user_agent, "Accept": "text/html,application/xhtml+xml,application/json,*/*"}
        request_headers.update(headers or {})
        request = Request(url, data=data, headers=request_headers, method="POST" if data else "GET")
        with urlopen(request, timeout=self.timeout_seconds) as response:
            if response.status != 200:
                raise TheaAdapterError(f"Thea request returned HTTP {response.status}")
            return response.read().decode(response.headers.get_content_charset() or "utf-8", "replace")

    def fetch_html(self) -> str:
        return self._request(FLOORPLANS_URL)

    def fetch_homepage_html(self) -> str:
        return self._request(OFFICIAL_URL)

    def _ajax(self, fields: dict[str, Any]) -> Any:
        data = urlencode(fields).encode()
        raw = self._request(AJAX_URL, data, {"Referer": FLOORPLANS_URL, "Origin": "https://theadtla.com", "X-Requested-With": "XMLHttpRequest", "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"})
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise TheaAdapterError("Thea AJAX response was not valid JSON") from exc

    @staticmethod
    def _html_response(payload: Any) -> str:
        if isinstance(payload, str):
            return payload
        if isinstance(payload, list):
            return "".join(item for item in payload if isinstance(item, str))
        if isinstance(payload, dict):
            for key in ("html", "data", "response"):
                if isinstance(payload.get(key), str):
                    return payload[key]
        raise TheaAdapterError("Thea AJAX response contained no HTML")

    def fetch_plan_cards(self, page_html: str) -> str:
        payload = self._ajax({"action": "spaces_switch_tab", "requested_tab": "plan", "requested_asset_id": SPACE_ASSET_ID, "spaces_url": FLOORPLANS_URL, "inventory_mode": "false"})
        return self._html_response(payload)

    def fetch_plan_detail(self, plan_id: str) -> str:
        payload = self._ajax({"action": "spaces_get_detail", "requested_object": "plan", "requested_object_id": plan_id, "requested_asset_id": SPACE_ASSET_ID})
        return self._html_response(payload)

    def fetch_fee_guide(self) -> dict[str, Any]:
        encoded = Sqids(alphabet=SQIDS_ALPHABET, min_length=11).encode([int(ENGRAIN_ASSET_ID)])
        url = EXPENSE_GUIDE_API.format(asset_id=encoded)
        raw = self._request(url, headers={
            "Referer": FLOORPLANS_URL,
            "Origin": "https://theadtla.com",
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Sec-Fetch-Site": "cross-site",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
        })
        payload = json.loads(raw)
        return parse_fee_guide_payload(payload, ENGRAIN_ASSET_ID, FLOORPLANS_URL, url)

    def fetch(self) -> dict[str, Any]:
        page_html = self.fetch_html()
        homepage_html = self.fetch_homepage_html()
        plan_html = self.fetch_plan_cards(page_html)
        plans = parse_plan_cards(plan_html)
        fragments = [(plan["plan_id"], self.fetch_plan_detail(plan["plan_id"])) for plan in plans if plan.get("available_count") != 0]
        result = parse_thea_inventory(page_html, plan_html, fragments)
        result["policies"]["concessions"] = _unique(
            result["policies"]["concessions"] + _promotion_text(homepage_html)
        )
        try:
            guide = self.fetch_fee_guide()
        except Exception as exc:
            result["policies"]["fee_guide"]["status"] = "widget_fetch_failed"
            result["policies"]["fee_guide"]["fetch_error"] = str(exc)
            result["warnings"].append("The official Thea fee widget could not be fetched during this scan.")
        else:
            result["policies"]["fee_guide"] = guide
            result["policies"]["fees"] = guide["fees"]
        return result

    def fetch_lease_terms(self, provider_unit_id: str, move_in_date: str) -> dict[str, Any]:
        iso = datetime.strptime(move_in_date, "%Y-%m-%d").date().isoformat()
        # The public JS attaches a ``formatted`` property to its Date object.
        # jQuery therefore serializes the object as requested_date[formatted],
        # rather than as Date.toString() or a plain ISO value.
        requested_date = datetime.strptime(iso, "%Y-%m-%d").strftime("%-m/%-d/%Y")
        payload = self._ajax({"action": "spaces_get_lease_terms_for_unit", "requested_asset_id": ENGRAIN_ASSET_ID, "requested_unit_id": provider_unit_id, "requested_date[formatted]": requested_date})
        if isinstance(payload, list) and payload:
            return {"requested_date": requested_date, "terms": payload}
        if isinstance(payload, dict) and payload.get("data"):
            return {"requested_date": requested_date, "terms": payload["data"]}
        # Keep a narrow fallback for provider changes that remove the custom
        # Date property, but never treat an empty response as a valid quote.
        for fallback in (iso,):
            payload = self._ajax({"action": "spaces_get_lease_terms_for_unit", "requested_asset_id": ENGRAIN_ASSET_ID, "requested_unit_id": provider_unit_id, "requested_date": fallback})
            if isinstance(payload, list) and payload:
                return {"requested_date": fallback, "terms": payload}
            if isinstance(payload, dict) and payload.get("data"):
                return {"requested_date": fallback, "terms": payload["data"]}
        raise TheaAdapterError("Thea returned no lease terms for the selected unit/date")

    def fetch_unit_quote(self, provider_unit_id: str, apartment_number: str, move_in_date: str, lease_term_months: int) -> dict[str, Any]:
        iso = datetime.strptime(move_in_date, "%Y-%m-%d").date().isoformat()
        display_date = datetime.strptime(iso, "%Y-%m-%d").strftime("%-m/%-d/%Y")
        inventory = self.fetch()
        unit = next((item for item in inventory["units"] if item.get("provider_unit_id") == str(provider_unit_id) or item.get("unit_id") == apartment_number.lstrip("#")), None)
        if not unit or not unit.get("application_url"):
            raise TheaAdapterError(f"No current RentCafe application URL found for Thea apartment {apartment_number}")
        parts = urlsplit(unit["application_url"])
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        query["MoveInDate"] = display_date
        query["leaseterm"] = str(lease_term_months)
        query["sLeaseTerm"] = str(lease_term_months)
        quote_url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
        html = self._request(quote_url, headers={"Referer": FLOORPLANS_URL, "Origin": "https://theadtla.com", "Accept": "text/html,application/xhtml+xml"})
        result = parse_thea_quote_html(html, quote_url, iso, lease_term_months)
        result["unit_id"] = result.get("unit_id") or apartment_number.lstrip("#")
        result["rentcafe_unit_id"] = result.get("provider_unit_id")
        result["provider_unit_id"] = str(provider_unit_id)
        result["floor_plan"] = unit.get("floor_plan")
        result["calculator_url"] = f"{SIGHTMAP_URL}?unit_number={apartment_number.lstrip('#')}&lease_term={lease_term_months}&move_in_date={display_date}&mode=expense_calculator"
        result["lease_terms"] = []
        result["lease_terms_source"] = "rentcafe_quote_page"
        return result
