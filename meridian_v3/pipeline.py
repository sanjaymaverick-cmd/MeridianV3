"""End-to-end cycle: tape → signals → decide → paper (and maybe live)."""

from __future__ import annotations

from datetime import date, datetime, timezone

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from meridian_v3.alerts.notify import emit_alert
from meridian_v3.capital.sizer import size_position
from meridian_v3.config import get_settings
from meridian_v3.decision.engine import DecisionInput, decide
from meridian_v3.engine.atr import average_true_range
from meridian_v3.engine.bayesian import BetaBelief, update_belief
from meridian_v3.engine.confluence import FactorVote
from meridian_v3.engine.drawdown import assess_drawdown
from meridian_v3.engine.edge import estimate_equity_costs
from meridian_v3.engine.meta_label import OnlineLogit, primary_direction, rsi
from meridian_v3.engine.walkforward import Robustness, hit_rate, robustness, walk_forward_folds
from meridian_v3.execution.brokers.paper_broker import PaperBroker
from meridian_v3.execution.oms import OrderManager
from meridian_v3.router.calendar import IST, india_session, is_india_market
from meridian_v3.router.markets import market_for
from meridian_v3.scoring.composite import blend_weights, composite_score, factor_parts_from_tape, map_action
from meridian_v3.signals.engines import evaluate_signals
from meridian_v3.storage.schema import (
    AccountState,
    BeliefRow,
    DeskEvent,
    EquityPoint,
    FactorScore,
    LogitWeight,
    Position,
    PriceBar,
    PriceCache,
    RegimeState,
    RobustnessSnapshot,
    SignalRow,
    WatchItem,
)

# Feature row name for the online logistic's intercept — kept distinct from
# any real feature key (see engine/meta_label.py:default_features).
_LOGIT_BIAS = "__bias__"


def _belief(session: Session, rule: str) -> BetaBelief:
    """Look up the Beta belief for ``rule``.

    Part 3 item 4 — ``run_cycle`` now calls this with the routed *market*
    (``equity_cash``, ``crypto_spot``, ...) instead of the single global
    ``"core"`` rule name, so each market keeps its own prior instead of all
    of them sharing one Beta(alpha, beta) blended across every asset class.
    A market with no row yet gets the same Beta(4.0, 4.0) cold-start prior
    ``"core"`` always used — there is no backfill from the old shared row.
    """
    row = session.scalar(select(BeliefRow).where(BeliefRow.rule_name == rule))
    if row is None:
        return BetaBelief(4.0, 4.0, 0, 0)
    return BetaBelief(row.alpha, row.beta, row.wins, row.losses)


def _account(session: Session, venue: str) -> AccountState:
    row = session.scalar(select(AccountState).where(AccountState.venue == venue))
    if row is None:
        raise RuntimeError("desk is not seeded")
    return row


def persist_belief(session: Session, won: bool, rule: str = "core") -> BetaBelief:
    row = session.scalar(select(BeliefRow).where(BeliefRow.rule_name == rule))
    if row is None:
        row = BeliefRow(rule_name=rule, alpha=4.0, beta=4.0, wins=0, losses=0)
        session.add(row)
        session.flush()
    belief = update_belief(BetaBelief(row.alpha, row.beta, row.wins, row.losses), won)
    row.alpha = belief.alpha
    row.beta = belief.beta
    row.wins = belief.wins
    row.losses = belief.losses
    return belief


