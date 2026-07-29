from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from trading_agent.config import Settings, get_settings
from trading_agent.models.orders import AssetClass, OptionDetails, OptionRight, OrderIntent
from trading_agent.models.portfolio import AccountSnapshot, Position

_OCC_OPTION_SYMBOL = re.compile(r"^([A-Z.]+)(\d{6})([CP])(\d{1,8})$")


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

        if intent.side.value == "buy":
            open_positions = {_position_key(p) for p in positions if abs(p.quantity) > 0}
            is_new = _intent_key(intent) not in open_positions
            at_position_limit = len(open_positions) >= self.settings.max_open_positions
            if at_position_limit and is_new:
                return RiskDecision(False, "Max open positions reached")

        notional = intent.estimated_notional(mark_price)
        if mark_price and notional > self.settings.max_order_notional_usd:
            return RiskDecision(
                False,
                f"Order notional ${notional:,.2f} exceeds max ${self.settings.max_order_notional_usd:,.2f}",
            )

        if account and account.buying_power is not None and intent.side.value == "buy":
            if notional and notional > account.buying_power:
                return RiskDecision(
                    False,
                    f"Insufficient buying power (${account.buying_power:,.2f}) for notional ${notional:,.2f}",
                )

        intent_position_key = _intent_key(intent)
        for position in positions:
            if _position_key(position) != intent_position_key:
                continue
            current_value = abs(position.market_value or 0)
            projected = current_value + notional
            if projected > self.settings.max_position_notional_usd:
                return RiskDecision(
                    False,
                    f"Position notional would exceed max ${self.settings.max_position_notional_usd:,.2f}",
                )

        return RiskDecision(True, "Approved")


def _intent_key(intent: OrderIntent) -> tuple[object, ...]:
    if intent.asset_class == AssetClass.OPTION and intent.option_details is not None:
        return ("option", *_option_details_key(intent.option_details))
    return (intent.asset_class.value, intent.symbol)


def _position_key(position: Position) -> tuple[object, ...]:
    if position.asset_class == AssetClass.OPTION:
        details = position.option_details or _parse_occ_option_symbol(position.symbol)
        if details is not None:
            return ("option", *_option_details_key(details))
    return (position.asset_class.value, position.symbol)


def _option_details_key(details: OptionDetails) -> tuple[object, ...]:
    return (
        details.underlying,
        details.expiry,
        float(details.strike),
        details.right.value,
    )


def _parse_occ_option_symbol(symbol: str) -> OptionDetails | None:
    match = _OCC_OPTION_SYMBOL.match(symbol.upper().replace(" ", ""))
    if match is None:
        return None

    underlying, expiry_raw, right_raw, strike_raw = match.groups()
    try:
        expiry = date(2000 + int(expiry_raw[:2]), int(expiry_raw[2:4]), int(expiry_raw[4:]))
        strike = int(strike_raw) / 1000 if len(strike_raw) == 8 else float(strike_raw)
    except ValueError:
        return None

    return OptionDetails(
        underlying=underlying,
        expiry=expiry,
        strike=strike,
        right=OptionRight.CALL if right_raw == "C" else OptionRight.PUT,
    )
