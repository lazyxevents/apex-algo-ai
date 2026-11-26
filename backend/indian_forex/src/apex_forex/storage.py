"""Durable paper-trade storage; supports SQLite locally and PostgreSQL in deployment."""
from datetime import datetime
from pathlib import Path

from sqlalchemy import DateTime, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column


class Base(DeclarativeBase):
    pass


class PaperTradeRow(Base):
    __tablename__ = "paper_trades"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), index=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    payload: Mapped[str] = mapped_column(Text)


class PaperTradeStore:
    def __init__(self, database_url: str):
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
        with Session(self.engine) as session:
            rows = session.scalars(
                select(PaperTradeRow).order_by(PaperTradeRow.opened_at.asc())
            ).all()
            return [row.payload for row in rows]

    def close(self) -> None:
        self.engine.dispose()
