#!/usr/bin/env python3
"""Fetch Thea lease terms and build a read-only expense-calculator URL."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from adapters.greystar import TheaAdapter  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider-unit-id", required=True, help="Engrain data-spaces-unit-id")
    parser.add_argument("--apartment", required=True, help="Resident-facing apartment number, e.g. 3906")
    parser.add_argument("--move-in-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--lease-term", required=True, type=int, help="Lease term in months")
    args = parser.parse_args()
    print(json.dumps(TheaAdapter().fetch_unit_quote(args.provider_unit_id, args.apartment, args.move_in_date, args.lease_term), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
