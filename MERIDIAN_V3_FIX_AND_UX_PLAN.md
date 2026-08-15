# MERIDIAN V3 — fix plan, UI/UX redesign, and suggested app changes

Companion to `MERIDIAN_V3_AUDIT.md` (findings F1–F16). This is a plan only — nothing here has been implemented. Each item lists what to change, why (tying back to a finding where relevant), and how to know it's done. Work top to bottom within each phase; phases are ordered money-first.

---

## Part 1 — Apply the audit fixes

### Phase 0 — Stop the bleeding (do before anything else touches the book)

**0.1 Freeze training on current artifacts.**
Delete or clearly mark `meridian_v3_meta_labels.csv`, `artifacts/meridian_v3_meta_labels.csv`, `artifacts/meridian_v3_with_meta_labels.db` as stale (F8) until 0.2–0.4 are done and the set is rebuilt. Add a `source_db_hash` + row-count stamp to every future export so staleness is detectable automatically, not by re-deriving it by hand.
*Done when:* the artifacts folder either has fresh, stamped output or a `STALE.md` marker; nobody trains on the current CSVs.

**0.2 Fix `_fill_price()` to never divide margin by lots.**
File: `execution/oms.py`. If `decision.price` is falsy, **reject the order** (same as the existing `price <= 0` guard in `capital/sizer.py:size_position`) instead of falling back to `notional / qty`. Add a unit test that constructs a `DecisionInput` with `price=0` on `india_futures`/`global_commodities` and asserts the order is refused, not mispriced. (F2)
*Done when:* the new test passes and a symbol whose cache is momentarily empty produces a Hold, never a fill.

**0.3 Add a fill-price sanity bound as a second gate.**
File: `execution/oms.py` or `execution/brokers/paper_broker.py`. Reject/flag any fill whose price differs from `price_cache.last` by more than, say, 25% (configurable). This is a backstop for the *next* unit-mismatch bug, not just this one. (F2, defense-in-depth for F10)
*Done when:* a fabricated 3× or 10× price cannot silently reach `Fill`/`Position` even if 0.2 has a gap somewhere.

**0.4 Fix `repair_shifted_clips` for `exit_price IS NULL` rows.**
File: `storage/repair.py`. Stop rewriting `avg_price` against `price_cache.last` when there is no `exit_price` to align it to — there is nothing honest to align against. Instead:
- Look up the matching exit `Fill` for that position (same approach `ui/book_view.py:_infer_exit` already uses) and reconstruct `exit_price`/`realized_pnl`/`close_qty` from it once, or
- If no matching fill exists either, leave the row untouched and mark it `status="unreconciled"` (new status value, or a `notes` flag) so it's visibly excluded from equity/training rather than silently wrong.
Add a regression test seeding a closed position with `exit_price=None` plus a real matching exit `Fill`, asserting repair reconstructs the position correctly and is a no-op on a second run. (F3)
*Done when:* the 26 currently-broken rows in the live DB either get honest `exit_price`/`realized_pnl` or are explicitly flagged, and no repair run ever again copies today's mark into a historical `avg_price`.

**0.5 Stop scoring EOD-flatten no-moves as belief losses.**
File: `autopilot.py:manage_exits`/`flatten_india_paper`, `pipeline.py:persist_belief`. Before calling `persist_belief`, check whether the exit was a genuine stop/target/tape-flip vs. a same-mark EOD/weekend flatten with `abs(exit_price - avg_price) < <epsilon>`. For the latter, either skip the belief update entirely or record it in a separate "no-signal, cost-only" bucket that doesn't move `alpha`/`beta`. (F1)
*Done when:* a flat EOD-flattened clip no longer decrements the Beta belief; a unit test simulates this and asserts `beliefs.losses` is unchanged.

