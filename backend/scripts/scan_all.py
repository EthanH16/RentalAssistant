#!/usr/bin/env python3
"""Fetch every supported direct-partnership building and update SQLite.

Network fetches run concurrently, while SQLite upserts are kept sequential so
one scan cannot contend with another writer. A failed building is reported and
does not prevent the remaining buildings from being scanned.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import sys
from time import monotonic
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from adapters.bozzuto import ParkFifthAdapter  # noqa: E402
from adapters.brookfield import BeaudryAdapter  # noqa: E402
from adapters.onni import BUILDINGS as ONNI_BUILDINGS, OnniAdapter  # noqa: E402
from adapters.related import BUILDINGS as RELATED_BUILDINGS, RelatedAdapter  # noqa: E402
from adapters.greystar import EdenAdapter, HabitatAdapter, TheaAdapter  # noqa: E402
from adapters.helio import BUILDINGS as HELIO_BUILDINGS, HelioAdapter  # noqa: E402
from storage.sqlite import InventoryDatabase  # noqa: E402


def _fetch_onni(building_id: str) -> dict[str, Any]:
    return OnniAdapter().fetch(building_id)


def _fetch_park_fifth(_: str) -> dict[str, Any]:
    return ParkFifthAdapter().fetch()


def _fetch_beaudry(_: str) -> dict[str, Any]:
    return BeaudryAdapter().fetch()


def _fetch_related(building_id: str) -> dict[str, Any]:
    return RelatedAdapter().fetch(building_id)


def _fetch_eden(_: str) -> dict[str, Any]:
    return EdenAdapter().fetch()


def _fetch_thea(_: str) -> dict[str, Any]:
    return TheaAdapter().fetch()


def _fetch_habitat(_: str) -> dict[str, Any]:
    return HabitatAdapter().fetch()


def _fetch_helio(building_id: str) -> dict[str, Any]:
    return HelioAdapter().fetch(building_id)


Job = tuple[str, str, Callable[[str], dict[str, Any]]]


def _jobs() -> dict[str, Job]:
    jobs: dict[str, Job] = {}
    for building_id in ONNI_BUILDINGS:
        jobs[building_id] = ("onni", building_id, _fetch_onni)
    jobs["park-fifth"] = ("bozzuto", "park-fifth", _fetch_park_fifth)
    jobs["beaudry"] = ("brookfield", "beaudry", _fetch_beaudry)
    for building_id in RELATED_BUILDINGS:
        jobs[building_id] = ("related", building_id, _fetch_related)
    jobs["the-eden"] = ("greystar", "the-eden", _fetch_eden)
    jobs["thea"] = ("greystar_spaces", "thea", _fetch_thea)
    jobs["habitat"] = ("greystar_spaces", "habitat", _fetch_habitat)
    for building_id in HELIO_BUILDINGS:
        jobs[building_id] = ("rentcafe_helio", building_id, _fetch_helio)
    return jobs


def _official_url(building_id: str, result: dict[str, Any]) -> str | None:
    if building_id == "park-fifth":
        return "https://www.parkfifthtower.com/floor-plans/"
    if building_id in RELATED_BUILDINGS:
        return RELATED_BUILDINGS[building_id].detail_url
    if building_id == "the-eden":
        return "https://edendtla.com/floorplans/"
    if building_id == "thea":
        return "https://theadtla.com/floor-plan/"
    if building_id == "habitat":
        return "https://habitat-residences.com/floorplans/"
    if building_id in HELIO_BUILDINGS:
        return HELIO_BUILDINGS[building_id]["inventory_url"]
    return result.get("source_url")


def _contact(building_id: str, result: dict[str, Any]) -> dict[str, Any] | None:
    if building_id == "park-fifth":
        return {"leasing": "parkfifth@bozzutoliving.com", "phone": "877-540-0401"}
    if building_id == "the-eden":
        return result.get("contact")
    return result.get("contact")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db", type=Path, default=ROOT / "data" / "rentalassistant.sqlite3"
    )
    parser.add_argument(
        "--workers", type=int, default=4,
        help="Maximum concurrent official-site fetches (default: 4)",
    )
    parser.add_argument(
        "--building", action="append", dest="buildings",
        help="Scan only this building id; repeat for multiple buildings",
    )
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers must be at least 1")

    all_jobs = _jobs()
    building_ids = args.buildings or list(all_jobs)
    unknown = sorted(set(building_ids) - set(all_jobs))
    if unknown:
        parser.error(f"unknown building id(s): {', '.join(unknown)}")

    database = InventoryDatabase(args.db)
    database.initialize()
    started = monotonic()
    successes: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []

    print(
        f"Scanning {len(building_ids)} buildings with up to "
        f"{min(args.workers, len(building_ids))} concurrent fetches...",
        flush=True,
    )
    with ThreadPoolExecutor(max_workers=min(args.workers, len(building_ids))) as pool:
        future_map = {
            pool.submit(all_jobs[building_id][2], building_id): building_id
            for building_id in building_ids
        }
        for index, future in enumerate(as_completed(future_map), start=1):
            building_id = future_map[future]
            try:
                result = future.result()
                summary = database.upsert_scan(
                    result,
                    official_url=_official_url(building_id, result),
                    contact=_contact(building_id, result),
                )
                item = {
                    "building_id": building_id,
                    "units_seen": summary.units_seen,
                    "units_added": summary.units_added,
                    "units_changed": summary.units_changed,
                    "units_marked_inactive": summary.units_marked_inactive,
                }
                successes.append(item)
                print(
                    f"[{index}/{len(building_ids)}] {building_id}: "
                    f"{summary.units_seen} units saved",
                    flush=True,
                )
            except Exception as exc:  # keep the all-scan resilient per building
                failures.append({"building_id": building_id, "error": str(exc)})
                print(
                    f"[{index}/{len(building_ids)}] {building_id}: FAILED - {exc}",
                    file=sys.stderr,
                    flush=True,
                )

    output = {
        "database": str(args.db),
        "elapsed_seconds": round(monotonic() - started, 2),
        "requested": len(building_ids),
        "succeeded": len(successes),
        "failed": len(failures),
        "scans": sorted(successes, key=lambda item: item["building_id"]),
        "failures": sorted(failures, key=lambda item: item["building_id"]),
    }
    print(json.dumps(output, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
