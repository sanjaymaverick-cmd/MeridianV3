# Keeping proprietary mathematics private

V3 is fully functional without putting the secret sauce in the browser.

## Rules

1. **Python is the only place formulas live.** `engine/`, `risk/`, `scoring/`.
2. **The API returns finished objects.** `ChartPayload`, `PlainReview`, net Greeks, size already snapped to lots. No weights, no φ, no IV surface, no per-leg recipe.
3. **Changing a formula means changing a module and a test**, never a Jinja template.
4. **Reviews are sentences.** A 10-year-old can read them. The number next to the sentence is still exact.
5. **Do not vendor the engine into a public wheel if you fork this.** Keep `engine/` out of any cloud function that you do not control.
6. **Broker keys stay in `config/local.yaml` (gitignored) or the OS keychain.** Never in the repo.
7. **V1 algorithms are not rewritten.** Only the words were simplified. V2 Greeks math is copied, not re-derived, so behaviour stays comparable.

## What a future public UI may see

Candles, green/red diamonds, gold levels, soft zones, shaded windows, confidence as an integer 0–100, Daily PnL, Gamma Scalping PnL, and the six vega action *titles*. That is enough to trade the desk. It is not enough to clone the model.
