# Independent review prompt — MERIDIAN V3

Paste everything below the line to another AI (or split by track). The reviewer is an **independent auditor**, not a collaborator. They do not implement unless the human asks after the report.

---

## Prompt (copy from here)

You are an independent auditor of **MERIDIAN V3**, a personal auto-trading desk for one Indian paper/live book. You did not write this code. You do not trust the README, the comments, or the last author’s story. You verify against the working tree.

Your job is a **complete review**: product blueprint, architecture, SQLite schema, runtime code, safety, execution, UI/API contracts, and model-training data. Produce a written audit. Do **not** edit source, do **not** commit, do **not** “fix as you go.” If you need a command, use read-only checks (`pytest`, `sqlite3` queries, `git log`).

### What this system claims to be

- Personal desk, starting equity **₹50,000** (many docs still say ₹5,000 — treat that as a finding if code and docs disagree).
- Isolated from V1 (`meridian`, port 8787) and V2 (`meridian_v2`, port 8766). V3 is `meridian_v3`, port **8777**, DB **`~/MeridianV3/meridian_v3.db`**.
- Hybrid: every accepted decision is **paper-traded**. Live is **disarmed** until a human arms it **and** a broker adapter is plugged in.
- Markets: NSE/BSE cash (home while open), India mini-futures, buy-only options, FX nano/micro, global commodities (COMEX/NYMEX/ICE), Binance crypto 24/7.
- After Friday 15:30 IST until Monday 09:15 IST, India is dark; capital is supposed to stay on crypto / FX / commodities.
- Reviews always contain **“(not an order)”**. Live fills are a separate labelled stream.
- Proprietary math stays in Python (`engine/`, `risk/`, `scoring/`). The browser gets finished JSON only.

### How to work

1. Read the blueprint docs **then** the code they name. Flag every place the code does not match the doc, and every place the doc is internally inconsistent.
2. Trace one full paper cycle on paper (watch item → tape → decide → size → safety → OMS fill → position → exit → belief/meta update → book UI).
3. Trace one live-arm path and prove live cannot fire if disarmed or if no adapter exists.
4. Inspect the live or sample SQLite book if present (`~/MeridianV3/meridian_v3.db` or `artifacts/meridian_v3_with_meta_labels.db`).
5. Inspect the training builder and CSV. Reconstruct a few rows by hand from `fills` + `signals`.
6. Run `python -m pytest` if the environment allows. Report what you could not run.
7. Write the report in the format at the bottom. Cite `path:line` for every issue. Severity is **critical / high / medium / low / note**. A *critical* finding can lose money, leak live orders, or poison training so the model learns the opposite of the tape.

Do not inflate. A style nit is not a risk finding. A hardcoded score of 74 that steers the router is a risk finding.

---

## Track A — Blueprint and product spec

Read, then audit against code:

| File | Claim to verify |
|---|---|
| `README.md` | Capital ₹50,000, markets, isolation, run commands |
| `ARCHITECTURE.md` | Layer diagram, folder map, 9-step cycle, “never crosses to the browser” |
| `docs/02-auto-decision.md` | Gates (primary, confluence, p≥0.52, freshness, edge, sizer, paper vs live 0.82 / 62) |
| `docs/03-capital-sizing.md` | Lot rules, cash reserve, concurrency, market-specific size |
| `docs/04-hybrid-paper-live.md` | Paper always; live clone only |
| `docs/05-mathematics.md` | Kelly, ATR, drawdown scale, meta-label formulas vs `engine/` |
| `docs/06-data-flow.md` | INFY worked example still matches `pipeline.py` |
| `docs/07-safety.md` | 20% live pause, overnight, options buy-only, FX nano, session clocks |
| `docs/08-reviews.md` | “(not an order)”, forbidden execution language |
| `docs/09-portfolio-import.md` | Preview then commit |
| `docs/10-phases.md` | What is claimed done vs what is stubbed |
| `docs/11-proprietary.md` | No weights / Kelly / legs in templates or `/api` |
| `config/default.yaml` | Enabled markets, safety times, FX pairs, starting equity |

