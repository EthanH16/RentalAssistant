#!/usr/bin/env python3
"""Run a read-only live scan for a Helio/RentCafe building."""

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
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args()
    result = HelioAdapter().fetch(args.building)
    if args.summary:
        print(json.dumps({
            "building_id": result["building_id"],
            "building_name": result["building_name"],
            "units_count": result["units_count"],
            "floorplans_count": result["floorplans_count"],
            "concessions": result["concessions"],
            "plans": result["plans"],
            "warnings": result["warnings"],
        }, indent=2))
    else:
        print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
