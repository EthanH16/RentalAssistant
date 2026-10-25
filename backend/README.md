# Building Inventory Backend

This folder is reserved for the backend and provider adapters that will eventually power the building map, live inventory search and price matching.

The reusable implementation checklist is in
[`ADAPTER_SOP.md`](ADAPTER_SOP.md). It covers provider discovery, inventory
patterns, fee and concession extraction, ad-hoc quotes, validation, and SQLite
persistence.

## Current scope

The first implementation target is the **Onni provider**, using **Hope + Flower** as the first building. **825 South Hill** is now validated against the same server-rendered availability shape. Olympic + Hill is also validated; it uses the same provider family but publishes unit rows on linked plan pages rather than directly on the floorplan index.

Park Fifth is the first non-Onni adapter target. Its Bozzuto-managed public floor-plan page publishes server-rendered inventory cards, pagination, total monthly leasing price, base rent, availability date, and a public fee table. The adapter lives in `adapters/bozzuto/park_fifth.py` and writes to the same normalized SQLite schema.

Related is the next provider-level adapter. The Grand by Gehry, Top of The Grand, and The Emerson share Related's property-filtered search and unit-detail pattern. The adapter lives in `adapters/related/adapter.py`; it uses the search page for discovery and follows official unit pages for resident-facing apartment numbers and application URLs.

Beaudry is the first Brookfield/LyvWell adapter. The current Beaudry site is
LyvWell-branded and loads the full live inventory from the official
`/wp-json/middleware/v1/getUnits/` endpoint. The adapter lives in
`adapters/brookfield/beaudry.py`; it captures resident-facing apartment
numbers, floor plans, total monthly leasing price, base-rent references,
availability dates, specials, and the structured fee payload returned by the
official endpoint. It also reads Beaudry's official fee-guide page as the
authoritative building-level fee source; the inventory fee payload is retained
only as a diagnostic cross-check. The first live scan found 75 units.

The Eden is the first Greystar adapter. Its official floorplans page embeds a
structured JSON application state block with resident-facing unit numbers,
floor plans, availability, the selected lease-term price, all available term
quotes, and the page's Total Monthly Leasing Price. The adapter lives in
`adapters/greystar/eden.py` and preserves both the total monthly price and the
base-rent reference in its normalized JSON output.

The Eden adapter also fetches the official building-level Engrain expense
guide mounted in the floorplans page. It reads the widget asset ID, encodes it
with the provider's Sqids alphabet, calls the read-only expense endpoint, and
normalizes required, optional, situational, recurring, one-time, utility,
parking, deposit, and third-party fee rows into `policies.fee_guide` and
`policies.fees`. If the widget is unavailable, the inventory scan still
completes and records the fetch error for retry instead of presenting an empty
fee policy as authoritative.

The Eden also has a separate ad-hoc quote path. It follows the official unit
page into the SecureCafe lease handoff, selects a requested move-in date and
lease term, and parses the returned base rent, recurring charges, total
monthly price, fee rows, and all term quotes for that date. It is deliberately
read-only and does not write SQLite or alter the broad inventory scan:

```bash
python3 backend/scripts/run_eden_quote.py \
  --unit-number 330 \
  --move-in-date 2026-11-04 \
  --lease-term 12

# Or use the provider's current public unit slug directly:
python3 backend/scripts/run_eden_quote.py \
  --unit-slug 4b96b2f8e68ffda712c2e826356d6d21 \
  --move-in-date 2026-11-04 \
  --lease-term 12
```

The unit-number form performs a fresh broad inventory lookup first, then uses
the matching public unit page. The quote response is suitable for an
on-demand client comparison; it is not a substitute for the persisted
building-level fees/concessions or the current inventory table.

THEA at Metropolis is the first Greystar Spaces/Engrain adapter. Its official
floor-plan page exposes plan cards through a public Spaces AJAX response, but
the plan cards are aggregates. The adapter follows each available plan into
its plan-detail response and stores the resident-facing apartment number,
provider unit ID, floor metadata, plan, square footage, availability date,
base rent, total monthly leasing price, and RentCafe application URL. It also
fetches THEA's building-level Engrain fee guide and the official homepage
promotion text (currently the up-to-eight-weeks-free and look-and-lease offer).

