"""Durable paper-trade storage; supports SQLite locally and PostgreSQL in deployment."""
from datetime import datetime
from pathlib import Path

from sqlalchemy import DateTime, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from pymongo import MongoClient


class Base(DeclarativeBase):
    pass


class PaperTradeRow(Base):
    __tablename__ = "paper_trades"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), index=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    payload: Mapped[str] = mapped_column(Text)


def normalize_database_url(database_url: str) -> str:
    """Use psycopg 3 for PostgreSQL URLs, including Render's legacy postgres:// form."""
    if database_url.startswith("postgres://"):
        return "postgresql+psycopg://" + database_url.removeprefix("postgres://")
    if database_url.startswith("postgresql://"):
        return "postgresql+psycopg://" + database_url.removeprefix("postgresql://")
    if database_url.startswith("postgresql+psycopg2://"):
        return "postgresql+psycopg://" + database_url.removeprefix("postgresql+psycopg2://")
    return database_url


class PaperTradeStore:
    def __init__(
        self,
        database_url: str,
        mongo_uri: str = "",
        mongo_database: str = "apgoai",
    ):
        self.mongo_client = None
        self.mongo_collection = None
        self.engine = None

        # Reuse the existing APEX MongoDB Atlas database for durable Forex trades.
        if mongo_uri.strip():
            self.mongo_client = MongoClient(
                mongo_uri,
                serverSelectionTimeoutMS=5000,
                connectTimeoutMS=5000,
                socketTimeoutMS=10000,
                retryWrites=True,
                appname="APEX-Indian-Forex",
            )
            self.mongo_client.admin.command("ping")
            self.mongo_collection = self.mongo_client[mongo_database]["indian_forex_paper_trades"]
            self.mongo_collection.create_index("opened_at")
            self.mongo_collection.create_index("status")
            return

        database_url = normalize_database_url(database_url)
        if database_url.startswith("sqlite:///"):
            raw_path = database_url.removeprefix("sqlite:///")
            if raw_path != ":memory:":
                Path(raw_path).parent.mkdir(parents=True, exist_ok=True)
            self.engine = create_engine(
                database_url, connect_args={"check_same_thread": False}
            )
        else:
            self.engine = create_engine(database_url, pool_pre_ping=True, pool_recycle=300)
        Base.metadata.create_all(self.engine)

    def save_trade(self, trade) -> None:
        payload = trade.model_dump_json()
        if self.mongo_collection is not None:
            self.mongo_collection.replace_one(
                {"_id": trade.id},
                {
                    "_id": trade.id,
                    "status": trade.status,
                    "opened_at": trade.opened_at,
                    "payload": payload,
                },
                upsert=True,
            )
            return
        with Session(self.engine) as session:
            row = session.get(PaperTradeRow, trade.id)
            if row is None:
                row = PaperTradeRow(
                    id=trade.id, status=trade.status, opened_at=trade.opened_at,
                    payload=payload,
                )
                session.add(row)
            else:
                row.status = trade.status
                row.payload = payload
            session.commit()

    def load_trades(self) -> list[str]:
        if self.mongo_collection is not None:
            rows = self.mongo_collection.find(
                {}, {"payload": 1, "_id": 0}
            ).sort("opened_at", 1)
            return [row["payload"] for row in rows if row.get("payload")]
        with Session(self.engine) as session:
            rows = session.scalars(
                select(PaperTradeRow).order_by(PaperTradeRow.opened_at.asc())
            ).all()
            return [row.payload for row in rows]

    def close(self) -> None:
        if self.mongo_client is not None:
            self.mongo_client.close()
        if self.engine is not None:
            self.engine.dispose()
