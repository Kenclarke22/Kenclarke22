from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from typing import Any

from rich.console import Console
from rich.table import Table

from trading_agent.agent.orchestrator import MasterTradingAgent
from trading_agent.config import get_settings
from trading_agent.execution.client import ExecutionClient
from trading_agent.models.orders import (
    AssetClass,
    OptionDetails,
    OptionRight,
    OrderIntent,
    OrderSide,
    OrderType,
)

console = Console()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)


def cmd_status(_: argparse.Namespace) -> int:
    settings = get_settings()
    with ExecutionClient(settings) as client:
        health = client.health()
        account = client.get_account()
        positions = client.get_positions()
        orders = client.get_orders()

    table = Table(title=f"Execution tool @ {settings.execution_base_url}")
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("Mode", settings.trading_mode)
    table.add_row("Health", json.dumps(health)[:120])
    table.add_row("Equity", str(account.equity))
    table.add_row("Buying power", str(account.buying_power))
    table.add_row("Positions", str(len(positions)))
    table.add_row("Open orders", str(len(orders)))
    console.print(table)
    return 0


def cmd_run_once(args: argparse.Namespace) -> int:
    settings = get_settings()
    metadata = _load_metadata(args.metadata)
    agent = MasterTradingAgent(settings=settings)
    try:
        result = agent.run_cycle(strategy_metadata=metadata)
    finally:
        agent.close()

    _print_cycle_result(result)
    return 0 if result.success else 1


def cmd_run_loop(args: argparse.Namespace) -> int:
    settings = get_settings()
    metadata = _load_metadata(args.metadata)
    agent = MasterTradingAgent(settings=settings)
    try:
        while True:
            result = agent.run_cycle(strategy_metadata=metadata)
            _print_cycle_result(result)
            time.sleep(settings.agent_cycle_seconds)
    except KeyboardInterrupt:
        console.print("\nStopped.")
        return 0
    finally:
        agent.close()


def cmd_submit(args: argparse.Namespace) -> int:
    settings = get_settings()
    option_details = None
    if args.asset_class == "option":
        if not all([args.underlying, args.expiry, args.strike, args.right]):
            console.print("[red]Options require --underlying, --expiry, --strike, --right[/red]")
            return 1
        from datetime import date

        option_details = OptionDetails(
            underlying=args.underlying.upper(),
            expiry=date.fromisoformat(args.expiry),
            strike=args.strike,
            right=OptionRight(args.right),
        )

    intent = OrderIntent(
        symbol=args.symbol.upper(),
        asset_class=AssetClass(args.asset_class),
        side=OrderSide(args.side),
        quantity=args.quantity,
        order_type=OrderType(args.order_type),
        limit_price=args.limit_price,
        option_details=option_details,
        strategy_name="manual_cli",
        rationale=args.rationale or "Manual CLI submission",
    )

    agent = MasterTradingAgent(settings=settings)
    try:
        account = agent.execution.get_account()
        positions = agent.execution.get_positions()
        decision = agent.risk.evaluate(
            intent,
            account=account,
            positions=positions,
            mark_price=args.mark_price if args.mark_price is not None else args.limit_price,
        )
        if not decision.approved:
            console.print(f"[red]Rejected: {decision.reason}[/red]")
            return 1
        response = agent.execution.submit_order(decision.adjusted_intent or intent)
        console.print(f"[green]Submitted order {response.id} status={response.status.value}[/green]")
        return 0
    finally:
        agent.close()


def _load_metadata(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _print_cycle_result(result) -> None:
    console.print(
        f"Cycle {result.started_at.isoformat()} → proposed={len(result.proposed)} "
        f"approved={len(result.approved)} submitted={len(result.submitted)} "
        f"rejected={len(result.rejected)} errors={len(result.errors)}"
    )
    for intent, reason in result.rejected:
        console.print(f"  [yellow]reject[/yellow] {intent.display_symbol}: {reason}")
    for response in result.submitted:
        console.print(f"  [green]submit[/green] {response.id} {response.symbol} {response.status.value}")
    for err in result.errors:
        console.print(f"  [red]error[/red] {err}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Master stock/options trading agent")
    sub = parser.add_subparsers(dest="command", required=True)

    status = sub.add_parser("status", help="Show execution tool and account status")
    status.set_defaults(func=cmd_status)

    once = sub.add_parser("run-once", help="Run a single agent cycle")
    once.add_argument("--metadata", help="JSON file with strategy metadata (quotes, targets)")
    once.set_defaults(func=cmd_run_once)

    loop = sub.add_parser("run", help="Run agent loop")
    loop.add_argument("--metadata", help="JSON file with strategy metadata")
    loop.set_defaults(func=cmd_run_loop)

    submit = sub.add_parser("submit", help="Submit a single order through risk checks")
    submit.add_argument("symbol")
    submit.add_argument("--asset-class", choices=["stock", "option"], default="stock")
    submit.add_argument("--side", choices=["buy", "sell"], required=True)
    submit.add_argument("--quantity", type=float, required=True)
    submit.add_argument("--order-type", choices=["market", "limit"], default="market")
    submit.add_argument("--limit-price", type=float)
    submit.add_argument("--mark-price", type=float, help="Current market price used only for risk checks")
    submit.add_argument("--underlying")
    submit.add_argument("--expiry")
    submit.add_argument("--strike", type=float)
    submit.add_argument("--right", choices=["call", "put"])
    submit.add_argument("--rationale", default="")
    submit.set_defaults(func=cmd_submit)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
