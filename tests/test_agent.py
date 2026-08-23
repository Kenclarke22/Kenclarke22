import json
from datetime import date
from types import SimpleNamespace

import pytest

import trading_agent.main as main_module
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


def test_run_loop_reloads_metadata_each_cycle(monkeypatch):
    settings = Settings(agent_cycle_seconds=0)
    loaded_metadata = [{"cycle": 1}, {"cycle": 2}]
    seen_metadata = []
    sleep_calls = 0

    class _FakeAgent:
        def __init__(self, *, settings):
            self.settings = settings
            self.closed = False

        def run_cycle(self, *, strategy_metadata=None):
            seen_metadata.append(strategy_metadata)
            return SimpleNamespace(
                started_at=SimpleNamespace(isoformat=lambda: "now"),
                proposed=[],
                approved=[],
                submitted=[],
                rejected=[],
                errors=[],
            )

        def close(self):
            self.closed = True

    def _load_metadata(path):
        assert path == "quotes.json"
        return loaded_metadata[len(seen_metadata)]

    def _sleep(seconds):
        nonlocal sleep_calls
        assert seconds == 0
        sleep_calls += 1
        if sleep_calls == 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(main_module, "get_settings", lambda: settings)
    monkeypatch.setattr(main_module, "MasterTradingAgent", _FakeAgent)
    monkeypatch.setattr(main_module, "_load_metadata", _load_metadata)
    monkeypatch.setattr(main_module, "_print_cycle_result", lambda result: None)
    monkeypatch.setattr(main_module.time, "sleep", _sleep)

    exit_code = main_module.cmd_run_loop(SimpleNamespace(metadata="quotes.json"))

    assert exit_code == 0
    assert seen_metadata == loaded_metadata


def test_run_loop_skips_cycle_when_metadata_reload_fails(monkeypatch):
    settings = Settings(agent_cycle_seconds=0)
    seen_metadata = []
    sleep_calls = 0

    class _FakeAgent:
        def __init__(self, *, settings):
            self.settings = settings

        def run_cycle(self, *, strategy_metadata=None):
            seen_metadata.append(strategy_metadata)
            return SimpleNamespace(
                started_at=SimpleNamespace(isoformat=lambda: "now"),
                proposed=[],
                approved=[],
                submitted=[],
                rejected=[],
                errors=[],
            )

        def close(self):
            return None

    reloads = [
        json.JSONDecodeError("partial metadata", "{", 0),
        {"cycle": 1},
    ]

    def _load_metadata(path):
        assert path == "quotes.json"
        value = reloads.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    def _sleep(seconds):
        nonlocal sleep_calls
        assert seconds == 0
        sleep_calls += 1
        if sleep_calls == 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(main_module, "get_settings", lambda: settings)
    monkeypatch.setattr(main_module, "MasterTradingAgent", _FakeAgent)
    monkeypatch.setattr(main_module, "_load_metadata", _load_metadata)
    monkeypatch.setattr(main_module, "_print_cycle_result", lambda result: None)
    monkeypatch.setattr(main_module.console, "print", lambda *args, **kwargs: None)
    monkeypatch.setattr(main_module.time, "sleep", _sleep)

    exit_code = main_module.cmd_run_loop(SimpleNamespace(metadata="quotes.json"))

    assert exit_code == 0
    assert seen_metadata == [{"cycle": 1}]


def test_load_metadata_requires_json_object(tmp_path):
    metadata_path = tmp_path / "metadata.json"
    metadata_path.write_text("[1, 2, 3]", encoding="utf-8")

    with pytest.raises(ValueError, match="metadata JSON must be an object"):
        main_module._load_metadata(str(metadata_path))


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
