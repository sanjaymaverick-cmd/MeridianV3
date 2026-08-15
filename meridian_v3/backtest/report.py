"""Plain-text summary of a BacktestResult."""

from __future__ import annotations

from meridian_v3.backtest.engine import BacktestResult


def summarize(result: BacktestResult) -> str:
    lines = [
        f"Backtest: {', '.join(result.symbols)}",
        f"Window: {result.start} to {result.end} ({result.trading_days_simulated} trading days)",
        f"Starting capital: ₹{result.starting_capital:,.2f}",
        f"Final equity: ₹{result.final_equity:,.2f} ({result.total_return_pct:+.1%})",
        f"Peak equity: ₹{result.peak_equity:,.2f}",
        f"Max drawdown: {result.max_drawdown_pct:.1%}",
        f"Closed trades: {result.closed_trades} (wins {result.wins}, losses {result.losses}, "
        f"win rate {result.win_rate:.1%})",
        f"Isolated DB (for further inspection): {result.db_path}",
    ]
    return "\n".join(lines)
