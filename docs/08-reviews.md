# Simple-language reviews

A 10-year-old and a 60-year-old should both understand every card. The numbers stay exact.

## Shape (binding)

1. Title with **“(not an order)”**
2. Delta / Gamma / Vega / Theta in plain words + numbers
3. Daily PnL line (time)
4. Gamma Scalping PnL line (the move)
5. Model suggestion (size and direction, review only)
6. Choices: Accept and write an intended-trade note / Dismiss / Snooze / Do nothing

Catalog: `meridian_v3/domain/templates.py`. Fill-in: `compose_review`.

## Auto decisions

Auto clips use the same skeleton (`auto_decision` template). They explain confidence and confluence. They still say “(not an order)”.

A **fill** is not a review. Fills use `PAPER FILL:` or `LIVE FILL:` via `domain/copy.py:live_note`. Those words are forbidden on review cards.

## Forbidden on reviews

`you should hedge`, `must reduce`, `place order`, `auto-send`, `live order sent`.

`assert_review_copy` raises if a review breaks the rule. Tests enforce it.
