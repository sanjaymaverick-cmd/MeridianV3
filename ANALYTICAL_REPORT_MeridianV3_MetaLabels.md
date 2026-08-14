# Meridian V3 – Analytical report (clean non-futures tape)

**Date:** 14 Aug 2026
**Universe:** 157 honest equity + crypto round-trips (`is_clean == 1`).
The 42 NIFTY.F / INFY.F rows are excluded from every number below.
They are 100 % mark-jump artefacts (seed ~₹7,920 / ~₹380 → live ~₹24,370 / ~₹1,169, qty 0.05).

**The modelling constraint, stated first:** this tape has **one** positive
`y_binary` example (RELIANCE, +₹1.91, held 8,015 s). A classifier fitted
on `y_binary` will memorise that one long-hold name. Rule changes —
cooldown, flatten-as-one-way, size caps — currently have higher leverage
than any meta-label model.

No data in this report is invented. Every figure is computed from
`meridian_v3.db` via `build_meta_labels.py`.

---

## Session facts (for context)

| Item | Value |
|------|------:|
| Fills | 412 (213 buy, 199 sell), all `venue=paper` |
| Honest round-trips | 199 (157 clean + 42 futures) |
| Signals | 21,977 (876 buy / 21,101 hold) |
| Paper equity | started ₹5,000 at 07:07 UTC; topped up to ₹50,000 at 07:57 UTC; peak ₹57,392.19 at 09:25; last ₹56,640.64 |
| Honest clean P&L | **−₹1,365.27** (fees ₹1,308.64, gross −₹56.63) |
| Clean win rate | **1 / 157 = 0.64 %** |
| Median hold | 60.08 s |
| Belief core | α=52, β=155 → 25.1 % (contaminated — see §4) |
| Open at stop | 10 names (INFY, BHARTIARTL, HCLTECH, M&M, BAJAJFINSV, BRITANNIA, GRASIM, LODHA, BNBUSDT, INFY.F) |

Headline “+₹6,641” is 42 fake futures wins (+₹7,871) minus the honest
−₹1,365, plus open-position mark-to-market.

---

## 1. Feature correlations on the clean tape

Decision features on this day barely move. `confluence` is 73.6 on 156 / 157
rows; `p_success` is 0.676427 on 156 / 157 rows (VISL is 70.4 / 0.6537).
Those two columns are perfectly collinear (Pearson 1.00).
`belief_posterior` is a constant 0.2512. None of them can rank trades.

Pearson / Spearman vs **`honest_pnl`** (n = 157 except `reentry_sec` n = 137):

| Feature | Pearson | Spearman | Reading |
|---------|--------:|---------:|---------|
| `fees` | **−0.79** | **−0.89** | P&L *is* the fee bill. 119 / 157 trips have `gross_pnl == 0`. |
| `atr_pct` / `approx_stop_pct` | +0.32 | +0.45 | Higher ATR% names were smaller notionals, so they paid less rupee fees. Not an edge. |
| `risk_rupees` | −0.24 | −0.27 | Larger risk clips lost more rupees (more fees). |
| `qty` | −0.21 | −0.18 | Same. |
| `reentry_sec` | +0.12 | +0.10 | Weak. Almost every re-entry is sub-second. |
| `same_symbol_seq` | −0.10 | −0.21 | Later clips in a churn sequence are slightly worse. |
| `confidence` | +0.05 | +0.04 | Noise. |
| `confluence` / `p_success` | −0.11 | −0.13 | Noise, and 156 identical values. |
| `hold_sec` | +0.05 | +0.09 | Weak on the rupee label. |

Pearson vs **`y_binary`** looks more dramatic (`hold_sec` +0.40,
`minutes_to_eod_flatten` +0.40, `minutes_since_midnight` −0.38) but this
is the single RELIANCE winner: bought at 07:25 UTC, held through flatten.
Those coefficients will vanish the moment a second winner appears. They
are not a signal.

Pearson vs **`y_R`**: `risk_rupees` +0.52 and `qty` +0.44 are mechanical.
R = PnL / (1.5 × ATR × qty). Most PnL is −fees. Larger risk in the
denominator shrinks |R|, so bigger clips look “better” in R-space while
losing more rupees. Do not optimise R on this tape.

**Feature × feature structure**

- `minutes_to_eod_flatten` ≈ 1 − `minutes_since_midnight` on this session
  (Pearson −0.98) and ≈ `hold_sec` (+1.00): almost every late entry is a
  60-second scratch, almost every early entry is a multi-thousand-second
  hold into flatten. Time-of-day, hold, and flatten proximity are one
  variable today.
