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
    capital: float = 20000
    max_risk_per_trade: float = 200
    max_daily_loss: float = 400
    max_weekly_loss: float = 800
    max_monthly_drawdown: float = 2000
    max_trades_per_day: int = 2
    max_concurrent_positions: int = 1
    allow_live_orders: bool = False

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
    from .models import Trade, AuditLog  # noqa: F401
    Base.metadata.create_all(bind=engine)


def db_health() -> str:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return "connected"
    except Exception:
        return "disconnected"
