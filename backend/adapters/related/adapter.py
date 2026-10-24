"""Read-only Related Rentals inventory adapter.

The Related Los Angeles site exposes a property-filtered search page with
pagination and links to individual unit detail pages.  The adapter uses the
search page for discovery and optionally follows each official detail page to
replace the site's listing id with the resident-facing apartment number.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Any
from urllib.parse import urlencode, urljoin, urlparse
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup


class RelatedAdapterError(RuntimeError):
    """Raised when Related's public inventory shape cannot be trusted."""


@dataclass(frozen=True)
class RelatedBuilding:
    building_id: str
    name: str
    property_filter: str
    detail_url: str
    phone: str


BUILDINGS = {
    "grand-residences": RelatedBuilding(
        "grand-residences", "The Grand by Gehry", "The Grand Residences",
        "https://www.relatedrentals.com/apartment-rentals/los-angeles/downtown/the-grand-residences",
        "213-814-4371",
    ),
    "top-of-grand": RelatedBuilding(
        "top-of-grand", "Top of The Grand", "Top of The Grand",
        "https://www.relatedrentals.com/apartment-rentals/los-angeles/downtown/the-grand-residences",
        "213-814-4371",
    ),
    "emerson": RelatedBuilding(
        "emerson", "The Emerson", "The Emerson",
        "https://www.relatedrentals.com/apartment-rentals/los-angeles/downtown/the-emerson",
        "213-269-5007",
    ),
}


