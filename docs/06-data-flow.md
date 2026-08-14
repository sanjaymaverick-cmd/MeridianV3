# Example data flow

Name: **INFY**. Dedicated book: ₹5,000 cash, no open clips. Live disarmed.

1. **Tape.** Last 1,488. SMA20 1,460. ATR 22. Volume 1.6× yesterday.
2. **V2 family.** `breakout_volume` and `trend_ma_regime` fire. Primary direction = +1 (“fast average is above the slow one; price pushed through a recent high”).
3. **Confluence.** Trend +0.8, breakout +0.6, score +0.4 → confluence ≈ 78.
4. **Freshness.** Signal age 4 minutes → freshness ≈ 1.0.
5. **Meta-label.** Logistic says p(success) = 0.61. Take for paper.
6. **Bayesian.** Rule “core” is Beta(4,4). Blend → confidence ≈ 0.58.
7. **Edge.** Expected extra ≈ ₹28 after a 2:1 payoff on one share. Costs on ₹1,488 ≈ ₹4 + ₹8 pad. Edge clears.
8. **Route.** Equity score 80 vs options 20 vs FX 15. Stay in **equity cash**.
9. **Size.** Fractional Kelly × confidence × ATR → 1 share, stop ≈ ₹33, risk ≈ ₹33 (0.7% of book).
10. **Safety.** Live disarmed. Session open. Drawdown 0. Paper allowed. Live blocked.
11. **OMS.** PaperBroker buys 1 INFY at 1,488. Cash 3,512. Review card written: “Auto decision review (not an order)”.
12. **If** the same clip later prints confidence 0.86, confluence 70, live armed, adapter present, and no 20% hole — the OMS clones the ticket to live.

The chart then shows a green diamond on today, SMA gold line, 20-day zone, and a shaded high-attention window. Tooltip: “Look at a long / entry · 58”.