- `qty` and `risk_rupees` are 0.89 collinear.
- `confidence` vs `same_symbol_seq` is −0.78: the online confidence
  number *falls* as the same name is re-entered. That is the only
  within-day movement in the primary model, and it still did not stop
  the loop.

**Practical feature set for later days**

Keep: `confidence`, `atr_pct`, `minutes_to_eod_flatten`, `reentry_sec`,
`same_symbol_seq`, `fees` (as a cost feature, not a look-ahead if
computed from the planned qty × tariff).
Drop or gate: `confluence` and `p_success` until they actually vary;
`belief_posterior` until it is time-varying and honest.

---

## 2. Alternative label definitions (usable class balance)

`y_binary = honest_pnl > 0` is the correct *eventual* meta-label, and it
is unusable today (1 / 157). Alternatives computed on the same 157 rows:

| Label | Positives | Rate | Use? |
|-------|----------:|-----:|------|
| `honest_pnl > 0` (`y_binary`) | 1 | 0.6 % | No. One example. |
| `gross_pnl > fees` (beat 1× fees) | 1 | 0.6 % | Same one row. |
| `gross_pnl > 2 × fees` | 0 | 0.0 % | Empty. |
| `y_R > 0` | 1 | 0.6 % | Identical to `y_binary`. |
| `y_R > 0.25` | 0 | 0.0 % | Empty. |
| `gross_pnl > 0` | **13** | **8.3 %** | Best rare *event* label. Price moved in your favour at all. Fees still ate 12 of the 13. |
| `hold_sec ≥ 120` | 25 | 15.9 % | Describes the loop, not edge. |
| `honest_pnl > −2` | 17 | 10.8 % | “Did not get fee-crushed.” |
| `honest_pnl > −5` | 45 | 28.7 % | More balanced, still a cost label. |
| `honest_pnl > p75` (−₹3.44) | 35 | 22.3 % | Ranking split. |
| `honest_pnl > median` (−₹9.92) | **71** | **45.2 %** | Only well-balanced target on this file. |
| `y_R > −0.1` | 132 | 84.1 % | Just “was a scratch, not a large adverse move.” Too easy, inverted. |
| `gross_pnl ≥ 0` | 132 | 84.1 % | 119 of those are exactly zero. |

**Recommendation**

- Production target, later: `y_binary` and `y_R`, on `is_clean == 1` only.
- This file: treat `honest_pnl` as a **continuous / ranking** target.
  If a binary is required for a smoke-test model, use
  `honest_pnl > median` (45 %) or `gross_pnl > 0` (8 %).
- Do not call either of those a meta-label of “the primary model was
  right.” On this day the primary model said buy with p ≈ 67.6 % on
  156 identical rows; the tape’s honest p is 0.64 %.

Gross vs honest split, because it matters for label design:

- `gross_pnl > 0`: 13
- `gross_pnl == 0`: 119 (pure fee scratches)
- `gross_pnl < 0`: 25
- price helped but fees flipped the sign: 12

The dominant class is not “the market went against us.” It is “we
crossed the spread / tariff twice in 60 seconds and the mid did not move.”

---

## 3. Position-sizing / Kelly integration

Empirical clean-tape inputs:

```
p      = 1/157 = 0.006369
avg W  = ₹1.91     (the one winner)
avg L  = ₹8.76     (156 losers)
b      = W/L = 0.218
q      = 0.9936
f*     = (p·b − q) / b = −4.55
```

Full Kelly is deeply negative. Break-even p for this payoff is
`1 / (1+b) = 82.1 %`. Observed p is 0.64 %. There is no size.

What the live sizer would have believed, if it trusted the signal
column:

- `p_success` mean on the clean tape = **0.676** (min 0.654, max 0.676)
- Production formula: `f* = (p·b − q) / b`, then
  `f = clip(κ · c · f*, 0, fmax)` with κ = 0.15, `fmax` = 0.25
- `b = max(assumed_payoff, 0.25)`
- With p = 0.676 you need b > 0.48 for f* > 0. An assumed 1:1 payoff
  gives f* ≈ 0.35, then × 0.15 × confidence ≈ 0.03 of book — which is
  how clips of several thousand rupees notionals got on (median notional
  on the clean tape: ₹6,845; max ₹10,279).

**Integration rules until the tape has real positives**

