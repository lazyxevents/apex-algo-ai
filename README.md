# APEX Algo AI

APEX Algo AI is a **risk-first options trading research and paper-trading platform** based on the supplied master specification. This repository intentionally keeps the codebase compact while preserving the critical design rule: **live orders are disabled by default and are not implemented in this MVP**.

## What is included

- FastAPI backend
- PostgreSQL via Docker (SQLite fallback for simple local development)
- Zerodha Kite Connect login/session/profile adapter
- Paper trading order endpoint
- Whole-lot risk-based position sizing
- Daily trade-count and concurrent-position limits
- Daily loss lock
- Emergency kill switch
- Trade persistence + audit logs
- React/Vite dashboard
- Docker Compose
- Basic pytest test
- Git-ready repository

## Architecture

```text
Browser / React Dashboard
          |
          | REST
          v
     FastAPI API
          |
   +------+--------+----------------+
   |               |                |
Risk Engine    Paper Engine    Zerodha Adapter
   |               |                |
   +-------+-------+                |
           |                        |
       PostgreSQL              Kite Connect

Safety flow for any future execution:
Signal -> Strategy Validation -> Market/Liquidity Validation -> Risk Validation
       -> Position Size -> Kill Switch -> Execution Validation -> Order

Current MVP stops at PAPER execution.
```

## Project files (kept intentionally small)

```text
apex-algo-ai/
├── backend/
│   ├── app/
│   │   ├── main.py       # API endpoints
│   │   ├── core.py       # settings + DB
│   │   ├── models.py     # trade + audit tables
│   │   ├── trading.py    # risk + paper execution
│   │   └── kite.py       # Zerodha adapter
│   ├── tests/test_risk.py
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/
│   ├── src/App.tsx
│   ├── src/main.tsx
│   ├── src/styles.css
│   ├── package.json
│   ├── tsconfig.json
│   ├── index.html
│   └── Dockerfile
├── .env.example
├── .gitignore
├── docker-compose.yml
└── README.md
```

# 1. Fastest way to run (Docker)

Requirements:
- Docker Desktop
- Git

```bash
git clone https://github.com/lazyxevents/apex-algo-ai.git
cd apex-algo-ai
cp .env.example .env

docker compose up --build
```

Open:
- Dashboard: `http://localhost:5173`
- Backend: `http://localhost:8000`
- Swagger API: `http://localhost:8000/docs`
- Health: `http://localhost:8000/api/health`

Stop:

```bash
docker compose down
```

Delete local PostgreSQL volume too:

```bash
docker compose down -v
```

# 2. Local run without Docker

## Backend

Python 3.12 recommended.

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cd ..
cp .env.example .env
```

For a simple local run, change in `.env`:

```env
DATABASE_URL=sqlite:///./apex.db
```

Then:

```bash
cd backend
uvicorn app.main:app --reload --port 8000
```

## Frontend

In another terminal:

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`.

# 3. `.env` explained

Never commit `.env`.

### App

```env
APP_NAME=APEX Algo AI
APP_ENV=development
CORS_ORIGINS_RAW=http://localhost:5173
```

### Database

Docker:

```env
DATABASE_URL=postgresql+psycopg://apex:apex@db:5432/apex
```

Simple local development:

```env
DATABASE_URL=sqlite:///./apex.db
```

For production, replace username/password/database/host with strong private values.

### Trading safety

```env
TRADING_MODE=OFF
CAPITAL=20000
MAX_RISK_PER_TRADE=200
MAX_DAILY_LOSS=400
MAX_WEEKLY_LOSS=800
MAX_MONTHLY_DRAWDOWN=2000
MAX_TRADES_PER_DAY=2
MAX_CONCURRENT_POSITIONS=1
ALLOW_LIVE_ORDERS=false
```

`ALLOW_LIVE_ORDERS=false` must stay false for this MVP. The code intentionally has no live-order endpoint.

### Zerodha Kite Connect

```env
KITE_API_KEY=
KITE_API_SECRET=
KITE_ACCESS_TOKEN=
```

Where they come from:
1. Create a Kite Connect app from Zerodha's developer platform.
2. Add the API key and API secret.
3. Start backend.
4. Open `GET /api/kite/login-url`.
5. Open returned Zerodha login URL in your browser.
6. After successful login, Zerodha redirects to your configured redirect URL with a `request_token`.
7. Send that token to `POST /api/kite/session`:

