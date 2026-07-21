from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    execution_base_url: str = Field(
        default="http://localhost:3000",
        description="Base URL of the order execution tool",
    )
    execution_api_key: str | None = Field(
        default=None,
        description="Optional API key sent as Authorization: Bearer <key>",
    )

    trading_mode: Literal["live", "paper"] = "live"
    agent_cycle_seconds: int = 60

    max_order_notional_usd: float = 5000.0
    max_position_notional_usd: float = 25000.0
    max_daily_orders: int = 20
    max_open_positions: int = 10
    allowed_symbols: str = ""

    active_strategies: str = ""

    @property
    def allowed_symbol_set(self) -> set[str] | None:
        if not self.allowed_symbols.strip():
            return None
        return {s.strip().upper() for s in self.allowed_symbols.split(",") if s.strip()}

    @property
    def active_strategy_names(self) -> set[str] | None:
        if not self.active_strategies.strip():
            return None
        return {s.strip() for s in self.active_strategies.split(",") if s.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
