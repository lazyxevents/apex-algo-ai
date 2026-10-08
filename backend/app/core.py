from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Settings(BaseSettings):
    app_name: str = "APEX Algo AI"
    app_env: str = "development"
    database_url: str = "sqlite:///./apex.db"
    cors_origins_raw: str = "http://localhost:5173"

    trading_mode: str = "OFF"
    auto_trading_enabled: bool = True
    auto_scan_interval_seconds: int = 30
    position_monitor_interval_seconds: float = 2.0
    timezone: str = "Asia/Kolkata"
    market_open_time: str = "09:15"
    market_close_time: str = "15:30"
    trade_start_time: str = "09:20"
    stop_new_trade_time: str = "15:15"
    force_exit_time: str = "15:20"

    capital: float = 40000
    min_trading_capital: float = 5000
    capital_usage_pct: float = 80.0
    auto_compound_profits: bool = True
    max_deployable_capital: float = 0.0
    dynamic_risk_limits: bool = True
    risk_per_trade_pct: float = 1.0
    max_daily_loss_pct: float = 2.0
    max_weekly_loss_pct: float = 4.0
    max_monthly_drawdown_pct: float = 10.0
    monthly_profit_target_pct: float = 20.0
    monthly_target_lock: bool = True
    max_risk_per_trade: float = 200
    max_daily_loss: float = 400
    max_weekly_loss: float = 800
    max_monthly_drawdown: float = 2000
    max_trades_per_day: int = 5
    max_concurrent_positions: int = 1
    allow_live_orders: bool = False

    candle_interval_minutes: int = 1
    signal_min_score: float = 0.65
    smc_override_min_score: float = 0.55
    reward_risk_ratio: float = 1.8
    active_indices_raw: str = "SENSEX"
    sensex_lot_size: int = 20
    scalp_target_points: float = 4.0
    scalp_entry_pullback_points: float = 10.0
    scalp_max_stop_points: float = 40.0

    # Explicit dashboard-triggered aggressive PAPER trade mode.
    manual_do_trade_enabled: bool = True
    manual_do_trade_cutoff_time: str = "15:20"
    manual_do_trade_target_points: float = 30.0
    manual_do_trade_stop_points: float = 15.0
    manual_do_trade_capital_usage_pct: float = 100.0
    manual_do_trade_min_mtf_score: float = 0.50
    manual_do_trade_carry_forward: bool = True
    swing_first_target_points: float = 55.0
    swing_runner_target_points: float = 150.0
    swing_partial_pct: float = 50.0
    option_stop_pct: float = 18.0
    max_option_spread_pct: float = 2.5
    min_option_volume: int = 1000
    target_delta: float = 0.42
    max_otm_steps: int = 3
    candle_confirmation_required: bool = True

    adaptive_learning_enabled: bool = True
    exploration_rate: float = 0.10
    learning_rate: float = 0.08
    minimum_learning_trades: int = 20
    research_prior_weight: float = 0.20
    allow_scalp_strategy: bool = True
    min_capital_for_scalp: float = 30000

    daily_research_enabled: bool = True
    daily_research_time: str = "15:45"
    premarket_research_enabled: bool = True
    premarket_research_time: str = "08:00"
    backtest_lookback_days: int = 30
    backtest_max_hold_bars: int = 6
    backtest_atr_stop_mult: float = 1.0

    force_exit_retry_count: int = 3
    force_exit_retry_delay_seconds: float = 1.0

    market_data_provider: str = "dhan"
    paper_broker: str = "internal"
    execution_broker: str = "dhan"

    live_trade_candle_max_age_seconds: int = 180

    synthetic_roundtrip_cost_pct: float = 0.20
    option_brokerage_per_order: float = 20.0
    option_stt_sell_pct: float = 0.15
    nse_option_transaction_pct: float = 0.03553
    bse_option_transaction_pct: float = 0.0325
    sebi_turnover_pct: float = 0.0001
    option_stamp_buy_pct: float = 0.003
    gst_pct: float = 18.0

    ollama_enabled: bool = False
    ollama_base_url: str = ""
    ollama_model: str = "llama3.1:8b"
    ollama_api_key: str = ""
    ollama_timeout_seconds: float = 12.0
    ollama_web_search_enabled: bool = False
    openrouter_api_key: str = ""
    openrouter_model: str = "openrouter/free"
    openrouter_timeout_seconds: float = 15.0
    news_query_limit: int = 6
    news_results_per_query: int = 5

    learning_worker_enabled: bool = True
    learning_worker_interval_minutes: int = 60
    learning_worker_daily_hours: int = 15
    learning_worker_window_start_time: str = "17:00"
    learning_worker_window_end_time: str = "08:00"
    premarket_plan_time: str = "08:00"
    learning_worker_max_sources: int = 120
    research_web_enabled: bool = True
    research_max_articles_per_cycle: int = 16
    research_source_timeout_seconds: float = 8.0

    neural_training_enabled: bool = True
    neural_min_labeled_samples: int = 500
    neural_hidden_units: int = 24
    neural_epochs: int = 45
    neural_batch_size: int = 256
    neural_learning_rate: float = 0.015
    neural_l2: float = 0.0005
    neural_promotion_min_auc: float = 0.54
    neural_promotion_max_brier: float = 0.25
    neural_inference_weight: float = 0.15

    push_notifications_enabled: bool = True
    vapid_public_key: str = ""
    vapid_private_key: str = ""
    vapid_subject: str = "mailto:officialtarun070@gmail.com"

    upstox_access_token: str = ""
    upstox_sandbox_token: str = ""
    upstox_sandbox_product: str = "I"
    upstox_nifty_key: str = "NSE_INDEX|Nifty 50"
    upstox_banknifty_key: str = "NSE_INDEX|Nifty Bank"
    upstox_sensex_key: str = "BSE_INDEX|SENSEX"

    # DhanHQ market-data provider. Keys are exchangeSegment|securityId|instrument.
    dhan_client_id: str = ""
    dhan_access_token: str = ""
    dhan_nifty_key: str = "IDX_I|13|INDEX"
    dhan_banknifty_key: str = "IDX_I|25|INDEX"
    dhan_sensex_key: str = "IDX_I|51|INDEX"
    dhan_intraday_lookback_days: int = 5
    dhan_product_type: str = "INTRADAY"

    kite_api_key: str = ""
    kite_api_secret: str = ""
    kite_access_token: str = ""

    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).resolve().parents[2] / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def cors_origins(self) -> list[str]:
        return [x.strip() for x in self.cors_origins_raw.split(",") if x.strip()]

    @property
    def active_indices(self) -> set[str]:
        return {x.strip().upper() for x in self.active_indices_raw.split(",") if x.strip()}

    @property
    def underlying_keys(self) -> dict[str, str]:
        provider = self.market_data_provider.strip().lower()
        if provider == "dhan":
            keys = {
                "NIFTY": self.dhan_nifty_key,
                "BANKNIFTY": self.dhan_banknifty_key,
                "SENSEX": self.dhan_sensex_key,
            }
        elif provider == "upstox":
            keys = {
                "NIFTY": self.upstox_nifty_key,
                "BANKNIFTY": self.upstox_banknifty_key,
                "SENSEX": self.upstox_sensex_key,
            }
        else:
            raise ValueError(f"Unsupported market data provider: {self.market_data_provider}")
        active = self.active_indices
        return {name: key for name, key in keys.items() if name in active}


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

database_url = settings.database_url
if database_url.startswith("postgresql://"):
    database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)
elif database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql+psycopg://", 1)

connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
engine = create_engine(database_url, pool_pre_ping=True, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    from .models import AuditLog, DailyMarketPlan, LearningRewardEvent, LearningSample, LiveMarketObservation, ModelEvaluation, NeuralModelArtifact, PushSubscription, ResearchActivity, ResearchKnowledge, ResearchRun, StrategyHypothesis, StrategyState, Trade  # noqa: F401
    Base.metadata.create_all(bind=engine)


def db_health() -> str:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return "connected"
    except Exception:
        return "disconnected"
