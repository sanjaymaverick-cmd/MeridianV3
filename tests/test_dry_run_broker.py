"""Part 3 item 7 -- DryRunBroker: a staging adapter that exercises the live
order path (arm -> live decision -> OMS -> PluginBroker -> broker) without
ever touching a real venue.

Covers:
- place() returns ok=True with a synthetic fill and logs a "would place" line.
- positions()/funds() update coherently across a buy then a sell.
- Positive control: once explicitly registered, PluginBroker.place() succeeds
  through DryRunBroker instead of refusing (mirrors
  test_paper_live.py::test_plugin_refuses_without_adapter, the negative case).
- Safety property: DryRunBroker is never auto-registered by normal app/CLI
  startup. get_live_broker() stays None unless someone explicitly calls
  register_broker(DryRunBroker()) (i.e. the new `register-dry-run-broker`
  CLI subcommand).
"""

from __future__ import annotations

from meridian_v3.execution.brokers.base import OrderRequest
from meridian_v3.execution.brokers.dry_run import DryRunBroker
from meridian_v3.execution.brokers.plugin import (
    _REGISTRY,
    PluginBroker,
    get_live_broker,
    register_broker,
)


def teardown_function(_fn):
    # Keep the module-level broker registry from leaking between tests.
    _REGISTRY.clear()


def test_dry_run_place_returns_ok_with_synthetic_fill():
    broker = DryRunBroker(cash=50_000)
    result = broker.place(OrderRequest("c1", "INFY", "buy", 2, 1400, "equity_cash", "live"))
    assert result.ok is True
    assert result.status == "filled"
    assert result.filled_qty == 2
    assert result.avg_price == 1400
    assert "DRY-RUN" in result.message


def test_dry_run_rejects_overspend_like_paper():
    broker = DryRunBroker(cash=1000)
    result = broker.place(OrderRequest("c1", "TCS", "buy", 1, 3125, "equity_cash", "live"))
    assert result.ok is False
    assert "synthetic cash" in result.message.lower()


def test_dry_run_positions_and_funds_update_across_buy_then_sell():
    broker = DryRunBroker(cash=50_000)

    buy = broker.place(OrderRequest("c1", "INFY", "buy", 10, 1000, "equity_cash", "live"))
    assert buy.ok
    assert broker.funds() == 50_000 - 10_000
    positions = broker.positions()
    assert len(positions) == 1
    assert positions[0].symbol == "INFY"
    assert positions[0].qty == 10
    assert positions[0].avg_price == 1000

    sell = broker.place(OrderRequest("c1", "INFY", "sell", 10, 1100, "equity_cash", "live"))
    assert sell.ok
    # cash comes back plus the sale proceeds
    assert broker.funds() == 50_000 - 10_000 + 11_000
    # fully closed position drops out
    assert broker.positions() == []


def test_dry_run_cancel_is_a_stub_like_paper_broker():
    broker = DryRunBroker()
    result = broker.cancel("DRYRUN-abc123")
    assert result.ok is False


def test_dry_run_health_reports_not_a_real_venue():
    broker = DryRunBroker()
    assert "dry-run" in broker.health().lower()


def test_dry_run_has_distinct_name_from_paper():
    assert DryRunBroker.name == "dry_run"
    assert DryRunBroker.name != "paper"


def test_plugin_broker_succeeds_once_dry_run_registered():
    """Positive-control counterpart to test_paper_live.py's
    test_plugin_refuses_without_adapter: register a DryRunBroker and confirm
    PluginBroker.place() now succeeds instead of refusing."""
    register_broker(DryRunBroker(cash=50_000))
    live = PluginBroker()
    result = live.place(OrderRequest("c1", "INFY", "buy", 1, 1400, "equity_cash", "live"))
    assert result.ok is True
    assert result.status == "filled"
    assert "DRY-RUN" in result.message

    # positions/funds flow through PluginBroker to the registered adapter too
    assert live.funds() == 50_000 - 1400
    positions = live.positions()
    assert len(positions) == 1
    assert positions[0].symbol == "INFY"


def test_get_live_broker_stays_none_without_explicit_registration():
    """Safety property: nothing at import time or module load registers a
    live broker. get_live_broker() must be None until someone explicitly
    calls register_broker(...) (e.g. via the register-dry-run-broker CLI
    command)."""
    assert get_live_broker() is None


def test_dry_run_broker_not_auto_registered_by_normal_app_startup(session):
    """create_app()/init_db() is the normal startup path (see app.py). It
    must never leave a live broker registered -- that would silently let
    live_armed=True place synthetic-but-live-shaped orders without the user
    explicitly running `meridian-v3 register-dry-run-broker`."""
    import meridian_v3.app as app_module

    app_module.create_app()
    assert get_live_broker() is None


def test_dry_run_broker_not_auto_registered_by_normal_cli_startup(session):
    """Same safety property via the CLI entry point: running an ordinary
    subcommand (seed) must not register any live broker as a side effect."""
    import meridian_v3.cli as cli_module

    rc = cli_module.main(["seed"])
    assert rc == 0
    assert get_live_broker() is None


def test_register_dry_run_broker_cli_command_registers_it_explicitly(session):
    """The new, explicit CLI subcommand is the only sanctioned way to enable
    the dry-run live path."""
    import meridian_v3.cli as cli_module

    rc = cli_module.main(["register-dry-run-broker"])
    assert rc == 0
    live = get_live_broker()
    assert live is not None
    assert live.name == "dry_run"


def test_register_dry_run_broker_cli_does_not_reach_a_separate_process(session):
    """The bug this whole env-var path exists to fix: `register-dry-run-broker`
    registers in ITS OWN process and exits — a separately-launched `serve`
    process has its own empty _REGISTRY, so that registration alone can never
    reach a running desk. Simulate the "separate process" by clearing the
    registry right after (as a real second OS process would start with an
    empty one) and confirm create_app() with no env var still stays None."""
    import meridian_v3.app as app_module
    import meridian_v3.cli as cli_module

    rc = cli_module.main(["register-dry-run-broker"])
    assert rc == 0
    assert get_live_broker() is not None  # registered in *this* process

    _REGISTRY.clear()  # simulate a fresh `serve` process's empty registry
    app_module.create_app()
    assert get_live_broker() is None  # the registration did not "persist"


def test_dry_run_broker_registered_via_env_var_reaches_create_app(session, monkeypatch):
    """The actual fix: MERIDIAN_V3_DRY_RUN_BROKER=1 set before create_app()
    runs (i.e. before `serve` in the same process) does register it, so the
    live path is genuinely exercisable against a running desk."""
    import meridian_v3.app as app_module

    monkeypatch.setenv("MERIDIAN_V3_DRY_RUN_BROKER", "1")
    app_module.create_app()
    live = get_live_broker()
    assert live is not None
    assert live.name == "dry_run"


def test_dry_run_broker_env_var_unset_stays_none(session, monkeypatch):
    """Positive control: with the env var absent (the default), create_app()
    behaves exactly as before — no live broker registered."""
    import meridian_v3.app as app_module

    monkeypatch.delenv("MERIDIAN_V3_DRY_RUN_BROKER", raising=False)
    app_module.create_app()
    assert get_live_broker() is None
