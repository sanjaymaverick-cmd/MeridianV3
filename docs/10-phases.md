# Phased development

The repository already contains a working Phase 1–3 spine. This is the recommended order if you extend it.

## Phase 1 — Desk that cannot hurt you (now)

- Seed ₹5,000 paper + live books (live disarmed)
- V1 score + V2 Greeks/reviews/charts
- Auto decision → **paper only**
- Import CSV / XLSX / PDF with preview
- Tests for Kelly, ATR, drawdown, reviews, import, smoke

**Done in this repo.**

## Phase 2 — Learning loop

- Nightly paper close → Bayesian + logistic update
- Walk-forward report per rule, size shrink when not robust
- Paper vs live dashboard with expectancy, hit rate, by market
- Image OCR hardened on real CAMS / Kite screenshots
- Alert worker on a 60s poll

## Phase 3 — Careful live

- One real broker adapter (your choice) behind `register_broker`
- Live arm + hardware-style confirm
- Kill switch, max rupee loss per day (₹200 default suggestion)
- Positional 1–3 day path with gap rules
- Windows package `MERIDIAN-V3`

Do not skip Phase 1 paper time. A ₹5,000 book does not get a second chance at the same speed as a research notebook.
