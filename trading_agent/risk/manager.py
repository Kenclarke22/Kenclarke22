from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from trading_agent.config import Settings, get_settings
from trading_agent.models.orders import OrderIntent
from trading_agent.models.portfolio import AccountSnapshot, Position


@dataclass
class RiskDecision:
    approved: bool
    reason: str
    adjusted_intent: OrderIntent | None = None


@dataclass
class RiskManager:
    settings: Settings = field(default_factory=get_settings)
    _orders_today: int = 0
    _order_date: date = field(default_factory=date.today)

    def reset_daily_counters_if_needed(self) -> None:
        today = date.today()
        if today != self._order_date:
            self._orders_today = 0
            self._order_date = today

    def record_submitted_order(self) -> None:
        self.reset_daily_counters_if_needed()
        self._orders_today += 1

    def evaluate(
        self,
        intent: OrderIntent,
        *,
        account: AccountSnapshot | None,
        positions: list[Position],
        mark_price: float | None = None,
    ) -> RiskDecision:
        self.reset_daily_counters_if_needed()

        if self.settings.trading_mode == "live":
            pass  # user requested live; guardrails still apply below

        allowed = self.settings.allowed_symbol_set
        if allowed is not None:
            symbol_key = intent.symbol
            if intent.option_details:
                symbol_key = intent.option_details.underlying
            if symbol_key not in allowed:
                return RiskDecision(False, f"Symbol {symbol_key} not in allowed list")

        if self._orders_today >= self.settings.max_daily_orders:
            return RiskDecision(False, "Daily order limit reached")

        if len(positions) >= self.settings.max_open_positions and intent.side.value == "buy":
            open_symbols = {p.symbol for p in positions if abs(p.quantity) > 0}
            is_new = intent.symbol not in open_symbols
            if is_new:
                return RiskDecision(False, "Max open positions reached")

        notional = intent.estimated_notional(mark_price)
        if mark_price and notional > self.settings.max_order_notional_usd:
            return RiskDecision(
                False,
                f"Order notional ${notional:,.2f} exceeds max ${self.settings.max_order_notional_usd:,.2f}",
            )

        if account and intent.side.value == "buy":
            if self.settings.trading_mode == "live" and account.buying_power is None:
                return RiskDecision(False, "Buying power unavailable for live buy order")
            if account.buying_power is not None and notional and notional > account.buying_power:
                return RiskDecision(
                    False,
                    f"Insufficient buying power (${account.buying_power:,.2f}) for notional ${notional:,.2f}",
                )

        for position in positions:
            if position.symbol != intent.symbol:
                continue
            current_value = abs(position.market_value or 0)
            projected = current_value + notional
            if projected > self.settings.max_position_notional_usd:
                return RiskDecision(
                    False,
                    f"Position notional would exceed max ${self.settings.max_position_notional_usd:,.2f}",
                )

        return RiskDecision(True, "Approved")
