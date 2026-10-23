# Onni Adapter — first provider investigation

## Target buildings

| Building | Role | Expected use |
|---|---|---|
| Hope + Flower | First target | Inspect the official site and identify the provider response shape |
| Olympic + Hill | Validation target | Confirm the adapter is provider-level, not building-specific |

## Investigation sequence

1. Record the official URL for each building.
2. Inspect the browser network requests while opening floor plans and availability.
3. Identify whether the site returns JSON, GraphQL, embedded state, or only rendered HTML.
4. Save one sanitized raw response as a fixture.
5. Document required request parameters, pagination and any building/property identifier.
6. Implement parsing against the fixture before making repeated live requests.
7. Compare normalized output for Hope + Flower and Olympic + Hill.
8. Add validation for missing units, invalid rents and changed response structure.

## First inspection result — Onni / Hope + Flower

Checked the official page on 2026-10-23:

- URL: `https://hopeflowerdtla.com/availability`
- The availability payload is server-rendered HTML; the first adapter does not need a hidden JSON endpoint.
- Unit rows use `.js-plan-row` and expose `data-unit`, `data-rent-min`, and `data-rent-max`.
- The parent `.js-plan-group` exposes `data-cat`, `data-length`, and `data-plan-slug`. Its intro text contains the plan name, bedroom category, and tower (`HOPE` or `FLOWER`).
- Each row contains square footage, a status or availability date, and a SecureCafe application link. The link includes `myOlePropertyId`, `FloorPlanID`, and `UnitID`.
- The parser is implemented in `backend/adapters/onni/adapter.py`. The saved sample fixture is intentionally small and sanitized; it is a parser test, not a live inventory snapshot.

## Second inspection result — Olympic + Hill

Checked the official page on 2026-10-23:

- URL: `https://www.rentolympicandhill.com/floorplans`
- It is also Onni/RentCafe, but the current page uses the newer RentCafe floor-plan widget. The visible page provides plan-level starting rent and available-count summaries, while the Hope + Flower unit-row attributes are not present.
- The page loads RentCafe widget assets and references property ID `1774338`; it also exposes official fee-guide and floor-plan assets.
- Olympic + Hill reuses the provider transport/configuration but has a two-step page shape: the floorplan index publishes summary cards, while each `/floorplans/plan-*` page publishes actual unit rows. The adapter validates the index, fetches each linked plan page read-only, and merges the normalized unit records.

## Normalized output target

The first JSON output should be easy to inspect and should not depend on SQLite:

```json
{
  "provider": "onni",
  "building_id": "hope-flower",
  "source_url": "https://…",
  "checked_at": "…",
  "status": "ok",
  "units": [
    {
      "unit_id": "…",
      "floor_plan": "studio",
      "beds": 0,
      "baths": 1,
      "sqft": null,
      "base_rent": null,
      "total_monthly_price": null,
      "available_date": null,
      "lease_terms": [],
      "concession_text": null,
      "source": "official-site"
    }
  ],
  "warnings": []
}
```

`null` is preferable to guessed values. The adapter must preserve the raw response reference and report warnings when a field is not available or changes shape.

## Definition of stable

The Onni adapter is ready for the next stage only when:

- the same provider adapter handles Hope + Flower and Olympic + Hill, with building-specific page parsers;
- fixture tests reproduce the expected normalized fields;
- a failed request cannot appear as zero available units;
- the output distinguishes base rent from total monthly price;
- availability and concession text retain source timestamps;
- the adapter reports structural changes for manual review;
- no login, CAPTCHA or access-control bypass is required.

Only after these conditions are met should we add polling and SQLite CRUD.
