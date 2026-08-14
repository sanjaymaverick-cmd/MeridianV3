# Safety on a ₹5,000 book

Module: `meridian_v3/safety/guards.py`

This account is small. Costs and one bad day matter.

| Rule | Default | Effect |
|---|---|---|
| Drawdown pause | 20% from peak | New **live** trades stop. Open live stays. Paper continues. |
| Soft shrink | from 8% | Size fades linearly toward the pause. |
| Cash reserve | 10% | Never spend the last ₹500 of a ₹5,000 book. |
| Daily live cap | 3 / 6 | 3 normal, 6 only on very high-confidence days. |
| Concurrent | 2 / 4 | More only when confidence is high and cash remains. |
| Overnight | options & FX off | Intraday flattened ~20 minutes before 15:30 IST. Equity CNC may stay. |
| Options | buy only | Selling premium is a hard reject. |
| Forex | nano/micro | Standard lots forbidden. |
| Live arm | off | No live ticket until a human arms the desk **and** a broker is plugged in. |

Open positions are never force-closed by the pause rule. That is deliberate: a 20% hole should not become a market-on-close panic on a one-share book.

Simple language on the pause card:

> Live trading is paused (not an order). The dedicated account has fallen 20% from its peak. Open positions may stay open. New live trades wait until the book heals.
