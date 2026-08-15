# Auto Decision Engine

Module: `meridian_v3/decision/engine.py`

The engine answers one question: **Buy, Sell, or Hold — and may this clip be paper-only or also live?**

## Inputs

| Input | Source |
|---|---|
| Primary direction | V2 signal families + SMA / RSI / breakout / mean-reversion |
| Confluence 0–100 | Weighted factor votes |
| Freshness | Half-life decay from signal time |
| p(success) | Meta-label logistic, updated from paper outcomes |
| Belief | Beta(α, β) per rule, updated from paper wins/losses |
| Edge vs costs | Expected rupees minus brokerage/STT/slippage/spread + pad |
| Route | Suffix/asset-class dispatch (`router/markets.py:market_for`) — a symbol's own suffix (`.F`/`.C`/`.X`/`USDT`) or asset class decides its market outright, e.g. equity cash for plain shares, crypto 24/7, FX 24/5, global commodities. There is no runtime scoring contest between markets; the same symbol always routes to the same market (F6) |
| Size | Confidence-weighted fractional Kelly × ATR |
| Safety | Drawdown, arm switch, daily live cap, session |

## Gates

1. Primary side must not be flat.
2. Confluence must agree with the side.
3. Meta-label `p ≥ 0.52` to even consider a clip.
4. Freshness above the floor (default 0.35).
5. Expected edge > estimated costs + safety margin.
6. Sizer must fit the ₹50,000 book (at least one share, or skip).
7. **Paper** if the above pass.
8. **Live** only if paper passed **and** live is armed **and** confidence ≥ 0.82 **and** confluence ≥ 62 **and** drawdown < 20% **and** daily live cap not spent.

A failed gate is Hold. The review still explains why, in simple words, and still says “(not an order)”.