1. **Do not pass raw `p_success` into Kelly.** It is miscalibrated by
   two orders of magnitude (67.6 % vs 0.64 %).
2. **Gate, then size.** Meta-label p is a skip switch, not a size-up
   knob, until it is isotonic-calibrated on a multi-day clean tape.
3. **Cap p at the honest belief.** Even the (contaminated) 25 %
   posterior needs b > 2.98 for f* > 0. Observed b = 0.22. Size is zero
   under any honest p.
4. **Use fees as the loss floor.** Median clean loss ≈ median fee
   (₹9.92). If a name cannot clear ~2× expected round-trip tariff inside
   the planned hold, Kelly is zero regardless of p.
5. **ATR stop is fine; the qty path is not.** `risk_rupees` median
   ₹224 vs `min_risk_inr` 25 — the sizer is doing what it was told.
   The error is upstream (taking the trade at all, then taking it again
   0.5 s later).

Once a clean sample exists (see §5), the loop is:

```
p_hat  = calibrated P(honest_pnl > 0 | x)     # meta-label
b_hat  = rolling avg_win / avg_loss           # from clean tape, not assumed
if p_hat < p_min or f*(p_hat, b_hat) <= 0: skip
else: qty = ATR-size( κ · c · f* · equity )
```

Default κ = 0.15 can stay. It is not the problem.

---

## 4. Risk and cooldown rule recommendations

These are the highest-leverage changes. Each line is measured.

### What the loop actually did

- 132 / 157 clean trips were entered **at or after** the 09:40 UTC
  (15:10 IST) flatten. Those 132: **0 wins, −₹1,166.98, fees ₹1,132.74**,
  median hold 60.07 s, 110 with `gross_pnl == 0`.
- Of 137 same-symbol re-entries, **135 happened in under 5 seconds**
  (median 0.49 s, 75th percentile 0.69 s).
- Crypto spot (18 BNBUSDT + LINKUSDT): 0 wins, −₹290.24, fees ₹291.50.
  BNBUSDT alone is −₹284 of pure tariff (gross ≈ 0 on most clips).

### Rules, in priority order

| # | Rule | What it would have done on this tape |
|---|------|--------------------------------------|
| 1 | **Flatten is one-way.** After the flatten flag fires, no new entries until next session. | Drops 132 late scratches. Leaves 25 early trips: −₹198.29 (fees ₹175.90). Saves ~₹1,167. |
| 2 | **Same-symbol cooldown ≥ 120 s** after any close. | 157 → 22 trips, −₹162.14 (fees ₹139.75). Saves ~₹1,203. |
| 3 | **1 + 2 together** | 19 trips, −₹145.01 (fees ₹122.62). |
| 4 | **At most one clip per symbol per session** (`same_symbol_seq == 1`) | 20 trips, −₹150.91 (fees ₹128.52). Almost identical to (3). |
| 5 | **Do not update beliefs from NULL or futures-mark P&L.** | Today 48 “wins” = 22 `realized_pnl > 0` + **26 NULL futures**. Honest clean wins = 1. The 25 % posterior is an artefact of counting missing P&L as a win. |
| 6 | **Write `realized_pnl` + `exit_reason` on every close.** | 26 / 199 closed rows are NULL, all INFY.F / NIFTY.F. |
| 7 | **Hard-cap re-entries even inside the session** (e.g. max 2 clips / symbol / day) and **block a name for the day after a scratch** (`gross_pnl ≤ 0` and `hold_sec < 120`). | Stops the 16–17 clip BHARTIARTL / HCLTECH / BNBUSDT / BAJAJFINSV stacks. |
| 8 | **Crypto tariff check.** If expected round-trip fees > 0.5 × ATR-stop risk, skip. | BNBUSDT fees ≈ ₹16–25 per 60 s clip against a mid that did not move. |

A 60-second median hold with a 0.49-second re-entry is not a strategy.
It is the flatten worker and the entry worker running on the same
one-minute cycle. Fix that before touching the meta-label model.

Drawdown scale (pause new live risk at 20 % off peak) would not have
fired on the *headline* equity, because the fake futures marks kept the
curve up. On the honest tape the session lost ₹1,365 on a ₹50,000 book
(2.7 %) — below the 8 % scale-in. The danger is not today’s drawdown;
it is repeating this loop for a week.

---

## 5. Multi-day data collection plan

You cannot train a meta-label model on one positive. You also should
not collect another 5,000 scratches to get ~30 honest wins at a 0.64 %
rate. Change the process, then collect.

