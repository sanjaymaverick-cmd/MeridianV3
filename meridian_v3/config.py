from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_YAML = ROOT / "config" / "default.yaml"


class AppCfg(BaseModel):
    name: str = "MERIDIAN V3"
    subtitle: str = "Personal auto-trading desk · India"
    host: str = "127.0.0.1"
    port: int = 8777
    theme: str = "dark"
    open_browser: bool = True


class PathsCfg(BaseModel):
    data_dir: str = "~/MeridianV3"
    db_filename: str = "meridian_v3.db"
    log_dir: str = "~/MeridianV3/logs"
    import_dir: str = "~/MeridianV3/imports"


class AccountCfg(BaseModel):
    starting_equity_inr: float = 50_000.0
    currency: str = "INR"
    compound_profits: bool = True
    name: str = "Meridian Dedicated"
    broker: str = "zerodha"


class MarketSpec(BaseModel):
    enabled: bool = True
    home: bool = False
    lot_step: float = 1.0
    min_notional: float = 1.0
    buying_only: bool = False
    selling_forbidden: bool = False
    min_premium_inr: float = 50.0
    max_premium_pct_of_equity: float = 0.12
    # Options need their own risk shape. An ATR multiple is the wrong tool
    # here: a weekly ATM option's daily ATR is ~33% of its premium, so the
    # universal 2 x ATR stop works out to 67% and the 3:1 target to a 200%
    # gain. Worse, the sizer used to set the stop to the *entire* premium,
    # so an option never stopped out until it was worthless -- while the
    # decision gate was modelling a 67% loss. The two disagreed.
    #
    # A long option's loss is bounded at 100% of premium (this book never
    # sells premium), so the stop is expressed as a fraction of it. At 0.50
    # the target is 3 x that = a 150% gain, which is a real move for an
    # option and clears the ~11.5% cost hurdle several times over.
    stop_pct_of_premium: float = 0.50
    nano_lots: bool = False
    micro_lots: bool = False
    standard_lots_forbidden: bool = False
    min_lot: float = 1.0
    # 2.1 — the qty at which a "nano/micro only" market (forex_micro) starts
    # being a standard lot. OMS._send rejects any order at or above this as
    # a second line of defense behind the sizer, which never produces one.
    standard_lot_qty: float = 1.0
    margin_pct: float = 0.12
    max_leverage: float = 2.0
    contract_size: float = 1.0
    premium_pct: float = 0.03


class MarketsCfg(BaseModel):
    default: str = "equity_cash"
    priority: list[str] = Field(
        default_factory=lambda: [
            "equity_cash",
            "options_buy",
            "india_futures",
            "global_commodities",
            "crypto_spot",
            "crypto_futures",
            "crypto_options",
            "forex_micro",
        ]
    )
    equity_cash: MarketSpec = Field(default_factory=lambda: MarketSpec(home=True))
    options_buy: MarketSpec = Field(
        default_factory=lambda: MarketSpec(buying_only=True, selling_forbidden=True)
    )
    india_futures: MarketSpec = Field(
        default_factory=lambda: MarketSpec(min_lot=0.05, lot_step=0.05, margin_pct=0.10, contract_size=65)
    )
    global_commodities: MarketSpec = Field(
        default_factory=lambda: MarketSpec(min_lot=0.01, lot_step=0.01, margin_pct=0.10, min_notional=200)
    )
    crypto_spot: MarketSpec = Field(
        default_factory=lambda: MarketSpec(lot_step=0.0001, min_lot=0.0001, min_notional=200)
    )
    crypto_futures: MarketSpec = Field(
        default_factory=lambda: MarketSpec(lot_step=0.0001, min_lot=0.0001, max_leverage=2.0, margin_pct=0.50)
    )
    crypto_options: MarketSpec = Field(
        default_factory=lambda: MarketSpec(
            buying_only=True, selling_forbidden=True, lot_step=0.001, min_lot=0.001, premium_pct=0.03
        )
    )
    forex_micro: MarketSpec = Field(
        default_factory=lambda: MarketSpec(
            nano_lots=True,
            micro_lots=True,
            standard_lots_forbidden=True,
            min_lot=0.01,
            standard_lot_qty=1.0,
        )
    )


