from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class AccountState(Base):
    __tablename__ = "account_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    venue: Mapped[str] = mapped_column(String(16), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(64), default="Meridian Dedicated")
    cash: Mapped[float] = mapped_column(Float, default=50_000.0)
    equity: Mapped[float] = mapped_column(Float, default=50_000.0)
    peak: Mapped[float] = mapped_column(Float, default=50_000.0)
    live_armed: Mapped[int] = mapped_column(Integer, default=0)
    paper_auto: Mapped[int] = mapped_column(Integer, default=1)
    last_cycle_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_cycle_note: Mapped[str] = mapped_column(Text, default="")
    broker: Mapped[str] = mapped_column(String(24), default="zerodha")
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    # Part 3 item 6 — edge-detection flag so the drawdown-pause alert fires
    # once per pause episode instead of every cycle while still paused.
    # Set True the first cycle `assess_drawdown().live_paused` is True for
    # this venue; reset False once it recovers, so a later pause alerts again.
    live_pause_alerted: Mapped[int] = mapped_column(Integer, default=0)


class EquityPoint(Base):
    __tablename__ = "equity_curve"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    venue: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    as_of: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    equity: Mapped[float] = mapped_column(Float, nullable=False)
    cash: Mapped[float] = mapped_column(Float, nullable=False)
    peak: Mapped[float] = mapped_column(Float, nullable=False)


class WatchItem(Base):
    __tablename__ = "watch_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    asset_class: Mapped[str] = mapped_column(String(16), nullable=False, default="equity")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class PriceBar(Base):
    __tablename__ = "price_bars"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    bar_date: Mapped[date] = mapped_column(Date, nullable=False)
    open: Mapped[float] = mapped_column(Float, nullable=False)
    high: Mapped[float] = mapped_column(Float, nullable=False)
    low: Mapped[float] = mapped_column(Float, nullable=False)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[float] = mapped_column(Float, default=0.0)