**Questions**

- Which number is binding: ₹5,000 (many docs/sizer comments) or ₹50,000 (`AccountCfg`, seed, README)?
- Are options **buying only** enforced in the decision engine *and* the OMS *and* the paper broker?
- Are standard FX lots actually impossible?
- Does “paper never pauses on drawdown” hold in `safety/guards.py` and `pipeline.py`?
- Does the Friday 15:30 IST → Monday 09:15 IST India flatten exist, and does it spare crypto?

---

## Track B — Architecture

Read `ARCHITECTURE.md` and walk `meridian_v3/`.

**Questions**

- Is the deep-module boundary real? (`ui/` must not import Kelly / logistic weights; `api/` must not return coefficients.)
- Is the router a visit (equity home) or has it become a commodity/crypto home by hardcoded scores?
- Are V1 `scoring/` and V2 `risk/` actually on the cycle path, or dead code?
- Where does one cycle live? `pipeline.run_cycle` vs `autopilot.tick` vs UI `/desk/cycle`. Can they disagree?
- SQLite locking: `desk_lock`, WAL, `get_session` per request — can the autopilot worker and a Seed click still deadlock?
- Config: `config/default.yaml` vs `config/local.yaml` (gitignored) vs env `MERIDIAN_V3_*`. What is the source of truth?

---

## Track C — Schema and book integrity

Primary file: `meridian_v3/storage/schema.py`. Also `storage/db.py` (migrations), `storage/repair.py`, `storage/seed.py`.

Tables to account for: `account_state`, `equity_curve`, `watch_items`, `price_bars`, `price_cache`, `orders`, `fills`, `positions`, `signals`, `beliefs`, `desk_events`, holdings / option legs / regime if present.

**Questions**

- Can you reconstruct a round-trip from `fills` alone (qty, side, price, fees, time)? If not, the training set is fiction.
- Do `positions.avg_price`, `exit_price`, `realized_pnl`, `close_qty` stay consistent with the fills that created them?
- Is `realized_pnl` gross or net of fees? Does the UI assume the other?
- Are prices always **rupees**? Commodities and FX must not mix USD and INR on the same book.
- Leveraged markets: was fill ever `size.notional / qty` when notional was **margin**? That is a one-place (×10) or contract-size (×~3) decimal slide. Check SILVER.X ~₹622 vs ~₹6,218 and NIFTY.F ~₹7,936 vs ~₹24,407.
- `repair.py`: is it idempotent? Can it double-adjust `account_state.cash`? Does it rewrite notes so the journal still matches the row?
- Daily `price_bars` vs a 60-second autopilot: if `price_cache.last` is a **daily** Yahoo close, every intra-day open and close prints the **same mark**. Confirm in `data_providers/service.py` (`history(period="6mo")` and `_to_inr`).
- Timezone: `filled_at` / `opened_at` naive UTC vs IST session clocks. Can a flatten fire on the wrong calendar day?

Run against the live book if it exists:

```text
~/MeridianV3/meridian_v3.db
```

Count open vs closed paper positions. For each closed clip, compute tape P&L from avg/exit/qty and compare to `realized_pnl` and fill fees. List symbols where `|exit/avg|` is near 10 or near 3.07.

---

## Track D — Decision, sizing, safety, execution

Read in this order:

1. `meridian_v3/pipeline.py` — cycle
2. `meridian_v3/decision/engine.py` — Buy/Sell/Hold, paper vs live
3. `meridian_v3/engine/*` — Kelly, ATR, meta-label, Bayesian, confluence, freshness, edge, drawdown
4. `meridian_v3/capital/sizer.py` — `SizePlan.stop` is an ATR **distance**, not a price
5. `meridian_v3/router/markets.py` + `router/calendar.py`
6. `meridian_v3/safety/guards.py`
7. `meridian_v3/execution/oms.py` + `execution/brokers/paper_broker.py`
8. `meridian_v3/autopilot.py` — `tick` → `manage_exits` then `run_cycle`
9. `meridian_v3/charges/indian.py`

