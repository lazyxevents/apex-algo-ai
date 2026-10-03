# APEX Algo AI — Adaptive Paper-Trading Engine

APEX is an automated Indian index-options research + paper-trading system. It scans NIFTY, BANK NIFTY and SENSEX, builds deterministic technical signals, selects a liquid option contract, sizes the position from configured capital, manages SL/target/trailing/time-exit, records performance, and adapts among pre-approved strategy variants from closed paper trades.

**No live broker order endpoint exists in this version. Zerodha is intentionally reserved for a later phase.**

## Automatic flow

~~~text
Backend starts
→ background scan every 60 sec
→ India time + stale-data + risk checks
→ Upstox intraday candles
→ adaptive strategy arm
→ EMA + RSI + breakout + volume + ATR scoring
→ NO TRADE / CE / PE
→ nearest expiry + option chain + Greeks
→ spread + volume + delta + ATM/near-OTM + affordability filters
→ dynamic whole-lot quantity from CAPITAL
→ optional Upstox Sandbox BUY
→ internal paper trade in PostgreSQL
→ monitor LTP
→ SL / trailing / target / time exit
→ optional Upstox Sandbox SELL
→ P&L + win rate + expectancy + profit factor + drawdown
→ learner updates strategy reward
~~~

NO TRADE is a valid outcome.

## Compact structure

~~~text
apex-algo-ai/
├── backend/app/
│   ├── main.py       FastAPI + background automation
│   ├── core.py       env/settings + DB
│   ├── models.py     Trade, AuditLog, StrategyState
│   ├── strategy.py   indicators, option selector, learning
│   ├── trading.py    risk, sizing, exits, analytics
│   ├── upstox.py     market data + Sandbox
│   └── kite.py       Zerodha reserved for later
├── frontend/
├── .env.example
├── docker-compose.yml
└── README.md
~~~

## APIs/accounts required now

### Upstox market data

Used for GET requests only: intraday candles, option contracts, option chain and quotes.

Official docs:
- https://upstox.com/developer/api-documentation/
- https://upstox.com/developer/api-documentation/v3/get-intra-day-candle-data/
- https://upstox.com/developer/api-documentation/get-option-contracts/
- https://upstox.com/developer/api-documentation/get-pc-option-chain/
- https://upstox.com/developer/api-documentation/get-full-market-quote-v3/

Add a valid market-data token:

~~~env
UPSTOX_ACCESS_TOKEN=YOUR_MARKET_DATA_TOKEN
~~~

This project never sends that token to a live order endpoint.

### Upstox Sandbox — optional but recommended

Docs:
- https://upstox.com/developer/api-documentation/sandbox/
- https://upstox.com/developer/api-documentation/v3/place-order/

After creating a Sandbox App and token:

~~~env
PAPER_BROKER=upstox_sandbox
UPSTOX_SANDBOX_TOKEN=YOUR_SANDBOX_TOKEN
UPSTOX_SANDBOX_PRODUCT=I
~~~

Without sandbox:

~~~env
PAPER_BROKER=internal
~~~

The complete strategy still runs internally; only the external sandbox order mirror is skipped.

## Setup

You already cloned the repo:

~~~bash
git pull origin main
cp .env.example .env
~~~

Edit .env once:

~~~env
TRADING_MODE=PAPER
AUTO_TRADING_ENABLED=true
CAPITAL=20000
UPSTOX_ACCESS_TOKEN=...
PAPER_BROKER=internal
~~~

Then run:

~~~bash
docker compose up --build
~~~

Open:

~~~text
Dashboard: http://localhost:5173
Swagger:   http://localhost:8000/docs
Health:    http://localhost:8000/api/health
~~~

After PAPER mode + market token are configured, normal scanning starts automatically with the backend.

For future code updates:

~~~bash
git pull origin main
docker compose down
docker compose up --build
~~~

## Capital and quantity scaling

CAPITAL is the hard maximum paper allocation.

Defaults:

~~~env
CAPITAL=20000
DYNAMIC_RISK_LIMITS=true
RISK_PER_TRADE_PCT=1
MAX_DAILY_LOSS_PCT=2
MAX_WEEKLY_LOSS_PCT=4
MAX_MONTHLY_DRAWDOWN_PCT=10
~~~

At ₹20,000 this is approximately:

~~~text
Risk / trade = ₹200
Daily lock   = ₹400
Weekly lock  = ₹800
Monthly lock = ₹2,000
~~~

