"""Shared RentCafe/Yardi adapter for Helio properties.

Arya publishes unit rows directly on ``/availableunits``. Venue and Kaia
publish floor-plan cards first, then unit rows on each plan availability page.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.cookiejar import CookieJar
import re
from typing import Any
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen

from bs4 import BeautifulSoup


class HelioAdapterError(RuntimeError):
    """Raised when an official Helio/RentCafe response is not trustworthy."""


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _money(value: Any) -> float | None:
    match = re.search(r"\$?\s*([0-9][0-9,]*(?:\.\d{1,2})?)", str(value or ""))
    return float(match.group(1).replace(",", "")) if match else None


def _integer(value: Any) -> int | None:
    match = re.search(r"\d[\d,]*", str(value or ""))
    if match:
        return int(match.group(0).replace(",", ""))
    try:
        return int(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _beds(value: Any, text: str = "") -> int | None:
    candidate = _clean(value) or _clean(text)
    if re.search(r"\bstudio\b", candidate, re.I):
        return 0
    match = re.search(r"(\d+)\s*(?:beds?|bedrooms?|br)\b", candidate, re.I)
    return int(match.group(1)) if match else None


def _baths(value: Any, text: str = "") -> float | None:
    candidate = _clean(value) or _clean(text)
    match = re.search(r"(\d+(?:\.\d+)?)\s*bath", candidate, re.I)
    return float(match.group(1)) if match else _money(value)


def _date(value: Any, text: str = "") -> str | None:
    candidate = _clean(value) or _clean(text)
    for raw, fmt in ((r"\d{4}-\d{2}-\d{2}", "%Y-%m-%d"), (r"\d{1,2}/\d{1,2}/\d{4}", "%m/%d/%Y")):
        match = re.search(raw, candidate)
        if match:
            try:
                return datetime.strptime(match.group(0), fmt).date().isoformat()
            except ValueError:
                pass
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


def _provider_date(value: str) -> str:
    """Return the date format used by RentCafe's revenue-management endpoint."""
    parsed = datetime.strptime(value, "%Y-%m-%d").date()
    return f"{parsed.month}/{parsed.day}/{parsed.year}"


def _parse_amounts(text: str) -> list[float]:
    return [float(value.replace(",", "")) for value in re.findall(r"\$\s*([0-9][0-9,]*(?:\.\d{1,2})?)", text)]


def parse_securecafe_quote_html(html: str, source_url: str = "") -> dict[str, Any]:
    """Extract the visible quote summary from an Arya/Venue RentalOptions page."""
    soup = BeautifulSoup(html, "html.parser")
    summary: dict[str, Any] = {"source_url": source_url, "fee_rows": []}
    for row in soup.select("#LeaseInfoSummary tr"):
        cells = [_clean(cell.get_text(" ")) for cell in row.select("th, td")]
        if len(cells) >= 2:
            label, value = cells[0], cells[-1]
            amount = _money(value) if "$" in value else None
            summary["fee_rows"].append({"label": label, "amount": amount, "value": value})
            key = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
            if key in {"rent", "base_rent"}:
                summary["base_rent"] = _money(value)
            elif key in {"net_rent", "total_monthly_rent", "monthly_total"}:
                summary["net_rent"] = _money(value)
            elif key == "lease_term":
                summary["lease_term"] = value
            elif key == "move_in_date":
                summary["move_in_date"] = _date(value)
    for label, selector in (("rent", "[data-selenium-id='Rent']"), ("internet_fee", "[data-selenium-id='InternetFee']"), ("net_rent", "[data-selenium-id='NetRent']")):
        node = soup.select_one(selector)
        if node and label not in summary:
            summary[label] = _money(node.get_text(" "))
    visible = _clean(soup.get_text(" "))
    if "base_rent" not in summary:
        match = re.search(r"Rent\s*\$\s*([0-9][0-9,]*(?:\.\d{2})?)", visible, re.I)
        if match:
            summary["base_rent"] = float(match.group(1).replace(",", ""))
    if "net_rent" not in summary:
        match = re.search(r"Net Rent\s*\$\s*([0-9][0-9,]*(?:\.\d{2})?)", visible, re.I)
        if match:
            summary["net_rent"] = float(match.group(1).replace(",", ""))
    return summary


def parse_rentcafe_price_response(text: str) -> float | None:
    """Parse the small HTML response returned by rcLoadContent getPrice."""
    return _money(BeautifulSoup(text, "html.parser").get_text(" ")) or _money(text)


