#!/usr/bin/env python3
"""
Meridian V3 – Meta-label training-set builder
=============================================

Reconstructs honest buy→sell round-trips from `fills`, joins the nearest
prior buy signal, attaches a causal 14-period Wilder ATR from `price_bars`
(no same-day look-ahead), and writes the training table.

Path resolution (first existing wins):
  DB  : --db | $MERIDIAN_V3_DB | ./meridian_v3.db | script-dir/meridian_v3.db
        | /home/workdir/attachments/meridian_v3.db
  OUT : --out | $MERIDIAN_V3_ARTIFACTS | /home/workdir/artifacts
        | <script-dir>/artifacts
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ATR_PERIOD = 14
STOP_MULT = 1.5
SIGNAL_WINDOW_PRIMARY = pd.Timedelta(seconds=2)
SIGNAL_WINDOW_FALLBACK = pd.Timedelta(seconds=5)
SHORT_HOLD_SEC = 120.0
FUTURES_SYMBOLS = ("NIFTY.F", "INFY.F", "BANKNIFTY.F")
# 15:10 IST = 20 minutes before the 15:30 NSE close, stored as naive UTC.
FLATTEN_UTC_HOUR = 9
FLATTEN_UTC_MINUTE = 40

UNDERLYING_MAP = {
    "INFY.F": "INFY",
    "NIFTY.F": "NIFTY",
    "BANKNIFTY.F": "BANKNIFTY",
    "NIFTY.C": "NIFTY",
    "BANKNIFTY.C": "BANKNIFTY",
}

CORE_FEATURE_COLS = [
    "confidence",
    "confluence",
    "p_success",
    "atr",
    "atr_pct",
    "approx_stop_pct",
    "minutes_since_midnight",
    "minutes_to_eod_flatten",
    "belief_posterior",
    "qty",
    "risk_rupees",
    "is_futures",
]
CORE_LABEL_COLS = ["y_binary", "honest_pnl", "y_R", "hold_sec"]
CORE_META_COLS = [
    "symbol",
    "signal_id",
    "buy_time",
    "sell_time",
    "buy_price",
    "sell_price",
    "fees",
    "atr_source",
    "is_short_hold",
    "direction",
]
EXTRA_COLS = [
    "gross_pnl",
    "signal_market",
    "signal_to_buy_sec",
    "reentry_sec",
    "same_symbol_seq",
    "is_clean",
]


def resolve_db_path(explicit: str | None = None) -> Path:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    env = os.environ.get("MERIDIAN_V3_DB")
    if env:
        candidates.append(Path(env))
    here = Path(__file__).resolve().parent
    candidates.extend(
        [
            Path.cwd() / "meridian_v3.db",
            here / "meridian_v3.db",
            Path("/home/workdir/attachments/meridian_v3.db"),
            Path("/home/workdir/artifacts/meridian_v3.db"),
        ]
    )
    for path in candidates:
        if path.is_file():
            return path.resolve()
    raise FileNotFoundError(
        "Could not find meridian_v3.db. Pass --db PATH or set MERIDIAN_V3_DB. "
        f"Tried: {', '.join(str(p) for p in candidates)}"
    )


def resolve_out_dir(explicit: str | None = None) -> Path:
    if explicit:
        out = Path(explicit)
        out.mkdir(parents=True, exist_ok=True)
        return out.resolve()
    env = os.environ.get("MERIDIAN_V3_ARTIFACTS")
    if env:
        out = Path(env)
        out.mkdir(parents=True, exist_ok=True)
        return out.resolve()
    sandbox = Path("/home/workdir/artifacts")
    if sandbox.is_dir() or sandbox.parent.is_dir():
        sandbox.mkdir(parents=True, exist_ok=True)
        return sandbox.resolve()
    out = Path(__file__).resolve().parent / "artifacts"
    out.mkdir(parents=True, exist_ok=True)
    return out.resolve()


def load_tables(conn: sqlite3.Connection):
    fills = pd.read_sql(
        "SELECT * FROM fills ORDER BY filled_at", conn, parse_dates=["filled_at"]
    )
    signals = pd.read_sql("SELECT * FROM signals", conn, parse_dates=["created_at"])
    price_cache = pd.read_sql("SELECT * FROM price_cache", conn, parse_dates=["as_of"])
    beliefs = pd.read_sql("SELECT * FROM beliefs", conn)
    price_bars = pd.read_sql(
        "SELECT symbol, bar_date, open, high, low, close, volume FROM price_bars",
        conn,
        parse_dates=["bar_date"],
    )
    return fills, signals, price_cache, beliefs, price_bars


def compute_historical_atr(
    price_bars: pd.DataFrame, period: int = ATR_PERIOD
) -> pd.DataFrame:
    """Wilder ATR known at the close of each bar_date. First ATR is the SMA of
    the first `period` true ranges; later values use Wilder smoothing."""
    if price_bars.empty:
        return pd.DataFrame(columns=["symbol", "bar_date", "atr"])

    frames = []
    for sym, group in price_bars.groupby("symbol", sort=False):
        g = group.sort_values("bar_date").copy()
        prev_close = g["close"].shift(1)
        tr = pd.concat(
            [
                g["high"] - g["low"],
                (g["high"] - prev_close).abs(),
                (g["low"] - prev_close).abs(),
            ],
            axis=1,
        ).max(axis=1)

        atr = pd.Series(np.nan, index=g.index, dtype=float)
        if len(tr) >= period:
            atr.iloc[period - 1] = float(tr.iloc[:period].mean())
            for i in range(period, len(tr)):
                atr.iloc[i] = (atr.iloc[i - 1] * (period - 1) + tr.iloc[i]) / period

        frames.append(
            pd.DataFrame(
                {
                    "symbol": sym,
                    "bar_date": g["bar_date"].values,
                    "atr": atr.values,
                }
            )
        )
    return pd.concat(frames, ignore_index=True)


def attach_causal_atr(
    df: pd.DataFrame, atr_history: pd.DataFrame, price_cache: pd.DataFrame
) -> pd.DataFrame:
    """Most recent ATR strictly before the decision day; else price_cache."""
    out = df.copy()
    out["atr_symbol"] = out["symbol"].map(lambda s: UNDERLYING_MAP.get(s, s))
    out["decision_date"] = pd.to_datetime(out["buy_time"]).dt.normalize()

    hist = atr_history.dropna(subset=["atr"]).copy()
    hist["bar_date"] = pd.to_datetime(hist["bar_date"])
    hist = hist.sort_values(["symbol", "bar_date"])

    left = out.reset_index(drop=False).sort_values("decision_date")
    right = (
        hist.rename(columns={"symbol": "atr_symbol", "bar_date": "decision_date"})
        .sort_values("decision_date")
    )
    merged = pd.merge_asof(
        left,
        right,
        by="atr_symbol",
        on="decision_date",
        direction="backward",
        allow_exact_matches=False,
    )
    merged = merged.sort_values("index").set_index("index")

    cache_atr = price_cache.set_index("symbol")["atr"].to_dict()
    atrs: list[float] = []
    sources: list[str] = []
    for _, row in merged.iterrows():
        val = row.get("atr")
        if val is not None and not (isinstance(val, float) and np.isnan(val)) and pd.notna(val):
            atrs.append(float(val))
            sources.append("price_bars")
            continue
        fallback = cache_atr.get(row["symbol"])
        if fallback is None or (isinstance(fallback, float) and np.isnan(fallback)):
            fallback = cache_atr.get(row["atr_symbol"])
        if fallback is not None and not (isinstance(fallback, float) and np.isnan(fallback)):
            atrs.append(float(fallback))
            sources.append("price_cache")
        else:
            atrs.append(np.nan)
            sources.append("none")

    out["atr"] = atrs
    out["atr_source"] = sources
    out["atr_pct"] = out["atr"] / out["buy_price"].replace(0, np.nan)
    return out.drop(columns=["atr_symbol", "decision_date"])


def reconstruct_honest_roundtrips(fills: pd.DataFrame) -> pd.DataFrame:
    """Pair opening and closing fills per symbol with a position-state stack.

    1.4 (F7 fix): the old version paired fills by naive adjacency — advance
    two at a time on ``buy`` then ``sell``, one at a time otherwise. That
    silently dropped every short (``sell`` to open, ``buy`` to cover) and,
    worse, could mispair an unrelated cover-buy with the *next* open-sell in
    an interleaved sequence like ``[sell, buy, sell, buy]`` (two back-to-back
    shorts), fabricating a fictitious round trip out of two fills that never
    belonged together.

    This version walks each symbol's fills in time order and keeps a stack
    of still-open lots. A fill on the *same* side as the top of the stack
    opens a new lot (or the stack starts fresh if it was empty). A fill on
    the *opposite* side closes lots off the top of the stack (LIFO),
    matching quantity lot-by-lot, so a cover-buy is only ever paired with
    the open-sell(s) it is actually closing — never a later, unrelated one.
    If a closing fill's quantity exceeds everything on the stack, the
    leftover opens a new lot on the new side (the position flipped).

    Each matched (open-lot, closing-fill) pair is one round trip, tagged
    ``direction`` = "long" (opened with a buy) or "short" (opened with a
    sell). ``gross_pnl = (sell_price - buy_price) * qty`` is direction-
    agnostic — it is simply "the buy fill's price vs. the sell fill's
    price," which is the right sign for a long (buy low, sell high) and a
    short (sell high, buy low to cover) alike. Fees are split pro-rata by
    matched quantity when a fill closes across more than one lot, or an
    opening fill only partly gets consumed before the stack empties.
    """
    rows: list[dict] = []
    for symbol, grp in fills.groupby("symbol", sort=False):
        grp = grp.sort_values("filled_at").reset_index(drop=True)
        stack: list[dict] = []  # open lots, all on the same side while non-empty
        for _, fill in grp.iterrows():
            side = str(fill["side"])
            qty = float(fill["qty"])
            if qty <= 1e-9:
                continue
            price = float(fill["price"])
            ts = fill["filled_at"]
            fees_total = float(fill["fees"])
            fid = int(fill["id"])
            remaining = qty

            while remaining > 1e-9 and stack and stack[-1]["side"] != side:
                lot = stack[-1]
                matched = min(lot["qty"], remaining)
                is_long = lot["side"] == "buy"
                if is_long:
                    buy_price, buy_time, buy_id = lot["price"], lot["time"], lot["id"]
                    sell_price, sell_time, sell_id = price, ts, fid
                else:
                    buy_price, buy_time, buy_id = price, ts, fid
                    sell_price, sell_time, sell_id = lot["price"], lot["time"], lot["id"]
                lot_fee_share = lot["fees"] * (matched / lot["qty"]) if lot["qty"] else 0.0
                close_fee_share = fees_total * (matched / qty) if qty else 0.0
                gross = (sell_price - buy_price) * matched
                total_fees = lot_fee_share + close_fee_share
                rows.append(
                    {
                        "symbol": symbol,
                        "direction": "long" if is_long else "short",
                        "buy_fill_id": buy_id,
                        "sell_fill_id": sell_id,
                        "buy_time": buy_time,
                        "sell_time": sell_time,
                        "buy_price": buy_price,
                        "sell_price": sell_price,
                        "qty": matched,
                        "gross_pnl": float(gross),
                        "fees": float(total_fees),
                        "honest_pnl": float(gross - total_fees),
                        "entry_time": lot["time"],
                        "exit_time": ts,
                        "hold_sec": (ts - lot["time"]).total_seconds(),
                    }
                )
                lot["qty"] -= matched
                lot["fees"] -= lot_fee_share
                remaining -= matched
                if lot["qty"] <= 1e-9:
                    stack.pop()

            if remaining > 1e-9:
                # Either the stack was empty (a fresh open), or this fill's
                # quantity flipped the position after closing everything else.
                stack.append(
                    {
                        "side": side,
                        "qty": remaining,
                        "price": price,
                        "time": ts,
                        "fees": fees_total * (remaining / qty) if qty else 0.0,
                        "id": fid,
                    }
                )
    return pd.DataFrame(rows)


def attach_nearest_signal(roundtrips: pd.DataFrame, signals: pd.DataFrame) -> pd.DataFrame:
    buy_signals = signals[signals["side"] == "buy"].sort_values("created_at")
    attached = []
    unmatched = 0
    for _, rt in roundtrips.iterrows():
        same = buy_signals[buy_signals["symbol"] == rt["symbol"]]
        cand = same[
            (same["created_at"] <= rt["buy_time"])
            & (same["created_at"] >= rt["buy_time"] - SIGNAL_WINDOW_PRIMARY)
        ]
        if cand.empty:
            cand = same[
                (same["created_at"] <= rt["buy_time"])
                & (same["created_at"] >= rt["buy_time"] - SIGNAL_WINDOW_FALLBACK)
            ]
        if cand.empty:
            unmatched += 1
            continue
        delta = (rt["buy_time"] - cand["created_at"]).dt.total_seconds().abs()
        best = cand.loc[delta.idxmin()]
        attached.append(
            {
                **rt.to_dict(),
                "signal_id": int(best["id"]),
                "confidence": float(best["confidence"]),
                "confluence": float(best["confluence"]),
                "p_success": float(best["p_success"]),
                "signal_market": best["market"],
                "signal_time": best["created_at"],
                "signal_to_buy_sec": float(delta.loc[best.name]),
            }
        )
    out = pd.DataFrame(attached)
    out.attrs["unmatched_roundtrips"] = unmatched
    return out


def add_remaining_features(df: pd.DataFrame, beliefs: pd.DataFrame) -> pd.DataFrame:
    alpha = float(beliefs["alpha"].iloc[0])
    beta = float(beliefs["beta"].iloc[0])
    df = df.copy()
    df["belief_alpha"] = alpha
    df["belief_beta"] = beta
    df["belief_posterior"] = alpha / (alpha + beta)

    df["buy_hour"] = df["buy_time"].dt.hour
    df["buy_minute"] = df["buy_time"].dt.minute
    df["minutes_since_midnight"] = df["buy_hour"] * 60 + df["buy_minute"]

    def mins_to_flatten(ts: pd.Timestamp) -> float:
        flatten = ts.normalize() + pd.Timedelta(
            hours=FLATTEN_UTC_HOUR, minutes=FLATTEN_UTC_MINUTE
        )
        return max(0.0, (flatten - ts).total_seconds() / 60.0)

    df["minutes_to_eod_flatten"] = df["buy_time"].apply(mins_to_flatten)

    df["approx_stop_dist"] = STOP_MULT * df["atr"]
    df["approx_stop_pct"] = df["approx_stop_dist"] / df["buy_price"].replace(0, np.nan)
    df["risk_rupees"] = df["approx_stop_dist"] * df["qty"]
    df["y_R"] = df["honest_pnl"] / df["risk_rupees"].replace(0, np.nan)
    df["y_binary"] = (df["honest_pnl"] > 0).astype(int)
    df["is_futures"] = df["symbol"].isin(FUTURES_SYMBOLS).astype(int)
    df["is_short_hold"] = (df["hold_sec"] < SHORT_HOLD_SEC).astype(int)
    df["is_clean"] = (df["is_futures"] == 0).astype(int)

    df = df.sort_values(["symbol", "buy_time"])
    prev_sell = df.groupby("symbol", sort=False)["sell_time"].shift(1)
    df["reentry_sec"] = (df["buy_time"] - prev_sell).dt.total_seconds()
    df["same_symbol_seq"] = df.groupby("symbol", sort=False).cumcount() + 1
    return df


def build_training_set(db_path: Path) -> pd.DataFrame:
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    fills, signals, price_cache, beliefs, price_bars = load_tables(conn)
    conn.close()

    print(f"Source DB          : {db_path}")
    print(f"Fills / signals    : {len(fills)} / {len(signals)}")
    print(f"price_bars symbols : {price_bars['symbol'].nunique()}  "
          f"({len(price_bars)} bars)")

    print("Computing historical Wilder ATR from price_bars …")
    atr_history = compute_historical_atr(price_bars, period=ATR_PERIOD)
    usable = atr_history.dropna(subset=["atr"])
    print(f"  → ATR series for {usable['symbol'].nunique()} symbols "
          f"({len(usable)} dated values)")

    print("Reconstructing honest round-trips from fills (position-state stack) …")
    rt_all = reconstruct_honest_roundtrips(fills)
    n_short = int((rt_all["direction"] == "short").sum()) if not rt_all.empty else 0
    rt = rt_all[rt_all["direction"] == "long"].reset_index(drop=True) if not rt_all.empty else rt_all
    print(f"  → {len(rt_all)} round-trip(s) total: {len(rt)} long, {n_short} short")
    if n_short:
        print(
            f"  → {n_short} short round-trip(s) paired correctly (a cover-buy is never "
            "matched to an unrelated open-sell — F7) but excluded from this training "
            "pass: attach_nearest_signal below only joins against 'buy' signals, so a "
            "short's entry (a 'sell' signal) has nothing honest to join against yet. "
            "Not silently mispaired — explicitly excluded, with this reason printed."
        )

    print("Attaching nearest prior buy signal (2s, fallback 5s) …")
    df = attach_nearest_signal(rt, signals)
    print(f"  → {len(df)} matched; "
          f"{df.attrs.get('unmatched_roundtrips', 0)} unmatched dropped")

    print("Joining causal historical ATR (bar_date < decision day) …")
    df = attach_causal_atr(df, atr_history, price_cache)
    print("  ATR source counts:")
    print(df["atr_source"].value_counts().to_string())

    print("Adding remaining features and quality flags …")
    df = add_remaining_features(df, beliefs)

    cols = CORE_FEATURE_COLS + CORE_LABEL_COLS + CORE_META_COLS + EXTRA_COLS
    df = df[cols].sort_values("buy_time").reset_index(drop=True)
    return df


def write_outputs(df: pd.DataFrame, db_path: Path, out_dir: Path) -> dict[str, Path]:
    written: dict[str, Path] = {}
    out_db = out_dir / "meridian_v3_with_meta_labels.db"
    if out_db.exists():
        out_db.unlink()

    src = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    dst = sqlite3.connect(out_db)
    src.backup(dst)
    src.close()
    df.to_sql("meta_label_training", dst, if_exists="replace", index=False)
    dst.execute(
        "CREATE INDEX IF NOT EXISTS idx_mlt_time ON meta_label_training(buy_time)"
    )
    dst.execute(
        "CREATE INDEX IF NOT EXISTS idx_mlt_symbol ON meta_label_training(symbol)"
    )
    dst.execute(
        "CREATE INDEX IF NOT EXISTS idx_mlt_clean ON meta_label_training(is_clean)"
    )
    dst.commit()
    dst.close()
    written["db"] = out_db
    print(f"Wrote {len(df)} rows → meta_label_training inside {out_db}")

    csv_path = out_dir / "meridian_v3_meta_labels.csv"
    df.to_csv(csv_path, index=False)
    written["csv"] = csv_path
    print(f"Saved csv → {csv_path}")

    try:
        parquet_path = out_dir / "meridian_v3_meta_labels.parquet"
        df.to_parquet(parquet_path, index=False)
        written["parquet"] = parquet_path
        print(f"Saved parquet → {parquet_path}")
    except Exception as exc:
        print(f"Parquet skipped ({exc.__class__.__name__}: {exc})")

    script_dst = out_dir / "build_meta_labels.py"
    src_script = Path(__file__).resolve()
    if src_script != script_dst.resolve():
        script_dst.write_text(src_script.read_text(encoding="utf-8"), encoding="utf-8")
        written["script"] = script_dst
        print(f"Saved script → {script_dst}")
    else:
        written["script"] = src_script

    stamp_path = _write_stamp(df, db_path, out_dir)
    written["stamp"] = stamp_path
    print(f"Stamped provenance → {stamp_path}")
    return written


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_stamp(df: pd.DataFrame, db_path: Path, out_dir: Path) -> Path:
    """Write a provenance stamp so staleness is detectable automatically.

    Records the source DB hash + row count and the headline metrics, so a
    reader never has to re-derive by hand whether a CSV was built from the
    current book (F8). ``STALE.md`` is retired on the first stamped export.
    """
    clean = df[df["is_clean"] == 1] if "is_clean" in df.columns else df
    stamp = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_db": db_path.as_posix(),
        "source_db_sha256": _sha256(db_path),
        "source_db_bytes": db_path.stat().st_size,
        "rows_all": int(len(df)),
        "rows_clean": int(len(clean)),
        "win_rate_all": float(df["y_binary"].mean()) if len(df) else None,
        "win_rate_clean": float(clean["y_binary"].mean()) if len(clean) else None,
        "avg_honest_pnl_all": float(df["honest_pnl"].mean()) if len(df) else None,
        "avg_honest_pnl_clean": float(clean["honest_pnl"].mean()) if len(clean) else None,
    }
    stamp_path = out_dir / "meridian_v3_meta_labels.STAMP.json"
    stamp_path.write_text(json.dumps(stamp, indent=2), encoding="utf-8")

    stale = out_dir / "STALE.md"
    if stale.exists():
        stale.unlink()
    return stamp_path


def print_summary(df: pd.DataFrame) -> None:
    clean = df[df["is_clean"] == 1]
    fut = df[df["is_futures"] == 1]
    print("\n===== Training set summary =====")
    print(f"Rows (all)              : {len(df)}")
    print(f"Rows (clean / non-fut)  : {len(clean)}")
    print(f"Rows (futures)          : {len(fut)}")
    print(f"Win rate all            : {df['y_binary'].mean():.1%}")
    print(f"Win rate clean          : {clean['y_binary'].mean():.1%}  "
          f"({int(clean['y_binary'].sum())} / {len(clean)})")
    print(f"Avg honest PnL all      : ₹{df['honest_pnl'].mean():.2f}")
    print(f"Avg honest PnL clean    : ₹{clean['honest_pnl'].mean():.2f}")
    print(f"Sum honest PnL clean    : ₹{clean['honest_pnl'].sum():.2f}")
    print(f"Avg hold (s)            : {df['hold_sec'].mean():.1f}")
    print(f"Median hold (s)         : {df['hold_sec'].median():.1f}")
    print(f"ATR coverage            : {df['atr'].notna().mean():.1%}")
    print("\nATR source breakdown:")
    print(df["atr_source"].value_counts().to_string())
    print("\nBy symbol (top 10):")
    print(
        df.groupby("symbol")
        .agg(
            n=("y_binary", "count"),
            wins=("y_binary", "sum"),
            avg_pnl=("honest_pnl", "mean"),
            avg_atr=("atr", "mean"),
            futures=("is_futures", "max"),
        )
        .sort_values("n", ascending=False)
        .head(10)
        .round(2)
        .to_string()
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build Meridian V3 meta-label training set")
    p.add_argument("--db", default=None, help="Path to meridian_v3.db")
    p.add_argument("--out", default=None, help="Output directory for artefacts")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    db_path = resolve_db_path(args.db)
    out_dir = resolve_out_dir(args.out)
    print(f"Output directory   : {out_dir}")
    df = build_training_set(db_path)
    print_summary(df)
    write_outputs(df, db_path, out_dir)
    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
