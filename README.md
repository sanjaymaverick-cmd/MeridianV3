# MERIDIAN V3

Personal-use proprietary auto-trading desk for a dedicated Indian account that starts at **₹50,000**.

V3 is the complete, unified system. It keeps every V1 advisory habit and every V2 Greeks / risk / review habit, then adds an auto-decision layer, hybrid paper + live execution, a broker-agnostic order manager, and portfolio import from PDF, Excel, and photos.

**Reviews always say “(not an order)”.** Live fills are a separate, clearly labelled stream. Live stays **disarmed** until you plug in a broker adapter and flip the switch.

## Isolation

| | v1 | v2 | **v3** |
|---|---|---|---|
| Package | `meridian` | `meridian_v2` | **`meridian_v3`** |
| Port | 8787 | 8766 | **8777** |
| Database | `data/meridian.db` | `~/MeridianV2/meridian_v2.db` | **`~/MeridianV3/meridian_v3.db`** |
| Role | Advisor | Advisor + Greeks | **Advisor + auto paper + optional live** |

The three desks can run at once. They do not share a schema.

## Run

```powershell
cd C:\Users\BHAGWAN\MeridianV3
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m meridian_v3 seed
python -m meridian_v3 serve
```

Desk: [http://127.0.0.1:8777](http://127.0.0.1:8777)

```powershell
python -m meridian_v3 cycle
python -m meridian_v3 prices
python -m meridian_v3 import --file tests\fixtures\zerodha_holdings.csv
python -m meridian_v3 import --file statement.xlsx --commit --account "Core"
python -m meridian_v3 arm --off
python -m pytest
```

Native window: `pip install -e ".[desktop]"` then `python -m meridian_v3 desktop`.

## What is inside

1. V1 equity book habits — five-factor score, regime hysteresis, SHAP-style notes kept private, simple language.
2. V2 risk desk — full Greeks, Daily PnL (theta), Gamma Scalping PnL, six vega actions, Lightweight Charts overlays.
3. V3 auto layer — decision engine, ₹5,000-aware sizer, multi-market router, hybrid paper + live OMS.
4. Portfolio import — CSV / XLSX / PDF / screenshot, Indian stocks + mutual funds / ETFs, review-then-commit.
5. Safety — 20% drawdown pause for **new** live risk, daily live cap, overnight filters, options buying only, forex nano/micro only.

## Capital rules (binding)

- Starting equity **₹50,000**. All profits compound. The algo scans NSE/BSE cash, India mini-futures, buy-only options, and Binance crypto (spot, futures, buy-only options).
- Equity cash is home. Capital may visit F&O (options **buying** only) or forex (nano/micro only) when that tape is clearly stronger.
- Risk per trade is confidence-weighted. High confidence can take more. Normal signals take less.
- Pause **new live** trades at 20% drawdown from peak. Open positions may stay. Paper never pauses.
- Mostly intraday. 1–3 day holds only on very high confidence.

## Proprietary math

The browser only receives finished JSON: candles, diamonds, lines, zones, windows, and plain-language reviews. Model coefficients, Kelly internals, meta-label weights, and per-leg construction stay in Python under `meridian_v3/engine` and `meridian_v3/risk`.

## Docs

| # | File | Covers |
|---|---|---|
| 1 | [ARCHITECTURE.md](ARCHITECTURE.md) | V1 → V2 → V3 layers |
| 2 | [docs/02-auto-decision.md](docs/02-auto-decision.md) | Auto Decision Engine |
| 3 | [docs/03-capital-sizing.md](docs/03-capital-sizing.md) | ₹5,000 sizer |
| 4 | [docs/04-hybrid-paper-live.md](docs/04-hybrid-paper-live.md) | Paper + live |
| 5 | [docs/05-mathematics.md](docs/05-mathematics.md) | Formulas |
| 6 | [docs/06-data-flow.md](docs/06-data-flow.md) | Signal → paper → live |
| 7 | [docs/07-safety.md](docs/07-safety.md) | ₹5,000 safety |
| 8 | [docs/08-reviews.md](docs/08-reviews.md) | Simple-language reviews |
| 9 | [docs/09-portfolio-import.md](docs/09-portfolio-import.md) | PDF / XLSX / OCR |
| 10 | [docs/10-phases.md](docs/10-phases.md) | Phased build |
| 11 | [docs/11-proprietary.md](docs/11-proprietary.md) | How the math stays private |

This is personal software. It is not an offer of investment advice to the public. You can lose the whole ₹5,000.