class SizingCfg(BaseModel):
    kelly_fraction: float = 0.15
    min_kelly_fraction: float = 0.05
    max_kelly_fraction: float = 0.25
    # 2.0 x ATR. Wider than the old 1.5 so ordinary chop doesn't take the
    # trade out before a real move develops -- the desk is hunting moves
    # several percent wide now, which need room to breathe.
    atr_stop_mult: float = 2.0
    # Target as a multiple of the stop distance (R). 3.0 = a 3:1 reward:risk
    # shape, so with a 2 x ATR stop the target sits 6 x ATR away. Was a
    # hardcoded 2.0 in autopilot._exit_reason. Breakeven win rate at 3:1 is
    # 25%, against 33% at the old 2:1.
    target_r_multiple: float = 3.0
    atr_period: int = 14
    max_risk_pct_high_conf: float = 0.04
    max_risk_pct_normal: float = 0.015
    max_risk_pct_low: float = 0.008
    min_risk_inr: float = 25.0
    high_confidence: float = 0.78
    live_confidence: float = 0.82
    positional_confidence: float = 0.88
    # Concentration over breadth: fewer, more convicted clips rather than a
    # thin slice of everything that clears the bar. Was 16/20.
    max_concurrent_normal: int = 8
    max_concurrent_high: int = 10
    max_position_pct: float = 0.18
    cash_reserve_pct: float = 0.10
    # 2.9 — stop_price() uses this to tell an ATR *distance* apart from an
    # already-a-price-line stop: a distance bigger than this fraction of
    # entry is treated as a level, not rupees of room. Was a bare 0.45.
    stop_distance_ratio_ceiling: float = 0.45


class SafetyCfg(BaseModel):
    drawdown_pause_pct: float = 0.20
    drawdown_scale_start_pct: float = 0.08
    max_daily_live_trades: int = 3
    max_daily_live_high_conf: int = 6
    # Part 3 item 3 — kill switch. An absolute rupee-per-day circuit breaker,
    # on top of the drawdown-percentage pause. Applies to paper too, not just
    # live: paper is exactly where "the desk is bleeding fast today" should
    # be caught early. docs/10-phases.md names this Phase 3 feature at this
    # default ("₹2,000 default suggestion").
    max_daily_loss_inr: float = 2000.0
    overnight_equity_ok: bool = True
    overnight_options_forbidden: bool = True
    overnight_fx_forbidden: bool = True
    overnight_commodities_ok: bool = True
    crypto_always_on: bool = True
    flatten_before_close_minutes: int = 20
    session_open: str = "09:15"
    session_close: str = "15:30"
    timezone: str = "Asia/Kolkata"


class ExecutionCfg(BaseModel):
    # Reject a fill whose price is more than this fraction away from the
    # cached tape. A second gate behind the ``_fill_price`` fix so a 3x/10x
    # unit-mismatch can never silently reach a Fill or Position.
    max_fill_deviation_pct: float = 0.25
    # A same-mark EOD/weekend flatten within this fraction of entry is a
    # cost-only "scratch", not a directional outcome — it must not move the
    # belief prior.
    flat_scratch_pct: float = 0.001


class DecisionCfg(BaseModel):
    paper_all_signals: bool = True
    live_requires_arm: bool = True
    live_min_confidence: float = 0.82
    live_min_confluence: float = 62.0
    edge_safety_margin: float = 0.0015
    freshness_half_life_hours: float = 6.0
    min_freshness: float = 0.35
    walkforward_oos_gap_max: float = 0.35
    # 2.5 — minimum gap since a symbol's last paper close before it can be
    # reopened, unless the new signal clears reentry_confidence_margin above
    # the confidence that closed clip was opened on (F16).
    reentry_cooldown_sec: int = 300
    reentry_confidence_margin: float = 0.05
    # The modelled target must be at least this multiple of the *round-trip*
    # cost before a trade is worth taking. Without it the desk churned:
    # measured over its own closed history, the median absolute price move at
    # exit was 0.00% and 96% of exits moved less than crypto's 2.236%
    # round-trip floor — it was systematically closing before a trade could
    # pay for itself. Scales per market automatically, since the cost side is
    # market-specific (commodities ~0.047%, equity ~0.222%, crypto ~2.236%).
    min_reward_cost_multiple: float = 3.0


class RegimeCfg(BaseModel):
    vix_calm_max: float = 14.0
    vix_elevated_max: float = 20.0
    vix_stress_exit: float = 18.0
    vix_elevated_exit_to_calm: float = 12.5
    ewma_vol_elevated: float = 0.16
    ewma_vol_stress: float = 0.24
    trend_z_risk_off: float = -1.2
    trend_z_risk_on: float = 1.0
    min_hold_days: int = 2
    confirmation_days: int = 2
    default_label: str = "Elevated"


class GreeksCfg(BaseModel):
    move_pct: float = 0.01
    rehedge_band_lots: float = 1.0
    vega_limit: float = 200_000
    warn_utilization: float = 0.80
    lot_step: float = 1.0


