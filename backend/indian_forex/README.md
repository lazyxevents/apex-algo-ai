# APEX Forex Engine

APEX is a Forex-focused research, market-data, backtesting and paper-trading system. It uses MetaTrader 5 as a broker-terminal market-data bridge and can also use OANDA v20 practice data. It is not yet a validated autonomous trading system.

## MetaTrader 5 integration

MT5 is a trading platform, not a broker or a single global Forex feed. The connected broker supplies the account, symbol catalogue, bid/ask prices, spreads, execution rules and historical data.

APEX includes an MQL5 Expert Advisor bridge at `mt5/Experts/APEX_MT5_MarketBridge.mq5`. It dynamically enumerates Market Watch symbols, samples bid/ask/last and broker timestamps for up to 100 symbols per batch, and sends snapshots to APEX over HTTPS every 500 ms. It authenticates with `X-APEX-MT5-KEY`. It does not place, modify or close broker orders.

This is a sampled quote bridge, not a guaranteed lossless tick recorder. Use MT5 Strategy Tester real-tick mode for historical tick-level EA testing when the broker has real tick history.

### Connect MT5

1. Run the APEX API on a reachable HTTPS host.
2. Set a long random `MT5_BRIDGE_TOKEN` in the APEX environment.
3. In MT5, open MetaEditor and compile `mt5/Experts/APEX_MT5_MarketBridge.mq5`.
4. In MT5: Tools → Options → Expert Advisors → enable WebRequest and allowlist the exact APEX HTTPS host.
5. Attach `APEX_MT5_MarketBridge` to any chart.
6. Set `InpApexEndpoint` to `https://YOUR-HOST/api/v1/mt5/ingest` and `InpBridgeSecret` to the same token.
7. Add desired broker Forex pairs to Market Watch.
8. Confirm `GET /api/v1/mt5/status` reports fresh symbols.

The terminal/EA runs on Windows or a Windows VPS with the broker's MT5 terminal installed. The cloud APEX API can run separately on Linux. The optional `MetaTrader5` Python package is Windows-only and talks to a locally installed terminal. A read-only adapter in `src/apex_forex/providers/mt5_terminal.py` exposes terminal status, account snapshot, Forex-only symbol discovery, historical ticks and candles; it deliberately has no `order_send` method. The cloud API does not require this package.

Install optional research dependencies on a Windows research machine with `pip install -e ".[dev,research,mt5]"`.

### MT5 and research API

- `POST /api/v1/mt5/ingest` — authenticated quote batches from the EA.
- `GET /api/v1/mt5/status` — connected symbols, latest bid/ask and quote age.
- `GET /api/v1/research/agents` — specialist role catalogue and task routing.
- `POST /api/v1/research/strategy/evaluate` — strategy metrics and promotion gates.
- `GET /api/v1/research/strategies` — list the five baseline Forex strategy candidates.
- `POST /api/v1/research/strategy/signal` — compute a research-only signal from supplied closed candles.
- `POST /api/v1/risk/position-size` — calculate lots/units using stop distance, pip size, round-trip cost buffer and quote-to-account conversion.
- `GET /api/v1/market/status` — unified normalized quote view.

A paper trade uses instrument key `MT5:EURUSD` (or the exact broker symbol uppercased).

## MT5 Strategy Tester

MT5's built-in tester supports multi-currency Expert Advisors, real-tick testing when broker history is available, parameter optimization and local/remote/cloud optimization agents. Use it for MQL5 execution simulation and parameter experiments. See the [Strategy Tester](https://www.metatrader5.com/en/terminal/help/algotrading/testing), [Real and Generated Ticks](https://www.metatrader5.com/en/terminal/help/algotrading/tick_generation), [Strategy Optimization](https://www.metatrader5.com/en/terminal/help/algotrading/strategy_optimization), and [Python integration](https://www.mql5.com/en/docs/python_metatrader5) documentation.

A broker's tick archive, symbol history and account permissions determine what is actually available. MT5 Strategy Tester agents are compute workers, not AI agents.

## Provider matrix

| Provider | Status | Current APEX use |
|---|---|---|
| MetaTrader 5 terminal | Bridge implemented; awaiting your terminal connection | Dynamic Market Watch quote snapshots into paper feed |
| OANDA v20 | Practice API adapter implemented | Accounts, instruments, bid/ask snapshots, stream and candles |
| Dukascopy JForex/FIX | Research candidate | No APEX adapter |
| Interactive Brokers TWS | Research candidate | No APEX adapter |
| FXCM FCLite | Research candidate | No APEX adapter |

Dhan, Kite and Upstox are excluded from this Forex engine.

## Research, libraries and strategy selection

Optional research dependencies are available via `pip install -e ".[research]"`: NumPy, pandas, scikit-learn, Optuna and backtesting.py. These are research tools; their presence does not mean every library is already wired into a production signal path.

The strategy evaluation endpoint reports win rate, profit factor, expectancy, drawdown, Sharpe-like score and out-of-sample expectancy. Initial promotion gates require at least 100 in-sample trades and 30 out-of-sample trades, positive net expectancy in both samples, profit factor >= 1.2 and maximum drawdown <= 12%.

These are initial research thresholds, not proof of profitability. PnL inputs must include spread, commission, slippage, financing/swap, latency and broker-specific execution. Win rate alone never selects a strategy.

## 120 specialist roles

APEX has 120 specialist role definitions across market data, price action, SMC, indicators, Forex sessions, macro, strategy research, backtesting, portfolio risk, ML, execution quality and behavioural review. The task router selects relevant roles and caps a task at 12 concurrent specialists.

This is a role catalogue and orchestration foundation, not 120 continuously running LLM agents. LLM-backed execution, provider credentials, memory, tool permissions and agent evaluation still need implementation. No agent can override deterministic risk.

## Current workflow

1. MT5 terminal streams sampled bid/ask snapshots; OANDA practice is an alternate feed.
2. APEX normalizes source-tagged quotes and rejects crossed quotes.
3. Research computes features and strategy hypotheses.
4. Strategy candidates are tested on historical data with transaction costs and out-of-sample validation.
5. Deterministic risk checks gate paper trades.
6. Internal paper broker simulates fills and persists the ledger.
7. Performance is reviewed by expectancy, profit factor, drawdown, costs and stability — not win rate alone.

The full strategy execution loop, multi-timeframe SMC engine, tick-replay pipeline, portfolio currency conversion/margin model, live ML/LLM orchestration and dashboard remain incomplete. No live broker order endpoint exists.

## Security and India regulatory boundary

Keep `MT5_BRIDGE_TOKEN` secret. Use HTTPS, restrict the EA WebRequest allowlist to your APEX host, and do not expose the token in source control or screenshots.

A broker offering MT5 or an offshore Forex demo does not establish that an Indian resident is eligible to trade that product live. Review the [RBI Forex Transactions FAQ](https://rbi.org.in/Scripts/FAQView.aspx?Id=146) and independently confirm the provider, instrument, venue and jurisdiction before funding or live execution. APEX keeps live order execution disabled.

## Run and validate

```bash
pip install -e ".[dev]"
ruff check src tests
pytest -q
uvicorn apex_forex.api.main:app --reload
```

Tests validate code behaviour, not provider entitlements, legal eligibility or profitability.