Run a read-only live scan:

```bash
python3 backend/scripts/run_thea.py
python3 backend/scripts/run_thea.py --json
```

Thea's ad-hoc lease-term path uses the provider's public lease-term request
and returns the read-only SightMap expense-calculator URL for a selected
apartment, move-in date, and lease term:

```bash
python3 backend/scripts/run_thea_quote.py \
  --provider-unit-id 10342778 \
  --apartment 3906 \
  --move-in-date 2026-11-21 \
  --lease-term 12
```

The broad scan and ad-hoc quote are intentionally separate. The broad scan
does not attempt every lease-term/date combination for every unit, and it does
not automatically apply a marketing concession to rent.

The first milestone intentionally excludes:

- scheduled polling;
- client-facing recommendations;
- writing dynamic inventory into the Vault;
- automatic application or registration actions.

The adapter produces inspectable normalized JSON and preserves the provider-source boundary. Persistence and polling can be added without redesigning the provider parser.

## SQLite MVP

The first persistence layer is intentionally two tables:

- `buildings`: one row per approved building, with `fees_json`,
  `concessions_json` and contact metadata;
- `inventory`: one current row per `(building_id, unit_id)`, with price,
  availability, application URL and compact `scan_history_json`.

Run a fixture scan without network access:

```bash
python3 backend/scripts/scan_onni.py hope-flower \
  --fixture backend/fixtures/onni/hope-flower-availability.sample.html \
  --db backend/data/rentalassistant.sqlite3
```

Run a live scan for any supported Onni building:

```bash
python3 backend/scripts/scan_onni.py 825-south-hill \
  --db backend/data/rentalassistant.sqlite3
```

Scan every currently supported Onni building in one run:

```bash
python3 backend/scripts/scan_onni.py all \
  --db backend/data/rentalassistant.sqlite3

# Park Fifth / Bozzuto
python3 backend/scripts/run_park_fifth.py
python3 backend/scripts/scan_park_fifth.py \
  --db backend/data/rentalassistant.sqlite3

# Related / The Grand, Top of The Grand, or The Emerson
python3 backend/scripts/run_related.py grand-residences --summary
python3 backend/scripts/scan_related.py grand-residences \
  --db backend/data/rentalassistant.sqlite3

# Brookfield / LyvWell / Beaudry
python3 backend/scripts/run_beaudry.py --summary
python3 backend/scripts/scan_beaudry.py \
  --db backend/data/rentalassistant.sqlite3

# Greystar / The Eden
python3 backend/scripts/run_eden.py
python3 backend/scripts/scan_eden.py \
  --db backend/data/rentalassistant.sqlite3

# All currently supported direct-partnership buildings
python3 backend/scripts/scan_all.py \
  --db backend/data/rentalassistant.sqlite3
```

`scan_all.py` currently covers the implemented live adapters: Hope + Flower,
Olympic + Hill, 825 South Hill, Park Fifth, Beaudry, The Grand Residences,
Top of The Grand, The Emerson, The Eden, and Thea at Metropolis. It fetches buildings
concurrently, writes SQLite sequentially, and continues scanning if one
official site fails. Use `--workers 2` or `--workers 1` if a provider
rate-limits requests. Use repeated `--building BUILDING_ID` arguments to run
a subset. Buildings without an implemented adapter are intentionally not
included in this runner yet.

If your shell is already inside `backend/`, use `--db data/rentalassistant.sqlite3`
instead; otherwise `backend/data/...` would create an unintended nested path.

The `all` command processes Hope + Flower, Olympic + Hill and 825 South Hill,
then prints a per-building summary. Successful buildings are committed even
if another building fails; the command exits non-zero and lists failures so a
partial provider outage cannot look like a clean full scan.

The database is local generated state and is ignored by Git. A successful scan
upserts current units; a unit missing from a successful scan becomes
`inactive`, while its row and history remain available for later comparison.

