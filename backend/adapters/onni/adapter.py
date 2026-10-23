"""Read-only adapter for Onni public availability pages.

The first supported pages use Onni/RentCafe's server-rendered availability
markup. Hope + Flower and 825 South Hill currently share the same page shape;
Olympic + Hill uses a related two-step plan-page flow.
The parser deliberately fails closed when the page shape changes; it must not
turn a parsing failure into an empty availability result.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
import json
from pathlib import Path
import re
from typing import Any, Iterable
from urllib.parse import parse_qs, urljoin, urlparse
from urllib.parse import urlencode, urlunparse
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup


class OnniAdapterError(RuntimeError):
    """Raised when a provider page cannot be safely interpreted."""


@dataclass(frozen=True)
class OnniBuilding:
    building_id: str
    name: str
    availability_url: str


BUILDINGS = {
    "hope-flower": OnniBuilding(
        "hope-flower", "Hope + Flower", "https://hopeflowerdtla.com/availability"
    ),
    "olympic-hill": OnniBuilding(
        "olympic-hill", "Olympic + Hill", "https://www.rentolympicandhill.com/floorplans"
    ),
    "825-south-hill": OnniBuilding(
        "825-south-hill", "825 South Hill", "https://825southhilldtla.com/availability"
    ),
}


def _money(value: str | None) -> int | None:
    if not value:
        return None
    if re.search(r"\$\s*-1(?:\.0+)?\b", value):
        return None
    digits = re.sub(r"[^0-9]", "", value)
    return int(digits) if digits else None


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", unescape(value or "")).strip()


def _query_value(url: str, key: str) -> str | None:
    values = parse_qs(urlparse(url).query).get(key)
    return values[0] if values else None


def _hidden_value(soup: Any, element_id: str) -> str | None:
    element = soup.select_one(f"#{element_id}")
    return element.get("value") if element else None


def _parse_pricing_data(html: str) -> dict[str, Any]:
    match = re.search(r"var\s+PricingData\s*=\s*(\{.*?\});", html, re.S)
    if not match:
        raise OnniAdapterError("SecureCafe pricing data was not found; refusing to guess a quote")
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise OnniAdapterError("SecureCafe pricing data changed shape") from exc


def _as_float(value: str | int | float | None) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _date_from_status(status: str) -> str | None:
    try:
        return datetime.strptime(status, "%m/%d/%Y").date().isoformat()
    except ValueError:
        return None


def _rent_range(value: str | None) -> tuple[int | None, int | None]:
    numbers = re.findall(r"\$?\s*([0-9][0-9,]*(?:\.\d{2})?)", value or "")
    parsed = [round(float(number.replace(",", ""))) for number in numbers]
    if not parsed:
        return None, None
    return parsed[0], parsed[-1]


def _plan_meta(group: Any) -> dict[str, str | None]:
    intro_element = group.select_one(".floorplan__availability__intro")
    heading = intro_element.select_one("h2, h3") if intro_element else None
    detail = intro_element.select_one("p") if intro_element else None
    if heading and detail:
        intro = f"{_clean(heading.get_text(' '))} | {_clean(detail.get_text(' '))}"
    else:
        intro = _clean(intro_element.get_text(" ") if intro_element else "")
    # Example: PLAN A | studio | FLOWER
    parts = [p.strip() for p in intro.split("|") if p.strip()]
    plan_name = parts[0][5:].strip() if parts and parts[0].upper().startswith("PLAN ") else (parts[0] if parts else None)
    category = parts[1].lower() if len(parts) > 1 else group.get("data-cat")
    tower = parts[2] if len(parts) > 2 else None
    return {
        "plan_name": plan_name,
        "category": category,
        "tower": tower,
        "plan_slug": group.get("data-plan-slug"),
        "data_length": group.get("data-length"),
    }


def _row_fields(row: Any) -> list[str]:
    return [_clean(li.get_text(" ")) for li in row.select(".floorplan__availability__li--data")]


def _fee_cell_text(cell: Any) -> str:
    """Return fee-table text without superscript footnote markers."""

    parts = [str(node) for node in cell.contents if getattr(node, "name", None) != "sup"]
    return _clean("".join(parts))


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def parse_building_policies(soup: Any, source_url: str) -> dict[str, Any]:
    """Parse building-wide fees and public concessions from an official page.

    Fees are intentionally kept as strings: Onni publishes fixed amounts,
    ranges, allocated utilities, and percentage-based charges in the same
    table. Converting those values to numbers would lose meaning.
    """

    fees: list[dict[str, Any]] = []
    for table in soup.select("table"):
        headers = [_clean(cell.get_text(" ")).lower() for cell in table.select("thead th")]
        if not headers or "fee label" not in headers:
            continue
        heading = table.find_previous(["h2", "h3"])
        category_label = _clean(heading.get_text(" ")) if heading else "Other"
        category = _slug(category_label)
        for row in table.select("tbody tr"):
            cells = row.select("td")
            if len(cells) < 4:
                continue
            fees.append(
                {
                    "category": category,
                    "category_label": category_label,
                    "label": _fee_cell_text(cells[0]),
                    "amount": _fee_cell_text(cells[1]),
                    "applied": _fee_cell_text(cells[2]),
                    "frequency_or_due_at": _fee_cell_text(cells[3]),
                    "requirement": _fee_cell_text(cells[4]) if len(cells) > 4 else None,
                    "source_url": source_url,
                }
            )

    page_text = _clean(soup.get_text(" "))
    issuance_match = re.search(r"Date of Issuance:\s*(\d{1,2}/\d{1,2}/\d{4})", page_text, re.I)
    concessions: list[dict[str, Any]] = []
    for heading in soup.find_all(["h1", "h2", "h3", "h4"]):
        if not re.search(r"special savings", heading.get_text(" "), re.I):
            continue
        container = heading.find_parent(class_=re.compile(r"content")) or heading.parent
        text = _clean(container.get_text(" ")) if container else _clean(heading.get_text(" "))
        paragraphs = [_clean(p.get_text(" ")) for p in container.find_all("p")] if container else []
        concession = {
            "title": _clean(heading.get_text(" ")),
            "text": next((p for p in paragraphs if p and "offering" in p.lower()), text),
            "terms": next((p for p in paragraphs if p.startswith("*") or "must lease" in p.lower()), None),
            "source_url": source_url,
        }
        concessions.append(concession)

    return {
        "fee_guide_issued": (
            datetime.strptime(issuance_match.group(1), "%m/%d/%Y").date().isoformat()
            if issuance_match
            else None
        ),
        "fees": fees,
        "concessions": concessions,
        "notes": [
            _clean(item.get_text(" "))
            for item in soup.select(".fee-guide li")
            if _clean(item.get_text(" "))
        ],
        "source_url": source_url,
    }


def parse_onni_availability_html(
    html: str,
    source_url: str,
    building_id: str,
    building_name: str,
) -> dict[str, Any]:
    """Parse a shared Onni/RentCafe server-rendered availability page."""

    soup = BeautifulSoup(html, "html.parser")
    groups = soup.select(".js-plan-group")
    rows = soup.select(".js-plan-row")
    if not groups or not rows:
        raise OnniAdapterError(
            f"{building_name} availability markup not found; refusing to return zero units"
        )

    units: list[dict[str, Any]] = []
    for group in groups:
        meta = _plan_meta(group)
        for row in group.select(".js-plan-row"):
            fields = _row_fields(row)
            if len(fields) < 4:
                raise OnniAdapterError("Availability row is missing required fields")
            apply_link = row.select_one("a[href]")
            application_url = urljoin(source_url, apply_link.get("href")) if apply_link else None
            price_min = _money(row.get("data-rent-min"))
            price_max = _money(row.get("data-rent-max"))
            sqft_match = re.search(r"([0-9,]+)\s*sqft", fields[2], re.I)
            status = fields[3]
            available_date = _date_from_status(status)
            available_now = status.lower() in {"available", "spot special", "spot special!"}
            unit: dict[str, Any] = {
                "unit_id": row.get("data-unit"),
                "floor_plan": meta["plan_name"],
                "floor_plan_slug": meta["plan_slug"],
                "category": meta["category"],
                "tower": meta["tower"],
                "beds": _beds_from_category(meta["category"]),
                "baths": None,
                "sqft": int(sqft_match.group(1).replace(",", "")) if sqft_match else None,
                "base_rent_min": price_min,
                "base_rent_max": price_max,
                "screening_base_rent": price_min,
                "status": "available" if status.lower() == "available" else status,
                "status_raw": status,
                "available_now": available_now,
                "available_date": available_date,
                "application_url": application_url,
                "floor_plan_id": _query_value(application_url or "", "FloorPlanID"),
                "securecafe_unit_id": _query_value(application_url or "", "UnitID"),
            }
            units.append(unit)

    return {
        "provider": "onni",
        "building_id": building_id,
        "building_name": building_name,
        "source_url": source_url,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "status": "ok",
        "units": units,
        "policies": parse_building_policies(soup, source_url),
        "warnings": [
            "Rent ranges are the values published by the official availability page; lease-term pricing may vary.",
            "Availability and incentives can change without notice.",
        ],
    }


def parse_hope_flower_html(html: str, source_url: str) -> dict[str, Any]:
    """Parse Hope + Flower's current server-rendered availability markup."""

    return parse_onni_availability_html(
        html, source_url, "hope-flower", "Hope + Flower"
    )


