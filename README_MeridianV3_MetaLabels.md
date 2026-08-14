# Meridian V3 – Meta-Label Training Set

**Generated:** 14 Aug 2026
**Source DB:** `meridian_v3.db` (paper auto-trading session)
**Initial capital:** ₹5,000, later topped up to ₹50,000
**Headline paper equity:** ₹56,640.64 (accounting artefact — see notes)
**Honest equity + crypto tape:** −₹1,365.27 after fees

This package is a reproducible, decision-time-safe training set for a
Meridian V3 meta-label model, plus the original database with a new
`meta_label_training` table.

---

## Files

| File | Description |
|------|-------------|
| `meridian_v3_meta_labels.csv` | Training set (199 honest round-trips). Open in Excel, pandas, or any ML tool. |
| `meridian_v3_with_meta_labels.db` | Full original SQLite database + table `meta_label_training` (indexed on `buy_time`, `symbol`, `is_clean`). |
| `build_meta_labels.py` | Reproducible builder (causal Wilder ATR join included). |
| `README_MeridianV3_MetaLabels.md` | This file. |
| `ANALYTICAL_REPORT_MeridianV3_MetaLabels.md` | Correlations, alternative labels, Kelly, risk rules, data plan, evaluation. |

Rebuild (DB is auto-discovered next to the script, or via `--db`):

```bash
python build_meta_labels.py --db meridian_v3.db --out artifacts
```

---

## What the training set contains

Each row is one **honest round-trip** reconstructed from the `fills` table:

- Consecutive `buy` → `sell` on the same symbol
- `honest_pnl = (sell_price − buy_price) × qty − (buy_fees + sell_fees)`
- Decision-time features taken from the nearest prior `side='buy'` signal
  (2 s window, 5 s fallback). All 199 pairs matched.
- ATR is **causal**: Wilder 14-period, using only `price_bars` with
  `bar_date <` midnight of the decision day. Futures map to the cash
  underlying (`INFY.F` → `INFY`, `NIFTY.F` → `NIFTY`, …). Fallback is
  `price_cache` when no prior bar exists.

Unpaired leftover buys (open positions and consecutive same-side fills)
are not rows. Shorts are not present in this session.

### Key columns

**Decision-time features (safe for training)**

- `confidence`, `confluence`, `p_success` — primary model outputs at the buy signal
- `atr`, `atr_pct` — causal 14-period Wilder ATR / buy price
- `approx_stop_pct`, `risk_rupees` — 1.5 × ATR stop, rupee risk
- `minutes_since_midnight`, `minutes_to_eod_flatten` — clock time (naive UTC)
  and minutes to the 15:10 IST / 09:40 UTC flatten
- `belief_posterior` — α / (α + β) from the belief core (constant on this day)
- `qty`, `is_futures`

**Labels**

- `y_binary` — 1 if `honest_pnl > 0`, else 0 (primary meta-label)
- `honest_pnl` — net P&L after fees (rupees)
- `y_R` — net R-multiple (`honest_pnl / risk_rupees`)
- `hold_sec` — actual holding time in seconds

**Metadata / quality**

- `symbol`, `signal_id`, `buy_time`, `sell_time`, `buy_price`, `sell_price`, `fees`
- `atr_source` — `price_bars` | `price_cache` | `none`
- `is_short_hold` — 1 if `hold_sec < 120`
- `is_clean` — 1 if `is_futures == 0` (not mark-contaminated)
- `gross_pnl` — P&L before fees
- `signal_market` — market on the joined buy signal
- `signal_to_buy_sec` — latency from signal to fill
- `reentry_sec` — seconds since the previous same-symbol sell (`NaN` on first trip)
- `same_symbol_seq` — 1-based trip index on that symbol

---

## Summary statistics (this run)

Computed from `fills`, not from `positions.realized_pnl`.

| | All 199 | Clean (`is_futures == 0`) | Futures |
|--|--------:|--------------------------:|--------:|
| Rows | 199 | 157 | 42 |
| Honest win rate | 21.6 % | **0.64 % (1 / 157)** | 100 % (artefact) |
| Average honest P&L | ₹32.69 | **−₹8.70** | ₹187.41 |
| Sum honest P&L | ₹6,505.96 | **−₹1,365.27** | ₹7,871.22 |
| Median hold | 60.1 s | 60.1 s | ~60 s |
| ATR coverage | 100 % `price_bars` | 100 % | 100 % |

The single clean winner is RELIANCE (+₹1.91 after fees, held 8,015 s from 07:25 UTC).

Top symbols by count: INFY.F (34), BNBUSDT / HCLTECH / BHARTIARTL (17 each),
BAJAJFINSV / GRASIM / M&M (16 each).

Paper account in `account_state`: cash ₹284.65, equity ₹56,640.64, peak ₹57,392.19.
Live account was never armed (₹50,000 / ₹50,000). Session fills: 412
(213 buy / 199 sell), 07:25–09:57 UTC on 14 Aug 2026.

