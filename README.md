# Scent Studio supplier catalogue

Data feed for the Supplier Catalogue window in Scent Studio. Data only, no app source.

A GitHub Action runs `scripts/build_catalog.py` every day. It reads the supplier's public product list, keeps the perfumery materials, guesses the note type, longevity, strength and scent profiles, applies `overrides/`, and commits any changes under `catalog/`:

| File | What it is |
|---|---|
| `catalog/manifest.json` | Checked by the app first: one entry per supplier with a `version`, plus the rates version |
| `catalog/fraterworks.json` | Products with sizes, prices, link, kind, form, CAS, dilution and the guesses |
| `catalog/rates.json` | Exchange rates from the supplier's currency (ECB via frankfurter.app) |

The app reads these from `https://raw.githubusercontent.com/zbagatsubway/scent-studio-catalog/main/catalog/`.

## Fixing a guess

Edit `overrides/fraterworks.json`. Keys are the product's URL handle (the part after `/products/`):

```json
{
  "helvetolide": { "noteType": "base", "strength": "Strong", "longevity": "Long", "profiles": ["Musky"] },
  "some-bottle":  { "exclude": true },
  "some-kit-item": { "include": true }
}
```

- `noteType` takes `top`, `middle` or `base`.
- Override fields: `noteType`, `longevity`, `strength`, `profiles`, `kind`, `form`, `cas`, `name`, `dilution`.
- Overridden fields stop being marked as estimated.
- The change appears after the next run. To run it straight away, use **Actions → Update supplier catalogue → Run workflow**.

## Run it on your Mac

```sh
cd ~/Documents/GitHub/scent-studio-catalog
python3 scripts/build_catalog.py                          # live
python3 scripts/build_catalog.py --from-file tests/sample_products.json --skip-rates   # offline test
```

## Safety rails

- Pages are fetched politely: one request every 2 seconds, once a day.
- If the product count drops by more than 20%, the run fails and the old file stays. Use `--force` if the drop is real.
- If the exchange-rate fetch fails, the previous `rates.json` is kept.
- Supplier descriptions are included by default. **Get the supplier's permission before this repo is public or the app ships.** Set `INCLUDE_DESCRIPTIONS=0` in the workflow to leave them out.