def _clean(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _money(value: str | None) -> float | None:
    match = re.search(r"\$?\s*([0-9][0-9,]*(?:\.\d{1,2})?)", value or "")
    return float(match.group(1).replace(",", "")) if match else None


def _date(value: str | None) -> str | None:
    if not value or value.lower() == "now":
        return None
    match = re.search(r"(\d{1,2})/(\d{1,2})", value)
    if not match:
        return None
    month, day = map(int, match.groups())
    year = datetime.now().year
    return f"{year:04d}-{month:02d}-{day:02d}"


def _beds(label: str) -> int | None:
    label = label.lower()
    if "studio" in label:
        return 0
    match = re.search(r"(\d+)\s*bed", label)
    return int(match.group(1)) if match else None


def _page_url(building: RelatedBuilding, page: int) -> str:
    params = {"property[]": building.property_filter, "city[]": "Los Angeles"}
    if page:
        params["page"] = page
    query = urlencode(params)
    return f"https://www.relatedrentals.com/search?{query}"


def _concessions(soup: BeautifulSoup) -> list[str]:
    found: list[str] = []
    for element in soup.find_all(string=re.compile(r"weeks?\s+free|look\s*&?\s*lease", re.I)):
        value = _clean(element)
        if value and value not in found and len(value) < 400:
            found.append(value)
    for heading in soup.find_all(["h1", "h2", "h3", "h4"]):
        value = _clean(heading.get_text(" "))
        if re.search(r"weeks?\s+free|look\s*&?\s*lease", value, re.I) and value not in found:
            found.append(value)
    return found


def _parse_search_page(html: str, source_url: str, building: RelatedBuilding) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    soup = BeautifulSoup(html, "html.parser")
    units: list[dict[str, Any]] = []
    for anchor in soup.select("a[href]"):
        href = urljoin(source_url, anchor.get("href"))
        text = _clean(anchor.get("aria-label") or anchor.get("title") or anchor.get_text(" "))
        if "/apartment-rentals/los-angeles/downtown/" not in href or not re.search(r"PRICE\s*:", text, re.I):
            continue
        price_match = re.search(r"PRICE\s*:\s*\$?\s*([0-9][0-9,]*(?:\.\d{1,2})?)", text, re.I)
        available_match = re.search(r"AVAILABLE:\s*(Now|\d{1,2}/\d{1,2})", text, re.I)
        if not price_match or not available_match:
            raise RelatedAdapterError("Related unit result changed price/availability markup")
        path_id = href.rstrip("/").rsplit("-", 1)[-1]
        if not path_id.isdigit():
            raise RelatedAdapterError("Related unit result is missing its listing id")
        plan_match = re.search(r"(Studio|Oversized Studio|Corner Studio|\d+ Bedroom|Oversized 1 Bedroom|Corner 2 Bedroom)[^P]*?\d+\s*Bath", text, re.I)
        plan = _clean(plan_match.group(0)) if plan_match else None
        units.append({
            "unit_id": f"listing-{path_id}",
            "provider_unit_id": path_id,
            "floor_plan": plan,
            "category": plan,
            "beds": _beds(plan or text),
            "baths": int(re.search(r"(\d+)\s*Bath", plan or text, re.I).group(1)) if re.search(r"(\d+)\s*Bath", plan or text, re.I) else None,
            "base_rent_min": _money(price_match.group(1)),
            "base_rent_max": _money(price_match.group(1)),
            "available_date": _date(available_match.group(1)),
            "available_now": available_match.group(1).lower() == "now",
            "status": "available",
            "detail_url": href,
            "application_url": None,
            "listing_text": text,
        })
    if not units:
        raise RelatedAdapterError("Related search returned no recognizable units; refusing to return zero inventory")
    pagination = []
    for anchor in soup.select("a[href]"):
        href = urljoin(source_url, anchor.get("href"))
        parsed_href = urlparse(href)
        normalized = parsed_href._replace(fragment="").geturl()
        if "page=" in parsed_href.query and normalized not in pagination:
            pagination.append(normalized)
    return units, pagination, _concessions(soup)


def _parse_detail(html: str, source_url: str, unit: dict[str, Any]) -> None:
    soup = BeautifulSoup(html, "html.parser")
    text = _clean(soup.get_text(" "))
    unit_match = re.search(r"Residence\s+([A-Za-z0-9-]+)\s*\|", text, re.I)
    if not unit_match:
        unit_match = re.search(r"Apartment\s+#\s*([A-Za-z0-9-]+)\s+at\s+", text, re.I)
    sqft_match = re.search(r"\|\s*([0-9][0-9,]*)\s*sq\.\s*ft\.", text, re.I)
    application = soup.select_one("a[href*='securecafe.com/onlineleasing']")
    if unit_match:
        unit["unit_id"] = unit_match.group(1)
        unit["resident_facing_unit"] = unit_match.group(1)
    if sqft_match:
        unit["sqft"] = int(sqft_match.group(1).replace(",", ""))
    if application:
        unit["application_url"] = urljoin(source_url, application.get("href"))
    unit["detail_checked"] = True


class RelatedAdapter:
    """Fetch Related-managed public inventory for one direct building."""

    def __init__(self, timeout_seconds: int = 30, user_agent: str | None = None):
        self.timeout_seconds = timeout_seconds
        self.user_agent = user_agent or "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/141.0 Safari/537.36"

    def fetch_html(self, url: str) -> str:
        request = Request(url, headers={"User-Agent": self.user_agent, "Accept": "text/html,application/xhtml+xml"})
        with urlopen(request, timeout=self.timeout_seconds) as response:
            if response.headers.get_content_type() not in {"text/html", "application/xhtml+xml"}:
                raise RelatedAdapterError("Related did not return HTML")
            return response.read().decode(response.headers.get_content_charset() or "utf-8", "replace")

    def fetch(self, building_id: str, *, fetch_details: bool = True) -> dict[str, Any]:
        building = BUILDINGS.get(building_id)
        if not building:
            raise RelatedAdapterError(f"Unknown Related building: {building_id}")
        first_url = _page_url(building, 0)
        first_html = self.fetch_html(first_url)
        units, page_links, concessions = _parse_search_page(first_html, first_url, building)
        detail_html = self.fetch_html(building.detail_url)
        concessions.extend(x for x in _concessions(BeautifulSoup(detail_html, "html.parser")) if x not in concessions)
        pages = [first_url]
        for page_url in page_links:
            if re.search(r"(?:[?&])page=0(?:&|$)", page_url) or page_url in pages:
                continue
            if page_url not in pages:
                page_units, _, page_concessions = _parse_search_page(self.fetch_html(page_url), page_url, building)
                units.extend(page_units)
                concessions.extend(x for x in page_concessions if x not in concessions)
                pages.append(page_url)
        deduped: dict[str, dict[str, Any]] = {unit["provider_unit_id"]: unit for unit in units}
        units = list(deduped.values())
        if fetch_details:
            for unit in units:
                _parse_detail(self.fetch_html(unit["detail_url"]), unit["detail_url"], unit)
        return {
            "provider": "related",
            "building_id": building.building_id,
            "building_name": building.name,
            "source_url": building.detail_url,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "status": "ok",
            "units": units,
            "policies": {"fees": [], "concessions": concessions},
            "contact": {"phone": building.phone, "email": "info@relatedrentals.com"},
            "pages_scanned": pages,
            "warnings": [
                "Related public pages expose current asking price, not a lease-term quote.",
                "Availability, pricing, fees, and incentives can change without notice.",
            ],
        }