---

## Important notes & known issues

1. **Futures mark mismatch still visible**
   NIFTY.F and INFY.F were bought at seed marks (~₹7,920 / ~₹380) and sold
   at live prices (~₹24,370 / ~₹1,169) with `qty = 0.05`. All 42 of those
   trips are artificial winners. Train only on `is_clean == 1` (or
   `is_futures == 0`) until the mark pipeline is fixed.

2. **Headline 21.6 % / ₹32.69 is not an equity/crypto win rate.**
   It is the 42 fake futures wins sitting on top of a 0.64 % clean tape.
   Always quote the clean split.

3. **Short holds dominate, and most of them are post-flatten scratches.**
   On the clean tape, 132 / 157 trips have `hold_sec < 120`. 132 / 157 were
   entered *at or after* the 09:40 UTC (15:10 IST) flatten. 119 / 157 have
   `gross_pnl == 0` (same price in and out — the loss is 100 % fees).
   Median same-symbol re-entry is **0.49 s**.

4. **Causal ATR**
   ATR uses only daily bars that closed before the decision day. No
   look-ahead. Wilder: first ATR is the SMA of the first 14 true ranges,
   then `(prev × 13 + TR) / 14`.

5. **Belief posterior is a single number, and it is contaminated.**
   α = 52, β = 155 → 25.1 %. Those 48 “wins” equal 22 closed rows with
   `realized_pnl > 0` plus all 26 closed rows with `realized_pnl IS NULL`
   (18 INFY.F + 8 NIFTY.F). Missing P&L was treated as a win. Honest
   clean wins are 1, not 48.

6. **Decision features barely vary.**
   On the clean tape, `confluence` is 73.6 on 156 / 157 rows and
   `p_success` is 0.6764 on 156 / 157 rows (VISL is the exception).
   `confluence` and `p_success` are perfectly collinear here. They cannot
   rank-order trades on this day.

7. **`positions.realized_pnl` is incomplete.**
   26 / 199 closed positions have NULL `realized_pnl`, all futures.
   Do not train on that column.

---

## How to use

### Load in Python

```python
import pandas as pd

df = pd.read_csv("meridian_v3_meta_labels.csv", parse_dates=["buy_time", "sell_time"])

# Required filter — exclude mark-contaminated futures
clean = df[df["is_clean"] == 1].copy()

X = clean[[
    "confidence", "confluence", "p_success",
    "atr_pct", "approx_stop_pct",
    "minutes_to_eod_flatten",
]]
y = clean["y_binary"]          # 1 positive — do not fit a classifier yet
y_rank = (clean["honest_pnl"] > clean["honest_pnl"].median()).astype(int)
```

### Load from SQLite

```python
import sqlite3
import pandas as pd

conn = sqlite3.connect("meridian_v3_with_meta_labels.db")
df = pd.read_sql(
    "SELECT * FROM meta_label_training",
    conn,
    parse_dates=["buy_time", "sell_time"],
)
conn.close()
```

### Rebuild from scratch

```bash
python build_meta_labels.py
# or, explicit paths:
python build_meta_labels.py --db meridian_v3.db --out artifacts
```

The script searches, in order: `--db`, `$MERIDIAN_V3_DB`, `./meridian_v3.db`,
the script directory, then `/home/workdir/attachments/meridian_v3.db`.
Outputs go to `--out`, `$MERIDIAN_V3_ARTIFACTS`, `/home/workdir/artifacts`
if that directory exists, otherwise `<script>/artifacts`.

The builder is deterministic given the same `meridian_v3.db`.

---

## Suggested next steps

Rule changes currently have **higher leverage than modelling**. This clean
tape has one positive label. See the analytical report before fitting
anything.

1. **Fix the production pipeline**
   - Correct futures mark source and multiplier
   - Write complete `realized_pnl` + `exit_reason` on every close
   - Do not update the belief core from NULL or mark-jump P&L
   - Block new entries once flatten starts; do not re-enter the same name
     for at least 120 s (preferably until the next session)

2. **Do not train `y_binary` on this file alone**
   - One positive example cannot identify a decision surface
   - If you must run a model, use a ranking / continuous target
     (`honest_pnl` or `honest_pnl > median`) and treat it as a smoke test

3. **Online loop (after several clean sessions)**
   - After each new closed trade, append a row to `meta_label_training`
   - Retrain or update only on `is_clean == 1`
   - Calibrate `p_success` before it touches Kelly

4. **Feature expansion once the loop stops scratching**
   - Rolling win-rate per symbol
   - Realised volatility at higher frequency
   - Portfolio heat / open risk at decision time
   - `reentry_sec` and `minutes_to_eod_flatten` as hard gates, not just features

---

## Contact / reproduction

All logic lives in `build_meta_labels.py`.
The script is deterministic given the same `meridian_v3.db`.

Generated by the Meridian V3 analysis pipeline – 14 Aug 2026.
