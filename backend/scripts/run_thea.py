#!/usr/bin/env python3
"""Fetch and print Thea's current official inventory and fee policy."""

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
    parser.add_argument("--json", action="store_true", help="Print the complete normalized result")
    args = parser.parse_args()
    result = TheaAdapter().fetch()
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(json.dumps({
            "building": result["building_name"],
            "plans": result["floorplans_count"],
            "units": result["units_count"],
            "concessions": result["policies"]["concessions"],
            "fee_rows": len(result["policies"].get("fees") or []),
            "warnings": result["warnings"],
        }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
