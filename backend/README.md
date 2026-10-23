# Building Inventory Backend

This folder is reserved for the backend and provider adapters that will eventually power the building map, live inventory search and price matching.

## Current scope

The first implementation target is the **Onni provider**, using **Hope + Flower** as the first building. **825 South Hill** is now validated against the same server-rendered availability shape. Olympic + Hill is also validated; it uses the same provider family but publishes unit rows on linked plan pages rather than directly on the floorplan index.

The first milestone intentionally excludes:

- SQLite or another database;
- scheduled polling;
- client-facing recommendations;
- writing dynamic inventory into the Vault;
- automatic application or registration actions.

The adapter produces inspectable normalized JSON and preserves the provider-source boundary. Persistence and polling can be added without redesigning the provider parser.

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
