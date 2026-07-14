from datetime import date

import pytest

from trading_agent.agent.orchestrator import MasterTradingAgent
from trading_agent.config import Settings
from trading_agent.execution.client import ExecutionClient, ExecutionClientError
from trading_agent.models.orders import (
    AssetClass,
    OptionDetails,
    OptionRight,
    OrderIntent,
    OrderSide,
    OrderType,
)
from trading_agent.risk.manager import RiskManager


def test_risk_rejects_over_notional():
    settings = Settings(max_order_notional_usd=1000, trading_mode="live")
    risk = RiskManager(settings=settings)
    intent = OrderIntent(
        symbol="AAPL",
        asset_class=AssetClass.STOCK,
        side=OrderSide.BUY,
        quantity=100,
        order_type=OrderType.LIMIT,
        limit_price=190.0,
    )
    decision = risk.evaluate(intent, account=None, positions=[], mark_price=190.0)
    assert not decision.approved
    assert "notional" in decision.reason.lower()


def test_option_intent_display_symbol():
    intent = OrderIntent(
        symbol="AAPL",
        asset_class=AssetClass.OPTION,
        side=OrderSide.BUY,
        quantity=1,
        order_type=OrderType.LIMIT,
        limit_price=5.5,
        option_details=OptionDetails(
            underlying="AAPL",
            expiry=date(2026, 6, 20),
            strike=200,
            right=OptionRight.CALL,
        ),
    )
    assert "AAPL" in intent.display_symbol
    assert "200C" in intent.display_symbol


def test_submit_order_rejects_broker_rejected_status():
    client = ExecutionClient(settings=Settings(execution_base_url="http://testserver"))
    client._client = _SingleResponseAdapter(  # type: ignore[assignment]
        {
            "id": "ord_123",
            "status": "rejected",
            "symbol": "AAPL",
            "asset_class": "stock",
            "side": "buy",
            "quantity": 1,
            "message": "risk rejected",
        }
    )

    with pytest.raises(ExecutionClientError, match="rejected"):
        client.submit_order(_stock_limit_intent())


def test_submit_order_rejects_empty_success_response():
    client = ExecutionClient(settings=Settings(execution_base_url="http://testserver"))
    client._client = _SingleResponseAdapter({}, content=b"")  # type: ignore[assignment]

    with pytest.raises(ExecutionClientError, match="missing order id"):
        client.submit_order(_stock_limit_intent())


def test_daily_order_limit_persists_across_risk_managers(tmp_path):
    settings = Settings(
        max_daily_orders=1,
        trading_mode="live",
        risk_state_path=str(tmp_path / "risk_state.json"),
    )
    first_process_risk = RiskManager(settings=settings)
    first_process_risk.record_submitted_order()

    next_process_risk = RiskManager(settings=settings)
    decision = next_process_risk.evaluate(
        _stock_limit_intent(),
        account=None,
        positions=[],
        mark_price=190.0,
    )

    assert not decision.approved
    assert "daily order limit" in decision.reason.lower()


def test_master_agent_cycle_with_mock_server():
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from trading_agent.mock_execution_server import app

    settings = Settings(
        execution_base_url="http://testserver",
        trading_mode="live",
        max_order_notional_usd=50_000,
    )
    test_client = TestClient(app)

    class _HttpAdapter:
        def request(self, method: str, path: str, **kwargs):
            return test_client.request(method, path, **kwargs)

        def close(self) -> None:
            return None

    client = ExecutionClient(settings=settings)
    client._client = _HttpAdapter()  # type: ignore[assignment]
    agent = MasterTradingAgent(
        settings=settings,
        execution=client,
        risk=RiskManager(settings=settings),
    )
    result = agent.run_cycle(
        strategy_metadata={
            "target_weights": {"AAPL": 0.1},
            "market_quotes": {"AAPL": 190.0},
            "rebalance_threshold": 0.001,
        }
    )
    agent.close()

    assert result.success
    assert len(result.submitted) >= 1


class _SingleResponseAdapter:
    def __init__(self, data: dict, *, content: bytes | None = None, status_code: int = 200):
        self._response = _Response(data, content=content, status_code=status_code)

    def request(self, method: str, path: str, **kwargs):
        return self._response

    def close(self) -> None:
        return None


class _Response:
    def __init__(self, data: dict, *, content: bytes | None, status_code: int):
        self._data = data
        self.status_code = status_code
        self.content = content if content is not None else b"{}"
        self.text = ""

    def json(self):
        return self._data


def _stock_limit_intent() -> OrderIntent:
    return OrderIntent(
        symbol="AAPL",
        asset_class=AssetClass.STOCK,
        side=OrderSide.BUY,
        quantity=1,
        order_type=OrderType.LIMIT,
        limit_price=190.0,
    )
