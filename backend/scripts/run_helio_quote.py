#!/usr/bin/env python3
"""Fetch an ad-hoc official lease quote for an Arya or Venue unit."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from adapters.helio import BUILDINGS, HelioAdapter  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("building", choices=sorted(BUILDINGS))
    parser.add_argument("--application-url", required=True)
    parser.add_argument("--move-in-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--lease-term", required=True, type=int, help="Lease term in months")
    args = parser.parse_args()
    result = HelioAdapter().fetch_unit_quote(
        args.building, args.application_url, args.move_in_date, args.lease_term
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
