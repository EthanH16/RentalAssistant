#!/usr/bin/env python3
"""Fetch or parse Park Fifth public inventory and print normalized JSON."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from adapters.bozzuto import ParkFifthAdapter, parse_park_fifth_html  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path)
    args = parser.parse_args()
    result = (
        parse_park_fifth_html(args.fixture.read_text(encoding="utf-8"), args.fixture.resolve().as_uri())
        if args.fixture
        else ParkFifthAdapter().fetch()
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
