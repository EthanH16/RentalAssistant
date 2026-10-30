"""Read-only Park Fifth / Bozzuto availability adapter.

Park Fifth publishes inventory in server-rendered HTML.  The adapter parses
the public floor-plan result cards and fee tables, follows the site's own
pagination links, and fails closed when the expected result shape disappears.
It does not submit applications or interact with resident portals.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import gzip
import json
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlsplit, urlunsplit
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
COMMUNITY_FEES_BASE = "https://communityfees.bozzuto.com"
COMMUNITY_FEES_SITE_ID = "7C65F7"
COMMUNITY_FEES_URL = f"{COMMUNITY_FEES_BASE}/expense-guide-embed/{COMMUNITY_FEES_SITE_ID}/?situational_fee=false"


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


def _iso_date(value: str) -> str:
    for pattern in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(value.strip(), pattern).date().isoformat()
        except ValueError:
            continue
    raise ParkFifthAdapterError("Move-in date must be YYYY-MM-DD or MM/DD/YYYY")


def _display_date(iso_date: str) -> str:
    parsed = datetime.strptime(iso_date, "%Y-%m-%d")
    return f"{parsed.month}/{parsed.day}/{parsed.year}"


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


def parse_fee_widget_html(html: str, source_url: str = COMMUNITY_FEES_URL) -> dict[str, Any]:
    """Parse Park Fifth's official Bozzuto community-fee widget."""

    soup = BeautifulSoup(html, "html.parser")
    container = soup.select_one(".rfwa-expense-guide-container")
    if not container:
        raise ParkFifthAdapterError("Park Fifth fee widget container was not found")
    fees: list[dict[str, Any]] = []
    for table in container.select("table.rfwa-expense-list"):
        accordion = table.find_parent(class_="rfwa-accordion-item")
        heading = accordion.select_one(".rfwa-group-title") if accordion else None
        category = _clean(heading.get_text(" ")) if heading else None
        description = accordion.select_one(".rfwa-group-description") if accordion else None
        for row in table.select("tbody tr"):
            cells = row.select("td")
            if len(cells) < 4:
                continue
            label = _clean(cells[0].get_text(" "))
            if not label:
                continue
            tooltip = cells[1].select_one("[data-tooltip]")
            fees.append({
                "name": label,
                "cost": _clean(cells[1].get_text(" ")),
                "frequency": _clean(cells[2].get_text(" ")),
                "type": _clean(cells[3].get_text(" ")),
                "category": category,
                "category_description": _clean(description.get_text(" ")) if description else None,
                "tooltip": tooltip.get("data-tooltip") if tooltip else None,
                "source_url": source_url,
                "provider": "bozzuto_community_fees",
            })
    if not fees:
        raise ParkFifthAdapterError("Park Fifth fee widget returned no fee rows")
    footer = container.select_one(".rfwa-expense-footer")
    title = container.select_one("h2")
    description = container.select_one("h2 + p")
    return {
        "provider": "bozzuto_community_fees",
        "site_id": COMMUNITY_FEES_SITE_ID,
        "source_url": source_url,
        "status": "parsed",
        "title": _clean(title.get_text(" ")) if title else None,
        "description": _clean(description.get_text(" ")) if description else None,
        "fees": fees,
        "footer": _clean(footer.get_text(" ")) if footer else None,
        "export_url": f"{COMMUNITY_FEES_BASE}/property/{COMMUNITY_FEES_SITE_ID}/export",
    }


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


def parse_park_fifth_calculator_html(
    html: str,
    source_url: str,
    unit_number: str,
    move_in_date: str,
    lease_term_months: int,
) -> dict[str, Any]:
    """Parse an explicit SightMap calculator response, if it contains one.

    SightMap is a client-rendered application.  The public unit page gives us
    the official calculator URL, but the calculator may return only an app
    shell or an error page to a non-browser client.  This parser therefore
    fails closed: it only returns a quote when the response contains an
    explicit base/total price for the requested date and term.
    """

    soup = BeautifulSoup(html, "html.parser")
    text = _clean(soup.get_text(" "))
    base = None
    total = None
    for label, value in re.findall(r"([^$]{0,80})\$\s*([0-9][0-9,]*(?:\.\d{1,2})?)", text):
        label_lower = label.lower()
        amount = float(value.replace(",", ""))
        if "total" in label_lower and total is None:
            total = amount
        elif "base" in label_lower and base is None:
            base = amount
    if base is None and total is None:
        raise ParkFifthAdapterError(
            "Park Fifth's SightMap calculator returned no explicit quote; "
            "the browser-rendered calculator requires an interactive session"
        )
    return {
        "provider": "bozzuto_sightmap",
        "building_id": BUILDING.building_id,
        "building_name": BUILDING.name,
        "source_url": source_url,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "status": "ok",
        "unit_id": unit_number.lstrip("#"),
        "move_in_date": move_in_date,
        "lease_term_months": lease_term_months,
        "base_rent": base,
        "total_monthly_price": total,
        "raw_text": text,
        "warnings": ["Read-only calculator response; does not submit an application."],
    }