def parse_825_south_hill_html(html: str, source_url: str) -> dict[str, Any]:
    """Parse 825 South Hill's current server-rendered availability markup."""

    return parse_onni_availability_html(
        html, source_url, "825-south-hill", "825 South Hill"
    )


def parse_olympic_hill_floorplans_html(html: str, source_url: str) -> dict[str, Any]:
    """Parse Olympic + Hill's floorplan summary page.

    This page publishes plan-level price/count cards. The individual unit
    rows are on linked ``/floorplans/plan-*`` pages and are parsed separately.
    """

    soup = BeautifulSoup(html, "html.parser")
    cards: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for link in soup.select('a[href*="/floorplans/plan-"]'):
        availability_url = urljoin(source_url, link["href"])
        if availability_url in seen_urls:
            continue
        seen_urls.add(availability_url)
        card = link.find_parent(class_=re.compile(r"card|floorplan"))
        plan_name = _clean(link.get("data-floorplan-name"))
        if not plan_name and card:
            heading = card.select_one("h2, h3, h4")
            plan_name = _clean(heading.get_text(" ")) if heading else ""
        if not plan_name:
            plan_name = _clean(link.get_text(" "))
        if not plan_name:
            continue
        card_text = _clean(card.get_text(" ") if card else "")
        price_text = link.get("data-floorplan-price")
        if not price_text:
            starting_price = re.search(r"Starting\s+at\s*(\$[\d,]+(?:\.\d{2})?)", card_text, re.I)
            price_text = starting_price.group(1) if starting_price else ""
        price_min, price_max = _rent_range(price_text)
        beds_match = re.search(r"(\d+)\s*Bed", card_text, re.I)
        baths_match = re.search(r"(\d+)\s*Bath", card_text, re.I)
        sqft_match = re.search(r"([0-9,]+)\s*Sq\.\s*Ft", card_text, re.I)
        available_match = re.search(r"(\d+)\s*Available", card_text, re.I)
        cards.append(
            {
                "floor_plan": plan_name,
                "floor_plan_slug": urlparse(availability_url).path.rstrip("/").split("/")[-1],
                "floor_plan_id": link.get("data-floorplan-id") or None,
                "beds": int(beds_match.group(1)) if beds_match else None,
                "baths": int(baths_match.group(1)) if baths_match else None,
                "sqft": int(sqft_match.group(1).replace(",", "")) if sqft_match else None,
                "available_count": int(available_match.group(1)) if available_match else None,
                "base_rent_min": price_min,
                "base_rent_max": price_max,
                "availability_url": availability_url,
            }
        )
    if not cards:
        raise OnniAdapterError("Olympic + Hill floorplan markup not found; refusing to return zero plans")

    nudge = soup.select_one("#customNudge")
    concessions: list[dict[str, Any]] = []
    if nudge:
        nudge_text = _clean(nudge.get_text(" "))
        title = _clean(nudge.select_one("#customNudgeTitle").get_text(" ")) if nudge.select_one("#customNudgeTitle") else None
        concessions.append(
            {
                "title": title,
                "text": nudge_text,
                "terms": next((line for line in re.split(r"(?<=!)\s+|(?<=\.)\s+", nudge_text) if "must" in line.lower() or "restriction" in line.lower()), None),
                "source_url": source_url,
            }
        )

    return {
        "provider": "onni",
        "building_id": "olympic-hill",
        "building_name": "Olympic + Hill",
        "source_url": source_url,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "status": "ok",
        "floorplans": cards,
        "units": [],
        "policies": parse_building_policies(soup, source_url),
        "concessions": concessions,
        "warnings": [
            "The summary page contains plan-level counts and prices; linked plan pages contain unit-level inventory.",
            "Availability and incentives can change without notice.",
        ],
    }