BUILDINGS: dict[str, dict[str, Any]] = {
    "arya": {
        "building_name": "Arya", "official_url": "https://www.live-arya.com/",
        "inventory_url": "https://www.live-arya.com/availableunits", "mode": "direct_units",
        "contact": {"phone": "+1 866-934-9338", "website": "https://www.live-arya.com/"},
    },
    "kaia": {
        "building_name": "Kaia", "official_url": "https://www.live-kaia.com/",
        "inventory_url": "https://www.live-kaia.com/floorplans", "mode": "plan_pages",
        "contact": {"phone": "+1 888-265-0963", "website": "https://www.live-kaia.com/"},
    },
    "venue": {
        "building_name": "Venue Residences", "official_url": "https://www.venue-residences.com/",
        "inventory_url": "https://www.venue-residences.com/floorplans", "mode": "plan_pages",
        "contact": {"phone": "+1 855-589-1294", "website": "https://www.venue-residences.com/"},
    },
}


def _plan_price(card: BeautifulSoup) -> tuple[float | None, float | None]:
    action = card.select_one("a.track-apply[data-floorplan-price]")
    raw = action.get("data-floorplan-price") if action else None
    if raw:
        prices = [_money(part) for part in re.split(r"\s*-\s*", str(raw))]
        prices = [price for price in prices if price is not None]
        if prices:
            return prices[0], prices[-1]
    text_prices = [_money(item.get_text(" ")) for item in card.select(".text-md, .starting-at")]
    text_prices = [price for price in text_prices if price is not None]
    return (text_prices[0], text_prices[-1]) if text_prices else (None, None)


def _parse_plan_card(card: BeautifulSoup, source_url: str) -> dict[str, Any]:
    heading = card.select_one("h2, h3, h4")
    name = _clean(card.get("data-name") or (heading.get_text(" ") if heading else ""))
    if not name:
        raise HelioAdapterError("RentCafe floor-plan card has no plan name")
    text = _clean(card.get_text(" "))
    availability = card.select_one(".fp-availability")
    count_match = re.search(r"(\d+)\s+Available", _clean(availability.get_text(" ") if availability else text), re.I)
    action = card.select_one("a.track-apply[href], a[href*='/floorplans/']")
    href = action.get("href") if action else None
    plan_id = card.get("data-id") or card.get("id", "").removeprefix("fp-container-")
    price_min, price_max = _plan_price(card)
    details = [_clean(item.get_text(" ")) for item in card.select(".card-header li, .list-inline-item")]
    sqft = next((_integer(value) for value in details if "sq. ft" in value.lower()), None)
    return {
        "plan_id": _clean(plan_id) or None, "plan_name": name,
        "beds": _beds(" ".join(details), text), "baths": _baths(" ".join(details), text),
        "sqft": sqft, "available_count": int(count_match.group(1)) if count_match else None,
        "base_rent_min": price_min, "base_rent_max": price_max, "source_url": source_url,
        "availability_url": urljoin(source_url, href) if href else None,
        "provider_payload": {key: value for key, value in card.attrs.items() if str(key).startswith("data-")},
    }