def _market_robustness(session: Session, market: str, settings) -> Robustness:
    """Part 3 item 5 — walk-forward robustness verdict for one market.

    Walks the time-ordered realized P&L of that market's *closed paper*
    positions (``venue="paper", status="closed"``; rows with no honest
    ``realized_pnl`` — e.g. Phase 0's ``status="unreconciled"`` repairs —
    are excluded by construction since they never reach ``status="closed"``
    with a non-null P&L). ``hit_rate`` is the per-fold score
    (``engine/walkforward.py``), folds come from ``walk_forward_folds``'s
    own defaults (train=60, test=20, embargo=2), and the final verdict is
    ``robustness(is_scores, oos_scores, max_gap=settings.decision.walkforward_oos_gap_max)``.

    A market with fewer than ``train + embargo + test`` (82 by default)
    closed positions produces zero folds; ``robustness([], [], ...)``
    already returns a conservative ``robust=False`` ("Not enough
    walk-forward folds.") for that case rather than this function inventing
    a lower-data fallback.
    """
    pnls = list(
        session.scalars(
            select(Position.realized_pnl)
            .where(
                Position.venue == "paper",
                Position.status == "closed",
                Position.market == market,
                Position.realized_pnl.is_not(None),
            )
            .order_by(Position.closed_at.asc())
        )
    )
    folds = walk_forward_folds(len(pnls))
    if folds:
        is_scores = [hit_rate(pnls[f.train_start : f.train_end]) for f in folds]
        oos_scores = [hit_rate(pnls[f.test_start : f.test_end]) for f in folds]
        return robustness(is_scores, oos_scores, max_gap=settings.decision.walkforward_oos_gap_max)

    # No live folds yet. Fall back to a backtest-seeded verdict if one has
    # been persisted for this market. Without this the desk is stuck in a
    # deadlock: robustness needs closed trades, but the 0.7x penalty for
    # having none suppresses the confidence needed to open any. Live always
    # wins the moment it has enough history — this branch only runs when it
    # has none — and the reason says plainly that it came from a backtest.
    seeded = session.scalar(select(RobustnessSnapshot).where(RobustnessSnapshot.market == market))
    if seeded is not None:
        return Robustness(
            is_score=seeded.is_score,
            oos_score=seeded.oos_score,
            gap=seeded.gap,
            robust=bool(seeded.robust),
            folds=seeded.folds,
            reason=seeded.reason,
        )
    return robustness([], [], max_gap=settings.decision.walkforward_oos_gap_max)


def seed_robustness_from_backtest(
    session: Session,
    market: str,
    *,
    symbols: list[str],
    start: date,
    end: date,
    starting_capital: float = 100_000.0,
) -> RobustnessSnapshot | None:
    """Replay the real pipeline over history for ``market`` and persist the
    walk-forward verdict, so a cold book isn't permanently penalised.

    The backtest runs on an isolated database (see ``backtest/engine.py``),
    so this reads nothing from and writes nothing to the live book except
    the resulting snapshot row.
    """
    from meridian_v3.backtest.engine import run_backtest

    result = run_backtest(symbols, start=start, end=end, starting_capital=starting_capital)
    pnls = result.closed_pnls
    folds = walk_forward_folds(len(pnls))
    is_scores = [hit_rate(pnls[f.train_start : f.train_end]) for f in folds]
    oos_scores = [hit_rate(pnls[f.test_start : f.test_end]) for f in folds]
    settings = get_settings()
    verdict = robustness(is_scores, oos_scores, max_gap=settings.decision.walkforward_oos_gap_max)

    reason = (
        f"{verdict.reason} (Seeded from a backtest of {len(symbols)} {market} symbol(s) over "
        f"{result.trading_days_simulated} trading days, {len(pnls)} closed trades — not live "
        "evidence. Live closed trades replace this as soon as there are enough for one fold.)"
    )
    row = session.scalar(select(RobustnessSnapshot).where(RobustnessSnapshot.market == market))
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if row is None:
        row = RobustnessSnapshot(market=market, computed_at=now)
        session.add(row)
    row.is_score = verdict.is_score
    row.oos_score = verdict.oos_score
    row.gap = verdict.gap
    row.robust = 1 if verdict.robust else 0
    row.folds = verdict.folds
    row.trades = len(pnls)
    row.reason = reason
    row.source = "backtest"
    row.computed_at = now
    return row


def load_logit(session: Session, rule: str = "core") -> OnlineLogit:
    """Rebuild the online logistic from its persisted weights (1.1).

    An untrained (``updates == 0``) model falls back to the fixed cold-start
    formula in ``engine/meta_label.py`` — same behaviour as before this was
    wired up, just no longer stuck there forever once real outcomes land.
    """
    rows = list(session.scalars(select(LogitWeight).where(LogitWeight.rule_name == rule)))
    model = OnlineLogit()
    updates = 0
    for row in rows:
        updates = max(updates, row.updates)
        if row.feature == _LOGIT_BIAS:
            model.bias = row.weight
        else:
            model.weights[row.feature] = row.weight
    model.updates = updates
    return model


