from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .core import Base


class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(128), index=True)
    direction: Mapped[str] = mapped_column(String(8))
    entry: Mapped[float] = mapped_column(Float)
    stop: Mapped[float] = mapped_column(Float)
    target: Mapped[float] = mapped_column(Float)
    quantity: Mapped[int] = mapped_column(Integer)
    lot_size: Mapped[int] = mapped_column(Integer)
    current_price: Mapped[float] = mapped_column(Float)
    pnl: Mapped[float] = mapped_column(Float, default=0)
    status: Mapped[str] = mapped_column(String(24), default="OPEN")
    reason: Mapped[str] = mapped_column(Text, default="")
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event: Mapped[str] = mapped_column(String(64), index=True)
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class StrategyState(Base):
    __tablename__ = "strategy_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    name: Mapped[str] = mapped_column(String(64), unique=True, default="APEX_ADAPTIVE_V1")
    q_values_json: Mapped[str] = mapped_column(Text, default="{}")
    counts_json: Mapped[str] = mapped_column(Text, default="{}")
    last_processed_trade_id: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class ResearchRun(Base):
    __tablename__ = "research_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_date: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    results_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class LearningSample(Base):
    __tablename__ = "learning_samples"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instrument: Mapped[str] = mapped_column(String(64), index=True)
    timeframe: Mapped[str] = mapped_column(String(16), index=True)
    setup_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    direction: Mapped[str] = mapped_column(String(8), index=True)
    strategy: Mapped[str] = mapped_column(String(64), index=True)
    features_json: Mapped[str] = mapped_column(Text, default="{}")
    label: Mapped[int] = mapped_column(Integer, default=0)
    outcome: Mapped[str] = mapped_column(String(32), default="UNKNOWN")
    mae_r: Mapped[float] = mapped_column(Float, default=0.0)
    mfe_r: Mapped[float] = mapped_column(Float, default=0.0)
    net_r: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class ModelEvaluation(Base):
    __tablename__ = "model_evaluations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version: Mapped[str] = mapped_column(String(64), index=True)
    role: Mapped[str] = mapped_column(String(24), default="CANDIDATE")
    status: Mapped[str] = mapped_column(String(24), default="EVALUATED")
    samples: Mapped[int] = mapped_column(Integer, default=0)
    metrics_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class LiveMarketObservation(Base):
    __tablename__ = "live_market_observations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    instrument: Mapped[str] = mapped_column(String(64), index=True)
    timeframe: Mapped[str] = mapped_column(String(16), default="1m", index=True)
    candle_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    market_price: Mapped[float] = mapped_column(Float, default=0.0)
    action: Mapped[str] = mapped_column(String(16), default="NO_TRADE", index=True)
    signal_score: Mapped[float] = mapped_column(Float, default=0.0)
    strategy: Mapped[str] = mapped_column(String(64), default="")
    context_json: Mapped[str] = mapped_column(Text, default="{}")
    patterns_json: Mapped[str] = mapped_column(Text, default="{}")
    llm_json: Mapped[str] = mapped_column(Text, default="{}")
    decision_json: Mapped[str] = mapped_column(Text, default="{}")
    trade_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    outcome: Mapped[str] = mapped_column(String(32), default="PENDING", index=True)
    outcome_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class ResearchKnowledge(Base):
    __tablename__ = "research_knowledge"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_type: Mapped[str] = mapped_column(String(32), index=True)
    category: Mapped[str] = mapped_column(String(48), index=True)
    title: Mapped[str] = mapped_column(String(300))
    url: Mapped[str] = mapped_column(String(900), unique=True, index=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    tags_json: Mapped[str] = mapped_column(Text, default="[]")
    status: Mapped[str] = mapped_column(String(32), default="RAW_RESEARCH", index=True)
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class StrategyHypothesis(Base):
    __tablename__ = "strategy_hypotheses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    family: Mapped[str] = mapped_column(String(32), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    evidence_json: Mapped[str] = mapped_column(Text, default="[]")
    rules_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(32), default="UNVERIFIED", index=True)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