def parse_olympic_hill_plan_html(html: str, source_url: str) -> dict[str, Any]:
    """Parse one Olympic + Hill floorplan page and its actual unit rows."""

    soup = BeautifulSoup(html, "html.parser")
    sections = soup.select(".floorplan-section")
    if not sections:
        raise OnniAdapterError("Olympic + Hill plan markup not found; refusing to return zero units")
    page_heading = soup.select_one("h1")
    expected_plan = _clean(page_heading.get_text(" ")) if page_heading else ""
    section = next(
        (
            candidate
            for candidate in sections
            if _clean(candidate.get("data-name") or "").casefold() == expected_plan.casefold()
        ),
        None,
    )
    if section is None:
        if len(sections) == 1:
            section = sections[0]
        else:
            raise OnniAdapterError(
                f"Olympic + Hill page plan mismatch: expected {expected_plan or 'unknown'}"
            )
    plan_name = _clean(section.get("data-name") or section.select_one("h2").get_text(" "))
    floor_plan_id = section.get("data-id")
    header_text = _clean(section.select_one(".row").get_text(" ") if section.select_one(".row") else "")
    beds_match = re.search(r"(\d+)\s*Bedroom", header_text, re.I)
    baths_match = re.search(r"(\d+)\s*Bathroom", header_text, re.I)
    units: list[dict[str, Any]] = []
    for row in section.select("tr.unit-container"):
        cells = row.select("td")
        if len(cells) < 4:
            # RentCafe can leave placeholder rows in the DOM while its
            # client-side filters are active. They are not inventory rows.
            continue
        apply_link = row.select_one("a[href]")
        if not apply_link:
            continue
        cell_texts = [_clean(cell.get_text(" ")) for cell in cells]
        unit_match = re.search(r"#?\s*(\d{3,5})\b", cell_texts[0])
        sqft_match = re.search(r"(\d[\d,]*)", cell_texts[1])
        if not unit_match or not sqft_match:
            continue
        raw_row_id = row.get("id", "")
        unit_id = (
            raw_row_id[len("unit-container-"):]
            if raw_row_id.startswith("unit-container-")
            else raw_row_id
        ) or unit_match.group(1)
        sqft = int(sqft_match.group(1).replace(",", ""))
        rent_text = cell_texts[2]
        price_min, price_max = _rent_range(rent_text)
        status = re.sub(r"^Date:\s*", "", cell_texts[3], flags=re.I)
        application_url = urljoin(source_url, apply_link["href"]) if apply_link else None
        units.append(
            {
                "unit_id": unit_id,
                "floor_plan": plan_name,
                "floor_plan_slug": urlparse(source_url).path.rstrip("/").split("/")[-1],
                "category": f"{beds_match.group(1)} br" if beds_match else None,
                "tower": None,
                "beds": int(beds_match.group(1)) if beds_match else None,
                "baths": int(baths_match.group(1)) if baths_match else None,
                "sqft": sqft,
                "base_rent_min": price_min,
                "base_rent_max": price_max,
                "screening_base_rent": price_min,
                "status": "available" if status.lower() == "available" else status,
                "status_raw": status,
                "available_now": status.lower() == "available",
                "available_date": _date_from_status(status),
                "application_url": application_url,
                "floor_plan_id": _query_value(application_url or "", "FloorPlanID") or floor_plan_id,
                "securecafe_unit_id": _query_value(application_url or "", "UnitID"),
            }
        )
    if not units:
        raise OnniAdapterError("Olympic + Hill plan contained no unit rows")
    return {
        "provider": "onni",
        "building_id": "olympic-hill",
        "building_name": "Olympic + Hill",
        "source_url": source_url,
        "floor_plan": plan_name,
        "floor_plan_id": floor_plan_id,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "status": "ok",
        "units": units,
        "policies": parse_building_policies(soup, source_url),
    }


