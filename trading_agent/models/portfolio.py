from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from trading_agent.models.orders import AssetClass, OptionDetails


class Position(BaseModel):
    symbol: str
    asset_class: AssetClass
    quantity: float
    average_cost: float | None = None
    market_value: float | None = None
    unrealized_pnl: float | None = None
    option_details: OptionDetails | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class AccountSnapshot(BaseModel):
    equity: float | None = None
    buying_power: float | None = None
    cash: float | None = None
    day_pnl: float | None = None
    mode: str = "live"
    as_of: datetime | None = None
    raw: dict[str, Any] = Field(default_factory=dict)
