#!/usr/bin/env python3
"""Fetch one Related building and print normalized JSON."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from adapters.related import BUILDINGS, RelatedAdapter  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("building", choices=sorted(BUILDINGS))
    parser.add_argument("--summary", action="store_true")
    parser.add_argument("--no-details", action="store_true")
    args = parser.parse_args()
    result = RelatedAdapter().fetch(args.building, fetch_details=not args.no_details)
    if args.summary:
        result = {k: result[k] for k in ("provider", "building_id", "building_name", "checked_at", "pages_scanned", "policies")}
        result["unit_count"] = len(RelatedAdapter().fetch(args.building, fetch_details=False)["units"])
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