```json
{
  "request_token": "TOKEN_FROM_REDIRECT"
}
```

8. The endpoint returns the session/access token. For development you may place the current access token in `.env` and restart.
9. Check `GET /api/kite/profile`.

Do not commit API key secrets or access tokens.

# 4. Trading flow in this MVP

System starts in `OFF`.

```text
OFF
  |
  | manual API/dashboard action
  v
PAPER
  |
  | paper order request
  v
Risk checks
  |-- kill switch active? -> reject
  |-- max trades/day reached? -> reject
  |-- open-position limit reached? -> reject
  |-- daily loss lock reached? -> reject
  |-- valid whole-lot quantity? -> reject if invalid
  |-- per-trade rupee risk fits? -> reject if not
  v
Paper Trade OPEN
  |
  | mark LTP
  +--> <= stop   -> STOPPED
  +--> >= target -> TARGET
  +--> otherwise -> OPEN
```

The project deliberately treats **NO TRADE** as valid behavior.

# 5. Try a paper trade

First put system in PAPER mode:

```bash
curl -X POST http://localhost:8000/api/system/mode \
  -H 'Content-Type: application/json' \
  -d '{"mode":"PAPER"}'
```

Then create a paper option-premium trade:

```bash
curl -X POST http://localhost:8000/api/paper/orders \
  -H 'Content-Type: application/json' \
  -d '{
    "symbol":"NIFTY26OCT25000CE",
    "direction":"CE",
    "entry":100,
    "stop":98,
    "target":106,
    "lot_size":25,
    "reason":"manual breakout test"
  }'
```

The backend calculates the maximum whole-lot quantity allowed by `MAX_RISK_PER_TRADE` unless you explicitly provide a smaller valid whole-lot quantity.

Mark price for trade `1`:

```bash
curl -X POST 'http://localhost:8000/api/paper/mark/1?ltp=106'
```

List trades:

```bash
curl http://localhost:8000/api/trades
```

# 6. Kill switch

Activate immediately:

```bash
curl -X POST http://localhost:8000/api/risk/kill-switch
```

New paper trades are blocked.

Reset manually:

```bash
curl -X POST http://localhost:8000/api/risk/reset-kill-switch
```

Reset intentionally returns the system to `OFF`, not PAPER.

# 7. Tests

```bash
cd backend
pytest -q
```

# 8. Git / GitHub setup

Repository:

```bash
git clone https://github.com/lazyxevents/apex-algo-ai.git
cd apex-algo-ai
```

Recommended workflow:

```bash
git checkout -b develop
git checkout -b feature/market-data
# make changes
git add .
git commit -m "feat: add market data service"
git push -u origin feature/market-data
```

# 9. What is NOT faked / NOT implemented yet

This repository is a runnable foundation/MVP, not a claim that the entire production trading platform is finished.

Not fabricated here:
- Historical expired-option data
- Option-chain Greeks where no reliable data source is configured
- FinBERT/news feed
- Historical options backtester
- Live WebSocket tick-to-candle pipeline
- Automated strike selection
- Automatic strategy signal generation
- Partial fills / broker reconciliation
- Weekly/monthly loss-window calculations beyond the stored configuration
- Live broker order placement

Those belong to the next development phases and must be built/tested against real broker/data capabilities.

# 10. Recommended next implementation order

```text
1. Zerodha instrument sync
2. KiteTicker live WebSocket service
3. Candle builder + stale-data detector
4. One deterministic strategy version (APEX_BREAKOUT_V1)
5. Option contract selector
6. Expanded risk engine (weekly/monthly/session locks)
7. Realistic paper fill/slippage engine
8. Restart recovery + broker reconciliation
9. Historical data importer/backtester
10. Dashboard scanner/options/backtest pages
11. News/event filter
12. Extended paper validation
13. Only after validation: separately designed limited live adapter
```

# 11. Production/VPS outline

For a VPS:

```text
Ubuntu VPS
  -> Docker Engine
  -> PostgreSQL private volume
  -> FastAPI backend
  -> React frontend/build
  -> Nginx reverse proxy
  -> HTTPS
  -> monitoring + backups
```

Keep `.env` only on the server and never inside Git.

# Important safety rule

This project starts in `OFF`, supports `PAPER`, and deliberately does **not** expose live-order placement. A trading strategy should not be judged by win rate alone. Backtesting must include realistic fees/slippage and option-specific historical data; do not fabricate expired-option prices or treat index-only results as a valid options execution backtest.
