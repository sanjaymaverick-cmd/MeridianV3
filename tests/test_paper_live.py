from meridian_v3.execution.brokers.paper_broker import PaperBroker
from meridian_v3.execution.brokers.base import OrderRequest
from meridian_v3.execution.brokers.plugin import PluginBroker


def test_paper_buy_and_funds():
    broker = PaperBroker(5000)
    result = broker.place(OrderRequest("c1", "INFY", "buy", 2, 1400, "equity_cash", "paper"))
    assert result.ok
    assert broker.funds() == 5000 - 2800
    assert broker.positions()[0].qty == 2


def test_paper_rejects_overspend():
    broker = PaperBroker(1000)
    result = broker.place(OrderRequest("c1", "TCS", "buy", 1, 3125, "equity_cash", "paper"))
    assert result.ok is False


def test_plugin_refuses_without_adapter():
    live = PluginBroker()
    result = live.place(OrderRequest("c1", "INFY", "buy", 1, 1400, "equity_cash", "live"))
    assert result.ok is False
    assert "no live broker" in result.message.lower()
