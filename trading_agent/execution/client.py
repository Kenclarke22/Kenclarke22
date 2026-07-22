from __future__ import annotations

import logging
from typing import Any

import httpx

from trading_agent.config import Settings, get_settings
from trading_agent.models.orders import OrderIntent, OrderResponse, OrderStatus
from trading_agent.models.portfolio import AccountSnapshot, Position

logger = logging.getLogger(__name__)


class ExecutionClientError(Exception):
    def __init__(self, message: str, status_code: int | None = None, body: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class ExecutionClient:
    """HTTP client for the automated order execution tool at localhost:3000."""

    _PAPER_EXECUTION_MODES = {"paper", "sandbox", "simulated", "simulation"}

    def __init__(self, settings: Settings | None = None, timeout: float = 15.0):
        self.settings = settings or get_settings()
        self._client = httpx.Client(
            base_url=self.settings.execution_base_url.rstrip("/"),
            timeout=timeout,
            headers=self._build_headers(),
        )

    def _build_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.settings.execution_api_key:
            headers["Authorization"] = f"Bearer {self.settings.execution_api_key}"
        return headers

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> ExecutionClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def health(self) -> dict[str, Any]:
        return self._get_json(["/health", "/api/health", "/"])

    def get_account(self) -> AccountSnapshot:
        data = self._get_json(["/api/account", "/account", "/api/v1/account"])
        return AccountSnapshot.model_validate(self._normalize_account(data))

    def get_positions(self) -> list[Position]:
        data = self._get_json(["/api/positions", "/positions", "/api/v1/positions"])
        items = data if isinstance(data, list) else data.get("positions", data.get("data", []))
        return [Position.model_validate(self._normalize_position(item)) for item in items]

    def get_orders(self, status: str | None = None) -> list[OrderResponse]:
        params = {"status": status} if status else None
        data = self._request_json("GET", ["/api/orders", "/orders", "/api/v1/orders"], params=params)
        items = data if isinstance(data, list) else data.get("orders", data.get("data", []))
        return [self._parse_order_response(item) for item in items]

    def submit_order(self, intent: OrderIntent) -> OrderResponse:
        self._assert_order_submission_allowed()
        payload = self._intent_to_payload(intent)
        logger.info("Submitting order: %s", payload)
        data = self._request_json(
            "POST",
            ["/api/orders", "/orders", "/api/v1/orders"],
            json_body=payload,
        )
        return self._parse_order_response(data)

    def _assert_order_submission_allowed(self) -> None:
        if self.settings.trading_mode != "paper":
            return

        reported_modes: list[str] = []
        try:
            reported_modes.extend(self._explicit_modes_from_payload(self.health()))
            account_payload = self._get_json(["/api/account", "/account", "/api/v1/account"])
            reported_modes.extend(self._explicit_modes_from_payload(account_payload))
        except ExecutionClientError as exc:
            raise ExecutionClientError(
                "Paper trading mode requires execution endpoint mode verification before submitting orders",
                status_code=exc.status_code,
                body=exc.body,
            ) from exc

        normalized_modes = {mode.lower() for mode in reported_modes if mode}
        if not normalized_modes:
            raise ExecutionClientError(
                "Paper trading mode requires execution endpoint to explicitly report paper or sandbox mode"
            )

        unsafe_modes = normalized_modes - self._PAPER_EXECUTION_MODES
        if unsafe_modes:
            modes = ", ".join(sorted(unsafe_modes))
            raise ExecutionClientError(
                f"Paper trading mode refuses to submit to execution endpoint reporting mode(s): {modes}"
            )

    @staticmethod
    def _explicit_modes_from_payload(data: Any) -> list[str]:
        modes: list[str] = []

        def collect(value: Any) -> None:
            if not isinstance(value, dict):
                return
            for key in ("mode", "trading_mode", "tradingMode", "environment", "env"):
                raw_mode = value.get(key)
                if isinstance(raw_mode, str) and raw_mode.strip():
                    modes.append(raw_mode.strip())
            for nested_key in ("account", "data"):
                collect(value.get(nested_key))

        collect(data)
        return modes

    def cancel_order(self, order_id: str) -> OrderResponse:
        data = self._request_json(
            "DELETE",
            [
                f"/api/orders/{order_id}",
                f"/orders/{order_id}",
                f"/api/v1/orders/{order_id}",
            ],
        )
        return self._parse_order_response(data)

    def _get_json(self, paths: list[str]) -> Any:
        return self._request_json("GET", paths)

    def _request_json(
        self,
        method: str,
        paths: list[str],
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        last_error: ExecutionClientError | None = None
        for path in paths:
            try:
                response = self._client.request(method, path, json=json_body, params=params)
                if response.status_code == 404:
                    continue
                if response.status_code >= 400:
                    raise ExecutionClientError(
                        f"{method} {path} failed",
                        status_code=response.status_code,
                        body=response.text,
                    )
                if not response.content:
                    return {}
                return response.json()
            except httpx.HTTPError as exc:
                last_error = ExecutionClientError(f"HTTP error on {method} {path}: {exc}")
                continue
        if last_error:
            raise last_error
        raise ExecutionClientError(
            f"No matching endpoint found for {method} {paths} on {self.settings.execution_base_url}"
        )

    def _intent_to_payload(self, intent: OrderIntent) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "symbol": intent.symbol,
            "asset_class": intent.asset_class.value,
            "side": intent.side.value,
            "quantity": intent.quantity,
            "order_type": intent.order_type.value,
            "time_in_force": intent.time_in_force,
            "client_order_id": intent.id,
            "strategy": intent.strategy_name,
            "rationale": intent.rationale,
            "metadata": intent.metadata,
            "mode": self.settings.trading_mode,
        }
        if intent.limit_price is not None:
            payload["limit_price"] = intent.limit_price
        if intent.stop_price is not None:
            payload["stop_price"] = intent.stop_price
        if intent.option_details is not None:
            od = intent.option_details
            payload["option_details"] = {
                "underlying": od.underlying,
                "expiry": od.expiry.isoformat(),
                "strike": od.strike,
                "right": od.right.value,
            }
        return payload

    def _parse_order_response(self, data: dict[str, Any]) -> OrderResponse:
        status_raw = str(data.get("status", "submitted")).lower()
        try:
            status = OrderStatus(status_raw)
        except ValueError:
            status = OrderStatus.SUBMITTED
        from trading_agent.models.orders import AssetClass, OrderSide

        asset_raw = data.get("asset_class", "stock")
        side_raw = data.get("side", "buy")
        return OrderResponse(
            id=str(data.get("id", data.get("order_id", ""))),
            status=status,
            symbol=str(data.get("symbol", "")).upper(),
            asset_class=AssetClass(asset_raw) if asset_raw in AssetClass._value2member_map_ else AssetClass.STOCK,
            side=OrderSide(side_raw) if side_raw in OrderSide._value2member_map_ else OrderSide.BUY,
            quantity=float(data.get("quantity", 0)),
            filled_quantity=float(data.get("filled_quantity", data.get("filled_qty", 0))),
            average_fill_price=data.get("average_fill_price", data.get("avg_fill_price")),
            message=data.get("message"),
            submitted_at=data.get("submitted_at"),
            raw=data,
        )

    def _normalize_account(self, data: dict[str, Any]) -> dict[str, Any]:
        nested = data.get("account", data)
        return {
            "equity": nested.get("equity", nested.get("portfolio_value")),
            "buying_power": nested.get("buying_power", nested.get("buyingPower")),
            "cash": nested.get("cash"),
            "day_pnl": nested.get("day_pnl", nested.get("dayPnl")),
            "mode": nested.get("mode", self.settings.trading_mode),
            "as_of": nested.get("as_of", nested.get("updated_at")),
            "raw": data,
        }

    def _normalize_position(self, item: dict[str, Any]) -> dict[str, Any]:
        option = item.get("option_details") or item.get("option")
        normalized: dict[str, Any] = {
            "symbol": item.get("symbol", ""),
            "asset_class": item.get("asset_class", item.get("assetClass", "stock")),
            "quantity": item.get("quantity", item.get("qty", 0)),
            "average_cost": item.get("average_cost", item.get("avg_cost")),
            "market_value": item.get("market_value", item.get("marketValue")),
            "unrealized_pnl": item.get("unrealized_pnl", item.get("unrealizedPnl")),
            "raw": item,
        }
        if option:
            normalized["option_details"] = option
        return normalized
