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
    starting_equity_inr: float = 5000.0
    currency: str = "INR"
    compound_profits: bool = True
    name: str = "Meridian Dedicated"


class MarketSpec(BaseModel):
    enabled: bool = True
    home: bool = False
    lot_step: float = 1.0
    min_notional: float = 1.0
    buying_only: bool = False
    selling_forbidden: bool = False
    min_premium_inr: float = 50.0
    max_premium_pct_of_equity: float = 0.12
    nano_lots: bool = False
    micro_lots: bool = False
    standard_lots_forbidden: bool = False
    min_lot: float = 1.0


class MarketsCfg(BaseModel):
    default: str = "equity_cash"
    priority: list[str] = Field(default_factory=lambda: ["equity_cash", "options_buy", "forex_micro"])
    equity_cash: MarketSpec = Field(default_factory=lambda: MarketSpec(home=True))
    options_buy: MarketSpec = Field(
        default_factory=lambda: MarketSpec(buying_only=True, selling_forbidden=True)
    )
    forex_micro: MarketSpec = Field(
        default_factory=lambda: MarketSpec(
            nano_lots=True, micro_lots=True, standard_lots_forbidden=True, min_lot=0.01
        )
    )


class SizingCfg(BaseModel):
    kelly_fraction: float = 0.15
    min_kelly_fraction: float = 0.05
    max_kelly_fraction: float = 0.25
    atr_stop_mult: float = 1.5
    atr_period: int = 14
    max_risk_pct_high_conf: float = 0.04
    max_risk_pct_normal: float = 0.015
    max_risk_pct_low: float = 0.008
    min_risk_inr: float = 25.0
    high_confidence: float = 0.78
    live_confidence: float = 0.82
    positional_confidence: float = 0.88
    max_concurrent_normal: int = 2
    max_concurrent_high: int = 4
    max_position_pct: float = 0.35
    cash_reserve_pct: float = 0.10


class SafetyCfg(BaseModel):
    drawdown_pause_pct: float = 0.20
    drawdown_scale_start_pct: float = 0.08
    max_daily_live_trades: int = 3
    max_daily_live_high_conf: int = 6
    overnight_equity_ok: bool = True
    overnight_options_forbidden: bool = True
    overnight_fx_forbidden: bool = True
    flatten_before_close_minutes: int = 20
    session_open: str = "09:15"
    session_close: str = "15:30"
    timezone: str = "Asia/Kolkata"


class DecisionCfg(BaseModel):
    paper_all_signals: bool = True
    live_requires_arm: bool = True
    live_min_confidence: float = 0.82
    live_min_confluence: float = 62.0
    edge_safety_margin: float = 0.0015
    freshness_half_life_hours: float = 6.0
    min_freshness: float = 0.35
    walkforward_oos_gap_max: float = 0.35


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
    active_cap: int = 50


class AlertsCfg(BaseModel):
    poll_seconds: int = 60
    min_days_between_prompts: int = 2
    snooze_hours: int = 24
    auto_start: bool = True
    price_every_cycles: int = 5


class ProvidersCfg(BaseModel):
    yfinance_enabled: bool = True
    price_ttl_minutes: int = 15
    request_sleep_ms: int = 400


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

    model_config = {"populate_by_name": True}


class FxCfg(BaseModel):
    pairs: list[str] = Field(default_factory=lambda: ["USDINR", "EURUSD", "USDJPY"])
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