def _json_response_body(raw: bytes) -> str:
    """Decode a JSON response, including SightMap's gzip-compressed payload."""

    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return raw.decode("utf-8", "replace")


def _sightmap_app_config(html: str) -> dict[str, Any]:
    """Extract the public SightMap app configuration from its HTML shell."""

    marker = "window.__APP_CONFIG__"
    start = html.find(marker)
    if start < 0:
        raise ParkFifthAdapterError("Park Fifth SightMap page has no public app configuration")
    object_start = html.find("=", start) + 1
    try:
        config, _ = json.JSONDecoder().raw_decode(html[object_start:].lstrip())
    except json.JSONDecodeError as exc:
        raise ParkFifthAdapterError("Park Fifth SightMap app configuration is not valid JSON") from exc
    if not isinstance(config, dict):
        raise ParkFifthAdapterError("Park Fifth SightMap app configuration has an unexpected shape")
    return config


def parse_sightmap_pricing_json(
    payload: dict[str, Any],
    unit_number: str,
    move_in_date: str,
    lease_term_months: int,
    *,
    pricing_url: str,
    calculator_url: str,
) -> dict[str, Any]:
    """Parse the official SightMap pricing response for one requested quote.

    SightMap may return a single advertised-price option with a null lease
    term. That is not a valid date/term quote, so this parser fails closed and
    reports the available option metadata instead of guessing.
    """

    data = payload.get("data") if isinstance(payload, dict) else None
    options = data.get("options") if isinstance(data, dict) else None
    if not isinstance(options, list):
        raise ParkFifthAdapterError("Park Fifth SightMap pricing response has no options list")
    selected = next(
        (
            option for option in options
            if isinstance(option, dict) and option.get("lease_term") == lease_term_months
        ),
        None,
    )
    if selected is None:
        available_terms = [
            option.get("lease_term")
            for option in options
            if isinstance(option, dict) and option.get("lease_term") is not None
        ]
        if not available_terms:
            raise ParkFifthAdapterError(
                "Park Fifth SightMap exposed only the current advertised price; "
                "it did not expose date/lease-term pricing. The official calculator "
                "requires the Apply portal for this unit."
            )
        raise ParkFifthAdapterError(
            f"Park Fifth did not return a {lease_term_months}-month option for "
            f"{move_in_date}; available terms: {', '.join(map(str, available_terms))}"
        )
    base = selected.get("price")
    total = _money(selected.get("total_display_price"))
    if base is None and total is None:
        raise ParkFifthAdapterError("Park Fifth SightMap returned a term option without pricing")
    return {
        "provider": "bozzuto_sightmap_api",
        "building_id": BUILDING.building_id,
        "building_name": BUILDING.name,
        "source_url": pricing_url,
        "calculator_url": calculator_url,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "status": "ok",
        "unit_id": unit_number.lstrip("#"),
        "move_in_date": move_in_date,
        "lease_term_months": lease_term_months,
        "base_rent": float(base) if base is not None else None,
        "total_monthly_price": total,
        "lease_term_options": options,
        "warnings": ["Read-only SightMap API response; does not submit an application."],
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

    def fetch_json(self, url: str, *, referer: str | None = None) -> dict[str, Any]:
        request = Request(
            url,
            headers={
                "User-Agent": self.user_agent,
                "Accept": "application/json",
                **({"Referer": referer} if referer else {}),
            },
        )
        with urlopen(request, timeout=self.timeout_seconds) as response:
            if response.status != 200:
                raise ParkFifthAdapterError(f"Park Fifth JSON endpoint returned HTTP {response.status}")
            try:
                payload = json.loads(_json_response_body(response.read()))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise ParkFifthAdapterError("Park Fifth returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise ParkFifthAdapterError("Park Fifth JSON endpoint returned an unexpected shape")
        return payload

    def fetch_fee_guide(self) -> dict[str, Any]:
        """Fetch Park Fifth's official building-level Bozzuto fee widget."""

        request = Request(
            COMMUNITY_FEES_URL,
            headers={
                "User-Agent": self.user_agent,
                "Referer": BUILDING.availability_url,
                "Origin": "https://www.parkfifthtower.com",
                "Accept": "text/html,application/xhtml+xml",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                if response.status != 200:
                    raise ParkFifthAdapterError(f"Park Fifth fee widget returned HTTP {response.status}")
                html = response.read().decode(response.headers.get_content_charset() or "utf-8", "replace")
        except ParkFifthAdapterError:
            raise
        except (OSError, UnicodeDecodeError) as exc:
            raise ParkFifthAdapterError(f"Park Fifth fee widget could not be fetched: {exc}") from exc
        return parse_fee_widget_html(html, COMMUNITY_FEES_URL)

    def fetch_unit_quote(
        self,
        unit_number: str,
        move_in_date: str,
        lease_term_months: int,
    ) -> dict[str, Any]:
        """Fetch a requested Park Fifth date/term quote without persistence.

        The official Park Fifth page links each unit to a SightMap expense
        calculator. We use SightMap's own public JSON metadata and leasing
        endpoint first. If the provider exposes only the advertised price and
        not a lease-term option, the method raises instead of silently treating
        that advertised price as a date/term quote.
        """

        if lease_term_months <= 0:
            raise ParkFifthAdapterError("Lease term must be a positive number of months")
        iso_date = _iso_date(move_in_date)
        target = unit_number.lstrip("#").strip()
        inventory = self.fetch()
        unit = next((item for item in inventory["units"] if item.get("unit_id") == target), None)
        if not unit:
            raise ParkFifthAdapterError(f"No current Park Fifth unit matched apartment {unit_number}")
        detail_url = unit.get("detail_url")
        if not detail_url:
            raise ParkFifthAdapterError(f"Park Fifth apartment {unit_number} has no official detail page")
        detail_html = self.fetch_html(detail_url)
        # The detail page's calculator link is the authoritative SightMap
        # endpoint.  Reconstruct it from the detail HTML instead of guessing
        # a provider API path.
        detail_soup = BeautifulSoup(detail_html, "html.parser")
        calculator_anchor = detail_soup.select_one("a[href*='sightmap.com'][href*='expense_calculator']")
        if not calculator_anchor:
            raise ParkFifthAdapterError(f"Park Fifth apartment {unit_number} has no official cost calculator link")
        calculator = urljoin(detail_url, calculator_anchor.get("href"))
        parts = urlsplit(calculator)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        query.update({"unit_number": target, "mode": "expense_calculator"})
        calculator_url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
        calculator_html = self.fetch_html(calculator_url)
        config = _sightmap_app_config(calculator_html)
        sightmaps = config.get("sightmaps")
        if not isinstance(sightmaps, list) or not sightmaps or not isinstance(sightmaps[0], dict):
            raise ParkFifthAdapterError("Park Fifth SightMap page has no public sightmap API link")
        sightmap_url = sightmaps[0].get("href")
        if not isinstance(sightmap_url, str) or not sightmap_url:
            raise ParkFifthAdapterError("Park Fifth SightMap page has no usable API link")
        sightmap_payload = self.fetch_json(sightmap_url, referer=calculator_url)
        data = sightmap_payload.get("data")
        sightmap_unit = next(
            (
                item for item in data.get("units", [])
                if isinstance(item, dict) and str(item.get("unit_number")) == target
            ),
            None,
        ) if isinstance(data, dict) and isinstance(data.get("units"), list) else None
        if not sightmap_unit:
            raise ParkFifthAdapterError(
                f"Park Fifth SightMap does not currently expose apartment {target}; "
                "the public floor-plan page and calculator are out of sync"
            )
        pricing_url = sightmap_unit.get("leasing_price_url")
        if not isinstance(pricing_url, str) or not pricing_url:
            raise ParkFifthAdapterError(f"Park Fifth apartment {target} has no public pricing endpoint")
        pricing_query = {"date": iso_date}
        pricing_query.update({key: value for key, value in query.items() if key.startswith("oll_")})
        pricing_url = f"{pricing_url}&{urlencode(pricing_query)}"
        pricing_payload = self.fetch_json(pricing_url, referer=calculator_url)
        quote = parse_sightmap_pricing_json(
            pricing_payload,
            target,
            iso_date,
            lease_term_months,
            pricing_url=pricing_url,
            calculator_url=calculator_url,
        )
        quote["calculator_url"] = calculator_url
        quote["floor_plan"] = unit.get("floor_plan")
        quote["advertised_unit"] = unit
        return quote

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
        try:
            first["policies"]["fee_guide"] = self.fetch_fee_guide()
            first["policies"]["fees"] = first["policies"]["fee_guide"]["fees"]
        except ParkFifthAdapterError as exc:
            first["policies"]["fee_guide"] = {
                "provider": "bozzuto_community_fees",
                "site_id": COMMUNITY_FEES_SITE_ID,
                "source_url": COMMUNITY_FEES_URL,
                "status": "fetch_failed",
                "fetch_error": str(exc),
                "fees": [],
            }
            first["warnings"].append("The official Park Fifth fee widget could not be fetched during this scan; fee rows require a retry.")
        return first
