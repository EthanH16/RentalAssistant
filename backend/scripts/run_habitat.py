#!/usr/bin/env python3
"""Print Habitat Residences' normalized official inventory."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from adapters.greystar import HabitatAdapter, parse_habitat_floorplans_html  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args()
    result = (
        parse_habitat_floorplans_html(args.fixture.read_text(encoding="utf-8"))
        if args.fixture else HabitatAdapter().fetch()
    )
    if args.summary:
        print(json.dumps({
            "building_id": result["building_id"],
            "units": len(result.get("units", [])),
            "floorplans": result.get("floorplans_count"),
            "fees": len(result.get("policies", {}).get("fees", [])),
            "concessions": result.get("policies", {}).get("concessions", []),
        }, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
