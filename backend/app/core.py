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
    auto_scan_interval_seconds: int = 60
    timezone: str = "Asia/Kolkata"
    trade_start_time: str = "09:20"
    stop_new_trade_time: str = "15:00"
    force_exit_time: str = "15:10"

    capital: float = 20000
    min_trading_capital: float = 5000
    capital_usage_pct: float = 85.0
    dynamic_risk_limits: bool = True
    risk_per_trade_pct: float = 1.0
    max_daily_loss_pct: float = 2.0
    max_weekly_loss_pct: float = 4.0
    max_monthly_drawdown_pct: float = 10.0
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

    adaptive_learning_enabled: bool = True
    exploration_rate: float = 0.10
    learning_rate: float = 0.08
    minimum_learning_trades: int = 20

    market_data_provider: str = "upstox"
    paper_broker: str = "internal"
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
        return {
            "NIFTY": self.upstox_nifty_key,
            "BANKNIFTY": self.upstox_banknifty_key,
            "SENSEX": self.upstox_sensex_key,
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, pool_pre_ping=True, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    from .models import AuditLog, StrategyState, Trade  # noqa: F401
    Base.metadata.create_all(bind=engine)


def db_health() -> str:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return "connected"
    except Exception:
        return "disconnected"
