"""Catalog of 120 Forex research specialist roles.

These are role definitions for task-level orchestration, not 120 continuously
running LLM instances. Deterministic risk controls never delegate authority.
"""
from dataclasses import dataclass


DOMAINS: dict[str, tuple[str, ...]] = {
    "market_data": (
        "tick_quality", "spread_monitor", "latency_monitor", "gap_detector",
        "outlier_detector", "session_clock", "symbol_metadata", "quote_reconciler",
        "feed_failover", "data_provenance",
    ),
    "price_action": (
        "market_structure", "bos_detector", "choch_detector", "liquidity_sweep",
        "equal_high_low", "support_resistance", "trendline_structure", "swing_mapper",
        "breakout_quality", "false_breakout",
    ),
    "smc": (
        "fair_value_gap", "order_block", "breaker_block", "mitigation_block",
        "premium_discount", "dealing_range", "inducement", "liquidity_pool",
        "displacement", "institutional_candle",
    ),
    "indicators": (
        "ema_stack", "sma_trend", "rsi_momentum", "macd_momentum", "atr_volatility",
        "bollinger_regime", "adx_strength", "stochastic_momentum", "vwap_context",
        "volume_tick_proxy",
    ),
    "forex_sessions": (
        "asian_session", "london_open", "new_york_open", "london_fix",
        "session_overlap", "rollover_risk", "weekend_gap", "holiday_calendar",
        "session_range", "killzone_context",
    ),
    "macro": (
        "central_bank_calendar", "rate_differential", "inflation_surprise",
        "employment_surprise", "bond_yield_context", "risk_on_off", "geopolitical_risk",
        "economic_news_guard", "currency_strength", "macro_regime",
    ),
    "strategy_research": (
        "trend_following", "mean_reversion", "breakout_strategy", "pullback_strategy",
        "range_fade", "volatility_expansion", "carry_context", "momentum_strategy",
        "multi_timeframe_confluence", "strategy_discovery",
    ),
    "backtesting": (
        "real_tick_validation", "spread_cost_model", "slippage_model", "swap_cost_model",
        "walk_forward", "out_of_sample", "monte_carlo", "parameter_stability",
        "lookahead_leakage", "backtest_reconciliation",
    ),
    "portfolio_risk": (
        "position_sizing", "currency_exposure", "correlation_exposure", "margin_monitor",
        "drawdown_guard", "daily_loss_guard", "weekly_loss_guard", "risk_of_ruin",
        "tail_risk", "kill_switch_audit",
    ),
    "machine_learning": (
        "feature_quality", "label_integrity", "regime_classifier", "probability_calibration",
        "drift_detection", "model_validation", "candidate_model_review",
        "production_model_monitor", "explainability", "ensemble_validation",
    ),
    "execution_quality": (
        "bid_ask_execution", "requote_monitor", "fill_quality", "order_rejection",
        "execution_latency", "partial_fill", "broker_constraints", "trade_reconciliation",
        "demo_forward_test", "execution_anomaly",
    ),
    "behavioral_review": (
        "overtrading_detector", "revenge_trade_guard", "fomo_detector", "rule_adherence",
        "missed_setup_review", "rejected_setup_review", "loss_review", "win_review",
        "journal_summarizer", "discipline_score",
    ),
    "research_governance": (
        "source_verification", "hypothesis_tracker", "experiment_registry",
        "strategy_challenger", "counterfactual_review", "reproducibility_audit",
        "model_card_writer", "risk_policy_audit", "research_prioritizer",
        "promotion_gatekeeper",
    ),
}


@dataclass(frozen=True)
class SpecialistAgent:
    agent_id: str
    domain: str
    specialty: str


AGENT_CATALOG = tuple(
    SpecialistAgent(f"{domain}.{specialty}", domain, specialty)
    for domain, specialties in DOMAINS.items()
    for specialty in specialties
)


def plan_agents(task: str, max_agents: int = 12) -> list[SpecialistAgent]:
    """Select relevant specialists by task keywords; no LLM call is implied."""
    if max_agents < 1:
        raise ValueError("max_agents must be positive")
    text = task.lower()
    domain_keywords = {
        "market_data": ("feed", "tick", "quote", "spread", "price", "data"),
        "price_action": ("structure", "bos", "choch", "breakout", "swing"),
        "smc": ("smc", "fvg", "order block", "liquidity"),
        "indicators": ("indicator", "ema", "rsi", "atr", "macd"),
        "forex_sessions": ("session", "london", "new york", "rollover"),
        "macro": ("news", "macro", "rate", "inflation", "central bank"),
        "strategy_research": ("strategy", "signal", "setup", "entry"),
        "backtesting": ("backtest", "walk forward", "out of sample", "optimization"),
        "portfolio_risk": ("risk", "drawdown", "margin", "exposure", "sizing"),
        "machine_learning": ("model", "ml", "probability", "calibration", "learning"),
        "execution_quality": ("execution", "fill", "broker", "slippage", "requote"),
        "behavioral_review": ("fomo", "discipline", "journal", "overtrade", "review"),
        "research_governance": ("research", "validate", "promote", "audit", "hypothesis"),
    }
    selected_domains = [
        domain for domain, keywords in domain_keywords.items()
        if any(keyword in text for keyword in keywords)
    ] or ["strategy_research", "backtesting", "portfolio_risk"]
    selected = [agent for agent in AGENT_CATALOG if agent.domain in selected_domains]
    return selected[:max_agents]
