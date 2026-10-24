"""Read-only Park Fifth / Bozzuto availability adapter.

Park Fifth publishes inventory in server-rendered HTML.  The adapter parses
the public floor-plan result cards and fee tables, follows the site's own
pagination links, and fails closed when the expected result shape disappears.
It does not submit applications or interact with resident portals.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Any
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup


class ParkFifthAdapterError(RuntimeError):
    """Raised when Park Fifth's public page cannot be safely interpreted."""


@dataclass(frozen=True)
class ParkFifthBuilding:
    building_id: str = "park-fifth"
    name: str = "Park Fifth"
    availability_url: str = "https://www.parkfifthtower.com/floor-plans/"


BUILDING = ParkFifthBuilding()


def _clean(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _money(value: str | None) -> float | None:
    if not value:
        return None
    match = re.search(r"\$\s*([0-9][0-9,]*(?:\.\d{1,2})?)", value)
    return float(match.group(1).replace(",", "")) if match else None


def _date(value: str | None) -> str | None:
    match = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", value or "")
    if not match:
        return None
    month, day, year = match.groups()
    return f"{year}-{int(month):02d}-{int(day):02d}"


def _beds(label: str) -> int | None:
    label = label.lower()
    if "studio" in label:
        return 0
    match = re.search(r"(\d+)\s*bed", label)
    return int(match.group(1)) if match else None


def _page_links(soup: BeautifulSoup, source_url: str) -> list[str]:
    links = {source_url}
    for anchor in soup.select("a[href]"):
        text = _clean(anchor.get_text(" ")).lower()
        href = urljoin(source_url, anchor.get("href"))
        parsed = urlparse(href)
        if parsed.netloc != urlparse(source_url).netloc:
            continue
        if "pagenumber=" in parsed.query and (text.isdigit() or text in {"next", "…", "..."}):
            links.add(href)
    return sorted(links)


def _parse_fees(soup: BeautifulSoup) -> list[dict[str, str]]:
    fees: list[dict[str, str]] = []
    for row in soup.select("table tr"):
        cells = [_clean(cell.get_text(" ")) for cell in row.select("th, td")]
        if len(cells) >= 4 and cells[0].lower() != "fee name":
            fees.append({"name": cells[0], "cost": cells[1], "frequency": cells[2], "type": cells[3]})
    return fees


def _parse_concessions(soup: BeautifulSoup) -> list[str]:
    # Promotions are optional and may be absent from the floor-plan page.
    found: list[str] = []
    for text_node in soup.find_all(string=re.compile(r"weeks?\s+free|look\s*&?\s*lease|special offer", re.I)):
        value = _clean(text_node)
        if value and value not in found:
            found.append(value)
        parent = text_node.parent
        if parent:
            parent_text = _clean(parent.get_text(" "))
            if parent_text and parent_text not in found and len(parent_text) <= 300:
                found.append(parent_text)
    return found


def parse_park_fifth_html(html: str, source_url: str | None = None) -> dict[str, Any]:
    source_url = source_url or BUILDING.availability_url
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select(".mt_fp_list_box")
    if not cards:
        raise ParkFifthAdapterError("Park Fifth inventory cards were not found; refusing to return zero units")

    units: list[dict[str, Any]] = []
    for card in cards:
        unit_text = _clean(card.select_one("[itemprop='name']").get_text(" ") if card.select_one("[itemprop='name']") else "")
        unit_match = re.search(r"(?:Apt\s*)?(\w[\w-]*)", unit_text, re.I)
        unit_id = unit_match.group(1) if unit_match else None
        if not unit_id:
            raise ParkFifthAdapterError("Park Fifth card is missing an apartment number")

        bed_label = _clean(card.select_one("[itemprop='numberOfBedrooms']").get_text(" ") if card.select_one("[itemprop='numberOfBedrooms']") else "")
        bath_label = _clean(card.select_one("[itemprop='numberOfBathroomsTotal']").get_text(" ") if card.select_one("[itemprop='numberOfBathroomsTotal']") else "")
        base_label = _clean(card.select_one(".base-rent").get_text(" ") if card.select_one(".base-rent") else "")
        total_label = _clean(card.select_one(".data-price").get_text(" ") if card.select_one(".data-price") else "")
        sqft_label = _clean(card.select_one(".data-sqft").get_text(" ") if card.select_one(".data-sqft") else "")
        available_label = ""
        detail_columns = card.select(".mt_fp_details")
        if len(detail_columns) >= 3:
            available_label = _clean(detail_columns[2].get_text(" "))
        detail_link = card.select_one("a[href*='/floor-plans/apartment/']")
        apply_link = card.select_one("a[href*='bozzuto.apply']")
        plan_link = card.select_one("[itemprop='numberOfBedrooms']")
        plan_name = None
        if plan_link:
            plan_href = plan_link.get("href", "")
            match = re.search(r"floorplanid=([^&]+)", plan_href)
            plan_name = match.group(1) if match else None
        unit_number = unit_id.zfill(4) if unit_id.isdigit() else unit_id
        units.append({
            "unit_id": unit_number,
            "provider_unit_id": unit_number,
            "floor_plan": plan_name or bed_label,
            "category": bed_label,
            "beds": _beds(bed_label),
            "baths": int(re.search(r"\d+", bath_label).group(0)) if re.search(r"\d+", bath_label) else None,
            "sqft": int(re.search(r"\d[\d,]*", sqft_label).group(0).replace(",", "")) if re.search(r"\d[\d,]*", sqft_label) else None,
            "base_rent_min": _money(base_label),
            "base_rent_max": _money(base_label),
            "total_monthly_leasing_price": _money(total_label),
            "available_date": _date(available_label),
            "available_now": "now" in available_label.lower(),
            "status": "available",
            "application_url": urljoin(source_url, apply_link.get("href")) if apply_link else None,
            "detail_url": urljoin(source_url, detail_link.get("href")) if detail_link else None,
        })

    return {
        "provider": "bozzuto",
        "building_id": BUILDING.building_id,
        "building_name": BUILDING.name,
        "source_url": source_url,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "status": "ok",
        "units": units,
        "policies": {"fees": _parse_fees(soup), "concessions": _parse_concessions(soup)},
        "warnings": [
            "Park Fifth publishes Total Monthly Leasing Price separately from base rent.",
            "Availability, pricing, fees, and incentives can change without notice.",
        ],
    }


class ParkFifthAdapter:
    """Fetch and normalize Park Fifth's public inventory pages."""

    def __init__(self, timeout_seconds: int = 30, user_agent: str | None = None):
        self.timeout_seconds = timeout_seconds
        self.user_agent = user_agent or (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36"
        )

    def fetch_html(self, url: str) -> str:
        request = Request(url, headers={"User-Agent": self.user_agent, "Accept": "text/html,application/xhtml+xml"})
        with urlopen(request, timeout=self.timeout_seconds) as response:
            if response.headers.get_content_type() not in {"text/html", "application/xhtml+xml"}:
                raise ParkFifthAdapterError("Park Fifth did not return HTML")
            return response.read().decode(response.headers.get_content_charset() or "utf-8", "replace")

    def fetch(self) -> dict[str, Any]:
        first_html = self.fetch_html(BUILDING.availability_url)
        first = parse_park_fifth_html(first_html, BUILDING.availability_url)
        soup = BeautifulSoup(first_html, "html.parser")
        page_urls = _page_links(soup, BUILDING.availability_url)
        for page_url in page_urls:
            if page_url == BUILDING.availability_url:
                continue
            page = parse_park_fifth_html(self.fetch_html(page_url), page_url)
            first["units"].extend(page["units"])
            first["policies"]["fees"] = first["policies"]["fees"] or page["policies"]["fees"]
            first["policies"]["concessions"] = list(dict.fromkeys(first["policies"]["concessions"] + page["policies"]["concessions"]))
        first["units"] = list({unit["unit_id"]: unit for unit in first["units"]}.values())
        first["pages_scanned"] = page_urls
        return first
