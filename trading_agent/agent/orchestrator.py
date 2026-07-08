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

        reserved_account = result.account
        reserved_positions = list(result.positions)
        for intent in result.proposed:
            mark = ctx.market_quotes.get(intent.symbol)
            if intent.option_details:
                mark = ctx.market_quotes.get(intent.option_details.underlying, mark)
            decision = self.risk.evaluate(
                intent,
                account=reserved_account,
                positions=reserved_positions,
                mark_price=mark,
            )
            if decision.approved:
                to_submit = decision.adjusted_intent or intent
                # Reserve the daily slot before evaluating later intents in this cycle.
                self.risk.record_submission_attempt()
                reserved_account, reserved_positions = self._reserve_cycle_risk(
                    to_submit,
                    mark_price=mark,
                    account=reserved_account,
                    positions=reserved_positions,
                )
                result.approved.append(to_submit)
            else:
                result.rejected.append((intent, decision.reason))

        for intent in result.approved:
            try:
                response = self.execution.submit_order(intent)
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

    def _reserve_cycle_risk(
        self,
        intent: OrderIntent,
        *,
        mark_price: float | None,
        account: AccountSnapshot | None,
        positions: list[Position],
    ) -> tuple[AccountSnapshot | None, list[Position]]:
        notional = intent.estimated_notional(mark_price)
        reserved_account = account
        if account and account.buying_power is not None and intent.side.value == "buy":
            reserved_account = account.model_copy(
                update={"buying_power": max(account.buying_power - notional, 0.0)}
            )

        reserved_positions = list(positions)
        for index, position in enumerate(reserved_positions):
            if position.symbol == intent.symbol:
                reserved_positions[index] = self._reserve_position(position, intent, notional)
                break
        else:
            reserved_positions.append(
                Position(
                    symbol=intent.symbol,
                    asset_class=intent.asset_class,
                    quantity=intent.quantity if intent.side.value == "buy" else -intent.quantity,
                    market_value=notional if intent.side.value == "buy" else -notional,
                    option_details=intent.option_details,
                )
            )

        return reserved_account, reserved_positions

    def _reserve_position(
        self,
        position: Position,
        intent: OrderIntent,
        notional: float,
    ) -> Position:
        current_quantity = position.quantity
        quantity_delta = intent.quantity if intent.side.value == "buy" else -intent.quantity
        projected_quantity = current_quantity + quantity_delta
        current_abs_value = abs(position.market_value or 0.0)
        increases_exposure = (
            (intent.side.value == "buy" and current_quantity >= 0)
            or (intent.side.value == "sell" and current_quantity <= 0)
        )
        projected_abs_value = (
            current_abs_value + notional if increases_exposure else max(current_abs_value - notional, 0.0)
        )
        if projected_quantity < 0:
            projected_market_value = -projected_abs_value
        elif projected_quantity > 0:
            projected_market_value = projected_abs_value
        else:
            projected_market_value = 0.0
        return position.model_copy(
            update={
                "quantity": projected_quantity,
                "market_value": projected_market_value,
            }
        )

    def close(self) -> None:
        self.execution.close()
