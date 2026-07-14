from datetime import date, datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator


class AssetClass(str, Enum):
    STOCK = "stock"
    OPTION = "option"


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


class OrderStatus(str, Enum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    PARTIAL = "partial"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class OptionRight(str, Enum):
    CALL = "call"
    PUT = "put"


class OptionDetails(BaseModel):
    underlying: str
    expiry: date
    strike: float
    right: OptionRight

    @field_validator("underlying")
    @classmethod
    def uppercase_underlying(cls, value: str) -> str:
        return value.upper()


class OrderIntent(BaseModel):
    """Structured order proposal from a strategy or the master agent."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    symbol: str
    asset_class: AssetClass
    side: OrderSide
    quantity: float = Field(gt=0)
    order_type: OrderType = OrderType.MARKET
    limit_price: float | None = None
    stop_price: float | None = None
    time_in_force: str = "day"
    option_details: OptionDetails | None = None
    strategy_name: str = "master"
    rationale: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("symbol")
    @classmethod
    def uppercase_symbol(cls, value: str) -> str:
        return value.upper()

    @model_validator(mode="after")
    def validate_option_and_prices(self) -> "OrderIntent":
        if self.asset_class == AssetClass.OPTION and self.option_details is None:
            raise ValueError("option_details required when asset_class is option")
        if self.asset_class == AssetClass.STOCK and self.option_details is not None:
            raise ValueError("option_details must be omitted for stock orders")
        if self.order_type in {OrderType.LIMIT, OrderType.STOP_LIMIT} and self.limit_price is None:
            raise ValueError("limit_price required for limit and stop_limit orders")
        if self.order_type in {OrderType.STOP, OrderType.STOP_LIMIT} and self.stop_price is None:
            raise ValueError("stop_price required for stop and stop_limit orders")
        return self

    @property
    def display_symbol(self) -> str:
        if self.asset_class == AssetClass.STOCK:
            return self.symbol
        assert self.option_details is not None
        od = self.option_details
        return (
            f"{od.underlying} {od.expiry.isoformat()} "
            f"{od.strike:g}{od.right.value[0].upper()}"
        )

    def estimated_notional(self, mark_price: float | None = None) -> float:
        price = mark_price or self.limit_price or 0.0
        multiplier = 100 if self.asset_class == AssetClass.OPTION else 1
        return abs(self.quantity) * price * multiplier


class OrderResponse(BaseModel):
    id: str
    status: OrderStatus
    symbol: str
    asset_class: AssetClass
    side: OrderSide
    quantity: float
    filled_quantity: float = 0.0
    average_fill_price: float | None = None
    message: str | None = None
    submitted_at: datetime | None = None
    raw: dict[str, Any] = Field(default_factory=dict)
