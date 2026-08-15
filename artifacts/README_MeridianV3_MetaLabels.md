# Meridian V3 – Meta-Label Training Set

**Generated:** 14 Aug 2026 (rebuilt after Phase 1 of `MERIDIAN_V3_FIX_AND_UX_PLAN.md`)
**Source DB:** `meridian_v3.db` (paper auto-trading session)
**Source DB hash:** `57b4eeca533eba7e48bd67b76ee2298bbf8be72e147b413f1f02440d34648595`
(see `artifacts/meridian_v3_meta_labels.STAMP.json` — this is the provenance
stamp added in Phase 0 so a reader never has to guess whether this file is
current)
**Initial capital:** ₹5,000, later topped up to ₹50,000
**Headline paper equity:** ₹56,943.15 (accounting artefact — see notes)
**Honest equity + crypto tape:** −₹1,337.78 after fees

This package is a reproducible, decision-time-safe training set for a
Meridian V3 meta-label model, plus the original database with a new
`meta_label_training` table.

---

## Files

| File | Description |
|------|-------------|
| `meridian_v3_meta_labels.csv` | Training set (220 honest round-trips). Open in Excel, pandas, or any ML tool. |
| `meridian_v3_with_meta_labels.db` | Full original SQLite database + table `meta_label_training` (indexed on `buy_time`, `symbol`, `is_clean`). |
| `build_meta_labels.py` | Reproducible builder (causal Wilder ATR join, position-state-stack round-trip pairing). |
| `README_MeridianV3_MetaLabels.md` | This file. |
| `ANALYTICAL_REPORT_MeridianV3_MetaLabels.md` | Correlations, alternative labels, Kelly, risk rules, data plan, evaluation (predates this rebuild — treat its counts as historical, not current). |

Rebuild (DB is auto-discovered next to the script, or via `--db`):

```bash
python build_meta_labels.py --db meridian_v3.db --out artifacts
```

---

## What the training set contains

Each row is one **honest round-trip** reconstructed from the `fills` table
with a per-symbol position-state stack (1.4 of the fix plan): a fill on the
same side as the open lot adds to it, a fill on the opposite side closes
lots off the stack lot-by-lot (LIFO), and a round trip is only emitted for
quantity that is actually closed against the lot that opened it. This
replaced a naive adjacent-pair walk that had no notion of position state and
could pair a cover-buy with an unrelated, later open-sell in an interleaved
short sequence.

- `honest_pnl = (sell_price − buy_price) × qty − (buy_fees + sell_fees)` —
  this formula is direction-agnostic: it is correct for both a long
  (`buy` then `sell`) and a short (`sell` then `buy`) round trip.
- Decision-time features taken from the nearest prior `side='buy'` signal
  (2 s window, 5 s fallback). All 220 pairs matched.
- ATR is **causal**: Wilder 14-period, using only `price_bars` with
  `bar_date <` midnight of the decision day. Futures map to the cash
  underlying (`INFY.F` → `INFY`, `NIFTY.F` → `NIFTY`, …). Fallback is
  `price_cache` when no prior bar exists.
- **Shorts:** 0 short round-trips in this session (every position in
  `positions` opened `side='buy'`), so the long-only signal-matching stage
  is unaffected here. The reconstruction itself now handles interleaved
  shorts and longs correctly (see `tests/test_build_meta_labels.py`); if a
  future session opens a short, it will be paired correctly and tagged
  `direction == "short"`, but is still excluded from `attach_nearest_signal`
  until that stage is extended to also join against `side='sell'` signals —
  the builder prints how many shorts it found and excluded, rather than
  silently mispairing or silently dropping them.

Unpaired leftover buys (open positions and consecutive same-side fills that
never closed) are not rows.

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
- `direction` — `"long"` or `"short"` (which side opened the round trip)
- `is_clean` — 1 if `is_futures == 0` (not mark-contaminated)
- `gross_pnl` — P&L before fees
- `signal_market` — market on the joined buy signal
- `signal_to_buy_sec` — latency from signal to fill
- `reentry_sec` — seconds since the previous same-symbol sell (`NaN` on first trip)
- `same_symbol_seq` — 1-based trip index on that symbol

---

## Summary statistics (this run)

Computed from `fills`, not from `positions.realized_pnl`.

| | All 220 | Clean (`is_futures == 0`) | Futures |
|--|--------:|--------------------------:|--------:|
| Rows | 220 | 169 | 51 |
| Honest win rate | 27.3 % | **5.3 % (9 / 169)** | 100 % (artefact) |
| Average honest P&L | ₹31.20 | **−₹7.92** | ₹160.83 |
| Sum honest P&L | ₹6,864.47 | **−₹1,337.78** | ₹8,202.25 |
| Median hold | 60.1 s | 60.1 s | 60.1 s |
| ATR coverage | 100 % `price_bars` | 100 % | 100 % |

