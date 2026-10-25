# RentalAssistant Provider-Adapter SOP

This is the reusable engineering SOP for adding an official apartment-site
adapter. It is provider-oriented, not building-oriented. Building profiles
remain the source of partnership, contact, and reusable building facts; this
document governs live inventory and fee integrations.

## 1. Adapter goals

Every adapter has two separate responsibilities:

1. **Broad scan**: discover current floor plans and actual available units for
   initial search and SQLite persistence.
2. **Ad-hoc quote**: calculate one selected unit for a specific move-in date
   and lease term without scanning every date/term combination.

Do not combine these flows. A broad scan should be fast and stable. A quote
may make extra requests because it is only run for shortlisted units.

## 2. Official-site investigation

Before writing a parser:

1. Open the official property page and floor-plan/availability page.
2. Inspect the rendered page and browser network requests.
3. Identify the provider family and the authoritative request shape:
   - server-rendered HTML;
   - embedded JSON/state;
   - WordPress/REST/GraphQL endpoint;
   - public AJAX endpoint;
   - provider widget or iframe;
   - external application portal such as RentCafe.
4. Record property/building IDs, asset IDs, API URLs, required headers,
   pagination, linked plan URLs, and unit-detail URLs.
5. Save a sanitized fixture for each materially different response shape.
6. Confirm the page is readable without login, CAPTCHA, or access-control
   bypass. If not, stop with `needs_manual_review`.

The visual page is useful for understanding the user workflow, but structured
network responses should be preferred for extraction.

## 3. Recognize the inventory pattern

### Pattern A: direct unit rows

The availability page contains one row per apartment with attributes such as
unit number, rent, area, availability, and application URL. Parse rows
directly and retain the parent plan metadata.

Examples: Onni-style Hope + Flower and 825 South Hill pages.

### Pattern B: plan index plus linked plan pages

The index shows only plan-level starting rent and unit counts. Follow every
available plan link and parse its actual unit rows. Never persist the plan
count as if it were the number of unit records.

Example: Olympic + Hill.

### Pattern C: embedded unit JSON

The page contains a structured application-state block with unit records,
floor plans, prices, dates, and application links. Parse the JSON, validate
its shape, and preserve the raw provider payload.

Example: The Eden.

### Pattern D: plan AJAX plus plan-detail AJAX

The page initially contains configuration and filters. A public request returns
plan cards; a second request for each available plan returns the actual unit
cards. Parse both layers.

Example: Thea at Metropolis.

### Pattern E: provider search plus unit-detail pages

The provider search endpoint discovers properties or units, while official
unit pages contain the resident-facing apartment number and application URL.
Use the search response for discovery and the detail response for identity and
final unit fields.

Examples: Related and Brookfield/LyvWell patterns.

## 4. Unit identity and normalization

Every unit record must distinguish:

- `unit_id`: resident-facing apartment number, such as `5203` or `3906`;
- `provider_unit_id`: internal provider/Engrain/RentCafe identifier;
- `floor_plan`: resident-facing plan name;
- `floor_plan_id`: provider plan identifier, when available;
- `application_url`: official application or rental-options URL;
- `detail_url`: official unit-detail URL, when available.

Never show an internal provider ID as the apartment number. Preserve all IDs
in the provider payload so later quote requests can use the correct one.

Normalize these fields whenever available:

```text
unit_id, provider_unit_id, floor_plan, beds, baths, sqft,
base_rent_min, base_rent_max,
total_monthly_leasing_price_min, total_monthly_leasing_price_max,
available_date, available_now, pricing_status,
application_url, detail_url, provider_payload
```

Use `null` for unavailable values. Never convert placeholders such as `$-1`,
blank prices, or “call for pricing” into a real rent.

## 5. Base rent versus total monthly price

These are separate concepts:

- **Base rent**: the advertised rent for the residence.
- **Total Monthly Leasing Price**: base rent plus the provider’s included or
  selected monthly charges, according to that provider’s definition.

Store both when the official site exposes both. Do not calculate one from the
other unless the provider explicitly documents the included fee components.

The database’s existing `min_price`/`max_price` columns represent the selected
base-rent reference used for broad search. Preserve total monthly price in the
normalized provider payload and building/quote output until the schema needs a
dedicated column.

