from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    database_url: str = "sqlite:///./data/apex.db"
    # Forex ledger reuses APEX MongoDB; SQLite is the local fallback.
    forex_database_url: str = "sqlite:///./indian_forex.db"
    mongodb_uri: str = ""
    mongodb_database: str = "apgoai"

    # OANDA v20 practice environment. APEX deliberately rejects live configuration.
    oanda_access_token: str = ""
    oanda_account_id: str = ""
    oanda_environment: str = "practice"
    oanda_timeout_seconds: float = 15.0
    oanda_default_instruments: str = "EUR_USD,GBP_USD,USD_JPY,AUD_USD,USD_CAD,USD_CHF,NZD_USD"

    # Shared secret used by the MT5 terminal Expert Advisor to push quotes to this API.
    mt5_bridge_token: str = ""

    market_stale_after_seconds: int = 10
    paper_starting_balance: float = 100_000
    paper_max_risk_per_trade: float = 500
    paper_max_daily_loss: float = 1_500
    paper_max_open_positions: int = 1
    paper_default_slippage_ticks: int = 1
    paper_fee_per_order: float = 0

    @model_validator(mode="after")
    def practice_only(self):
        if self.oanda_environment.strip().lower() != "practice":
            raise ValueError("APEX Forex currently supports OANDA practice environment only")
        return self

    @property
    def paper_only(self) -> bool:
        return True


@lru_cache
def get_settings() -> Settings:
    return Settings()
