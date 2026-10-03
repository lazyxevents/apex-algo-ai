# APEX Algo AI — Advanced Adaptive Paper Engine

APEX is an automated **Indian index-options research and paper-trading system** for NIFTY, BANK NIFTY and SENSEX. It is intentionally paper-only: **there is no live-order mode in this version**.

## Current engine flow

```text
Market data
  → concurrent NIFTY / BANKNIFTY / SENSEX scan
  → market regime/context detection
  → contextual reinforcement-bandit chooses approved strategy
  → EMA + RSI + breakout + volume + ATR
  → candlestick confirmation (pin bar / engulfing / breakout / strong body)
  → NO TRADE / CE / PE
  → nearest expiry
  → ATM + near-OTM candidate ranking
  → delta + spread + volume + affordability
  → actual lot size from broker instrument master
  → quantity from effective capital + risk budget
  → internal paper order / optional Upstox Sandbox mirror
  → SL + protected trailing + target + scheduled exit
  → P&L / win rate / expectancy / profit factor / drawdown
  → reinforcement reward update
  → daily historical OOS research prior
```

`NO TRADE` is always a valid decision.

## Important behavior

### Capital scales automatically

Default:

```env
CAPITAL=20000
AUTO_COMPOUND_PROFITS=true
RISK_PER_TRADE_PCT=1.0
```

The bot calculates effective paper capital as base capital plus realized paper P&L. If you later change:

```env
CAPITAL=40000
```

risk budgets and valid whole-lot quantities scale from that capital. The bot **never invents NIFTY/BANKNIFTY/SENSEX lot sizes**; it reads `lot_size` / `minimum_lot` from the option contract master.

If you want an absolute ceiling even after profit:

```env
MAX_DEPLOYABLE_CAPITAL=50000
```

`0` means no extra ceiling.

### Monthly 20% target lock

```env
MONTHLY_PROFIT_TARGET_PCT=20
MONTHLY_TARGET_LOCK=true
```

The target is based on **month-start paper equity**. Example: month begins at ₹20,000 → target amount is ₹4,000. If prior realized paper profits make next month's starting equity ₹24,000, the next target is ₹4,800.

When the target is reached:

```text
new trades blocked
→ any remaining paper position force-flattened
→ mode becomes SAFE
→ every automation cycle re-checks the target
→ restart cannot bypass it
→ next calendar month gets a fresh target
```

**20% is a target/stop condition, not a promised or expected monthly return.**

### Risk locks

Default dynamic limits:

```env
RISK_PER_TRADE_PCT=1
MAX_DAILY_LOSS_PCT=2
MAX_WEEKLY_LOSS_PCT=4
MAX_MONTHLY_DRAWDOWN_PCT=10
MAX_TRADES_PER_DAY=2
MAX_CONCURRENT_POSITIONS=1
```

A new trade must fit both:

```text
rupee-risk budget
AND
premium cash budget
AND
actual exchange lot size
```

One valid lot does not fit → `NO TRADE`.

## Strategies

Approved arms:

```text
APEX_BREAKOUT_BALANCED
APEX_BREAKOUT_FAST
APEX_BREAKOUT_STABLE
APEX_SCALP_MOMENTUM
```

The scalp arm is only eligible when:

```env
ALLOW_SCALP_STRATEGY=true
MIN_CAPITAL_FOR_SCALP=30000
```

and the market context is not flat/low-volatility. Capital does **not** randomly choose a strategy; market regime + learned reward decide the arm. Capital decides affordability and quantity.

## Candlestick confirmation

Enabled by default:

```env
CANDLE_CONFIRMATION_REQUIRED=true
```

The signal engine recognizes:

- bullish / bearish pin bar
- bullish / bearish engulfing
- breakout / breakdown close
- strong green / red body

Trend/momentum alone is not enough when confirmation is required.

## Reinforcement-style learning

The learner is now **contextual**, not just global.

Context includes:

```text
trend: UP / DOWN / FLAT
volatility: HIGH / NORMAL / LOW
time: OPEN / MID / LATE
candlestick bias: BULL / BEAR / NONE
```

For each closed automated paper trade:

```text
reward = realized P&L / initial rupee risk
```

The learner updates both:

```text
global strategy Q-value
context-specific strategy Q-value
```

It also keeps controlled exploration:

```env
EXPLORATION_RATE=0.10
LEARNING_RATE=0.08
MINIMUM_LEARNING_TRADES=20
```

