# AGENTS.md

## Project overview

This repository contains a **master stock/options trading agent** that orchestrates strategies, applies risk checks, and submits orders to an external **order execution tool** at `http://localhost:3000`.

| Component | Path | Purpose |
|-----------|------|---------|
| Master agent | `trading_agent/agent/orchestrator.py` | Perceive → strategize → risk → execute loop |
| Execution client | `trading_agent/execution/client.py` | HTTP adapter to localhost:3000 |
| Risk manager | `trading_agent/risk/manager.py` | Live-trading guardrails |
| Strategies | `trading_agent/strategies/` | Pluggable signal modules |
| Reference executor | `trading_agent/mock_execution_server.py` | Dev/demo API on port 3000 |

## Cursor Cloud specific instructions

### Services

| Service | Required | How to start |
|---------|----------|--------------|
| Order execution tool | **Yes** | Your tool at `http://localhost:3000`, or reference mock: `python -m trading_agent.mock_execution_server` |
| Master agent | **Yes** | `python -m trading_agent.main run-once` or `run` |

Start the execution tool **before** the agent. The agent will fail fast if `:3000` is unreachable.

### Environment

Copy `.env.example` to `.env`. Key variables:

- `EXECUTION_BASE_URL` — default `http://localhost:3000`
- `EXECUTION_API_KEY` — optional Bearer token for your execution API
- `TRADING_MODE=live` — v1 uses live mode (risk limits still enforced)
- `RISK_STATE_PATH` — local JSON state used to persist the daily order counter across `run-once` or CLI processes

### Commands

```bash
pip install -r requirements.txt
python -m trading_agent.mock_execution_server   # terminal 1 (if no real tool)
python -m trading_agent.main status
python -m trading_agent.main run-once --metadata examples/strategy_metadata.json
python -m trading_agent.main submit AAPL --side buy --quantity 1 --order-type limit --limit-price 190
pytest tests/ -q
```

### Execution API contract

The client probes these paths (first match wins):

- `GET /health`, `GET /api/account`, `GET /api/positions`, `GET /api/orders`
- `POST /api/orders`, `DELETE /api/orders/{id}`

See `trading_agent/mock_execution_server.py` for the reference payload shape. Align your real execution tool to this contract or extend `ExecutionClient`.

### Gotchas

- **Live trading**: orders are submitted for real when pointed at a live execution tool. Risk limits in `.env` are enforced but are not a substitute for broker-level controls.
- Keep `RISK_STATE_PATH` on persistent storage for cron/systemd `run-once` deployments; deleting it resets the local `MAX_DAILY_ORDERS` counter.
- The agent does **not** fetch market data by default; strategies need quotes via `--metadata` JSON or future data providers.
- `cmd_submit` for options requires `--underlying`, `--expiry`, `--strike`, `--right`.