def persist_logit_update(
    session: Session, features: dict[str, float], won: bool, rule: str = "core"
) -> OnlineLogit:
    """Load the persisted logistic, call ``.update()`` on a real outcome, save it back.

    Called from the same place ``persist_belief`` is — a paper clip closing —
    so the meta-label logistic actually learns from the book instead of
    computing a brand-new, zero-``updates`` instance on every decision (F4).
    """
    model = load_logit(session, rule)
    model.update(features, won)
    bias_row = session.scalar(
        select(LogitWeight).where(LogitWeight.rule_name == rule, LogitWeight.feature == _LOGIT_BIAS)
    )
    if bias_row is None:
        session.add(LogitWeight(rule_name=rule, feature=_LOGIT_BIAS, weight=model.bias, updates=model.updates))
    else:
        bias_row.weight = model.bias
        bias_row.updates = model.updates
    for key, value in model.weights.items():
        row = session.scalar(select(LogitWeight).where(LogitWeight.rule_name == rule, LogitWeight.feature == key))
        if row is None:
            session.add(LogitWeight(rule_name=rule, feature=key, weight=value, updates=model.updates))
        else:
            row.weight = value
            row.updates = model.updates
    session.flush()
    return model


def _score_symbol(
    session: Session,
    symbol: str,
    cache: PriceCache,
    desk_mood: str,
    rsi_value: float | None,
    now: datetime,
) -> float:
    """Compute and persist a real V1 five-factor row for this symbol (1.2).

    Only ``technical`` and ``valuation`` have a live data source (the tape in
    ``price_cache``) — ``composite_score`` skips the ``quality``/``ownership``/
    ``sentiment`` factors we have no fundamentals feed for, rather than us
    faking numbers for them.
    """
    parts = factor_parts_from_tape(
        last=cache.last,
        sma20=cache.sma20,
        sma50=cache.sma50,
        high20=cache.high20,
        low20=cache.low20,
        rsi_value=rsi_value,
    )
    weights = blend_weights(desk_mood)
    composite = composite_score(parts, weights)
    action = map_action(composite, desk_mood)
    session.add(
        FactorScore(
            symbol=symbol,
            quality=parts.get("quality"),
            valuation=parts.get("valuation"),
            technical=parts.get("technical"),
            ownership=parts.get("ownership"),
            sentiment=parts.get("sentiment"),
            composite=float(composite) if composite is not None else None,
            action=action,
            as_of=now,
        )
    )
    return float(composite) if composite is not None else 6.5


def _reentry_blocked(
    session: Session, symbol: str, confidence: float, now: datetime, settings
) -> str | None:
    """2.5 — live enforcement of the re-entry cooldown (F16).

    ``reentry_sec`` used to only exist as a post-hoc training feature in
    ``build_meta_labels.py`` — nothing stopped the live cycle from reopening
    the same symbol on back-to-back cycles once its last clip closed, paying
    brokerage + GST again each time. This blocks that, unless the new
    signal is materially more confident than the one that closed the last
    clip (so a genuine regime change isn't stuck waiting out the clock).

    Returns a hold reason string when re-entry should wait, or ``None`` when
    it's fine to open (no recent close, cooldown has elapsed, or confidence
    cleared the margin).
    """
    cooldown = settings.decision.reentry_cooldown_sec
    if cooldown <= 0:
        return None
    last_closed = session.scalar(
        select(Position)
        .where(Position.venue == "paper", Position.symbol == symbol, Position.status == "closed")
        .order_by(Position.closed_at.desc())
    )
    if last_closed is None or last_closed.closed_at is None:
        return None
    elapsed = (now - last_closed.closed_at).total_seconds()
    if elapsed >= cooldown:
        return None
    prior_confidence = float(last_closed.opened_confidence or 0.0)
    if confidence >= prior_confidence + settings.decision.reentry_confidence_margin:
        return None
    return (
        f"{symbol} closed {elapsed:.0f}s ago — inside the {cooldown:.0f}s re-entry cooldown. "
        f"Confidence {confidence:.0%} is not materially higher than the {prior_confidence:.0%} "
        "that closed. Holding rather than paying fees to reopen it."
    )