## 6. Fee-policy extraction

Fees are building-level policy data and must not be duplicated into every unit
row. Fee discovery is a required workstream, not an optional enhancement.

### 6.1 Fee-source decision tree

Check all of these locations in order, stopping only after the authoritative
source is identified or explicitly confirmed unavailable:

1. Official floor-plan page fee section, such as “Easy-to-Use Guide” or “The
   Fine Print, Simplified”.
2. Official fee-guide page or expandable fee sections.
3. Official embedded widget or fee API referenced by the page.
4. Official leasing/application page, only when it is clearly the building’s
   fee policy rather than a quote-specific charge.
5. Unit quote response only as a diagnostic or quote-specific supplement.

The page’s own explanatory text is important. For example, a provider may say
that displayed prices include mandatory monthly fees but exclude usage-based
utilities and move-in charges. Store that disclaimer with the fee policy.

### 6.2 Recognizing a fee widget

A fee widget commonly appears as an empty container in the initial HTML. Look
for:

- `iframe` elements;
- script names containing `expense`, `fee`, `pricing`, `widget`, or
  `engrain`;
- configuration objects containing an asset/property/community ID;
- `data-*` attributes containing an asset ID;
- external widget hosts;
- network requests made immediately after the fee section enters view;
- expandable buttons whose contents are absent from the initial HTML.

Do not conclude that fees are missing because the initial HTML has no rows.
Inspect the referenced JavaScript and network request first.

### 6.3 Widget/API implementation sequence

For a widget-backed fee guide:

1. Record the official page URL and the widget/container selector.
2. Identify the provider asset/property ID.
3. Identify the widget host, endpoint, HTTP method, and required headers.
4. Reproduce only the public read-only request.
5. Save one sanitized response fixture.
6. Parse category, label, amount type, frequency, timing, required status,
   optional status, situational status, third-party status, disclaimer, and
   tooltip.
7. Compare at least one fixed fee against the amount shown in the official
   unit or plan display. For example, if total monthly price exceeds base rent
   by $14, verify that the fee guide explains the $14 difference.
8. Store the complete normalized fee guide and the raw provider payload.

If the widget uses an encoded asset ID, document the encoding method and test
it independently. Do not hardcode an encoded value without retaining the
original official asset ID.

### 6.4 Fee categories to preserve

The normalized fee record should support at least:

- application and administrative fees;
- holding, security, and other deposits;
- fixed monthly mandatory charges;
- usage-based utilities;
- package, trash, pest, porter, and billing charges;
- parking and storage;
- pet deposit and pet rent;
- renter liability or renter-content programs;
- third-party insurance;
- move-in, move-out, transfer, late, returned-payment, and violation fees;
- percentage-based or screening-dependent charges.

The adapter must preserve ranges, percentages, “varies”, and usage-based
values instead of coercing them into a single numeric amount.

### 6.5 Policy fee versus quote fee

Keep these concepts separate:

| Type | Meaning | Storage |
|---|---|---|
| Building policy fee | General fee applicable across the property | `buildings.fees_json` |
| Optional add-on | Resident-selected parking, storage, insurance, etc. | Building fee policy, marked optional |
| Situational fee | Applies only under a stated condition | Building fee policy, marked situational |
| Unit quote charge | Exact charge returned for one unit/date/term | Ad-hoc quote only |
| Usage-based charge | Depends on consumption or provider allocation | Policy text; do not invent an amount |

A unit quote can validate or supplement the policy, but it must not silently
replace the building-level fee guide.

### 6.6 Missing or blocked fee sources

If the official page references a widget but the widget request fails:

- preserve the widget reference and asset ID;
- record `widget_fetch_failed` and the error;
- do not copy fee rows from an unrelated building or provider;
- do not use a unit quote as an automatic fallback;
- keep the inventory scan usable while clearly reporting incomplete fees.

If the official site has no fee guide at all, record `not_available` with the
checked URL and timestamp. This is different from successfully confirming that
there are no fees.

For each fee preserve:

- exact label;
- amount, range, percentage, or “varies” text;
- required/optional/situational classification;
- recurring frequency or one-time timing;
- per-unit/per-applicant/per-leaseholder basis;
- third-party status;
- disclaimer and tooltip;
- official source URL;
- provider payload.