class WatchlistCfg(BaseModel):
    active_cap: int = 200
    # Binance lists ~490 USDT spot pairs. The crypto sleeve is taken from
    # whatever it currently lists, cut by 24h turnover: below this the
    # spread/slippage on a clip costs more than any modelled edge, and each
    # extra pair is another klines round trip on every price refresh.
    # ~$5M ≈ 40 pairs, ~$1M ≈ 130, ~$250k ≈ 290. Set 0 to take everything.
    # $5M is the default deliberately: Indian crypto TDS is 1% of turnover
    # per side, so a round trip costs ~2.24% before any spread. Thin names
    # cannot carry that, and slippage there is worst, not best.
    crypto_min_quote_volume_usd: float = 5_000_000.0
    # Hard ceiling on the crypto sleeve regardless of the turnover cut.
    # None = no ceiling.
    crypto_max_symbols: int | None = None


class AlertsCfg(BaseModel):
    poll_seconds: int = 60
    min_days_between_prompts: int = 2
    snooze_hours: int = 24
    auto_start: bool = True
    price_every_cycles: int = 5
    # Part 3 item 6 — optional outbound webhook for operator alerts (drawdown
    # pause, live arm/disarm, worker death, large repair runs). Unset by
    # default: alerting works with zero external config (log line + DeskEvent
    # row); a webhook is an opt-in extra for whoever configures one.
    webhook_url: str | None = None


class ProvidersCfg(BaseModel):
    yfinance_enabled: bool = True
    price_ttl_minutes: int = 15
    request_sleep_ms: int = 400
    # 0.6 — overlay an intraday mark on top of the 6mo daily context so a
    # same-day paper clip can actually move between its open and its close.
    intraday_marks: bool = True
    intraday_interval: str = "5m"
    intraday_period: str = "1d"


class ModulesCfg(BaseModel):
    watchlist: bool = True
    scoring: bool = True
    greeks: bool = True
    gamma_scalp: bool = True
    vega: bool = True
    paper: bool = True
    live: bool = True
    import_: bool = Field(default=True, alias="import")
    charts: bool = True
    alerts: bool = True
    mcx: bool = True
    fx: bool = True
    commodities: bool = True

    model_config = {"populate_by_name": True}


class FxCfg(BaseModel):
    pairs: list[str] = Field(
        default_factory=lambda: [
            "USDINR",
            "EURUSD",
            "GBPUSD",
            "USDJPY",
            "USDCHF",
            "AUDUSD",
            "USDCAD",
            "EURINR",
            "GBPINR",
        ]
    )
    move_review_pct: float = 1.0


class HedgeCfg(BaseModel):
    roll_blackout_days: int = 3
    min_days_between_prompts: int = 2


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MERIDIAN_V3_", extra="ignore")

    app: AppCfg = Field(default_factory=AppCfg)
    paths: PathsCfg = Field(default_factory=PathsCfg)
    account: AccountCfg = Field(default_factory=AccountCfg)
    markets: MarketsCfg = Field(default_factory=MarketsCfg)
    sizing: SizingCfg = Field(default_factory=SizingCfg)
    safety: SafetyCfg = Field(default_factory=SafetyCfg)
    execution: ExecutionCfg = Field(default_factory=ExecutionCfg)
    decision: DecisionCfg = Field(default_factory=DecisionCfg)
    regime: RegimeCfg = Field(default_factory=RegimeCfg)
    greeks: GreeksCfg = Field(default_factory=GreeksCfg)
    watchlist: WatchlistCfg = Field(default_factory=WatchlistCfg)
    alerts: AlertsCfg = Field(default_factory=AlertsCfg)
    providers: ProvidersCfg = Field(default_factory=ProvidersCfg)
    modules: ModulesCfg = Field(default_factory=ModulesCfg)
    fx: FxCfg = Field(default_factory=FxCfg)
    hedge: HedgeCfg = Field(default_factory=HedgeCfg)
    test_db: str | None = None

    @property
    def data_dir(self) -> Path:
        return Path(self.paths.data_dir).expanduser()

    @property
    def db_path(self) -> Path:
        if self.test_db:
            return Path(self.test_db)
        return self.data_dir / self.paths.db_filename

    @property
    def log_dir(self) -> Path:
        return Path(self.paths.log_dir).expanduser()

    @property
    def import_dir(self) -> Path:
        return Path(self.paths.import_dir).expanduser()

    def ensure_dirs(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.import_dir.mkdir(parents=True, exist_ok=True)


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in override.items():
        if key in out and isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    payload = _load_yaml(DEFAULT_YAML)
    user = ROOT / "config" / "local.yaml"
    if user.exists():
        payload = _deep_merge(payload, _load_yaml(user))
    settings = Settings.model_validate(payload)
    env_db = os.environ.get("MERIDIAN_V3_TEST_DB")
    if env_db:
        settings.test_db = env_db
    settings.ensure_dirs()
    return settings


def reset_settings_cache() -> None:
    get_settings.cache_clear()
