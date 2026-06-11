"""
Reference order execution API for local development and integration testing.

Run: python -m trading_agent.mock_execution_server

Implements the REST contract expected by ExecutionClient. Point your agent at
http://localhost:3000 or align your real execution tool to these endpoints.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="Order Execution Tool", version="0.1.0")

_orders: dict[str, dict[str, Any]] = {}
_positions: dict[str, dict[str, Any]] = {}
_account: dict[str, Any] = {
    "equity": 100_000.0,
    "buying_power": 100_000.0,
    "cash": 100_000.0,
    "day_pnl": 0.0,
    "mode": "live",
}


class OptionDetailsIn(BaseModel):
    underlying: str
    expiry: str
    strike: float
    right: str


class OrderIn(BaseModel):
    symbol: str
    asset_class: str = "stock"
    side: str
    quantity: float
    order_type: str = "market"
    limit_price: float | None = None
    stop_price: float | None = None
    time_in_force: str = "day"
    client_order_id: str | None = None
    strategy: str | None = None
    rationale: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    mode: str = "live"
    option_details: OptionDetailsIn | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "order-execution", "mode": "live"}


@app.get("/")
def root() -> dict[str, str]:
    return {"message": "Order execution tool", "docs": "/docs"}


@app.get("/api/account")
def get_account() -> dict[str, Any]:
    return {"account": {**_account, "as_of": _now()}}


@app.get("/api/positions")
def get_positions() -> dict[str, Any]:
    return {"positions": list(_positions.values())}


@app.get("/api/orders")
def list_orders() -> dict[str, Any]:
    return {"orders": list(_orders.values())}


@app.post("/api/orders")
def place_order(order: OrderIn) -> dict[str, Any]:
    order_id = str(uuid.uuid4())
    fill_price = order.limit_price or _mock_mark(order.symbol)
    multiplier = 100 if order.asset_class == "option" else 1
    notional = order.quantity * fill_price * multiplier

    if order.side == "buy" and notional > _account["buying_power"]:
        raise HTTPException(status_code=400, detail="Insufficient buying power")

    record = {
        "id": order_id,
        "status": "filled",
        "symbol": order.symbol.upper(),
        "asset_class": order.asset_class,
        "side": order.side,
        "quantity": order.quantity,
        "filled_quantity": order.quantity,
        "average_fill_price": fill_price,
        "message": f"Live fill @ {fill_price}",
        "submitted_at": _now(),
        "strategy": order.strategy,
        "rationale": order.rationale,
        "mode": order.mode,
        "option_details": order.option_details.model_dump() if order.option_details else None,
    }
    _orders[order_id] = record

    pos_key = order.symbol.upper()
    if order.asset_class == "option" and order.option_details:
        od = order.option_details
        pos_key = f"{od.underlying}:{od.expiry}:{od.strike}:{od.right}"

    signed_qty = order.quantity if order.side == "buy" else -order.quantity
    existing = _positions.get(pos_key)
    if existing:
        existing["quantity"] += signed_qty
        existing["market_value"] = abs(existing["quantity"]) * fill_price * multiplier
    else:
        _positions[pos_key] = {
            "symbol": order.symbol.upper(),
            "asset_class": order.asset_class,
            "quantity": signed_qty,
            "average_cost": fill_price,
            "market_value": abs(signed_qty) * fill_price * multiplier,
            "unrealized_pnl": 0.0,
            "option_details": order.option_details.model_dump() if order.option_details else None,
        }

    if order.side == "buy":
        _account["buying_power"] -= notional
        _account["cash"] -= notional
    else:
        _account["buying_power"] += notional
        _account["cash"] += notional
    _account["equity"] = _account["cash"] + sum(p.get("market_value", 0) for p in _positions.values())

    return record


@app.delete("/api/orders/{order_id}")
def cancel_order(order_id: str) -> dict[str, Any]:
    order = _orders.get(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if order["status"] == "filled":
        raise HTTPException(status_code=400, detail="Cannot cancel filled order")
    order["status"] = "cancelled"
    return order


def _mock_mark(symbol: str) -> float:
    marks = {"AAPL": 190.0, "MSFT": 420.0, "SPY": 520.0, "QQQ": 450.0}
    return marks.get(symbol.upper(), 100.0)


def run() -> None:
    import uvicorn

    uvicorn.run("trading_agent.mock_execution_server:app", host="0.0.0.0", port=3000, reload=False)


if __name__ == "__main__":
    run()
