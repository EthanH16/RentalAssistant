#!/usr/bin/env python3
"""Fetch or parse one Onni building and print normalized JSON."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from adapters.onni import OnniAdapter, parse_fixture  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("building", choices=["hope-flower", "olympic-hill"])
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--unit-url", help="SecureCafe application URL for one unit")
    parser.add_argument("--move-in-date", help="Move-in date, for example 12/13/2026")
    parser.add_argument("--lease-term", type=int, help="Lease term in months")
    args = parser.parse_args()
    adapter = OnniAdapter()
    if args.unit_url:
        result = adapter.fetch_unit_quote(args.unit_url, args.move_in_date, args.lease_term)
    else:
        result = parse_fixture(args.fixture, args.building) if args.fixture else adapter.fetch(args.building)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