If CAPITAL becomes 30000, percentage budgets scale to approximately ₹300 / ₹600 / ₹1,200 / ₹3,000 and whole-lot quantity can increase automatically.

Quantity must satisfy all three:

~~~text
risk budget
AND premium cash budget (CAPITAL_USAGE_PCT)
AND exchange lot size
~~~

If one valid lot does not fit, the result is NO TRADE. MIN_TRADING_CAPITAL blocks new entries below the configured minimum.

## Strategy

Approved variants:

~~~text
APEX_BREAKOUT_BALANCED
APEX_BREAKOUT_FAST
APEX_BREAKOUT_STABLE
~~~

Signals use:
- fast/slow EMA trend
- RSI momentum
- breakout/breakdown
- relative volume
- candle direction
- ATR volatility sanity check

Below SIGNAL_MIN_SCORE → NO TRADE.
Bullish valid setup → CE.
Bearish valid setup → PE.

No win rate is hard-coded or assumed.

## ATM / OTM decision

The selector considers ATM and up to MAX_OTM_STEPS near-OTM strikes and ranks them using:
- delta proximity
- bid/ask spread
- volume
- distance from spot
- premium affordability
- actual lot size from the instrument master

A cheaper OTM option can be selected when ATM is inefficient for the capital, but cheap illiquid/wide-spread contracts are rejected.

## Adaptive learning

The learner is a bounded epsilon-greedy multi-armed bandit.

For each closed automated paper trade:

~~~text
reward = realized P&L / initial rupee risk
~~~

The reward is clipped and updates only the strategy arm that generated that trade.

~~~env
ADAPTIVE_LEARNING_ENABLED=true
EXPLORATION_RATE=0.10
LEARNING_RATE=0.08
MINIMUM_LEARNING_TRADES=20
~~~

Learning can select among approved strategy variants, but it cannot change:
- kill switch
- capital limits
- daily/weekly/monthly locks
- lot-size rules
- trading hours
- live-order authorization
- core execution safety

## India market time behavior

NSE equity-derivatives regular trading currently opens at 09:15 IST. APEX waits until 09:20 by default.

~~~env
TRADE_START_TIME=09:20
STOP_NEW_TRADE_TIME=15:00
FORCE_EXIT_TIME=15:10
~~~

~~~text
before 09:20 → no entry
09:20–15:00  → strategy may enter
after 15:00  → no new entry
15:10+       → open paper trade time-exit
weekend      → no entry
stale data   → NO TRADE
~~~

On a holiday, missing/stale data also leads to NO TRADE.

## Exit logic

~~~env
OPTION_STOP_PCT=18
REWARD_RISK_RATIO=1.8
~~~

~~~text
initial stop = configured % below option premium
target       = risk distance × reward/risk ratio
50% progress → move stop to breakeven
75% progress → protect part of the move
SL hit       → exit
target hit   → exit
15:10        → time exit
~~~

No martingale and no averaging down.

## Performance

Dashboard/API tracks:
- trades / wins / losses
- win rate
- net P&L
- average win/loss
- expectancy
- profit factor
- max drawdown
- day/week/month P&L
- strategy Q-values and experiment counts

Endpoints:

~~~text
GET  /api/system/status
GET  /api/performance
GET  /api/risk/status
GET  /api/strategy/status
GET  /api/trades
POST /api/automation/run-once
~~~

## Tests

~~~bash
cd backend
pytest -q
~~~

Current tests cover dynamic whole-lot sizing and a NO TRADE strategy case.

## Current limitations

This is an automated paper engine, not a proven profitable system.

Still pending before any live phase:
- long historical option backtests
- out-of-sample and walk-forward validation
- realistic fees/slippage calibration
- exchange holiday calendar
- WebSocket feed (current version polls)
- news/event lockout
- FinBERT/news layer
- broker reconciliation
- Zerodha live adapter
- any live-order mode

A win rate after a small sample of paper trades is not statistically reliable.

## Zerodha later

Kite placeholders remain, but current automation does not use Zerodha.

Recommended progression:

~~~text
paper automation
→ historical option backtest
→ out-of-sample / walk-forward
→ extended paper validation
→ restart/failure tests
→ manual review
→ separate limited live adapter
~~~

Live trading must remain explicit and separate; better paper results must never automatically enable it.