def _day_start_equity(session: Session, venue: str, now: datetime) -> float:
    """Equity for ``venue`` at the start of the current IST trading day (Part 3 item 3).

    Used to compute "loss so far today" for the rupee-per-day kill switch.
    IST, not UTC midnight, is the day boundary — this is an India-hours desk
    and the rest of the codebase (``router/calendar.py``) already reasons
    about "today" the same way.

    Three cases, in order:
      1. Today (IST) already has an ``EquityPoint`` for this venue — use the
         earliest one (the mark closest to today's open).
      2. Today has none yet (e.g. first cycle of the day) — fall back to the
         most recent ``EquityPoint`` strictly before today (yesterday's last
         snapshot).
      3. No history at all — fall back to the account's current equity, i.e.
         "loss so far today" is 0 until there is something to compare against.
    """
    aware = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    local = aware.astimezone(IST)
    ist_midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    day_start_utc = ist_midnight.astimezone(timezone.utc).replace(tzinfo=None)

    today_point = session.scalar(
        select(EquityPoint)
        .where(EquityPoint.venue == venue, EquityPoint.as_of >= day_start_utc)
        .order_by(EquityPoint.as_of.asc())
    )
    if today_point is not None:
        return float(today_point.equity)

    prior_point = session.scalar(
        select(EquityPoint)
        .where(EquityPoint.venue == venue, EquityPoint.as_of < day_start_utc)
        .order_by(EquityPoint.as_of.desc())
    )
    if prior_point is not None:
        return float(prior_point.equity)

    return float(_account(session, venue).equity)


def alert_on_drawdown_transition(session: Session, acct: AccountState, dd) -> None:
    """Part 3 item 6 — fire the drawdown-pause alert once per pause episode.

    ``assess_drawdown()`` is a stateless pure function recomputed every
    cycle, so telling "just entered pause" apart from "still paused from
    last cycle" needs its own edge-detection: alert (and latch
    ``live_pause_alerted``) the first cycle a venue enters pause, stay quiet
    on every subsequent still-paused cycle, and reset the latch once the
    venue recovers so a later pause alerts again instead of going silent
    forever after the first episode.
    """
    if dd.live_paused and not acct.live_pause_alerted:
        emit_alert(session, "drawdown_pause", f"{acct.venue} account: {dd.reason}")
        acct.live_pause_alerted = 1
    elif not dd.live_paused and acct.live_pause_alerted:
        acct.live_pause_alerted = 0


def mark_to_market(session: Session, venue: str = "paper") -> float:
    acct = _account(session, venue)
    open_pos = list(
        session.scalars(select(Position).where(Position.venue == venue, Position.status == "open"))
    )
    value = 0.0
    for pos in open_pos:
        cache = session.scalar(select(PriceCache).where(PriceCache.symbol == pos.symbol))
        last = cache.last if cache and cache.last else pos.avg_price
        signed = pos.qty if pos.side == "buy" else -pos.qty
        value += signed * last
    acct.equity = acct.cash + value
    acct.peak = max(acct.peak, acct.equity)
    acct.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    return acct.equity


