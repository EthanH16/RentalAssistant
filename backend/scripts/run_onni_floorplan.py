#!/usr/bin/env python3
"""Fetch Hope + Flower floor-plan diagram metadata or download its assets."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from adapters.onni import OnniAdapter  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("floor_plan", help="Hope + Flower slug, such as plan-a or a2")
    parser.add_argument("--download-dir", type=Path, help="Optional directory for downloaded diagram assets")
    args = parser.parse_args()
    result = OnniAdapter().fetch_floorplan_media(args.floor_plan, download_dir=args.download_dir)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
