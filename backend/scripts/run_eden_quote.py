#!/usr/bin/env python3
"""Fetch one The Eden date/lease-term quote without writing SQLite."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from adapters.greystar import EdenAdapter  # noqa: E402


def _slug_from_detail_url(url: str) -> str | None:
    match = re.search(r"/unit-([^/]+)/?", url)
    return match.group(1) if match else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--unit-slug", help="Eden renderable unit slug")
    target.add_argument("--unit-number", help="Resident-facing apartment number, looked up from current inventory")
    parser.add_argument("--move-in-date", required=True, help="YYYY-MM-DD or MM/DD/YYYY")
    parser.add_argument("--lease-term", required=True, type=int, help="Lease term in months")
    args = parser.parse_args()

    adapter = EdenAdapter()
    unit_slug = args.unit_slug
    if args.unit_number:
        inventory = adapter.fetch()
        unit = next((item for item in inventory["units"] if item["unit_id"] == args.unit_number.lstrip("#")), None)
        if not unit:
            parser.error(f"No current Eden unit matched apartment {args.unit_number}")
        unit_slug = _slug_from_detail_url(unit["detail_url"])
        if not unit_slug:
            parser.error("The matched Eden unit has no detail slug")

    result = adapter.fetch_unit_quote(unit_slug, args.move_in_date, args.lease_term)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
