"""End-to-end cycle: tape → signals → decide → paper (and maybe live)."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from meridian_v3.capital.sizer import size_position
from meridian_v3.config import get_settings
from meridian_v3.decision.engine import DecisionInput, decide
from meridian_v3.engine.atr import average_true_range
from meridian_v3.engine.bayesian import BetaBelief, update_belief
from meridian_v3.engine.confluence import FactorVote
from meridian_v3.engine.drawdown import assess_drawdown
from meridian_v3.engine.edge import estimate_equity_costs
from meridian_v3.engine.meta_label import primary_direction, rsi
from meridian_v3.execution.brokers.paper_broker import PaperBroker
from meridian_v3.execution.oms import OrderManager
from meridian_v3.router.markets import market_for
from meridian_v3.signals.engines import evaluate_signals
from meridian_v3.storage.schema import (
    AccountState,
    BeliefRow,
    DeskEvent,
    EquityPoint,
    FactorScore,
    Position,
    PriceBar,
    PriceCache,
    RegimeState,
    SignalRow,
    WatchItem,
)


def _belief(session: Session, rule: str) -> BetaBelief:
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


def mark_to_market(session: Session, venue: str = "paper") -> float:
    acct = _account(session, venue)
    open_pos = list(
        session.scalars(select(Position).where(Position.venue == venue, Position.status == "open"))
    )
    value = 0.0
    for pos in open_pos:
        cache = session.scalar(select(PriceCache).where(PriceCache.symbol == pos.symbol))
        last = cache.last if cache and cache.last else pos.avg_price
        value += pos.qty * last
    acct.equity = acct.cash + value
    acct.peak = max(acct.peak, acct.equity)
    acct.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    return acct.equity


def run_cycle(session: Session, *, live_armed: bool | None = None) -> dict:
    settings = get_settings()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    paper_acct = _account(session, "paper")
    live_acct = _account(session, "live")
    armed = bool(live_acct.live_armed) if live_armed is None else live_armed
    regime = session.scalar(select(RegimeState).order_by(RegimeState.as_of.desc()))
    desk_mood = regime.desk if regime else "Elevated"
    paper_dd = assess_drawdown(paper_acct.equity, paper_acct.peak)
    live_dd = assess_drawdown(live_acct.equity, live_acct.peak)

    live_today = session.scalar(
        select(func.count(SignalRow.id)).where(SignalRow.live == 1, SignalRow.created_at >= now.replace(hour=0, minute=0))
    ) or 0
    open_paper = session.scalar(
        select(func.count(Position.id)).where(Position.venue == "paper", Position.status == "open")
    ) or 0

    broker = PaperBroker(cash=paper_acct.cash)
    oms = OrderManager(session, broker)
    opened = 0
    decided = 0
    holds = 0
    paper_clips: list[str] = []
    skipped_no_tape = 0
    ranked: list[tuple[float, object, object]] = []

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
        score_row = session.scalar(
            select(FactorScore).where(FactorScore.symbol == item.symbol).order_by(FactorScore.as_of.desc())
        )
        score = score_row.composite if score_row else 6.5
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
            rsi=rsi(closes) if closes else None,
            breakout=any(r.rule_name == "breakout_volume" for r in raws),
            mean_revert=any(r.rule_name == "mean_reversion_bands" and r.side == "buy" for r in raws),
            regime=desk_mood,
        )
        votes = [
            FactorVote("primary", max(-1, min(1, primary.raw_score / 2)), 1.2, primary.reason),
            FactorVote("score", ((score or 5) - 5) / 5, 0.8, f"multi-factor {score}"),
            FactorVote("trend", 0.4 if cache.sma20 and cache.last > cache.sma20 else -0.3, 1.0, "tape vs average"),
        ]
        win = (atr or cache.last * 0.015) * 2.0
        loss = (atr or cache.last * 0.015) * settings.sizing.atr_stop_mult
        costs = estimate_equity_costs(notional=cache.last)
        held = session.scalar(
            select(Position).where(
                Position.symbol == item.symbol,
                Position.venue == "paper",
                Position.status == "open",
            )
        )
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
                live_armed=armed,
                live_today=int(live_today),
                open_count=int(open_paper),
                equity_score=70 if item.asset_class == "equity" else 40,
                options_score=70 if item.asset_class in {"option", "index"} or item.symbol.endswith(".C") else 20,
                forex_score=50 if item.asset_class == "fx" else 15,
                crypto_score=75 if "crypto" in item.asset_class or item.symbol.endswith("USDT") or ".USDT" in item.symbol else 15,
                futures_score=72 if item.asset_class in {"future", "crypto_futures"} or item.symbol.endswith(".F") else 15,
                preferred_market=market_for(item.asset_class, item.symbol),
                now=now,
                belief=_belief(session, "core"),
                held_qty=held.qty if held else 0.0,
            ),
            settings,
        )
        decided += 1
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
    return {
        "decided": decided,
        "paper_opened": opened,
        "holds": holds,
        "skipped_no_tape": skipped_no_tape,
        "live_armed": armed,
        "paper_clips": paper_clips,
    }
