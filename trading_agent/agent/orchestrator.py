from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from trading_agent.config import Settings, get_settings
from trading_agent.execution.client import ExecutionClient, ExecutionClientError
from trading_agent.models.orders import OrderIntent, OrderResponse
from trading_agent.models.portfolio import AccountSnapshot, Position
from trading_agent.risk.manager import RiskManager
from trading_agent.strategies.base import StrategyContext
from trading_agent.strategies.registry import get_registered_strategies

logger = logging.getLogger(__name__)


@dataclass
class CycleResult:
    started_at: datetime
    finished_at: datetime
    account: AccountSnapshot | None
    positions: list[Position]
    proposed: list[OrderIntent] = field(default_factory=list)
    approved: list[OrderIntent] = field(default_factory=list)
    rejected: list[tuple[OrderIntent, str]] = field(default_factory=list)
    submitted: list[OrderResponse] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return not self.errors


@dataclass
class MasterTradingAgent:
    """Master orchestrator: perceive → strategize → risk-check → execute."""

    settings: Settings = field(default_factory=get_settings)
    execution: ExecutionClient | None = None
    risk: RiskManager | None = None

    def __post_init__(self) -> None:
        if self.execution is None:
            self.execution = ExecutionClient(self.settings)
        if self.risk is None:
            self.risk = RiskManager(settings=self.settings)

    def run_cycle(self, *, strategy_metadata: dict[str, Any] | None = None) -> CycleResult:
        started = datetime.now(timezone.utc)
        result = CycleResult(started_at=started, finished_at=started, account=None, positions=[])

        try:
            self.execution.health()
        except ExecutionClientError as exc:
            result.errors.append(f"Execution tool unreachable: {exc}")
            result.finished_at = datetime.now(timezone.utc)
            return result

        try:
            result.account = self.execution.get_account()
            result.positions = self.execution.get_positions()
        except ExecutionClientError as exc:
            result.errors.append(f"Failed to load portfolio state: {exc}")
            result.finished_at = datetime.now(timezone.utc)
            return result

        ctx = StrategyContext(
            account=result.account,
            positions=result.positions,
            market_quotes=strategy_metadata.get("market_quotes", {}) if strategy_metadata else {},
            metadata=strategy_metadata or {},
        )

        strategies = get_registered_strategies(self.settings.active_strategy_names)
        for strategy in strategies:
            try:
                intents = strategy.generate_intents(ctx)
                result.proposed.extend(intents)
            except Exception as exc:  # noqa: BLE001 - strategy isolation
                logger.exception("Strategy %s failed", strategy.name)
                result.errors.append(f"Strategy {strategy.name}: {exc}")

        for intent in result.proposed:
            mark = ctx.market_quotes.get(intent.symbol)
            if intent.option_details:
                mark = ctx.market_quotes.get(intent.option_details.underlying, mark)
            decision = self.risk.evaluate(
                intent,
                account=result.account,
                positions=result.positions,
                mark_price=mark,
            )
            if decision.approved:
                to_submit = decision.adjusted_intent or intent
                result.approved.append(to_submit)
            else:
                result.rejected.append((intent, decision.reason))

        for intent in result.approved:
            try:
                response = self.execution.submit_order(intent)
                self.risk.record_submitted_order()
                result.submitted.append(response)
                logger.info(
                    "Order %s %s status=%s",
                    response.id,
                    intent.display_symbol,
                    response.status.value,
                )
            except ExecutionClientError as exc:
                result.errors.append(f"Submit failed for {intent.display_symbol}: {exc}")

        result.finished_at = datetime.now(timezone.utc)
        return result

    def close(self) -> None:
        self.execution.close()
