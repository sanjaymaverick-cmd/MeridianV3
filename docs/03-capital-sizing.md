# Capital & Position Sizing Engine

Module: `meridian_v3/capital/sizer.py`

Bankroll is the **whole dedicated book**, starting at ₹5,000. Profits compound because yesterday’s equity is today’s bankroll.

## Stack

```
risk_rupees = equity × min(κ · c · f*, cap(c)) × drawdown_scale
qty         = floor(risk_rupees / (ATR × k_stop))
qty         = min(qty, cash_after_reserve / price, max_position)
```

- `f*` is full Kelly from meta-label `p` and payoff `b`.
- `κ` is 0.15 (never more than 0.25 on this book).
- `c` is blended confidence.
- `cap(c)` is 4% / 1.5% / 0.8% of equity for high / normal / low confidence.
- `drawdown_scale` shrinks from 8% drawdown and hits zero at 20%.
- 10% cash is always kept back.

## Market constraints

| Market | Rule |
|---|---|
| Equity cash | Whole shares. One share is allowed even when ATR wants zero, if cash fits. |
| Options | **Buying only.** One lot. Premium ≤ 12% of equity. Selling premium is forbidden. |
| India futures | Paper mini-lot. Full NSE lots are too big for this book. |
| Global commodities | Paper mini-lot, 10% margin, marked in rupees. COMEX / NYMEX / ICE. |
| Crypto | Fractional coins. Futures capped at 2×. Options buying only. 24/7. |
| Forex | Nano/micro only. Standard lots forbidden. Open Sunday–Friday. |

## Concurrency

Normal days: at most 2 open clips. High confidence: at most 4, and only if cash remains.

## Horizon

Intraday by default. Positional (1–3 days) only when confidence ≥ 0.88.