Do not use a unit quote as a fallback for a missing official building fee
guide. If the official fee source cannot be fetched, store an explicit
`widget_fetch_failed` or `not_available` status and leave the fee list empty
or incomplete with a warning.

## 7. Concession extraction

Search all likely locations:

- homepage popup or pop-down;
- homepage promotion banner;
- floor-plan page banner;
- plan-card “special” indicator;
- unit-detail special text;
- application/quote response.

Preserve the exact marketing wording, source URL, and scan timestamp. Record
scope when known:

- building-level marketing offer;
- plan-level special;
- unit-level special;
- lease-term/date-specific quote special.

An indicator such as `Has Special` is not enough to calculate a concession.
Do not infer free weeks, bonus amounts, eligibility, or net effective rent
unless the official response provides the terms. Marketing concessions must be
kept separate from base rent and applied only after checking lease term,
move-in date, unit, application timing, and other restrictions.

## 8. Ad-hoc quote flow

For a shortlisted unit:

1. Resolve the provider unit ID and official application/detail URL.
2. Submit only read-only requests for the selected move-in date and lease
   term.
3. Retrieve provider lease-term options if available.
4. Follow the official rental-options page or calculator iframe.
5. Parse base rent, recurring charges, total monthly price, one-time charges,
   fee rows, lease-specific specials, and selected date/term.
6. Verify that the returned apartment number and provider IDs match the
   requested unit.
7. Return the quote as an ephemeral result; do not write it into the broad
   inventory table unless explicitly requested.

The quote implementation must fail closed if the provider ignores the
requested date or term. Never report a default term as the requested term.

## 9. Validation and failure rules

Before a scan is accepted:

- the response has the expected provider shape;
- at least one actual unit is present when the site indicates availability;
- unit IDs are non-empty and unique within the building;
- plan-level counts are not mistaken for unit counts;
- prices are numeric or explicitly marked unavailable;
- dates are normalized and plausible;
- application URLs belong to the official property/provider flow;
- required fee/concession sections have an explicit parse status;
- a failed request cannot appear as a successful zero-unit scan.

On failure, return a provider-specific error and preserve the failure reason.
Do not mark all previous units inactive after an unsuccessful or structurally
invalid scan.

## 10. Persistence contract

The normalized adapter result should contain:

```json
{
  "provider": "...",
  "building_id": "...",
  "building_name": "...",
  "source_url": "...",
  "checked_at": "...",
  "status": "ok",
  "units": [],
  "policies": {
    "fees": [],
    "concessions": [],
    "fee_guide": {}
  },
  "contact": {},
  "warnings": []
}
```

SQLite stores one building-level policy record and one current row per
`(building_id, unit_id)`. Missing units from a successful scan become
inactive, with history retained. Client names, budgets, searches, and tour
notes do not belong in adapter output or building fee/inventory records.

## 11. Testing checklist

Every new adapter should include:

- fixture tests for the main page/config;
- plan-card tests;
- actual-unit tests;
- resident-facing versus provider-ID tests;
- base-rent versus total-price tests;
- fee-widget or fee-page tests;
- concession-text tests;
- empty/changed-response failure tests;
- ad-hoc quote parsing tests;
- one live read-only validation against the official site;
- SQLite upsert validation if the adapter is added to `scan_all.py`.

The adapter is ready only when the normalized output, fee policy, concession
handling, and ad-hoc quote path have all been checked independently.

## 12. Extending the SOP when a provider is new

The existing patterns are a starting taxonomy, not a closed list. When a new
site does not fit:

1. Describe the observed page, network, inventory, fee, and quote behavior in
   a provider investigation note.
2. Identify which existing pattern is insufficient and why.
3. Define the smallest new normalized fields or transport behavior needed.
4. Add a sanitized fixture and parser tests before adding live persistence.
5. Add a new named pattern or a provider-specific exception to this SOP.
6. Document the new pattern’s failure modes and validation rules.
7. Update the README and adapter registry so future work can find the pattern.
8. Only then add the adapter to the full scan runner.

Every new adapter should leave behind reusable knowledge: the page shape, the
request shape, the authoritative fee source, the quote flow, and the reason
the chosen parser is trustworthy. If a provider changes its page, update the
pattern and fixture rather than silently weakening validation.
