# APEX Forex Architecture

## MT5-centered data plane

1. Broker MT5 terminal — broker supplies available Forex symbols, quotes, account rules and execution environment.
2. MQL5 Market Bridge EA — samples Market Watch bid/ask/last and broker timestamps, batches up to 100 symbols and posts HTTPS to APEX every 500 ms. It never submits orders.
3. Authenticated ingest API — validates shared secret, schema and crossed quotes; normalizes source-tagged keys such as `MT5:EURUSD`.
4. OANDA practice adapter — alternate Forex source with REST snapshots, stream and historical candles.
5. Quality layer — stale quote rejection, source provenance, feed heartbeat and crossed/outlier checks. Durable raw tick archive and gap replay remain planned.
6. Research layer — features, regime, SMC/price action, strategy candidates and multi-timeframe context.
7. Backtesting — MT5 Strategy Tester real ticks for EA simulation; independent Python engines for research, costs and robustness.
8. Specialist orchestration — 120 role catalogue; route tasks to relevant specialists with bounded concurrency, evidence and audit requirements.
9. Model layer — baseline ML, calibration and candidate/production registry; LLMs research and explain only.
10. Deterministic risk engine — hard limits, sizing, currency exposure, margin, drawdown and kill switch. Agents cannot override.
11. Paper execution — bid/ask-aware simulation, fees, slippage, financing and durable trade ledger.
12. Monitoring — quote age, bridge health, decisions, rejected setups, fills, PnL, model drift and audit.

## MT5 constraints

MT5 is a platform, not a broker or consolidated Forex exchange. The broker determines symbols, suffixes, trading conditions, spread, available tick history and execution. The EA bridge currently samples snapshots every 500 ms and may miss intermediate ticks; use broker real-tick history and MT5 Strategy Tester for historical tick testing. The MT5 Python package is Windows-only and talks to a locally installed terminal; the cloud Linux API uses the MQL5 HTTPS bridge instead.

## Specialist roles

120 domain-specific role definitions are catalogued across 12 domains. The task router selects relevant roles and caps active specialists per task. This is not 120 running LLM instances. LLM execution and provider credentials remain a separate implementation stage.

## Strategy promotion

Win rate is not a promotion metric by itself. Evaluate net expectancy, profit factor, drawdown, sample size, out-of-sample performance, stability by regime and transaction-cost sensitivity. Initial gate: >=100 in-sample trades, >=30 out-of-sample trades, positive expectancy in both, PF >=1.2 and max drawdown <=12%. These are research thresholds, not guarantees.

## Risk authority

- No fresh quote => no new paper entry.
- No valid stop/target => reject.
- No risk sizing including spread/slippage/currency conversion => reject.
- Daily/weekly drawdown or kill switch => lock.
- No LLM/agent override of hard risk.
- No live broker order endpoint in current build.