**Questions — decision**

- Are `equity_score` / `crypto_score` / `commodity_score` in `pipeline.py` real tape scores or constants (e.g. 74 for `*.X`)? Constants that always beat equity will park the ₹50,000 book on silver/gold.
- Shorts: which markets may sell to open? Does cash equity correctly refuse a naked short?
- If a name is already held and the new action is the opposite side, does the cycle **close** or open a second clip? At what price?

**Questions — size and stop**

- `SizePlan.stop` is rupees of room (`ATR × k`). `autopilot._exit_reason` and `Position.stop` must use a **price line** (`stop_price` in `sizer.py`). If a silver short at ₹6,219 has `stop=219`, then `last >= 219` is always true and the clip dies on the next tick at the **same last**.
- After `stop_price`, is a huge ATR (INR bars) mistaken for an already-absolute price (`dist > 0.45 * entry`)?
- India futures: `notional` on the plan is **margin**. OMS must not do `notional/qty` for the fill.

**Questions — exit / same-price clips**

A settled row with avg ≈ exit is **not** automatically a successful round-trip. Classify each cause:

| Cause | Sense? | What to look for |
|---|---|---|
| Daily Yahoo mark unchanged between open and close | No edge, only fees | `price_bars.bar_date` daily; `cache.last` static for hours |
| Repair scaled a 10× entry up to the exit | Accounting fix, not a trade | `exit/avg` was ~10 before repair; Why says “Stop hit … line was ₹219” |
| Stop compared as a price when it was a distance | Bug | `Position.stop` ≪ last for a short |
| Session flatten (India EOD, FX Friday 17:00 ET, Globex halt) | Intentional | Why text names the clock |
| “Tape flipped” on the same SMA snapshot used to open | Likely inconsistent primary vs decision | `_exit_reason` vs `decide()` inputs |
| Cover of a short at the only available mark | Accounting identity | side=sell open, buy cover, same `cache.last` |

It is **not** “price went up so we settled at the buy price as a stop.” A long stop is `entry − distance`. A short stop is `entry + distance`. Closing at `cache.last` when `cache.last == avg_price` means the mark never moved (or the mark used to open was the 10×-wrong number and the mark used to close was the real tape).

**Questions — fees and P&L**

- Net = tape − fees. When tape is ₹0, net equals **−fees**. The book must show tape, fees, and net as three numbers (`ui/book_view.py`, `ui/templates/book.html`).
- `charges.indian.levy` on crypto / COMEX / FX: are Indian STT/stamp lines being applied to non-Indian tape? That would fabricate costs and train the model that every clip loses the bill.

**Questions — live**

- Prove there is no path from `paper_auto` to a live order without `live_armed` and a real adapter.
- Daily live cap, drawdown pause: new live only, or does it flatten live?

---

## Track E — Data, clocks, universe

- `data_providers/service.py` — Yahoo + Binance; `_to_inr`, `_usdinr`
- `universe/nse_bse.py`, `universe/crypto.py`, `universe/derivatives.py`, `universe/global_markets.py`
- `router/calendar.py` — IST cash, ET FX, CME/ICE halt

**Questions**

- Is USDINR required before commodity marks are written? Default 83.5 vs live ~95 — a 14% book error.
- Are OHLC inverted correctly for USDJPY-style pairs?
- Can `GOLD` (NSE proxy) and `GOLD.X` (COMEX) collide in routing or repair?
- Autopilot poll (`alerts.poll_seconds`) vs daily bars: is the desk pretending to be a minute trader on a daily close?

---

## Track F — Model training and labels

Read:

- `meridian_v3/engine/meta_label.py` — primary direction + logistic
- `meridian_v3/engine/bayesian.py` — Beta(α, β) update on paper close
- `build_meta_labels.py` — reconstructs buy→sell from fills
- `README_MeridianV3_MetaLabels.md`
- `ANALYTICAL_REPORT_MeridianV3_MetaLabels.md`
- `meridian_v3_meta_labels.csv` / `artifacts/meridian_v3_meta_labels.csv`

