# APEX Forex Implementation Roadmap

## Implemented foundation

- Forex-only canonical contracts and OANDA v20 practice adapter.
- MT5 MQL5 Expert Advisor bridge for authenticated Market Watch quote snapshots.
- Dynamic broker symbol discovery via MT5 Market Watch; no hardcoded pair list.
- Unified MT5/OANDA normalized quote feed and paper broker input.
- SQLite/PostgreSQL-compatible paper trade ledger and deterministic baseline risk checks.
- SMA/EMA/ATR and baseline regime classifier.
- Conservative OHLC bracket backtester.
- 120 specialist role catalogue and task-level deterministic router.
- Strategy evaluation metrics and initial promotion gates.

## Stage 1 — connect and validate MT5

- Install EA on a Windows MT5 terminal/VPS and confirm WebRequest delivery.
- Test broker symbols, suffixes, timestamps, reconnects and stale-feed rejection.
- Add durable raw quote/tick storage, sequence/gap detection and replay.
- Build historical MT5 data export/ingestion and compare with broker history.
- Add terminal heartbeat and bridge health alerts.

Exit gate: stable demo feed over multiple market sessions; measured latency, gaps and broker spreads; no silent disconnect.

## Stage 2 — research libraries and strategy lab

- Build executable EMA trend, RSI mean-reversion, Donchian breakout, Bollinger reversion, ATR expansion and session breakout candidates.
- Use MT5 Strategy Tester real-tick mode for MQL5 execution tests.
- Use pandas/NumPy for features, backtesting.py or vectorbt-style engines for independent validation, Optuna for bounded parameter search and scikit-learn for baseline classifiers.
- Use chronological splits, walk-forward and Monte Carlo robustness.
- Model variable spread, commissions, slippage, swaps, latency and rejected fills.

Exit gate: positive net expectancy out of sample, sufficient trades, stable across regimes/symbols and realistic transaction-cost sensitivity.

## Stage 3 — specialist research orchestration

- Route tasks to 120 specialist role definitions; cap concurrency and compute budget.
- Add LLM providers only with explicit credentials, structured outputs and evidence.
- Use specialists for independent hypotheses, code review, strategy challenge and post-trade analysis.
- Keep research agents read-only; deterministic risk remains the only execution authority.

Exit gate: reproducible outputs, no fabricated market data, audit trail and measurable improvement over deterministic baselines.

## Stage 4 — portfolio risk and paper forward test

- Pair pip-value conversion to account currency.
- Currency-factor and correlated-pair exposure.
- Margin/leverage, lot steps, min/max volume, swap and rollover.
- Daily/weekly drawdown, loss streak and kill switch.
- Bid/ask-aware paper fills and demo reconciliation.

## Stage 5 — model validation and readiness

- Versioned datasets/features and production/candidate model separation.
- Calibrated probabilities, drift monitoring and walk-forward validation.
- Multi-month shadow/paper forward results across market regimes.
- Independent legal, broker, security and risk review.

Live execution is not implemented. No timeline or win-rate promise substitutes for these gates.