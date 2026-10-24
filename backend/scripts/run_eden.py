#!/usr/bin/env python3
"""Print The Eden's normalized official inventory."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from adapters.greystar import EdenAdapter  # noqa: E402
from adapters.greystar.eden import parse_floorplans_html  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path)
    args = parser.parse_args()
    result = parse_floorplans_html(args.fixture.read_text(encoding="utf-8")) if args.fixture else EdenAdapter().fetch()
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
