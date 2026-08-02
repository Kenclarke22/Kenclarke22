"""Example strategy: maintain target stock weights from strategy metadata."""

from __future__ import annotations

import math

from trading_agent.models.orders import AssetClass, OrderIntent, OrderSide, OrderType
from trading_agent.models.portfolio import Position
from trading_agent.strategies.base import Strategy, StrategyContext


class RebalanceStrategy(Strategy):
    """
    Reads target weights from ctx.metadata['target_weights'], e.g.:
    {"AAPL": 0.5, "MSFT": 0.5}
    Emits limit orders when drift exceeds threshold.
    """

    name = "rebalance"

    def generate_intents(self, ctx: StrategyContext) -> list[OrderIntent]:
        targets = self._validated_targets(ctx.metadata.get("target_weights", {}))
        if not targets or ctx.account.equity is None or ctx.account.equity <= 0:
            return []

        threshold = float(ctx.metadata.get("rebalance_threshold", 0.05))
        intents: list[OrderIntent] = []

        position_map = {p.symbol: p for p in ctx.positions if p.asset_class == AssetClass.STOCK}
        equity = ctx.account.equity

        for symbol in sorted(set(targets) | set(position_map)):
            target_weight = targets.get(symbol, 0.0)
            mark = ctx.market_quotes.get(symbol)
            if mark is None or mark <= 0:
                mark = self._price_from_position(position_map.get(symbol))
                if mark is None or mark <= 0:
                    continue

            target_value = equity * target_weight
            current = position_map.get(symbol)
            current_value = current.market_value if current and current.market_value else 0.0
            drift = abs(target_value - current_value) / equity

            if drift < threshold:
                continue

            delta_value = target_value - current_value
            qty = abs(delta_value / mark)
            if qty < 1:
                continue

            side = OrderSide.BUY if delta_value > 0 else OrderSide.SELL
            intents.append(
                OrderIntent(
                    symbol=symbol,
                    asset_class=AssetClass.STOCK,
                    side=side,
                    quantity=round(qty, 4),
                    order_type=OrderType.LIMIT,
                    limit_price=round(mark, 2),
                    strategy_name=self.name,
                    rationale=f"Rebalance toward {target_weight:.0%} weight (drift {drift:.1%})",
                )
            )

        return intents

    def _validated_targets(self, raw_targets: object) -> dict[str, float]:
        if not isinstance(raw_targets, dict):
            raise ValueError("target_weights must be a mapping of symbol to weight")

        targets: dict[str, float] = {}
        for raw_symbol, raw_weight in raw_targets.items():
            symbol = str(raw_symbol).upper()
            weight = float(raw_weight)
            if not math.isfinite(weight) or weight < 0:
                raise ValueError(f"Invalid target weight for {symbol}: {raw_weight}")
            targets[symbol] = weight

        total_weight = sum(targets.values())
        if total_weight > 1.0 + 1e-9:
            raise ValueError(f"Target weights must sum to 100% or less, got {total_weight:.2%}")
        return targets

    def _price_from_position(self, position: Position | None) -> float | None:
        if position is None or not position.market_value or not position.quantity:
            return None
        return abs(position.market_value / position.quantity)
