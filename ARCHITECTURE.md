# MERIDIAN V3 — Architecture

How V1, V2, and V3 connect. The working code is this repository. V1 and V2 stay in their own apps and databases.

```
                    ┌──────────────────────────────────────┐
                    │           Presentation               │
                    │  Jinja + Lightweight Charts + JSON   │
                    │  finished words, diamonds, PnL lines │
                    └──────────────────▲───────────────────┘
                                       │ ChartPayload / PlainReview / DecisionCard
                    ┌──────────────────┴───────────────────┐
                    │         Execution & safety           │
   V3 NEW ───────►  │  Auto Decision · Sizer · Router      │
                    │  Hybrid OMS · PaperBroker · Plugin   │
                    │  Drawdown pause · overnight · caps   │
                    └──────────────────▲───────────────────┘
                                       │ GreekSnapshot / RawSignal / SizePlan
                    ┌──────────────────┴───────────────────┐
                    │         V2 risk engine               │
   V2 ────────────► │  Greeks book · gamma scalp           │
                    │  six vega actions · reviews          │
                    │  watch / journal / MCX / FX context  │
                    └──────────────────▲───────────────────┘
                                       │ factors / regime / tape
                    ┌──────────────────┴───────────────────┐
                    │         V1 core                      │
   V1 ────────────► │  five-factor score · hysteresis      │
                    │  ingestion · symbols · money         │
                    │  simple English notes                │
                    └──────────────────────────────────────┘
                                       │
                              SQLite  ~/MeridianV3
```

## Folder map

```
meridian_v3/
  engine/          proprietary math (Kelly, ATR, meta-label, Bayesian,
                   confluence, freshness, walk-forward, edge filter)
  decision/        Auto Decision Engine
  capital/         ₹5,000-aware position sizing
  router/          equity-home multi-market router
  execution/       OMS + paper broker + live plugin slot
  safety/          drawdown, session, overnight
  risk/            V2 Greeks / gamma / vega (unchanged math)
  scoring/         V1 five-factor composite (unchanged math)
  signals/         V2 families + chart DTO
  ingestion/       PDF / XLSX / CSV / image OCR
  portfolio/       imported holdings → watch + risk book
  domain/          money, symbols, review templates, copy rules
  storage/         SQLite schema
  ui/              desk (no math)
  api/             finished JSON only
```

## Data flow (one cycle)

1. Tape and imported holdings sit in SQLite.
2. V1-style factors + V2 signal families produce a primary direction.
3. V3 confluence, freshness, meta-label, Bayesian blend, and cost filter decide Hold / Buy / Sell.
4. The sizer turns confidence into a share/lot count the ₹5,000 book can actually hold.
5. Safety checks: drawdown, daily live cap, session, overnight, options-buying-only.
6. Every accepted clip is paper-traded.
7. The same clip is cloned to live only if live is armed, confidence is very high, and a broker adapter is registered.
8. A review card is written in simple English and always contains “(not an order)”.
9. The chart receives candles + green/red diamonds + lines + zones + shaded windows. No coefficients.

## What never crosses to the browser

- Kelly internals, logistic weights, Beta(α, β)
- Per-leg option construction beyond net Δ Γ ν Θ
- Hedge ratios, SHAP φ, IV surface
- Broker secrets
