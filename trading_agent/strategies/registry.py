from trading_agent.strategies.base import Strategy

_REGISTRY: dict[str, Strategy] = {}


def register_strategy(strategy: Strategy) -> Strategy:
    _REGISTRY[strategy.name] = strategy
    return strategy


def get_registered_strategies(active_only: set[str] | None = None) -> list[Strategy]:
    strategies = list(_REGISTRY.values())
    if active_only is None:
        return strategies
    return [s for s in strategies if s.name in active_only]
