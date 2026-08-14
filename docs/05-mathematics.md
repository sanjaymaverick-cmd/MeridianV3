# Mathematical formulations

All of these run in `meridian_v3/engine`. The UI never sees the symbols.

## 1. Confidence-weighted fractional Kelly

\[
f^* = \frac{p b - q}{b},\quad q = 1-p
\]

\[
f = \mathrm{clip}(\kappa \cdot c \cdot f^*,\, 0,\, f_{\max})
\]

Default \(\kappa = 0.15\), \(f_{\max} = 0.25\). Negative \(f^*\) sizes to zero.

## 2. Volatility-normalized ATR size

\[
\mathrm{TR}_t = \max(H_t-L_t,\, |H_t-C_{t-1}|,\, |L_t-C_{t-1}|)
\]

\[
\mathrm{ATR}_n = \frac{1}{n}\sum_{i=0}^{n-1} \mathrm{TR}_{t-i},\quad
\mathrm{qty} = \left\lfloor \frac{\text{risk ₹}}{\mathrm{ATR}\cdot k} \right\rfloor
\]

Default \(k = 1.5\).

## 3. Drawdown-aware scale

\[
\mathrm{dd} = 1 - \frac{E}{E_{\mathrm{peak}}}
\]

\[
\mathrm{scale} =
\begin{cases}
1 & \mathrm{dd} < 0.08 \\
1 - \frac{\mathrm{dd}-0.08}{0.12} & 0.08 \le \mathrm{dd} < 0.20 \\
0 & \mathrm{dd} \ge 0.20
\end{cases}
\]

Scale 0 pauses **new live** risk only.

## 4. Meta-labeling

Primary: direction \(d \in \{-1,0,+1\}\) from trend / RSI / breakout / mean-reversion.

Secondary: online logistic

\[
p = \sigma(w^\top x + b)
\]

updated from paper wins and losses. Live requires a high \(p\).

## 5. Regime

Desk mood (V1): Calm / Elevated / Stress from VIX, EWMA vol, trend z, with hysteresis.

Tape shape (V3): Hurst-like score → trending vs mean-reverting; ATR% / EWMA → high vs low vol.

## 6. Cost-aware edge

\[
\mathrm{edge} = p\cdot W - (1-p)\cdot L
\]

Trade only if \(\mathrm{edge} > \text{brokerage}+\text{STT}+\text{slip}+\text{spread}+\text{margin}\).

## 7. Bayesian confidence

Beta prior \(\mathrm{Beta}(\alpha,\beta)\). After a paper close:

\[
\hat p = \frac{\alpha + \text{wins}}{\alpha + \beta + n}
\]

Blended with the one-shot model score.

## 8. Confluence

\[
s = 50 + 50\tanh\Big(\frac{\sum w_i x_i}{\sum w_i}\Big) \in [0,100]
\]

## 9. Freshness

\[
\mathrm{fresh} = 2^{-\mathrm{age}/\tau}
\]

Default half-life \(\tau = 6\) hours. Below 0.35 → Hold.

## 10. Walk-forward

Train / embargo / test folds. A rule is allowed to size hard only if out-of-sample mean > 0 and

\[
\frac{\mathrm{IS}-\mathrm{OOS}}{|\mathrm{IS}|} \le 0.35
\]

## Greeks (V2, unchanged)

Daily PnL \(= \Theta\).

Gamma Scalping PnL \(= \tfrac12 \Gamma (\Delta S)^2 \times \text{multiplier}\). Long \(\Gamma\) helps. Short \(\Gamma\) hurts.

Vega actions: limit cap, cut, hedge, book balance, regime line (Calm 120% / Elevated 100% / Stress 70%), time bleed inside 21 days.
