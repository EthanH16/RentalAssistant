"""Read-only adapter for Onni public availability pages.

The first supported page is Hope + Flower's server-rendered availability page.
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
}


def _money(value: str | None) -> int | None:
    if not value:
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


def _plan_meta(group: Any) -> dict[str, str | None]:
    intro = _clean(group.select_one(".floorplan__availability__intro").get_text(" ")
                   if group.select_one(".floorplan__availability__intro") else "")
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


def parse_hope_flower_html(html: str, source_url: str) -> dict[str, Any]:
    """Parse Hope + Flower's current server-rendered availability markup."""

    soup = BeautifulSoup(html, "html.parser")
    groups = soup.select(".js-plan-group")
    rows = soup.select(".js-plan-row")
    if not groups or not rows:
        raise OnniAdapterError(
            "Hope + Flower availability markup not found; refusing to return zero units"
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
        "building_id": "hope-flower",
        "building_name": "Hope + Flower",
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

    def __init__(self, timeout_seconds: int = 30, user_agent: str = "LARental-OnniAdapter/0.1"):
        self.timeout_seconds = timeout_seconds
        self.user_agent = user_agent

    def fetch_html(self, url: str) -> str:
        request = Request(url, headers={"User-Agent": self.user_agent})
        with urlopen(request, timeout=self.timeout_seconds) as response:
            content_type = response.headers.get_content_type()
            if content_type not in {"text/html", "application/xhtml+xml"}:
                raise OnniAdapterError(f"Expected HTML response, received {content_type}")
            return response.read().decode(response.headers.get_content_charset() or "utf-8", "replace")

    def parse(self, building_id: str, html: str, source_url: str | None = None) -> dict[str, Any]:
        if building_id != "hope-flower":
            raise OnniAdapterError(
                f"No unit-level parser is enabled for {building_id}; Olympic + Hill needs its own page-shape validation"
            )
        building = BUILDINGS[building_id]
        return parse_hope_flower_html(html, source_url or building.availability_url)

    def fetch(self, building_id: str) -> dict[str, Any]:
        building = BUILDINGS.get(building_id)
        if not building:
            raise OnniAdapterError(f"Unknown Onni building: {building_id}")
        html = self.fetch_html(building.availability_url)
        return self.parse(building_id, html, building.availability_url)

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
        return parse_unit_detail_html(html, detail_url, move_in_date, lease_term_months)


def parse_fixture(path: str | Path, building_id: str = "hope-flower") -> dict[str, Any]:
    adapter = OnniAdapter()
    fixture_path = Path(path).resolve()
    return adapter.parse(building_id, fixture_path.read_text(encoding="utf-8"), fixture_path.as_uri())