def parse_unit_detail_html(
    html: str,
    source_url: str,
    requested_move_in_date: str | None = None,
    requested_lease_term: int | None = None,
) -> dict[str, Any]:
    """Parse a SecureCafe unit quote page without submitting an application."""

    soup = BeautifulSoup(html, "html.parser")
    pricing_data = _parse_pricing_data(html)
    date_keys = [key for key in pricing_data if re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", key)]
    if not date_keys:
        raise OnniAdapterError("SecureCafe returned no move-in-date pricing data")

    selected_date = requested_move_in_date or _hidden_value(soup, "dtMIDateSel") or date_keys[0]
    if selected_date not in pricing_data:
        raise OnniAdapterError(f"No quote was returned for move-in date {selected_date}")
    date_prices = pricing_data[selected_date]
    term = str(requested_lease_term or _hidden_value(soup, "sLeaseTerm") or "")
    if term not in date_prices:
        raise OnniAdapterError(f"No quote was returned for a {term}-month lease on {selected_date}")

    recurring_addons = sum(
        value or 0.0
        for value in (
            _as_float(_hidden_value(soup, "RentableItemTypesRent")),
            _as_float(_hidden_value(soup, "MonthlyOtherRent")),
            _as_float(date_prices[term].get("Tax")),
        )
    )
    selected = date_prices[term]
    base_rent = _as_float(selected.get("price"))
    total_monthly = base_rent + recurring_addons if base_rent is not None else None
    return {
        "provider": "onni",
        "building_id": "hope-flower",
        "source_url": source_url,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "status": "ok",
        "unit_id": _hidden_value(soup, "UnitId") or _query_value(source_url, "UnitID"),
        "floor_plan_id": _hidden_value(soup, "FloorplanId") or _query_value(source_url, "FloorPlanID"),
        "move_in_date": selected_date,
        "lease_term_months": int(term),
        "base_rent": base_rent,
        "recurring_addons": round(recurring_addons, 2),
        "total_monthly_price": round(total_monthly, 2) if total_monthly is not None else None,
        "term_quotes": [
            {
                "lease_term_months": int(key),
                "base_rent": _as_float(value.get("price")),
                "emc_base_rent": _as_float(value.get("EMCBasePrice")),
                "special_amount": _as_float(value.get("SpecialAmount")),
                "tax": _as_float(value.get("Tax")),
            }
            for key, value in date_prices.items()
            if str(key).isdigit()
        ],
        "warnings": [
            "Quoted total includes recurring charges exposed by SecureCafe; optional parking, pet rent and other selections are not included unless selected on the detail page.",
            "Quote is read-only and does not submit an application.",
        ],
    }


def _beds_from_category(category: str | None) -> int | None:
    if not category:
        return None
    if "studio" in category:
        return 0
    match = re.search(r"(\d)", category)
    return int(match.group(1)) if match else None


class OnniAdapter:
    """Provider-level adapter with building-specific public page parsers."""

    def __init__(
        self,
        timeout_seconds: int = 30,
        user_agent: str = (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36"
        ),
    ):
        self.timeout_seconds = timeout_seconds
        self.user_agent = user_agent

    def fetch_html(self, url: str) -> str:
        request = Request(
            url,
            headers={
                "User-Agent": self.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": f"{urlparse(url).scheme}://{urlparse(url).netloc}/",
                "Upgrade-Insecure-Requests": "1",
            },
        )
        with urlopen(request, timeout=self.timeout_seconds) as response:
            content_type = response.headers.get_content_type()
            if content_type not in {"text/html", "application/xhtml+xml"}:
                raise OnniAdapterError(f"Expected HTML response, received {content_type}")
            return response.read().decode(response.headers.get_content_charset() or "utf-8", "replace")

    def parse(self, building_id: str, html: str, source_url: str | None = None) -> dict[str, Any]:
        building = BUILDINGS[building_id]
        resolved_url = source_url or building.availability_url
        if building_id == "hope-flower":
            return parse_hope_flower_html(html, resolved_url)
        if building_id == "825-south-hill":
            return parse_825_south_hill_html(html, resolved_url)
        if building_id == "olympic-hill":
            return parse_olympic_hill_floorplans_html(html, resolved_url)
        raise OnniAdapterError(f"No parser is enabled for {building_id}")

    def fetch(self, building_id: str) -> dict[str, Any]:
        building = BUILDINGS.get(building_id)
        if not building:
            raise OnniAdapterError(f"Unknown Onni building: {building_id}")
        html = self.fetch_html(building.availability_url)
        result = self.parse(building_id, html, building.availability_url)
        if building_id == "olympic-hill":
            # Olympic + Hill publishes counts on the index and actual rows on
            # each linked plan page. Fetch those pages read-only and merge the
            # normalized unit rows into the same output shape as Hope + Flower.
            units: list[dict[str, Any]] = []
            for plan in result["floorplans"]:
                plan_html = self.fetch_html(plan["availability_url"])
                plan_result = parse_olympic_hill_plan_html(plan_html, plan["availability_url"])
                units.extend(plan_result["units"])
                if not result["policies"]["fees"] and plan_result["policies"]["fees"]:
                    result["policies"] = plan_result["policies"]
            result["units"] = units
        return result

    def fetch_unit_quote(
        self,
        application_url: str,
        move_in_date: str | None = None,
        lease_term_months: int | None = None,
    ) -> dict[str, Any]:
        """Fetch one unit's public SecureCafe quote for a date/term combination."""

        parsed = urlparse(application_url)
        query = parse_qs(parsed.query)
        if move_in_date:
            query["MoveInDate"] = [move_in_date]
        detail_url = urlunparse(parsed._replace(query=urlencode(query, doseq=True)))
        html = self.fetch_html(detail_url)
        result = parse_unit_detail_html(html, detail_url, move_in_date, lease_term_months)
        if "rentolympicandhill.securecafe.com" in parsed.netloc:
            result["building_id"] = "olympic-hill"
            result["building_name"] = "Olympic + Hill"
        if "825southhilldtla.securecafe.com" in parsed.netloc:
            result["building_id"] = "825-south-hill"
            result["building_name"] = "825 South Hill"
        return result


def parse_fixture(path: str | Path, building_id: str = "hope-flower") -> dict[str, Any]:
    adapter = OnniAdapter()
    fixture_path = Path(path).resolve()
    return adapter.parse(building_id, fixture_path.read_text(encoding="utf-8"), fixture_path.as_uri())
