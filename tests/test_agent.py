from datetime import date

import pytest

from trading_agent.agent.orchestrator import MasterTradingAgent
from trading_agent.config import Settings
from trading_agent.models.orders import (
    AssetClass,
    OptionDetails,
    OptionRight,
    OrderIntent,
    OrderResponse,
    OrderSide,
    OrderStatus,
    OrderType,
)
from trading_agent.models.portfolio import AccountSnapshot, Position
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
    decision = risk.evaluate(intent, account=None, positions=[], mark_price=None)
    assert not decision.approved
    assert "notional" in decision.reason.lower()


def test_risk_rejects_order_without_price_reference():
    settings = Settings(max_order_notional_usd=1000, trading_mode="live")
    risk = RiskManager(settings=settings)
    intent = OrderIntent(
        symbol="AAPL",
        asset_class=AssetClass.STOCK,
        side=OrderSide.BUY,
        quantity=100,
        order_type=OrderType.MARKET,
    )

    decision = risk.evaluate(intent, account=None, positions=[], mark_price=None)

    assert not decision.approved
    assert "notional" in decision.reason.lower()


def test_risk_rejects_new_position_over_position_notional():
    settings = Settings(
        max_order_notional_usd=100_000,
        max_position_notional_usd=1_000,
        trading_mode="live",
    )
    risk = RiskManager(settings=settings)
    intent = OrderIntent(
        symbol="AAPL",
        asset_class=AssetClass.STOCK,
        side=OrderSide.BUY,
        quantity=20,
        order_type=OrderType.LIMIT,
        limit_price=100.0,
    )

    decision = risk.evaluate(intent, account=None, positions=[], mark_price=None)

    assert not decision.approved
    assert "position notional" in decision.reason.lower()


def test_risk_allows_reducing_oversized_position():
    settings = Settings(
        max_order_notional_usd=100_000,
        max_position_notional_usd=1_000,
        trading_mode="live",
    )
    risk = RiskManager(settings=settings)
    intent = OrderIntent(
        symbol="AAPL",
        asset_class=AssetClass.STOCK,
        side=OrderSide.SELL,
        quantity=1,
        order_type=OrderType.LIMIT,
        limit_price=100.0,
    )
    positions = [
        Position(
            symbol="AAPL",
            asset_class=AssetClass.STOCK,
            quantity=20,
            market_value=2_000.0,
        )
    ]

    decision = risk.evaluate(intent, account=None, positions=positions, mark_price=None)

    assert decision.approved


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


def test_master_agent_cycle_with_mock_server():
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from trading_agent.execution.client import ExecutionClient
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


def test_master_agent_reserves_daily_slots_during_cycle(monkeypatch):
    from trading_agent.agent import orchestrator

    settings = Settings(
        execution_base_url="http://testserver",
        trading_mode="live",
        max_daily_orders=1,
        max_order_notional_usd=10_000,
    )
    intents = [
        OrderIntent(
            symbol="AAPL",
            asset_class=AssetClass.STOCK,
            side=OrderSide.BUY,
            quantity=1,
            order_type=OrderType.LIMIT,
            limit_price=100.0,
        ),
        OrderIntent(
            symbol="MSFT",
            asset_class=AssetClass.STOCK,
            side=OrderSide.BUY,
            quantity=1,
            order_type=OrderType.LIMIT,
            limit_price=100.0,
        ),
    ]

    class _TwoOrderStrategy:
        name = "two_order"

        def generate_intents(self, ctx):
            return intents

    class _Execution:
        def __init__(self) -> None:
            self.submitted: list[OrderIntent] = []

        def health(self):
            return {"status": "ok"}

        def get_account(self):
            return AccountSnapshot(equity=100_000, buying_power=100_000)

        def get_positions(self):
            return []

        def submit_order(self, intent: OrderIntent):
            self.submitted.append(intent)
            return OrderResponse(
                id=f"order-{len(self.submitted)}",
                status=OrderStatus.SUBMITTED,
                symbol=intent.symbol,
                asset_class=intent.asset_class,
                side=intent.side,
                quantity=intent.quantity,
            )

        def close(self) -> None:
            return None

    monkeypatch.setattr(orchestrator, "get_registered_strategies", lambda names: [_TwoOrderStrategy()])
    execution = _Execution()
    agent = MasterTradingAgent(
        settings=settings,
        execution=execution,  # type: ignore[arg-type]
        risk=RiskManager(settings=settings),
    )

    result = agent.run_cycle(strategy_metadata={"market_quotes": {"AAPL": 100.0, "MSFT": 100.0}})

    assert len(result.proposed) == 2
    assert len(result.submitted) == 1
    assert len(result.rejected) == 1
    assert result.rejected[0][1] == "Daily order limit reached"
    assert execution.submitted == [intents[0]]


def test_submit_order_does_not_retry_after_transport_error():
    import httpx

    from trading_agent.execution.client import ExecutionClient, ExecutionClientError

    settings = Settings(execution_base_url="http://testserver", trading_mode="live")

    class _FailingClient:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        def request(self, method: str, path: str, **kwargs):
            self.calls.append((method, path))
            raise httpx.ReadTimeout("timed out after order submission")

        def close(self) -> None:
            return None

    transport = _FailingClient()
    client = ExecutionClient(settings=settings)
    client._client = transport  # type: ignore[assignment]
    intent = OrderIntent(
        symbol="AAPL",
        asset_class=AssetClass.STOCK,
        side=OrderSide.BUY,
        quantity=1,
        order_type=OrderType.LIMIT,
        limit_price=190.0,
    )

    with pytest.raises(ExecutionClientError):
        client.submit_order(intent)

    assert transport.calls == [("POST", "/api/orders")]
