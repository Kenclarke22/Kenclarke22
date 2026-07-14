from __future__ import annotations

import json
import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Iterator

import fcntl

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
        with self._locked_state_path() as path:
            self.reset_daily_counters_if_needed()
            state = self._read_state(path)
            self._orders_today = self._orders_today_for_state(state)
            self._orders_today += 1
            self._write_state(path, self._state_payload())

    def evaluate(
        self,
        intent: OrderIntent,
        *,
        account: AccountSnapshot | None,
        positions: list[Position],
        mark_price: float | None = None,
    ) -> RiskDecision:
        try:
            self._sync_daily_counters_from_state()
        except (OSError, ValueError) as exc:
            return RiskDecision(False, f"Risk state unavailable: {exc}")

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

        if account and account.buying_power is not None and intent.side.value == "buy":
            if notional and notional > account.buying_power:
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

    def _sync_daily_counters_from_state(self) -> None:
        with self._locked_state_path() as path:
            self.reset_daily_counters_if_needed()
            self._orders_today = self._orders_today_for_state(self._read_state(path))

    def _orders_today_for_state(self, state: dict[str, Any]) -> int:
        if state.get("order_date") != self._order_date.isoformat():
            return 0

        orders_today = state.get("orders_today", 0)
        if not isinstance(orders_today, int) or orders_today < 0:
            raise ValueError("risk state has invalid orders_today")
        return orders_today

    def _state_payload(self) -> dict[str, Any]:
        return {
            "order_date": self._order_date.isoformat(),
            "orders_today": self._orders_today,
        }

    @contextmanager
    def _locked_state_path(self) -> Iterator[Path]:
        path = Path(self.settings.risk_state_path).expanduser()
        if path.parent != Path("."):
            path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = Path(f"{path}.lock")
        with lock_path.open("a+", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield path
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _read_state(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        with path.open(encoding="utf-8") as state_file:
            data = json.load(state_file)
        if not isinstance(data, dict):
            raise ValueError("risk state must be a JSON object")
        return data

    def _write_state(self, path: Path, state: dict[str, Any]) -> None:
        temp_path = Path(f"{path}.tmp")
        with temp_path.open("w", encoding="utf-8") as state_file:
            json.dump(state, state_file, sort_keys=True)
            state_file.write("\n")
        os.replace(temp_path, path)
