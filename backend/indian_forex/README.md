# APEX Indian Forex module

This Forex engine is embedded inside the existing APEX backend. It does **not** run as a second Render service.

The existing FastAPI application mounts it under:

- Swagger: `https://apex-algo-backend.onrender.com/api/indian-forex/docs`
- Health: `/api/indian-forex/health`
- MT5 quote ingest: `/api/indian-forex/api/v1/mt5/ingest`
- MT5 status: `/api/indian-forex/api/v1/mt5/status`
- Strategy catalogue: `/api/indian-forex/api/v1/research/strategies`
- Agent role catalogue: `/api/indian-forex/api/v1/research/agents`

## MT5 bridge

Compile `mt5/Experts/APEX_MT5_MarketBridge.mq5` in MetaEditor and attach it to an MT5 chart. Set the endpoint to:

`https://apex-algo-backend.onrender.com/api/indian-forex/api/v1/mt5/ingest`

The EA sends sampled Market Watch bid/ask snapshots. It does not place, modify or close broker orders. Add the APEX host to MT5's WebRequest allowlist.

## Providers and safety

- OANDA v20 is practice-only and needs `OANDA_ACCESS_TOKEN` and `OANDA_ACCOUNT_ID`.
- MT5 needs `MT5_BRIDGE_TOKEN` configured on Render and the same value in the EA.
- The module reuses the existing APEX `MONGODB_URI` / `MONGODB_DATABASE` for its own `indian_forex_paper_trades` collection. SQLite is a local fallback only.
- Broker order execution remains disabled. This is not a validated profitable trading system.

Indian residents must independently verify the legal eligibility of any provider, instrument and venue before funding an account.
