#!/usr/bin/env python3
"""Fetch and print one official Beaudry date/term quote."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from adapters.brookfield import BeaudryAdapter  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--unit-number", required=True)
    parser.add_argument("--move-in-date", required=True, help="YYYY-MM-DD or MM/DD/YYYY")
    parser.add_argument("--lease-term", required=True, type=int, help="Lease term in months")
    args = parser.parse_args()
    result = BeaudryAdapter().fetch_unit_quote(
        args.unit_number,
        args.move_in_date,
        args.lease_term,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
