# APEX Algo AI — Adaptive Paper Trading Engine

APEX is an automated Indian index research/paper-trading project for NIFTY, BANK NIFTY and SENSEX. Live orders are intentionally disabled in the current build.

## Fastest demo: no account, PAN/KYC or token

The default demo provider is now **Yahoo/yfinance**.

```bash
git pull origin main
cp .env.demo .env
docker compose down
docker compose up --build
```

Open:

```text
Dashboard  http://localhost:5173
Swagger    http://localhost:8000/docs
Health     http://localhost:8000/api/health
```

`.env.demo` already contains:

```env
TRADING_MODE=PAPER
AUTO_TRADING_ENABLED=true
MARKET_DATA_PROVIDER=yfinance
PAPER_BROKER=internal
CAPITAL=20000

UPSTOX_ACCESS_TOKEN=
UPSTOX_SANDBOX_TOKEN=
KITE_API_KEY=
KITE_API_SECRET=
KITE_ACCESS_TOKEN=
```

**No token is required in yfinance mode.**

If you already have a local `.env`, remember that `git pull` does not replace it because `.env` is gitignored. Either edit it manually or, for a clean demo configuration, back it up and copy `.env.demo`.

## What Yahoo demo mode tests

Yahoo supplies NIFTY/BANKNIFTY/SENSEX index candles. APEX still runs:

```text
EMA / RSI / ATR
+ breakout
+ candlestick confirmation
+ market regime/context
+ contextual reinforcement learning
+ capital/risk sizing
+ SL / target / trailing
+ forced exit
+ daily research
+ P&L / win rate / expectancy / drawdown
```

Candlestick recognition currently includes:

- bullish/bearish pin bar
- bullish/bearish engulfing
- breakout/breakdown close
- strong green/red body

Approved strategy arms:

```text
APEX_BREAKOUT_BALANCED
APEX_BREAKOUT_FAST
APEX_BREAKOUT_STABLE
APEX_SCALP_MOMENTUM
```

Closed paper trades update the global and context-specific strategy Q-values using:

```text
reward = realized paper P&L / initial paper risk
```

The context includes trend, volatility, time bucket and candlestick bias.

## Important Yahoo demo limitation

Yahoo mode **does not provide the same real NSE option-chain execution model as a broker feed**.

Therefore the paper option in this mode is explicitly marked:

```text
syntheticDemo=true
```

APEX creates a synthetic paper option premium from the underlying index and updates it using a fixed demo delta. The demo lot size defaults to `1` paper unit.

So this week can validate:

- bot automation
- indicator/candle decisions
- RL learning flow
- capital/risk rules
- kill/forced exit
- reporting
- stability

It cannot prove:

- actual NSE option premium fills
- real exchange lot-size affordability
- real bid/ask spread
- true Greeks/IV
- slippage/brokerage accuracy

Do not compare Yahoo synthetic P&L directly with real option returns.

## Capital and monthly lock

Default:

```env
CAPITAL=20000
RISK_PER_TRADE_PCT=1
AUTO_COMPOUND_PROFITS=true

MONTHLY_PROFIT_TARGET_PCT=20
MONTHLY_TARGET_LOCK=true
```

For a ₹20,000 month-start paper equity, 20% means a ₹4,000 target lock. This is a **target/stop rule, not a promised return**.

When the target is reached the engine blocks new trades, force-flattens remaining paper positions and moves to SAFE.

## Daily research

Default:

```env
DAILY_RESEARCH_ENABLED=true
DAILY_RESEARCH_TIME=15:45
BACKTEST_LOOKBACK_DAYS=30
```

The learner runs historical directional out-of-sample research on the approved strategy arms and uses the result as a bounded prior. Yahoo intraday history is used only within the provider's available intraday history window.

## Switching to real broker market data later

When you are ready for broker-grade options validation, set:

```env
MARKET_DATA_PROVIDER=upstox
PAPER_BROKER=internal
UPSTOX_ACCESS_TOKEN=...
```

The existing Upstox path then uses option contracts, option chain, actual contract lot size, liquidity/spread and Greeks.

Zerodha variables are reserved for the later real-money integration phase:

```env
KITE_API_KEY=
KITE_API_SECRET=
KITE_ACCESS_TOKEN=
```

Do not enable real-money execution after only a few demo trades. First validate the system over a larger paper/backtest sample and reconcile real option pricing/fees.

## Safety controls

Default trading window:

```env
TRADE_START_TIME=09:20
STOP_NEW_TRADE_TIME=15:00
FORCE_EXIT_TIME=15:10
```

Dynamic locks include per-trade risk, daily loss, weekly loss, monthly drawdown, maximum trades/day and maximum concurrent positions.

Important endpoints:

```text
GET  /api/system/status
GET  /api/risk/status
GET  /api/performance
GET  /api/strategy/status
GET  /api/learning/status
GET  /api/learning/dataset
POST /api/learning/run-once
POST /api/learning/evaluate-candidate
GET  /api/trades
POST /api/automation/run-once
POST /api/research/run-once
POST /api/risk/flatten
POST /api/risk/kill-switch
POST /api/risk/reset-kill-switch
```

## Tests

```bash
cd backend
pytest -q
```

## Stage 1 learning pipeline

The continuous worker now records structured 1-minute directional setup samples with EMA/RSI/ATR/volume, candlestick patterns and SMC context (BOS, CHOCH, liquidity sweep, FVG and fake breakout), plus target-before-stop labels, MAE/MFE and R-multiple outcome. Candidate evaluation uses chronological train/validation/out-of-sample splits.

The current candidate scorer is deliberately labelled a deterministic baseline, **not a trained neural network**. A neural model should only be promoted after the historical dataset is large enough, leakage checks pass, calibration is acceptable and walk-forward/OOS performance is stable.

Research/LLM output cannot place orders or override deterministic risk locks.

## Still pending before real-money mode

- full historical option-premium backtester
- realistic taxes/brokerage/slippage calibration
- longer walk-forward validation
- exchange holiday calendar
- resilient streaming/tick engine
- broker reconciliation/idempotent live order state
- news/event safeguards
- explicit limited-live activation gate

Adaptive learning improves selection only from the data it sees; it does not guarantee profitability.
