# Indian Forex module integration

The Forex-only engine is embedded in the existing APEX backend and mounted at `/api/indian-forex`. It does not create another Render service or web process.

The parent FastAPI app adds `backend/indian_forex/src` to the import path, mounts `apex_forex.api.main:app`, and enters the Forex sub-app lifespan inside the parent lifespan.

Environment variables:
- `OANDA_ACCESS_TOKEN` and `OANDA_ACCOUNT_ID` for OANDA practice only.
- `OANDA_ENVIRONMENT=practice`.
- `MT5_BRIDGE_TOKEN` shared with the MT5 Expert Advisor.
- Existing `MONGODB_URI` / `MONGODB_DATABASE` for durable Forex paper trades (dedicated `indian_forex_paper_trades` collection).
- `FOREX_DATABASE_URL` for SQLite fallback when MongoDB is not configured.

No live broker order-placement API is exposed. SQLite on Render's ephemeral filesystem is not durable across instance replacement.