**0.6 Get an intraday mark, or explicitly gate the autopilot to end-of-day cadence.**
File: `data_providers/service.py`. Either (a) pull `interval="1m"`/`"5m"` intraday history where Yahoo supports it (Indian cash + major crypto/FX/commodity futures) so a same-day clip can actually move, with `period="6mo"` daily history kept only for ATR/SMA context, or (b) if intraday data isn't available/affordable, change the *product* claim: run the auto-decision cycle once per session (not every 60s) and stop describing this as an intraday desk in the UI and docs until real intraday data exists. (F1, ties to F16)
*Done when:* either live prices actually move between a clip's open and its same-day close under normal conditions, or the docs/UI stop claiming ~60s intraday behavior.

### Phase 1 — Close the model-integrity gaps

**1.1 Wire (or remove) the online logistic.**
File: `engine/meta_label.py`, `decision/engine.py`, `pipeline.py`. Persist `OnlineLogit` weights (new table, e.g. `logit_weights(rule_name, feature, weight)` + `bias`), load them into `DecisionInput.logit` in `pipeline.py`, and call `.update()` at the same place `persist_belief` runs. If this is more than the personal-desk scope wants right now, remove the "updated from paper outcomes" language from README/docs/02/docs/05 instead and say plainly that `p_success` is a fixed heuristic. Either fix is acceptable; leaving it half-described is not. (F4)
*Done when:* either `p_success` measurably changes over a session as trades close (add a smoke test asserting two different `p_success` values for the same features before/after a synthetic `.update()` call goes through the real pipeline), or the docs no longer claim learning.

**1.2 Wire (or remove) the V1 five-factor score.**
File: `scoring/composite.py`, `pipeline.py`. Either call `scoring.composite` from the price-refresh or cycle path and write real `FactorScore` rows, or delete the dead read path in `pipeline.py:134-137` and stop presenting V1 scoring as a live input in `ARCHITECTURE.md`. (F5)
*Done when:* `factor_scores` has rows that vary by symbol, or the docs no longer show V1 as feeding V2/V3.

**1.3 Decide what the router should actually do, then match docs to it.**
File: `router/markets.py`, `pipeline.py`. Either delete `pipeline.py:198-203`'s dead score constants and the unreachable comparison branch in `route_market()` (routing stays suffix-based, document it that way), or actually remove `preferred_market` from the call so the scores compete as advertised. Pick one; right now the code computes something it never uses. (F6)
*Done when:* `route_market()` has no dead branch, and ARCHITECTURE.md/docs/02 match whichever behavior was kept.

**1.4 Fix short round-trip reconstruction.**
File: `build_meta_labels.py:reconstruct_honest_roundtrips`. Replace the naive adjacent-pair walk with a per-symbol position-state stack: push on an opening side, pop on the matching closing side, only emit a round trip when a position fully flattens. Add a test with an interleaved `[sell, buy, sell, buy]` sequence (short, then long, or two shorts) asserting no cover-buy is ever paired with an unrelated open-sell. (F7)
*Done when:* the new test passes and the builder correctly labels short round-trips (or explicitly excludes them with a stated reason, not a silent mis-pair).

**1.5 Rebuild and re-stamp the training set.**
Run `build_meta_labels.py` against the live DB after 0.2–1.4 land, replacing the stale artifacts from 0.1. Update `README_MeridianV3_MetaLabels.md`'s headline numbers to match. (F8)
*Done when:* CSV row count, win rate, and avg honest P&L in the README match a fresh run, with the source DB stamped.

### Phase 2 — Second lines of defense and cleanup

