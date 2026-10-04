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
    trade_start_time: str = "09:20"
    stop_new_trade_time: str = "15:00"
    force_exit_time: str = "15:10"

    capital: float = 20000
    min_trading_capital: float = 5000
    capital_usage_pct: float = 85.0
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
    max_trades_per_day: int = 2
    max_concurrent_positions: int = 1
    allow_live_orders: bool = False

    candle_interval_minutes: int = 5
    signal_min_score: float = 0.65
    reward_risk_ratio: float = 1.8
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
    backtest_lookback_days: int = 30
    backtest_max_hold_bars: int = 6
    backtest_atr_stop_mult: float = 1.0

    force_exit_retry_count: int = 3
    force_exit_retry_delay_seconds: float = 1.0

    market_data_provider: str = "yfinance"
    paper_broker: str = "internal"

    yfinance_nifty_symbol: str = "^NSEI"
    yfinance_banknifty_symbol: str = "^NSEBANK"
    yfinance_sensex_symbol: str = "^BSESN"
    yfinance_intraday_period: str = "5d"
    yfinance_max_delay_minutes: int = 30
    yfinance_synthetic_premium_pct: float = 0.50
    yfinance_synthetic_delta: float = 0.45
    yfinance_synthetic_lot_size: int = 1

    upstox_access_token: str = ""
    upstox_sandbox_token: str = ""
    upstox_sandbox_product: str = "I"
    upstox_nifty_key: str = "NSE_INDEX|Nifty 50"
    upstox_banknifty_key: str = "NSE_INDEX|Nifty Bank"
    upstox_sensex_key: str = "BSE_INDEX|SENSEX"

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
    def underlying_keys(self) -> dict[str, str]:
        provider = self.market_data_provider.strip().lower()
        if provider in {"yfinance", "yahoo"}:
            return {
                "NIFTY": self.yfinance_nifty_symbol,
                "BANKNIFTY": self.yfinance_banknifty_symbol,
                "SENSEX": self.yfinance_sensex_symbol,
            }
        return {
            "NIFTY": self.upstox_nifty_key,
            "BANKNIFTY": self.upstox_banknifty_key,
            "SENSEX": self.upstox_sensex_key,
        }


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
    from .models import AuditLog, ResearchRun, StrategyState, Trade  # noqa: F401
    Base.metadata.create_all(bind=engine)


def db_health() -> str:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return "connected"
    except Exception:
        return "disconnected"