**Questions**

- Labels must be **decision-time safe**: no future bars, no same-bar close, no exit price in the feature set. Confirm the ATR join (`bar_date < decision day`).
- `build_meta_labels.py` pairs **buy → sell** only. Shorts (sell → buy cover) are dropped or, worse, a cover-buy paired with the **next** open-sell as a fake long. Verify.
- `honest_pnl` after the 10× repair: GOLD.X should be rupees of tape, not +₹7,624 on a 0.02 lot. SILVER shorts should not appear as −₹224 per minute.
- `autopilot.persist_belief(won=pnl > 0)` on a same-mark clip: `pnl` is about **−fees**, so every flat clip is a **loss** to the Beta prior. That teaches the model “this setup fails” when the tape never moved. Is that intended?
- Signal join window is 2 s / 5 s. Autopilot is ~60 s. How many honest round-trips fail to match a signal?
- `p_success` in the CSV is the **old** logistic output, used as a feature to train a **new** logistic — leakage / circularity?
- Walk-forward (`engine/walkforward.py`): is it on the live cycle or only a unit-tested stub?
- Sample size: tens of same-day paper clips on a daily mark are not a trading edge. Say so if that is what the CSV is.

Rebuild check (do not overwrite unless asked):

```bash
python build_meta_labels.py --db %USERPROFILE%\MeridianV3\meridian_v3.db --out artifacts
```

Compare row counts, win rate, and mean `honest_pnl` to the README headline numbers. If they disagree, the docs are stale.

---

## Track G — UI / API contract

- `meridian_v3/ui/routes.py`, `ui/book_view.py`, templates
- `meridian_v3/api/routes.py`
- `domain/copy.py`, `domain/reviews.py`

**Questions**

- Does any template or `/api` response include Kelly *f*, logistic weights, α/β, or per-leg recipes?
- Book page: tape / fees / net. Can a reviewer still confuse “Charges paid” (all fills) with settled net?
- Chart diamonds: do they match actual fills or only signals?
- Import preview cannot commit without an explicit commit path.

---

## Track H — Tests and operability

- `tests/` — especially `test_repair.py`, `test_autopilot.py`, `test_book_view.py`, `test_decision.py`, `test_safety.py`, `test_global_markets.py`
- CLI: `seed`, `serve`, `cycle`, `prices`, `flatten-india`, `repair-book`, `arm`

**Questions**

- Which of the bugs above have a red-capable test? Missing tests are findings.
- `test_seed_and_cycle` must not depend on “today is Friday after 15:30 IST.”
- Can `repair-book` run while `serve` holds the DB?

---

## Report format (mandatory)

```markdown
# MERIDIAN V3 independent audit
Date, git HEAD, DB path inspected (or “no DB”).

## Verdict
One paragraph. Ship / ship with fixes / do not train / do not arm live.

## Spec vs code
Table: claim | where claimed | what code does | status (match / drift / missing).

## Findings
### F1 — Severity: critical
- File: path:line
- What I verified (command, query, or trace)
- Why it matters (money, live, training)
- What “fixed” would look like

## Same-price clips
Classification of open≈close rows (repair artifact / stale daily mark / bad stop / clock flatten / other).

## Training data
Usable / not usable, and why. Shorts, leakage, sample size, fee-only labels.

## Architecture
Boundaries that hold. Boundaries that leak. Dead modules.

## What I did not check
Honest list.

## Suggested order of work
Numbered, money-first, then labels, then docs.
```

Start now. Read before you conclude. Cite lines. Do not implement.

---

## How a human should use this

- Give one agent the **full prompt** for a single end-to-end audit, **or**
- Split Tracks A–H across agents and merge the reports yourself (do not let one agent “reconcile away” another’s critical).
- Point them at a **frozen git SHA** (for example the tip of `fix/honest-paper-pnl` / PR #3) so reviews are comparable.
- After the report, only then ask for patches, one finding at a time.