def run_cycle(
    session: Session,
    *,
    live_armed: bool | None = None,
    now: datetime | None = None,
) -> dict:
    settings = get_settings()
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is not None:
        now = now.astimezone(timezone.utc).replace(tzinfo=None)
    india_open = india_session(now, settings).in_session
    paper_acct = _account(session, "paper")
    live_acct = _account(session, "live")
    armed = bool(live_acct.live_armed) if live_armed is None else live_armed
    regime = session.scalar(select(RegimeState).order_by(RegimeState.as_of.desc()))
    desk_mood = regime.desk if regime else "Elevated"
    paper_dd = assess_drawdown(paper_acct.equity, paper_acct.peak)
    live_dd = assess_drawdown(live_acct.equity, live_acct.peak)
    alert_on_drawdown_transition(session, paper_acct, paper_dd)
    alert_on_drawdown_transition(session, live_acct, live_dd)
    # Part 3 item 3 — computed once per cycle, same as paper_dd/live_dd above,
    # rather than threading a Session into safety/guards.py.
    paper_day_start = _day_start_equity(session, "paper", now)
    live_day_start = _day_start_equity(session, "live", now)
    paper_daily_loss = max(0.0, paper_day_start - paper_acct.equity)
    live_daily_loss = max(0.0, live_day_start - live_acct.equity)

    live_today = session.scalar(
        select(func.count(SignalRow.id)).where(SignalRow.live == 1, SignalRow.created_at >= now.replace(hour=0, minute=0))
    ) or 0
    open_paper = session.scalar(
        select(func.count(Position.id)).where(Position.venue == "paper", Position.status == "open")
    ) or 0

    # 2.6 — the only record of what a cycle did used to be a DeskEvent row
    # per symbol; nothing summarized it anywhere an operator could grep.
    logger.info("run_cycle start: live_armed={} open_paper={}", armed, open_paper)

    broker = PaperBroker(cash=paper_acct.cash)
    oms = OrderManager(session, broker)
    # 1.1 — load the persisted online logistic once per cycle so every
    # decision this cycle scores against the same, real, trained weights
    # (instead of a brand-new zero-`updates` OnlineLogit every call).
    logit_model = load_logit(session, "core")
    opened = 0
    decided = 0
    holds = 0
    paper_clips: list[str] = []
    skipped_no_tape = 0
    ranked: list[tuple[float, object, object]] = []

    # Part 3 item 5 — walk-forward robustness is scoped per market (same
    # granularity as the item-4 belief split, just below) and involves a DB
    # query plus a walk-forward computation, so it is computed at most once
    # per distinct market this cycle (<= 8 possible markets), not once per
    # watchlist symbol, and cached here for the rest of this call.
    robustness_cache: dict[str, Robustness] = {}

    def _cached_robustness(market: str) -> Robustness:
        cached = robustness_cache.get(market)
        if cached is None:
            cached = _market_robustness(session, market, settings)
            robustness_cache[market] = cached
        return cached

    names = list(session.scalars(select(WatchItem).where(WatchItem.status == "active")))
    for item in names:
        cache = session.scalar(select(PriceCache).where(PriceCache.symbol == item.symbol))
        if cache is None or not cache.last or cache.quality == "missing":
            skipped_no_tape += 1
            continue
        bars = list(
            session.scalars(
                select(PriceBar).where(PriceBar.symbol == item.symbol).order_by(PriceBar.bar_date.asc())
            )
        )
        closes = [b.close for b in bars]
        highs = [b.high for b in bars]
        lows = [b.low for b in bars]
        atr = cache.atr or average_true_range(highs, lows, closes, 14)
        rsi_value = rsi(closes) if closes else None
        # 1.2 — write a real V1 five-factor row every cycle instead of reading
        # the dead `factor_scores` table (which nothing ever inserted into).
        score = _score_symbol(session, item.symbol, cache, desk_mood, rsi_value, now)
        raws = evaluate_signals(
            symbol=item.symbol,
            asset_class=item.asset_class,
            score=score,
            last=cache.last,
            sma=cache.sma20,
            sma50=cache.sma50,
            weekly_last=cache.last,
            weekly_sma=cache.sma50,
            high20=cache.high20,
            low20=cache.low20,
            volume=cache.volume,
            prev_volume=cache.prev_volume,
            regime=desk_mood,
            in_book=True,
            usdinr_move_pct=None,
            commodity_stress=False,
        )
        primary = primary_direction(
            last=cache.last,
            sma_fast=cache.sma20,
            sma_slow=cache.sma50,
            rsi=rsi_value,
            breakout=any(r.rule_name == "breakout_volume" for r in raws),
            mean_revert=any(r.rule_name == "mean_reversion_bands" and r.side == "buy" for r in raws),
            regime=desk_mood,
        )
        votes = [
            FactorVote("primary", max(-1, min(1, primary.raw_score / 2)), 1.2, primary.reason),
            FactorVote("score", ((score or 5) - 5) / 5, 0.8, f"multi-factor {score}"),
            FactorVote("trend", 0.4 if cache.sma20 and cache.last > cache.sma20 else -0.3, 1.0, "tape vs average"),
        ]
        # The modelled win must be the same target the exit actually aims at,
        # or the pre-trade gate is pricing a trade the desk never takes. Loss
        # is the stop distance (R); win is `target_r_multiple` x R, matching
        # autopilot._exit_reason. Was a hardcoded 2.0 x ATR against a
        # 1.5 x ATR stop — a 1.33:1 model of a 2:1 exit.
        _atr = atr or cache.last * 0.015
        loss = _atr * settings.sizing.atr_stop_mult
        win = loss * settings.sizing.target_r_multiple
        costs = estimate_equity_costs(notional=cache.last)
        held = session.scalar(
            select(Position).where(
                Position.symbol == item.symbol,
                Position.venue == "paper",
                Position.status == "open",
            )
        )
        # Part 3 items 4/5 — the routed market is resolved once here and
        # reused for the belief key, the robustness cache lookup, and
        # `preferred_market` itself, so all three agree on exactly the same
        # market a symbol was scored against.
        market = market_for(item.asset_class, item.symbol)
        decision = decide(
            DecisionInput(
                symbol=item.symbol,
                price=cache.last,
                atr=atr or cache.last * 0.015,
                created_at=now,
                primary=primary,
                votes=votes,
                win_rupees=max(win, 8.0),
                loss_rupees=max(loss, 8.0),
                costs=costs,
                payoff=max(win / max(loss, 1e-6), 0.4),
                equity=paper_acct.equity,
                cash=paper_acct.cash,
                drawdown=live_dd if armed else paper_dd,
                daily_loss_inr=live_daily_loss if armed else paper_daily_loss,
                live_armed=armed,
                live_today=int(live_today),
                open_count=int(open_paper),
                preferred_market=market,
                now=now,
                belief=_belief(session, market),
                logit=logit_model,
                robustness=_cached_robustness(market),
                held_qty=held.qty if held else 0.0,
            ),
            settings,
        )
        decided += 1
        if decision.paper and is_india_market(decision.market) and not india_open:
            decision.paper = False
            decision.reasons.append(
                "Indian market is shut until Monday 09:15 IST. "
                "No new India paper — cash stays free for crypto, FX, and commodities."
            )
        if decision.paper and held is None:
            # 2.5 — a symbol that just closed shouldn't reopen (and pay fees
            # again) within its cooldown window unless this signal is
            # materially more confident than the one that closed it (F16).
            cooldown_reason = _reentry_blocked(session, item.symbol, decision.confidence, now, settings)
            if cooldown_reason:
                decision.paper = False
                decision.reasons.append(cooldown_reason)
        session.add(
            SignalRow(
                symbol=item.symbol,
                side=decision.action,
                confidence=decision.confidence,
                confluence=decision.confluence,
                p_success=decision.p_success,
                market=decision.market,
                paper=1 if decision.paper else 0,
                live=1 if decision.live else 0,
                reason=" | ".join(decision.reasons),
                created_at=now,
            )
        )
        session.add(
            DeskEvent(
                policy_kind="auto_decision",
                symbol=item.symbol,
                rule_name="auto",
                strength=decision.confidence,
                reason=decision.review.body,
                copy_review=decision.review.body,
                payload_json="{}",
                status="pending",
                created_at=now,
            )
        )
        if decision.paper:
            if held and held.qty > 0 and decision.action == held.side:
                holds += 1
                continue
            rank = decision.confidence * (decision.confluence / 100.0) + decision.p_success * 0.15
            ranked.append((rank, item, decision))
        else:
            holds += 1

    ranked.sort(key=lambda row: row[0], reverse=True)
    for _rank, item, decision in ranked:
        cache = session.scalar(select(PriceCache).where(PriceCache.symbol == item.symbol))
        last = cache.last if cache and cache.last else 0.0
        atr = (cache.atr if cache and cache.atr else last * 0.015)
        fresh = size_position(
            equity=paper_acct.equity,
            cash=broker.funds(),
            price=last,
            atr=atr,
            p_success=decision.p_success,
            payoff=1.3,
            confidence=decision.confidence,
            drawdown=live_dd if armed else paper_dd,
            settings=settings,
            market=decision.market,
            open_count=int(open_paper),
        )
        if fresh.blocked or fresh.qty <= 0:
            holds += 1
            continue
        decision.size = fresh
        oms.execute(decision)
        opened += 1
        open_paper += 1
        paper_acct.cash = broker.funds()
        paper_clips.append(f"{item.symbol} {decision.action} {fresh.qty:g}")
        if decision.live:
            live_today += 1

    session.add(EquityPoint(venue="paper", as_of=now, equity=paper_acct.equity, cash=paper_acct.cash, peak=paper_acct.peak))
    session.add(EquityPoint(venue="live", as_of=now, equity=live_acct.equity, cash=live_acct.cash, peak=live_acct.peak))
    session.flush()
    logger.info(
        "run_cycle end: decided={} paper_opened={} holds={} skipped_no_tape={}",
        decided, opened, holds, skipped_no_tape,
    )
    return {
        "decided": decided,
        "paper_opened": opened,
        "holds": holds,
        "skipped_no_tape": skipped_no_tape,
        "live_armed": armed,
        "paper_clips": paper_clips,
        "open_count": int(open_paper),
        "cash": float(broker.funds()),
    }
