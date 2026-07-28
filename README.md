- 👋 Hi, I’m @Kenclarke22
- 👀 I’m interested in building companies
- 🌱 I’m currently learning code
- 💞️ I’m looking to collaborate on creating disruptive and new products
- 📫 How to reach me clarkeken22@gmail.com

---

## Master trading agent

Automated stock and options orchestrator that drives an external order execution tool at **`http://localhost:3000`**.

### Quick start

```bash
pip install -r requirements.txt
cp .env.example .env

# Terminal 1 — your execution tool OR reference mock:
python -m trading_agent.mock_execution_server

# Terminal 2 — agent
python -m trading_agent.main status
python -m trading_agent.main run-once --metadata examples/strategy_metadata.json
```

### CLI

| Command | Description |
|---------|-------------|
| `status` | Health, account, positions from execution tool |
| `run-once` | Single perceive → strategize → risk → execute cycle |
| `run` | Continuous loop (`AGENT_CYCLE_SECONDS`) |
| `submit` | Manual order through risk checks |

### Architecture

```
Strategies → Master Agent → Risk Manager → Execution Client → localhost:3000
```

See [AGENTS.md](AGENTS.md) for API contract, env vars, and development notes.

<!---
Kenclarke22/Kenclarke22 is a ✨ special ✨ repository because its `README.md` (this file) appears on your GitHub profile.
You can click the Preview link to take a look at your changes.
--->