**2.1 Market-aware order validation at the broker/OMS boundary.** Reject a `sell` on `options_buy`/`crypto_options`, reject `qty` above the configured lot ceiling for `forex_micro`, inside `OrderManager._send`/`PaperBroker.place` — not just upstream in `decision/engine.py`. (F10)
**2.2 Flag stale-FX-fallback marks.** When `_usdinr()` falls back to 83.5, set `quality="fx_fallback"` on affected `PriceCache` rows and surface it in the UI (see Part 2, 2.6). (F12)
**2.3 Fix the ₹5,000→₹50,000 migration gap.** Replace the `peak <= 5000.01` heuristic in `storage/db.py:_migrate` with an explicit `schema_version`/`migrated_at` marker instead of inferring state from a rupee threshold. (F11)
**2.4 Stop mutating `Fill.note` in repair.** Keep the original note immutable; write corrections to a new `desk_events` row or a `correction_json` column instead. (F15)
**2.5 Add a re-entry cooldown per symbol.** Enforce the already-computed-but-unused `reentry_sec` concept live: don't reopen a symbol within N minutes of its own close in the same session unless confidence is materially higher. (F16)
**2.6 Log exceptions in `_repair_book()` instead of swallowing them**, and call `setup_logging()` at app/CLI startup (currently defined but never invoked — see Part 3). (F14)
**2.7 Fix the `/desk/cycle` notice to reflect actual `live_armed` state** instead of a hardcoded string. (F13)
**2.8 Reconcile the ₹5,000 vs ₹50,000 docs** (README bullet 3 + closing line, docs/03/06/07/10, concurrency numbers). (F9)
**2.9 Note the stop-distance-vs-price 0.45×entry heuristic as a config constant**, not a magic number, and add a test with a large-ATR instrument that would have tripped the old bug, to lock in the fix.

---

## Part 2 — Frontend UI/UX

### Current state (read directly, not assumed)

The desk is a server-rendered FastAPI + Jinja2 app: every action (`Run cycle`, `Seed demo`, `Start/stop auto`, `Arm/disarm live`, `Broker switch`) is a full-page POST/redirect. Styling is a single 124-line CSS file — a genuinely nice dark-gold "trading terminal" aesthetic (IBM Plex fonts, tabular numerals, restrained palette) — with one real interactive element: a per-symbol Lightweight Charts candlestick view on `/chart`, driven by 171 lines of vanilla JS. Tables are plain HTML, unpaginated, unsorted, unfiltered. There's no equity-curve chart anywhere in the UI despite `/api/equity` already returning both paper and live curves. `Arm live` is a single click with no confirmation. Nothing in the UI distinguishes a genuine trade outcome from a same-price fee-scratch or a fabricated futures "win" — exactly the numbers Part 1 is fixing under the hood are currently presented with the same visual weight as real ones.