Inspect the database as readable JSON:

```bash
python3 backend/scripts/inspect_db.py \
  --db backend/data/rentalassistant.sqlite3

# One building only, including inactive units and scan history:
python3 backend/scripts/inspect_db.py \
  --db backend/data/rentalassistant.sqlite3 \
  --building 825-south-hill \
  --include-inactive --history
```

The inspector now defaults to compact terminal tables. Add `--format json`
when you need the full machine-readable output.

For ad-hoc SQL, the native SQLite CLI is also useful:

```bash
sqlite3 backend/data/rentalassistant.sqlite3
```

```sql
.headers on
.mode column
SELECT building_id, unit_id, floor_plan, min_price, max_price, status, available_date
FROM inventory
ORDER BY building_id, min_price;
```

## Planned layout

```text
backend/
  adapters/
    onni/              # provider-level adapter
  fixtures/
    onni/              # saved raw responses for repeatable tests
  output/              # generated normalized JSON; not Vault source-of-truth
  scripts/             # manual inspection and adapter runs
  tests/
  README.md
```

## Source-of-truth boundary

- The Vault building index remains the human-readable roster and partnership source.
- The map’s building JSON remains the code-readable roster replica.
- This backend owns only provider responses and normalized live-inventory output.
- No dynamic provider result should be copied into a building profile until it becomes a reusable, dated fact.

## Adapter principle

Build the adapter around the provider, not around one building:

```text
OnniAdapter
  ├── Hope + Flower input/config
  ├── 825 South Hill input/config
  └── Olympic + Hill input/config
```

The adapter must inspect the provider’s actual network/JSON response before parsing. It should not depend on brittle visual text scraping if a structured endpoint is available.

If the provider requires an authenticated portal, CAPTCHA, or an unavailable endpoint, the adapter should return a clear `needs_manual_review` result rather than bypassing controls or inventing inventory.

## Running the first adapter

Install the single parser dependency from `requirements.txt`, then run the fixture:

```bash
python3 -m pip install -r backend/requirements.txt
python3 backend/scripts/run_onni.py hope-flower \
  --fixture backend/fixtures/onni/hope-flower-availability.sample.html
```

The command prints normalized JSON. A live read uses the same parser and transport:

```bash
python3 backend/scripts/run_onni.py hope-flower
```

The live command is read-only and fails closed if the official page changes shape or returns a non-HTML response.

For a shortlisted unit, fetch a move-in-date/lease-term quote from its public SecureCafe detail page:

```bash
python scripts/run_onni.py hope-flower \
  --unit-url 'https://hopeflowerdtla.securecafe.com/onlineleasing/hope-flower/oleapplication.aspx?stepname=RentalOptions&myOlePropertyId=626694&FloorPlanID=3122773&UnitID=30308695&header=1' \
  --move-in-date 12/13/2026 \
  --lease-term 12
```

This returns the selected base rent, recurring charges exposed by SecureCafe, total monthly price, and all available term quotes for that move-in date. It does not submit an application.

## Building-level fees and concessions

The normal building fetch also returns a separate `policies` object. It parses the official page's fee guide into building-level records (application and deposits, required utilities, parking, pet rent, storage, and situational charges) and preserves non-numeric values such as `Allocated (Utilities)`, ranges, and percentage formulas. It also captures public concession banners and their stated terms.

These policies are deliberately not copied onto every unit. A broad scan can use `policies` together with inventory, while `--unit-url` remains the on-demand path for a specific unit's move-in date, lease term, recurring charges, and unit-specific special.

Olympic + Hill uses a two-step read: the official floorplan index supplies plan-level counts, prices, and public promotions; each linked plan page supplies the actual unit rows. The adapter merges those rows into the same normalized `units` shape used by Hope + Flower.

825 South Hill uses the same single-page server-rendered availability shape as
Hope + Flower, including unit rows, SecureCafe application links, fee-guide
tables and building-level policy metadata. Both buildings are parsed by the
shared Onni availability parser.