The clean tape has **9 winners** (up from 1 in the pre-fix build, mostly
because the position-state-stack reconstruction (1.4) recovers round trips
the old adjacent-pair walk silently dropped): RELIANCE (+₹1.91, held
8,015 s), INFY ×4 (+₹8.17, +₹8.17, +₹8.17, +₹7.01, all ~6,000–6,094 s),
M&M (+₹4.53), BRITANNIA (+₹2.11), GRASIM (+₹12.46), LODHA (+₹7.48, the
latter four all ~9,836 s).

Top symbols by count: INFY.F (43), INFY (19), BHARTIARTL / HCLTECH (18 each),
BNBUSDT / BAJAJFINSV / M&M / GRASIM (17 each).

Paper account in `account_state`: cash ₹56,382.24, equity ₹56,943.15,
peak ₹57,392.19. Live account was never armed (₹50,000 / ₹50,000). Session
fills: 437 (221 buy / 216 sell), signals span 07:08–10:52 UTC on 14 Aug 2026.

---

## Important notes & known issues

1. **Futures mark mismatch still visible in this historical data**
   NIFTY.F and INFY.F were bought at seed marks and sold at live prices with
   `qty = 0.05`, from *before* Phase 0's fill-price fix (`execution/oms.py`
   now refuses to fabricate a fill price — see `_fill_price`). All 51 of
   those trips are artificial winners; they predate the fix and are still
   sitting in `fills` as historical fact. Train only on `is_clean == 1` (or
   `is_futures == 0`).

2. **Headline 27.3 % / ₹31.20 is not an equity/crypto win rate.**
   It is the 51 fake futures wins sitting on top of a 5.3 % clean tape.
   Always quote the clean split.

3. **Short holds dominate, and most of them are post-flatten scratches.**
   On the clean tape, 132 / 169 trips have `hold_sec < 120`. 123 / 169 have
   `gross_pnl == 0` (same price in and out — the loss is 100 % fees).
   Median same-symbol re-entry is **0.48 s**. (Phase 0's 0.5 fix stops a
   same-mark EOD/weekend flatten from moving the belief core going forward,
   but this historical data still contains scratches from before that fix.)

4. **Causal ATR**
   ATR uses only daily bars that closed before the decision day. No
   look-ahead. Wilder: first ATR is the SMA of the first 14 true ranges,
   then `(prev × 13 + TR) / 14`.

5. **Belief posterior is a single number, and it is contaminated.**
   α = 66, β = 158 → 29.5 %. Those 62 "wins" equal 36 closed rows with
   `realized_pnl > 0` plus all 26 closed rows with `realized_pnl IS NULL`
   (18 INFY.F + 8 NIFTY.F). Missing P&L was treated as a win. Honest
   clean wins are 9, not 62.

6. **Decision features barely vary.**
   On the clean tape, `confluence` is 73.6 on 167 / 169 rows and
   `p_success` is 0.6764 on 167 / 169 rows. `confluence` and `p_success`
   are almost perfectly collinear here. They cannot rank-order trades on
   this day. (This DB snapshot predates Phase 1's online-logistic wiring —
   `p_success` in fresh signals going forward will vary as the persisted
   `logit_weights` update from real outcomes; see `engine/meta_label.py`
   and `pipeline.py:load_logit`/`persist_logit_update`.)

7. **`positions.realized_pnl` is incomplete.**
   26 of 216 closed positions have NULL `realized_pnl`, all futures. Do not
   train on that column.

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
y = clean["y_binary"]          # 9 positives out of 169 — do not fit a classifier yet
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

The builder is deterministic given the same `meridian_v3.db`, and every run
writes `meridian_v3_meta_labels.STAMP.json` (source DB sha256 + row count +
headline metrics) next to the CSV so staleness is detectable at a glance —
do not trust a CSV without a matching stamp.

---

## Suggested next steps

Rule changes currently have **higher leverage than modelling**. This clean
tape has nine positive labels out of 169. See the analytical report before
fitting anything (note: that report predates this rebuild).

1. **Keep closing the production-pipeline gaps (Phase 1 of the fix plan)**
   - Online logistic (1.1) and the V1 five-factor score (1.2) are now wired
     into the live cycle and persisted — `p_success` and `factor_scores`
     will actually move going forward instead of being a fixed constant.
   - Short round-trip reconstruction (1.4) now tracks real position state.
   - Still open: extend `attach_nearest_signal` to also join short round
     trips against `side='sell'` signals once a session actually opens one.

2. **Do not train `y_binary` on this file alone**
   - Nine positive examples on the clean tape is still too few to identify
     a decision surface
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

Generated by the Meridian V3 analysis pipeline — rebuilt 14 Aug 2026 after
Phase 1 of `MERIDIAN_V3_FIX_AND_UX_PLAN.md` (1.1–1.4) landed.