This is a good foundation (the visual language doesn't need a rewrite) with real functional gaps. The plan below keeps the aesthetic and fixes the interaction/information-design problems.

### 2.A — Trust and safety (do alongside Phase 0, these are cheap and high-value)

1. **Confirm before arming live.** `templates/safety.html`'s "Arm live" button currently submits instantly. Add a two-step confirm (JS `confirm()` at minimum, or a proper modal: "Arm live trading on the ₹50,000 book? No broker adapter is registered — orders will still be rejected until one is." — and that adapter-status line should be computed server-side from `get_live_broker()`, not just asserted in copy).
2. **Show adapter status, not just the arm switch.** Right now "Arm live" and "is a broker actually registered" are two separate facts the user can't see together. Add a visible `Broker adapter: none registered — live orders will be rejected` / `Broker adapter: <name> connected` line on the Safety page, sourced from `execution/brokers/plugin.py:get_live_broker()`.
3. **Data-quality badges on every P&L number.** Once Part 1's repair/mark fixes land, keep the underlying flags (`quality="fx_fallback"`, `is_clean`/futures-mismatch flag, `status="unreconciled"`) and surface them as small inline badges next to the affected row in `book.html` and `command.html` — e.g. a dotted-underline "estimate" tag with a tooltip explaining why. This turns "silently wrong" into "visibly flagged," which is the single highest-leverage UI change given what the audit found.
4. **Distinguish "no move" from "loss" in the settled-trades tables.** `book.html`'s settled tables currently color a same-price fee-only clip identically to a real directional loss (`p.pnl_class` = "down" either way). Add a third visual state — e.g. neutral grey "scratch" — when `gross ≈ 0`, so a glance at the book doesn't read as "the model is losing" when it's actually "the tape didn't move."

### 2.B — Make the desk feel alive (currently: click a button, wait for a full page reload)

5. **Move mutating actions to `fetch()` + partial refresh.** `Run cycle`, `Start/stop paper auto`, `Seed demo desk` are exactly the kind of action that benefits from an inline spinner + result toast instead of a full navigation. Keep the POST endpoints (no API redesign needed), just call them via `fetch()` from `meridian-v3.js` and patch the DOM (or re-fetch and swap the relevant `<article class="panel">` fragments) instead of `RedirectResponse`. This alone would make the "auto is ticking" feel real instead of static.
6. **Auto-refresh the Command and Book pages** on a short interval (10–15s) while `paper_auto` is on, via a lightweight `setInterval` + fetch-and-diff, so the desk shows near-live state without manual reloads. Pause the interval when the tab is hidden (`document.visibilityState`) to avoid needless load.
7. **Loading/empty states.** Tables currently jump straight from "no rows" text to a full table with no in-between. Add a skeleton/spinner state for the async chart and review fetches in `meridian-v3.js` (`mountChart`/`mountReview` currently show nothing while awaiting `fetch`).

### 2.C — Information architecture

8. **Put an equity curve on the Command page.** `/api/equity` already returns paper+live curves; nothing renders them. Add a small Lightweight Charts line chart (reuse the existing chart machinery) to `command.html` showing paper equity vs. its peak, with the drawdown band shaded — this is the single most important "is the book okay" glance-view and it doesn't exist today.
9. **Symbol picker instead of a bare query param.** `/chart` and `/review` currently expect `?symbol=X` typed by hand (`review.html` hardcodes `symbol="NIFTY"` as the only default; `chart_page` reads a raw query param). Add a `<datalist>`/autocomplete sourced from `/watch`'s active symbols so a user can actually navigate to a name without knowing its exact ticker string.
10. **Table sort/filter/search, client-side.** `signals.html` (last 50 decisions), `book.html`'s Fills table (last 40), and the settled-trades tables have no way to filter by symbol, market, or outcome. A small dependency-free JS table enhancer (click-header-to-sort, a text filter input) on the existing `<table>` markup gets most of the value without a framework.
11. **Pagination for Fills/Signals.** Both are currently hard-limited (`limit(40)`, `limit(50)`) with no "load more" — old data is simply invisible. Add a `?before=<id>`/cursor param and a "Load more" button.
12. **Mobile: tables need a real strategy, not just hiding the rail.** The only responsive rule today (`@media (max-width: 980px) { .rail { display: none; } }`) doesn't address that a 9–12 column table (Fills, settled trades) will overflow badly on a phone. Either switch dense tables to a card layout under a breakpoint, or make them horizontally scrollable with a sticky first column (symbol), which is the more realistic effort/impact tradeoff here.

### 2.D — Decisions/Signals page specifically

13. **Show the *reason* inline, not just the pass/fail.** `signals.html` currently shows side/confidence/confluence/paper/live columns; the actual "why" text (`SignalRow.reason`, already stored) isn't rendered on that table at all — only on the desk-events feed below it. Pull `reason` into an expandable row or a hover tooltip so a user can see *why* a Hold happened without cross-referencing a separate list.
14. **Group by symbol with a running tally**, so "INFY.F opened and closed 43 times today" is visible as a pattern (this is exactly what Finding F16 describes) rather than 43 separate rows a user has to notice by eye.

### 2.E — Visual polish (lower priority, the aesthetic itself is fine)

15. Add a subtle empty/loading skeleton to `.chart-box` before `LightweightCharts` mounts (currently a blank panel).
16. `figure`/`.num` classes already use tabular numerals — extend the same treatment to the new equity-curve/summary cards for consistency.
17. Light theme (`body.light`) exists in CSS but wasn't visually spot-checked against every page — worth a quick pass to confirm contrast holds on the gold accents once the new badges (2.A.3) are added.

---

## Part 3 — Suggested app/product changes (beyond bug fixes)

These are things not directly tied to a specific audit finding but that came up while reading the code end to end.

1. **Turn on the logging that already exists.** `meridian_v3/logging.py:setup_logging()` is fully written (loguru, file rotation, 14-day retention) and is never called from `app.py` or `cli.py`. Grep confirms zero `logger.info/warning/error` calls anywhere in the codebase. Call `setup_logging()` at startup and add log lines at the handful of decision points that matter most: cycle start/end counts, repair runs (and what they changed), price-refresh failures, live-arm toggles, and any order rejection. Right now the only record of what happened is what made it into a `DeskEvent`/`Fill.note` — there is no operational log to debug a bad night from.
2. **Add CI.** No `.github/workflows` exists; `python -m pytest` (85 tests, all passing) isn't run anywhere automatically. A minimal GitHub Actions workflow (checkout → `pip install -e ".[dev]"` → `pytest`) on every push/PR would have caught the stale-artifact and dead-code issues far earlier, and prevents silent regressions on the fixes in Part 1.
3. **Implement the Phase 3 kill switch the docs already promise.** `docs/10-phases.md` names "max rupee loss per day (₹200 default suggestion)" as a Phase 3 safety feature; `safety/guards.py` only implements a *drawdown percentage* pause and a *daily live trade count* cap — there's no absolute rupee-per-day circuit breaker for either paper or live. Worth adding regardless of when live actually gets used, since paper is exactly where you'd want to catch "the desk is bleeding fast today" early.
4. **Per-rule/per-market belief instead of one global `beliefs` row.** Today there's exactly one `BeliefRow(rule_name='core')` shared across every market and symbol (equity, crypto, futures, commodities all update the same Beta prior). Once F1/F2 stop poisoning it, a single scalar still can't tell you "equity is working, crypto isn't" — split belief tracking by market (or at least by `is_futures`/asset class) so the live-confidence gate and any retrain can react to where the edge actually is.
5. **A real backtesting/walk-forward harness wired to the live cycle**, not just the unit-tested `engine/walkforward.py` module. Given `p_success`/confidence currently gate live-eligibility, having zero historical validation of the *actual* decision function (not just the isolated math) before ever arming live is a gap worth closing before Phase 3 of the roadmap.
6. **Alerting for state changes that matter.** Nothing currently notifies the operator (email/desktop notification/webhook) when: drawdown crosses the pause threshold, live gets armed/disarmed, the autopilot worker dies (`last_error()` is tracked but only surfaces on the next page load), or a repair run changes more than a few rows. A single outbound webhook hook (even just to a local file or Slack) would close the "I wasn't watching when it mattered" gap.
7. **A staging/"dry adapter" broker for live-path testing.** There is currently no way to exercise the live order path end-to-end (arm → decide → OMS → broker) without either registering a real broker or leaving it permanently untestable. A `DryRunBroker(BrokerAdapter)` that logs what it *would* have sent without touching real money would let Phase 3 be tested safely before a real adapter exists.
8. **Config-drift guardrail.** Given how much of Part 1 is "docs said X, code does Y," consider a small script (or pytest) that parses the numeric claims in `docs/*.md` (starting equity, concurrency limits, drawdown %) against `config/default.yaml` and fails CI if they diverge — cheap insurance against this exact class of drift recurring.
9. **Explicit `schema_version` table.** Migrations today (`storage/db.py:_migrate`) infer state from data heuristics (`peak <= 5000.01`) rather than tracking an explicit version — brittle by construction (Finding F11) and will only get more so as more migrations accumulate. Worth introducing before the next schema change, not after.

---

## Suggested execution order

1. Part 1, Phase 0 (0.1–0.6) — stops the book from lying to itself and to any retrain.
2. Part 1, Phase 1 (1.1–1.5) — closes the "looks like learning, isn't" gaps and rebuilds honest training data.
3. Part 2, section 2.A (trust/safety UI) — cheap, and directly makes the Phase 0/1 fixes *visible*, which matters as much as making them true.
4. Part 1, Phase 2 (defense-in-depth, cleanup) and Part 2, sections 2.B–2.D (interactivity, IA) in parallel — neither blocks the other.
5. Part 3 (product/architecture) — pull items forward opportunistically (logging and CI in particular are cheap enough to do anytime; do them early since everything else gets easier to debug once they exist).