**Per close, persist (append-only)**

- The row `build_meta_labels.py` already builds (decision features +
  honest fill P&L + flags).
- `exit_reason` (flatten / stop / target / signal-flip / manual).
- `is_clean`, mark-source, and the seed vs live mid if the name is a
  future.
- Open-book heat at decision time: open count, gross rupee risk, cash.
- The p that Kelly actually used, and the qty plan.

**Session gates before a day is allowed into the training pool**

- Futures marks reconciled (or those rows stay `is_clean = 0`).
- Every close has non-null `realized_pnl` that matches fill math
  within a few paise.
- Flatten did not re-enter.
- Cooldown ≥ 120 s was on.

**How much data**

- Target **≥ 30 honest clean wins** and **≥ 10 distinct sessions**
  before fitting a secondary model.
- With rules 1–4, this session would have produced ~20 trips and still
  only one win. Expect to need on the order of **15–30 well-behaved
  sessions**, not 15–30 copies of today.
- Keep a frozen “tainted_2026-08-14” split (this file) as a
  *regression test for the pipeline*, not as training data.

**Belief core**

- Update α, β only from `is_clean == 1` and non-null honest P&L.
- Make the posterior time-varying (one snapshot per close), so
  `belief_posterior` is actually a feature.
- After this day, reset or down-weight the 48/151 count. It is not
  honest.

**Do not** mix live and paper, do not mix futures-mark rows, do not
upsample the one RELIANCE win.

---

## 6. Evaluation framework (high-churn, low-win-rate)

Accuracy, ROC-AUC, and a random 80/20 split are the wrong tools. With
one positive they are theatre.

**What to evaluate now (rules, not models)**

Report these on every session, clean tape only:

- Honest win rate, sum P&L, sum fees, median hold
- Scratch rate: `gross_pnl == 0` and `hold_sec < 120`
- Re-entry median and share `< 5 s`
- Share of entries with `minutes_to_eod_flatten == 0`
- Counterfactual P&L of rules 1–4 in §4 (already −₹1,365 → −₹145
  on this day)
- Calibration of `p_success` vs `y_binary` (today: 67.6 % vs 0.64 %)
- Brier score of `p_success` (will be ~0.46 today; a constant-0 model
  scores ~0.006)

**What to evaluate once there are ≥ 30 clean wins**

1. **Unit of split is the day**, never the row. Random row splits leak
   the same-name 0.5 s re-entries into both sides.
2. **Purged / embargoed walk-forward** (López de Prado): train on days
   1…k, test day k+1, embargo the last hour of day k. Combinatorial
   purged CV if you need error bars.
3. **Primary metric is rupee P&L of the policy**, not classification
   accuracy. A meta-label model is a gate:
   `take = (primary_side ≠ 0) and (p_hat ≥ p_min)`.
   Score `sum(honest_pnl | take)` and `sum(honest_pnl | skip)` on the
   hold-out days.
4. **PR-AUC and precision–recall at the operating point**, not ROC.
   ROC is optimistic when negatives are 99 % scratches that look alike.
5. **Cost-sensitive threshold.** Sweep `p_min` to maximise
   `sum(pnl | take) − λ · n_take` with λ ≈ median fee. The useful
   model on a tape like this is one that **refuses** 60-second
   re-entries, not one that finds RELIANCE.
6. **Calibration.** Reliability diagram + isotonic fit on a later
   fold. Kelly only sees the calibrated p.
7. **Baselines that a model must beat, in this order:**
   - Take nothing (P&L 0)
   - Take first clip per symbol only
   - Flatten one-way + 120 s cooldown
   - Constant p = clean historical win rate
   If the model cannot beat the cooldown rule on a later week, ship
   the rule.

**What not to do**

- Do not SMOTE the one winner.
- Do not report “21.6 % win rate” without the clean split.
- Do not use `y_R` as the fitness function while fees dominate
  (see §1).
- Do not walk-forward *inside* 14 Aug 2026. There is one regime
  (the re-entry loop) and one winner.

---

## Bottom line

The clean tape lost ₹1,365, almost all of it in post-flatten 60-second
fee scratches with a half-second re-entry. There is one honest winner.
`p_success` is a constant 67.6 % and Kelly of the honest numbers is
−4.55. Fix flatten, cooldown, and belief accounting; collect 10–30
clean sessions; then, and only then, fit a meta-label gate and put a
*calibrated* p into the existing 0.15-fractional Kelly sizer.

The training file is ready. The model is not.
