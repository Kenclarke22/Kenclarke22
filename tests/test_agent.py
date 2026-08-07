from datetime import date

import pytest

from trading_agent.agent.orchestrator import MasterTradingAgent
from trading_agent.config import Settings
from trading_agent.models.orders import (
    AssetClass,
    OptionDetails,
    OptionRight,
    OrderIntent,
    OrderSide,
    OrderType,
)
from trading_agent.models.portfolio import AccountSnapshot, Position
from trading_agent.risk.manager import RiskManager
from trading_agent.strategies.base import StrategyContext
from trading_agent.strategies.rebalance import RebalanceStrategy


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


def test_rebalance_uses_quote_value_and_does_not_oversell_stale_position_value():
    strategy = RebalanceStrategy()
    ctx = StrategyContext(
        account=AccountSnapshot(equity=100_000, buying_power=100_000),
        positions=[
            Position(
                symbol="AAPL",
                asset_class=AssetClass.STOCK,
                quantity=100,
                market_value=25_000,
            )
        ],
        market_quotes={"AAPL": 190.0},
        metadata={"target_weights": {"AAPL": 0.05}, "rebalance_threshold": 0.001},
    )

    intents = strategy.generate_intents(ctx)

    assert len(intents) == 1
    assert intents[0].side == OrderSide.SELL
    assert intents[0].quantity == pytest.approx(73.6842)
    assert intents[0].quantity <= 100


def test_risk_rejects_sell_without_matching_long_position():
    settings = Settings(max_order_notional_usd=500_000, trading_mode="live")
    risk = RiskManager(settings=settings)
    intent = OrderIntent(
        symbol="AAPL",
        asset_class=AssetClass.STOCK,
        side=OrderSide.SELL,
        quantity=1000,
        order_type=OrderType.LIMIT,
        limit_price=190.0,
    )

    decision = risk.evaluate(intent, account=None, positions=[], mark_price=190.0)

    assert not decision.approved
    assert "long position" in decision.reason


def test_risk_rejects_sell_quantity_above_holdings():
    settings = Settings(max_order_notional_usd=500_000, trading_mode="live")
    risk = RiskManager(settings=settings)
    intent = OrderIntent(
        symbol="AAPL",
        asset_class=AssetClass.STOCK,
        side=OrderSide.SELL,
        quantity=11,
        order_type=OrderType.LIMIT,
        limit_price=190.0,
    )
    positions = [
        Position(
            symbol="AAPL",
            asset_class=AssetClass.STOCK,
            quantity=10,
            market_value=1_900,
        )
    ]

    decision = risk.evaluate(intent, account=None, positions=positions, mark_price=190.0)

    assert not decision.approved
    assert "exceeds holdings" in decision.reason


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