class HistoricalBar(Base):
    """Multi-year daily bars for backtesting/research — deliberately a
    separate table from ``PriceBar``.

    ``PriceBar`` is the live tape: every refresh cycle wipes and reinserts
    a rolling ~6mo window per symbol (data_providers/service.py:_apply_frame).
    A years-deep backfill living in that same table would get silently
    deleted the next time the live desk refreshes prices. This table is
    never touched by the live refresh path — only by an explicit backfill
    (``meridian-v3 backfill-history``), so a backtest always has the full
    history it was given regardless of what the live desk is doing.
    """

    __tablename__ = "historical_bars"
    __table_args__ = (UniqueConstraint("symbol", "bar_date", name="uq_historical_bar_symbol_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    bar_date: Mapped[date] = mapped_column(Date, nullable=False)
    open: Mapped[float] = mapped_column(Float, nullable=False)
    high: Mapped[float] = mapped_column(Float, nullable=False)
    low: Mapped[float] = mapped_column(Float, nullable=False)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[float] = mapped_column(Float, default=0.0)
    source: Mapped[str] = mapped_column(String(24), default="jugaad_nse")


class PriceCache(Base):
    __tablename__ = "price_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    last: Mapped[float | None] = mapped_column(Float, nullable=True)
    prev_close: Mapped[float | None] = mapped_column(Float, nullable=True)
    sma20: Mapped[float | None] = mapped_column(Float, nullable=True)
    sma50: Mapped[float | None] = mapped_column(Float, nullable=True)
    high20: Mapped[float | None] = mapped_column(Float, nullable=True)
    low20: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    prev_volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    atr: Mapped[float | None] = mapped_column(Float, nullable=True)
    as_of: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    quality: Mapped[str] = mapped_column(String(24), default="ok")


class OptionLegRow(Base):
    __tablename__ = "option_legs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    leg_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    contract_label: Mapped[str] = mapped_column(String(64), default="")
    lots: Mapped[float] = mapped_column(Float, nullable=False)
    multiplier: Mapped[float] = mapped_column(Float, default=1.0)
    mark_inr: Mapped[float] = mapped_column(Float, default=0.0)
    delta: Mapped[float] = mapped_column(Float, default=0.5)
    gamma: Mapped[float] = mapped_column(Float, default=0.0)
    vega_per_lot: Mapped[float] = mapped_column(Float, default=0.0)
    theta_per_lot: Mapped[float] = mapped_column(Float, default=0.0)
    iv: Mapped[float | None] = mapped_column(Float, nullable=True)
    greeks_as_of: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    stale: Mapped[int] = mapped_column(Integer, default=0)


class Holding(Base):
    __tablename__ = "holdings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    exchange: Mapped[str] = mapped_column(String(8), default="NSE")
    company_name: Mapped[str] = mapped_column(String(128), default="")
    isin: Mapped[str | None] = mapped_column(String(16), nullable=True)
    instrument: Mapped[str] = mapped_column(String(24), default="equity")
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    avg_cost: Mapped[float] = mapped_column(Float, nullable=False)
    last_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(24), default="import")
    account_name: Mapped[str] = mapped_column(String(64), default="Imported")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class ImportJob(Base):
    __tablename__ = "import_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    filename: Mapped[str] = mapped_column(String(256), nullable=False)
    broker: Mapped[str] = mapped_column(String(32), default="generic")
    source_kind: Mapped[str] = mapped_column(String(16), default="tabular")
    status: Mapped[str] = mapped_column(String(16), default="preview")
    accepted: Mapped[int] = mapped_column(Integer, default=0)
    rejected: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class DeskEvent(Base):
    __tablename__ = "desk_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    policy_kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    rule_name: Mapped[str] = mapped_column(String(64), default="")
    strength: Mapped[float] = mapped_column(Float, default=0.0)
    reason: Mapped[str] = mapped_column(Text, default="")
    copy_review: Mapped[str] = mapped_column(Text, default="")
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    acked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class SignalRow(Base):
    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    side: Mapped[str] = mapped_column(String(8), default="hold")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    confluence: Mapped[float] = mapped_column(Float, default=0.0)
    p_success: Mapped[float] = mapped_column(Float, default=0.5)
    market: Mapped[str] = mapped_column(String(24), default="equity_cash")
    paper: Mapped[int] = mapped_column(Integer, default=0)
    live: Mapped[int] = mapped_column(Integer, default=0)
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    broker_id: Mapped[str] = mapped_column(String(64), default="")
    venue: Mapped[str] = mapped_column(String(16), nullable=False)
    market: Mapped[str] = mapped_column(String(24), default="equity_cash")
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    qty: Mapped[float] = mapped_column(Float, nullable=False)
    limit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="new")
    message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class Fill(Base):
    __tablename__ = "fills"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"), nullable=True)
    venue: Mapped[str] = mapped_column(String(16), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    qty: Mapped[float] = mapped_column(Float, nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    fees: Mapped[float] = mapped_column(Float, default=0.0)
    charges_json: Mapped[str] = mapped_column(Text, default="{}")
    filled_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    note: Mapped[str] = mapped_column(Text, default="")
    # 2.4 — the fill journal is append-only. `note` is the original ticket
    # text written the moment the fill happened and must never be rewritten
    # after the fact, even to fix it. A later repair that corrects price or
    # P&L records what changed and why here instead (F15).
    correction_note: Mapped[str] = mapped_column(Text, default="")


class Position(Base):
    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    venue: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    market: Mapped[str] = mapped_column(String(24), default="equity_cash")
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    qty: Mapped[float] = mapped_column(Float, nullable=False)
    avg_price: Mapped[float] = mapped_column(Float, nullable=False)
    stop: Mapped[float] = mapped_column(Float, default=0.0)
    horizon: Mapped[str] = mapped_column(String(16), default="intraday")
    status: Mapped[str] = mapped_column(String(16), default="open")
    source: Mapped[str] = mapped_column(String(16), default="auto")
    opened_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    realized_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    close_qty: Mapped[float | None] = mapped_column(Float, nullable=True)
    # The meta-label feature vector at the moment this clip was opened, so
    # the online logistic can be trained on the same features it was scored
    # with, once we know whether the clip won. "{}" when unknown (e.g. a
    # manually-seeded row, or a position opened before this column existed).
    feature_json: Mapped[str] = mapped_column(Text, default="{}")
    # 2.5 — the decision confidence this clip was opened on, so the live
    # re-entry cooldown can tell "a materially more confident signal" apart
    # from "the same near-miss ranking near the top again." 0.0 when unknown
    # (a manually-seeded row, or a position opened before this column
    # existed) — treated as "no bar to clear" by the cooldown check.
    opened_confidence: Mapped[float] = mapped_column(Float, default=0.0)


class BeliefRow(Base):
    __tablename__ = "beliefs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rule_name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    alpha: Mapped[float] = mapped_column(Float, default=4.0)
    beta: Mapped[float] = mapped_column(Float, default=4.0)
    wins: Mapped[int] = mapped_column(Integer, default=0)
    losses: Mapped[int] = mapped_column(Integer, default=0)


class LogitWeight(Base):
    """Persisted coefficients for the online meta-label logistic (per rule).

    One row per feature, plus one row with ``feature="__bias__"`` for the
    intercept. Kept as separate rows (not a single JSON blob) so a reader
    can `SELECT * FROM logit_weights` and see the model in plain SQL.
    """

    __tablename__ = "logit_weights"
    __table_args__ = (UniqueConstraint("rule_name", "feature", name="uq_logit_weight_rule_feature"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rule_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    feature: Mapped[str] = mapped_column(String(32), nullable=False)
    weight: Mapped[float] = mapped_column(Float, default=0.0)
    updates: Mapped[int] = mapped_column(Integer, default=0)


class FactorScore(Base):
    __tablename__ = "factor_scores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    quality: Mapped[float | None] = mapped_column(Float, nullable=True)
    valuation: Mapped[float | None] = mapped_column(Float, nullable=True)
    technical: Mapped[float | None] = mapped_column(Float, nullable=True)
    ownership: Mapped[float | None] = mapped_column(Float, nullable=True)
    sentiment: Mapped[float | None] = mapped_column(Float, nullable=True)
    composite: Mapped[float | None] = mapped_column(Float, nullable=True)
    action: Mapped[str] = mapped_column(String(16), default="")
    as_of: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class RegimeState(Base):
    __tablename__ = "regime_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    desk: Mapped[str] = mapped_column(String(16), nullable=False)
    tape: Mapped[str] = mapped_column(String(24), default="mean_reverting")
    vol: Mapped[str] = mapped_column(String(16), default="low_vol")
    reason: Mapped[str] = mapped_column(Text, default="")
    as_of: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class RobustnessSnapshot(Base):
    """A walk-forward verdict for one market, seeded from a backtest.

    `pipeline._market_robustness` scores a market by walking its *closed
    paper positions* forward. A fresh book has almost none, so every market
    returned "Not enough walk-forward folds" -> robust=False -> a 0.7x
    confidence penalty on every single decision, indefinitely: the desk
    could not trade its way out, because it needed trades to earn the
    confidence to trade.

    A backtest replaying the real pipeline over years of `HistoricalBar`
    produces exactly the P&L series that check needs, so it can seed the
    cold start. This is deliberately kept as a *fallback*: once a market
    has enough live closed positions to form real folds, the live verdict
    wins. `source` records which produced the row, and the reason string
    says so in words, so a seeded verdict is never mistaken for live
    evidence.
    """

    __tablename__ = "robustness_snapshots"
    __table_args__ = (UniqueConstraint("market", name="uq_robustness_market"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    market: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    is_score: Mapped[float] = mapped_column(Float, default=0.0)
    oos_score: Mapped[float] = mapped_column(Float, default=0.0)
    gap: Mapped[float] = mapped_column(Float, default=1.0)
    robust: Mapped[int] = mapped_column(Integer, default=0)
    folds: Mapped[int] = mapped_column(Integer, default=0)
    trades: Mapped[int] = mapped_column(Integer, default=0)
    reason: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(24), default="backtest")
    computed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class SchemaVersion(Base):
    """One row per one-time data migration that has actually run (2.3).

    Replaces inferring migration state from data shape (e.g. "peak is still
    near ₹5,000 so the ₹50,000 credit must not have run yet") — that kind of
    heuristic both re-fires on data it shouldn't and skips data it should
    touch. A migration name in this table, once written, means "never do
    this again," full stop, independent of what the numbers happen to look
    like on a later run.
    """

    __tablename__ = "schema_version"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    migration: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    migrated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class FxMark(Base):
    __tablename__ = "fx_marks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pair: Mapped[str] = mapped_column(String(16), unique=True, nullable=False)
    last: Mapped[float | None] = mapped_column(Float, nullable=True)
    prev_close: Mapped[float | None] = mapped_column(Float, nullable=True)
    as_of: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
