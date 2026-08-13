"""Example strategy: maintain target stock weights from strategy metadata."""

from __future__ import annotations

from trading_agent.models.orders import AssetClass, OrderIntent, OrderSide, OrderType
from trading_agent.strategies.base import Strategy, StrategyContext


class RebalanceStrategy(Strategy):
    """
    Reads target weights from ctx.metadata['target_weights'], e.g.:
    {"AAPL": 0.5, "MSFT": 0.5}
    Emits limit orders when drift exceeds threshold.
    """

    name = "rebalance"

    def generate_intents(self, ctx: StrategyContext) -> list[OrderIntent]:
        targets: dict[str, float] = ctx.metadata.get("target_weights", {})
        if not targets or ctx.account.equity is None or ctx.account.equity <= 0:
            return []

        threshold = float(ctx.metadata.get("rebalance_threshold", 0.05))
        intents: list[OrderIntent] = []

        position_map = {p.symbol: p for p in ctx.positions if p.asset_class == AssetClass.STOCK}
        equity = ctx.account.equity
        total_target_weight = sum(max(float(weight), 0.0) for weight in targets.values())
        if total_target_weight <= 0:
            return []

        non_stock_value = sum(
            abs(position.market_value or 0.0)
            for position in ctx.positions
            if position.asset_class != AssetClass.STOCK
        )
        stock_budget = max(equity - non_stock_value, 0.0)
        target_stock_value = min(equity * total_target_weight, stock_budget)

        for symbol, target_weight in targets.items():
            symbol = symbol.upper()
            target_weight = max(float(target_weight), 0.0)
            if target_weight == 0:
                continue
            mark = ctx.market_quotes.get(symbol)
            if mark is None or mark <= 0:
                continue

            target_value = target_stock_value * (target_weight / total_target_weight)
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
