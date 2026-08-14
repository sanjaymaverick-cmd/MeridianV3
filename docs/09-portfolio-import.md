# Portfolio import

Modules: `meridian_v3/ingestion/*`, `meridian_v3/portfolio/mapper.py`

## Formats

| Kind | How |
|---|---|
| CSV / TXT | pandas, several encodings |
| XLSX / XLS | openpyxl, header-row hunt in the first 12 rows |
| PDF | pdfplumber tables, same header hunt |
| PNG / JPG / WEBP | OCR via pytesseract if installed, else a clear message + regex fallback |

## Fields

Symbol / scheme, company or fund name, ISIN, quantity or units, average buy / average NAV, last / current NAV, invested, current value, exchange.

## Indian brokers and fund houses

Zerodha, Angel One, Dhan, ICICI Direct, Groww, Upstox, MF Central, CAMS, KFintech. Unknown files fall back to `generic` column aliases.

Stocks, ETFs, and mutual funds are classified (`classify_instrument`). `INF*` ISINs are treated as funds.

## Review then commit

1. Upload.
2. Preview every row. Bad rows are marked, not silently dropped from view.
3. Confirm. Only then do rows become `holdings`.
4. `map_into_book` adds the name to the watchlist. Option-like rows also become a Greeks leg so Δ Γ ν Θ reviews apply.

CLI:

```powershell
python -m meridian_v3 import --file statement.pdf
python -m meridian_v3 import --file screenshot.png --account "Family MF"
python -m meridian_v3 import --file holdings.xlsx --commit --account "Core — Zerodha"
```

OCR is best-effort. The second human check is the real control.