It cannot rewrite the kill switch, capital rules, loss locks, lot sizing, time windows or live authorization.

## Daily research / backtesting

At/after:

```env
DAILY_RESEARCH_TIME=15:45
```

APEX fetches historical index candles and runs an out-of-sample validation of each approved strategy arm:

```env
DAILY_RESEARCH_ENABLED=true
BACKTEST_LOOKBACK_DAYS=30
BACKTEST_MAX_HOLD_BARS=6
BACKTEST_ATR_STOP_MULT=1.0
RESEARCH_PRIOR_WEIGHT=0.20
```

This result is used as a bounded research prior for strategy selection.

**Important:** this daily research validates underlying directional signals; it is **not** presented as a full historical options-P&L backtest. A true options backtest still needs historical option premiums, spread/slippage and fees.

## Trading time

NSE equity derivatives regular session is currently 09:15–15:40 IST. APEX deliberately uses a narrower risk window:

```env
TRADE_START_TIME=09:20
STOP_NEW_TRADE_TIME=15:00
FORCE_EXIT_TIME=15:10
```

So it waits after the open, stops taking new risk early, and attempts to flatten well before exchange close.

## Exit resilience

```env
FORCE_EXIT_RETRY_COUNT=3
FORCE_EXIT_RETRY_DELAY_SECONDS=1
```

For kill switch / scheduled flatten:

1. Retry fresh quote.
2. If quote still fails, close the **internal paper position** at the last known paper price so the simulator cannot remain logically stuck forever.
3. If Upstox Sandbox is enabled, retry the Sandbox SELL separately and audit its result.

This is appropriate for paper/sandbox mode. A future real-money adapter will require broker reconciliation and must not assume an internal close means the broker position is closed.

## Upstox setup

Official developer portal:

- https://upstox.com/developer/api-documentation/
- Historical V3: https://upstox.com/developer/api-documentation/v3/get-historical-candle-data/
- Intraday V3: https://upstox.com/developer/api-documentation/v3/get-intra-day-candle-data/
- Option contracts: https://upstox.com/developer/api-documentation/get-option-contracts/
- Option chain: https://upstox.com/developer/api-documentation/get-pc-option-chain/
- Sandbox: https://upstox.com/developer/api-documentation/sandbox/
- Place Order V3: https://upstox.com/developer/api-documentation/v3/place-order/
- Cancel Order V3: https://upstox.com/developer/api-documentation/v3/cancel-order/

Market data:

```env
UPSTOX_ACCESS_TOKEN=YOUR_MARKET_DATA_TOKEN
```

Start with pure internal paper simulation:

```env
PAPER_BROKER=internal
```

Optional Sandbox mirror:

```env
PAPER_BROKER=upstox_sandbox
UPSTOX_SANDBOX_TOKEN=YOUR_SANDBOX_TOKEN
```

Zerodha variables remain reserved for the later validated live-integration phase.

## Run

If already cloned:

```bash
git pull origin main
```

If `.env` does not exist:

```bash
cp .env.example .env
```

Minimum initial configuration:

```env
TRADING_MODE=PAPER
AUTO_TRADING_ENABLED=true
CAPITAL=20000
UPSTOX_ACCESS_TOKEN=...
PAPER_BROKER=internal
```

Run:

```bash
docker compose up --build
```

Open:

```text
Dashboard  http://localhost:5173
Swagger    http://localhost:8000/docs
Health     http://localhost:8000/api/health
```

## Important endpoints

```text
GET  /api/system/status
GET  /api/risk/status
GET  /api/performance
GET  /api/strategy/status
GET  /api/trades
POST /api/automation/run-once
POST /api/research/run-once
POST /api/risk/flatten
POST /api/risk/kill-switch
POST /api/risk/reset-kill-switch
```

`/api/risk/kill-switch` now also force-flattens open internal paper positions.

## Tests

```bash
cd backend
pytest -q
```

## What is still intentionally pending

Before any real-money mode:

- full historical **option-premium** backtester
- realistic brokerage/taxes/slippage calibration
- walk-forward across multiple market regimes with much larger sample sizes
- exchange holiday calendar integration
- WebSocket tick engine / candle builder
- news/event lockout and optional sentiment layer
- persistent broker reconciliation / idempotent live order state machine
- extended paper validation
- limited live adapter with explicit manual enablement

A bot becoming more adaptive does not guarantee profitability. The current system is designed to learn within bounded paper-trading rules while keeping deterministic risk controls above the learning layer.