def parse_plans(html: str, source_url: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select(".fp-container") or soup.select(".floorplan-section")
    plans = [_parse_plan_card(card, source_url) for card in cards]
    if not plans:
        raise HelioAdapterError("RentCafe page contained no floor-plan cards")
    return plans


def _unit_rows(soup: BeautifulSoup, source_url: str, plan: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    units: list[dict[str, Any]] = []
    for row in soup.select("tr.unit-container, [id^='unit-container-']"):
        cells = row.select("td")
        if len(cells) < 3:
            continue
        apartment = _clean((row.select_one(".td-card-name") or cells[0]).get_text(" ")).lstrip("#").strip()
        sqft = _integer((row.select_one(".td-card-sqft") or cells[1]).get_text(" "))
        price = _money((row.select_one(".td-card-rent") or cells[2]).get_text(" "))
        apply_link = row.select_one("a[href]")
        if not apartment or not apply_link:
            continue
        href = _clean(apply_link.get("href"))
        parent = row.find_parent(class_=re.compile(r"floorplan-section"))
        parent_name = _clean(parent.get("data-name")) if parent else ""
        name = parent_name or (plan or {}).get("plan_name")
        parent_text = _clean(parent.get_text(" ")) if parent else ""
        context = plan or {
            "plan_name": name,
            "beds": _beds("", parent_text),
            "baths": _baths("", parent_text),
            "sqft": _integer(re.search(r"([\d,]+)\s*Sq\.\s*Ft", parent_text, re.I).group(1))
            if re.search(r"([\d,]+)\s*Sq\.\s*Ft", parent_text, re.I) else None,
        }
        unit_id = _clean(row.get("id", "").removeprefix("unit-container-")) or apartment
        units.append({
            "unit_id": apartment, "resident_facing_unit": apartment, "provider_unit_id": unit_id,
            "floor_plan": name, "floor_plan_id": context.get("plan_id"),
            "beds": context.get("beds"), "baths": context.get("baths"),
            "sqft": sqft or context.get("sqft"), "base_rent_min": price, "base_rent_max": price,
            "total_monthly_leasing_price_min": price, "total_monthly_leasing_price_max": price,
            "pricing_status": "priced" if price is not None else "call_for_pricing", "available_now": True,
            "available_date": _date(apply_link.get("href"), row.get_text(" ")),
            "application_url": urljoin(source_url, href), "detail_url": urljoin(source_url, href),
            "provider_payload": {"row_id": row.get("id"), "application_url": urljoin(source_url, href)},
        })
    return units


def parse_units(html: str, source_url: str, plan: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Parse resident-facing apartment rows from a RentCafe page."""
    return _unit_rows(BeautifulSoup(html, "html.parser"), source_url, plan)


def parse_promotion(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    values: list[str] = []
    selectors = (
        "#nudgeTop", ".rent-special", ".special-offer",
        "[data-selenium-id*='Special']", "[class*='banner']",
        "[class*='promo']", "[class*='special']", "[id*='banner']",
        "[id*='promo']", "[id*='special']",
    )
    for selector in selectors:
        for node in soup.select(selector):
            text = _clean(node.get_text(" "))
            if text.lower().startswith("rent specials"):
                text = _clean(re.sub(r"^rent specials\s*", "", text, flags=re.I))
            if text and re.search(
                r"look\s+(?:&|and)\s*lease|\b\d+\s+(?:weeks?|months?)\s+free|rent specials?",
                text,
                re.I,
            ):
                values.append(text)
    return _unique(values)


def parse_page(html: str, source_url: str, building: dict[str, Any]) -> dict[str, Any]:
    plans = parse_plans(html, source_url)
    units = parse_units(html, source_url)
    if building["mode"] == "direct_units" and not units:
        raise HelioAdapterError("Arya availability page contained no unit rows")
    return {"plans": plans, "units": units, "concessions": parse_promotion(html)}


class HelioAdapter:
    """Fetch current normalized inventory for Arya, Kaia, or Venue."""

    def __init__(self, timeout_seconds: int = 30, user_agent: str | None = None):
        self.timeout_seconds = timeout_seconds
        self.user_agent = user_agent or (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 Chrome/141.0 Safari/537.36"
        )

    def _request(self, url: str, referer: str | None = None) -> str:
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        if referer:
            headers["Referer"] = referer
        with urlopen(Request(url, headers=headers), timeout=self.timeout_seconds) as response:
            if response.status != 200:
                raise HelioAdapterError(f"RentCafe page returned HTTP {response.status}: {url}")
            return response.read().decode(response.headers.get_content_charset() or "utf-8", "replace")

    def fetch(self, building_id: str) -> dict[str, Any]:
        if building_id not in BUILDINGS:
            raise HelioAdapterError(f"Unknown Helio building: {building_id}")
        building = BUILDINGS[building_id]
        source_url = building["inventory_url"]
        page_html = self._request(source_url)
        parsed = parse_page(page_html, source_url, building)
        plans, units = parsed["plans"], parsed["units"]
        warnings: list[str] = []
        concessions = parsed["concessions"]
        if building["official_url"] != source_url:
            try:
                homepage_html = self._request(building["official_url"], referer=source_url)
                concessions = _unique(concessions + parse_promotion(homepage_html))
            except Exception as exc:
                warnings.append(f"Could not read official homepage promotion banner: {exc}")
        if building["mode"] == "direct_units":
            by_plan: dict[str, list[dict[str, Any]]] = {}
            for unit in units:
                by_plan.setdefault(unit.get("floor_plan") or "", []).append(unit)
            for plan in plans:
                matching = by_plan.get(plan["plan_name"], [])
                if matching:
                    prices = [unit["base_rent_min"] for unit in matching if unit.get("base_rent_min") is not None]
                    plan["available_count"] = len(matching)
                    plan["base_rent_min"] = min(prices) if prices else None
                    plan["base_rent_max"] = max(prices) if prices else None
                    plan["sqft"] = matching[0].get("sqft")
        if building["mode"] == "plan_pages":
            targets = [plan for plan in plans if plan.get("availability_url") and (plan.get("available_count") or 0) > 0]
            def load(plan: dict[str, Any]) -> list[dict[str, Any]]:
                detail_url = plan["availability_url"]
                return parse_units(self._request(detail_url, referer=source_url), detail_url, plan)
            with ThreadPoolExecutor(max_workers=min(4, max(1, len(targets)))) as pool:
                for result in pool.map(load, targets):
                    units.extend(result)
            warnings.append("Venue/Kaia plan cards are aggregates; unit records come from each available plan page.")
        if not units:
            raise HelioAdapterError(f"{building_id} returned no actual available unit rows")
        return {
            "provider": "rentcafe_helio", "building_id": building_id,
            "building_name": building["building_name"], "source_url": source_url,
            "checked_at": datetime.now(timezone.utc).isoformat(), "status": "ok",
            "units": units, "plans": plans, "concessions": concessions,
            "policies": {
                "fees": [], "concessions": concessions,
                "fee_guide": {"status": "not_implemented", "source_url": building["official_url"]},
            },
            "contact": building["contact"],
            "provider_config": {"mode": building["mode"], "inventory_url": source_url},
            "floorplans_count": len(plans), "units_count": len(units),
            "warnings": warnings + [
                "RentCafe prices are current advertised base-rent references and may exclude parking, utilities, fees, or eligibility conditions.",
                "Building-level fee-guide extraction is a separate follow-up; empty fees are not authoritative fee confirmation.",
            ],
        }

    def fetch_unit_quote(
        self,
        building_id: str,
        application_url: str,
        move_in_date: str,
        lease_term_months: int,
    ) -> dict[str, Any]:
        """Fetch an ad-hoc official quote for a specific SecureCafe unit."""
        if building_id not in BUILDINGS:
            raise HelioAdapterError(f"Unknown Helio building: {building_id}")
        if building_id == "kaia":
            raise HelioAdapterError("Kaia's RentCafe applicant portal requires a browser session for ad-hoc quotes")
        if lease_term_months < 1:
            raise HelioAdapterError("lease_term_months must be positive")
        provider_date = _provider_date(move_in_date)
        parts = urlsplit(application_url)
        query = parse_qs(parts.query, keep_blank_values=True)
        query["MoveInDate"] = [provider_date]
        quote_url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query, doseq=True), parts.fragment))
        jar = CookieJar()
        opener = build_opener(HTTPCookieProcessor(jar))
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        with opener.open(Request(quote_url, headers=headers), timeout=self.timeout_seconds) as response:
            page_html = response.read().decode(response.headers.get_content_charset() or "utf-8", "replace")
        page_values = parse_securecafe_quote_html(page_html, quote_url)
        property_id = (query.get("myOlePropertyId") or [None])[0]
        unit_id = (query.get("UnitID") or query.get("UnitId") or [None])[0]
        price_endpoint = None
        endpoint_price = None
        warnings: list[str] = []
        if property_id and unit_id:
            price_endpoint = f"{parts.scheme}://{parts.netloc}//onlineleasing/rcLoadContent.ashx?" + urlencode({
                "contentclass": "revenuemanagementwrapper", "method": "GetContent",
                "PropertyId": property_id, "action": "getPrice", "unitid": unit_id,
                "sLeaseterm": lease_term_months, "dtMovein": provider_date,
            })
            endpoint_headers = {**headers, "Accept": "*/*", "Referer": quote_url, "X-Requested-With": "XMLHttpRequest"}
            with opener.open(Request(price_endpoint, headers=endpoint_headers), timeout=self.timeout_seconds) as response:
                endpoint_text = response.read().decode(response.headers.get_content_charset() or "utf-8", "replace")
            endpoint_price = parse_rentcafe_price_response(endpoint_text)
            if endpoint_price in (None, 0):
                warnings.append("RentCafe returned no usable term-specific price for this move-in/date selection.")
        else:
            warnings.append("Could not identify property/unit IDs from the SecureCafe application URL.")
        return {
            "provider": "rentcafe_helio", "building_id": building_id,
            "building_name": BUILDINGS[building_id]["building_name"],
            "application_url": quote_url, "move_in_date": move_in_date,
            "lease_term_months": lease_term_months,
            "base_rent": endpoint_price if endpoint_price not in (None, 0) else page_values.get("base_rent"),
            "page_quote": page_values, "price_endpoint_value": endpoint_price,
            "price_endpoint_url": price_endpoint,
            "pricing_status": "priced" if endpoint_price not in (None, 0) else "page_quote_only",
            "warnings": warnings + [
                "Quote-specific fees are from the official unit quote page; use a building fee guide for building-wide policy when available."
            ],
        }
