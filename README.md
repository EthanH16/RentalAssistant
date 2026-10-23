# RentalAssistant — first demo

Local-only prototype for exploring collaboration buildings by area, roster source, and reference price. The project is now named `rentalassistant` so the map, live-inventory adapters, and future rental-agent tooling can grow together.

## Run

From this folder:

```bash
python3 -m http.server 4173
```

Then open `http://localhost:4173`.

## Notes

- Backend work lives in `backend/`. The first target is a provider-level Onni adapter using Hope + Flower, then Olympic + Hill as the validation building. The backend currently produces inspectable JSON only; SQLite and scheduled polling are intentionally deferred until the adapter is stable.
- The map uses MapLibre GL JS with OpenFreeMap's vector style (`https://tiles.openfreemap.org/styles/positron`). OpenFreeMap does not require an API key for its public instance; keep its attribution visible.
- For production deployment, replace the demo layer with an approved provider/API key and review its attribution and usage limits.
- Coordinates are approximate for this first demo.
- The roster in `buildings.json` is the normalized snapshot of `LA-Rental-Vault/02 Research/00 Property Research Index.md`: 20 Direct partnership properties and 52 Niko properties. If a future canonical name overlaps, Direct takes precedence and the duplicate is removed from the Niko output. The map uses green pins for Direct and blue pins for Niko.
- The app does not read the vault at runtime. It loads `buildings.json`; the vault remains the source of truth and the JSON is the static web export.
- The Git repository is intentionally scoped to this app and its backend. Human-readable rental operations and building ground truth remain in the sibling `LA-Rental-Vault` folder.
