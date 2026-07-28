from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from trading_agent.models.orders import OrderIntent
from trading_agent.models.portfolio import AccountSnapshot, Position


@dataclass
class StrategyContext:
    account: AccountSnapshot
    positions: list[Position]
    market_quotes: dict[str, float] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


class Strategy(ABC):
    name: str = "base"

    @abstractmethod
    def generate_intents(self, ctx: StrategyContext) -> list[OrderIntent]:
        """Return zero or more order intents based on current portfolio state."""
