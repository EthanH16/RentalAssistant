#!/usr/bin/env python3
"""Fetch and print the current Beaudry inventory from the official site."""

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
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args()
    result = BeaudryAdapter().fetch()
    if args.summary:
        from collections import Counter
        print(json.dumps({
            "building": result["building_name"],
            "checked_at": result["checked_at"],
            "units": len(result["units"]),
            "floor_plans": Counter(u["floor_plan"] for u in result["units"]),
            "beds": Counter(u["beds"] for u in result["units"]),
            "price_range": [min(u["base_rent_min"] for u in result["units"]), max(u["base_rent_max"] for u in result["units"])],
            "concessions": result["policies"]["concessions"],
            "fee_count": len(result["policies"]["fees"]),
        }, indent=2))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
