#!/usr/bin/env python3
"""Fetch one read-only Habitat move-in/lease-term quote."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from adapters.greystar import HabitatAdapter  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--unit-slug", required=True)
    parser.add_argument("--move-in-date", required=True)
    parser.add_argument("--lease-term", required=True, type=int)
    args = parser.parse_args()
    print(json.dumps(HabitatAdapter().fetch_unit_quote(
        args.unit_slug, args.move_in_date, args.lease_term
    ), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
