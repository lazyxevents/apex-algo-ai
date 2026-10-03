from dataclasses import dataclass
from datetime import datetime, timezone
from math import floor

from sqlalchemy import func, select

from .core import SessionLocal, settings
from .models import AuditLog, Trade


@dataclass
class RuntimeState:
    mode: str = settings.trading_mode
    killed: bool = False


class TradingEngine:
    def __init__(self):
        self.state = RuntimeState()

    def _audit(self, event: str, detail: str) -> None:
        with SessionLocal() as db:
            db.add(AuditLog(event=event, detail=detail))
            db.commit()

    def set_mode(self, mode: str) -> None:
        if mode == "LIVE":
            raise ValueError("LIVE mode is intentionally unavailable in this MVP")
        if self.state.killed and mode not in {"KILLED", "OFF"}:
            raise ValueError("Kill switch is active; reset it first")
        self.state.mode = mode
        self._audit("mode.changed", mode)

    def kill_switch(self, reason: str) -> None:
        self.state.killed = True
        self.state.mode = "KILLED"
        self._audit("risk.kill_switch", reason)

    def reset_kill_switch(self) -> None:
        self.state.killed = False
        self.state.mode = "OFF"
        self._audit("risk.kill_switch_reset", "manual reset; mode returned to OFF")

    def _today_count(self, db) -> int:
        today = datetime.now(timezone.utc).date()
        rows = db.execute(select(Trade)).scalars().all()
        return sum(1 for t in rows if t.opened_at.date() == today)

    def _open_count(self, db) -> int:
        return db.scalar(select(func.count()).select_from(Trade).where(Trade.status == "OPEN")) or 0

    def _realized_pnl(self, db) -> float:
        return float(db.scalar(select(func.coalesce(func.sum(Trade.pnl), 0)).where(Trade.status != "OPEN")) or 0)

    def _quantity_for_risk(self, entry: float, stop: float, lot_size: int) -> int:
        risk_per_unit = abs(entry - stop)
        if risk_per_unit <= 0:
            return 0
        max_units = floor(settings.max_risk_per_trade / risk_per_unit)
        lots = max_units // lot_size
        return lots * lot_size

    def risk_snapshot(self) -> dict:
        with SessionLocal() as db:
            realized = self._realized_pnl(db)
            daily_loss = max(0.0, -realized)
            return {
                "capital": settings.capital,
                "maxRiskPerTrade": settings.max_risk_per_trade,
                "maxDailyLoss": settings.max_daily_loss,
                "maxWeeklyLoss": settings.max_weekly_loss,
                "maxMonthlyDrawdown": settings.max_monthly_drawdown,
                "maxTradesPerDay": settings.max_trades_per_day,
                "maxConcurrentPositions": settings.max_concurrent_positions,
                "tradesToday": self._today_count(db),
                "openPositions": self._open_count(db),
                "realizedPnl": realized,
                "dailyLossUsed": daily_loss,
                "dailyLocked": daily_loss >= settings.max_daily_loss,
                "killSwitch": self.state.killed,
            }

    def _authorize(self, db) -> None:
        if self.state.mode != "PAPER":
            raise ValueError("System must be in PAPER mode")
        if self.state.killed:
            raise ValueError("Kill switch is active")
        if self._open_count(db) >= settings.max_concurrent_positions:
            raise ValueError("Concurrent position limit reached")
        if self._today_count(db) >= settings.max_trades_per_day:
            raise ValueError("Daily trade-count limit reached")
        realized = self._realized_pnl(db)
        if max(0.0, -realized) >= settings.max_daily_loss:
            raise ValueError("Daily loss lock reached")

    def open_paper_trade(self, payload: dict) -> dict:
        with SessionLocal() as db:
            self._authorize(db)
            entry, stop, target = float(payload["entry"]), float(payload["stop"]), float(payload["target"])
            lot_size = int(payload["lot_size"])
            if entry == stop:
                raise ValueError("Entry and stop cannot be equal")
            if payload["direction"] == "CE" and not (stop < entry < target):
                raise ValueError("For CE paper trade use stop < entry < target")
            if payload["direction"] == "PE" and not (stop < entry < target):
                raise ValueError("Premium-based PE trade still requires stop < entry < target")

            allowed_qty = self._quantity_for_risk(entry, stop, lot_size)
            requested = payload.get("quantity")
            qty = requested or allowed_qty
            if qty <= 0 or qty % lot_size != 0:
                raise ValueError("Quantity must be one or more valid whole lots")
            if qty > allowed_qty:
                raise ValueError(f"Risk budget permits at most {allowed_qty} units for this stop distance")

            trade = Trade(
                symbol=payload["symbol"], direction=payload["direction"], entry=entry, stop=stop,
                target=target, quantity=qty, lot_size=lot_size, current_price=entry,
                pnl=0, status="OPEN", reason=payload.get("reason", "paper signal"),
            )
            db.add(trade)
            db.commit(); db.refresh(trade)
            self._audit("trade.opened", f"paper trade #{trade.id} {trade.symbol} qty={qty}")
            return self._trade_dict(trade)

    def mark_trade(self, trade_id: int, ltp: float) -> dict:
        with SessionLocal() as db:
            trade = db.get(Trade, trade_id)
            if not trade or trade.status != "OPEN":
                raise ValueError("Open trade not found")
            trade.current_price = ltp
            trade.pnl = round((ltp - trade.entry) * trade.quantity, 2)
            if ltp <= trade.stop:
                trade.status = "STOPPED"
                trade.closed_at = datetime.now(timezone.utc)
            elif ltp >= trade.target:
                trade.status = "TARGET"
                trade.closed_at = datetime.now(timezone.utc)
            db.commit(); db.refresh(trade)
            if trade.status != "OPEN":
                self._audit("trade.closed", f"trade #{trade.id} status={trade.status} pnl={trade.pnl}")
            return self._trade_dict(trade)

    def list_trades(self, limit: int = 100) -> list[dict]:
        with SessionLocal() as db:
            rows = db.execute(select(Trade).order_by(Trade.id.desc()).limit(limit)).scalars().all()
            return [self._trade_dict(t) for t in rows]

    @staticmethod
    def _trade_dict(t: Trade) -> dict:
        return {
            "id": t.id, "symbol": t.symbol, "direction": t.direction, "entry": t.entry,
            "stop": t.stop, "target": t.target, "quantity": t.quantity, "lotSize": t.lot_size,
            "currentPrice": t.current_price, "pnl": t.pnl, "status": t.status, "reason": t.reason,
            "openedAt": t.opened_at.isoformat() if t.opened_at else None,
            "closedAt": t.closed_at.isoformat() if t.closed_at else None,
        }

    def status(self, broker: dict) -> dict:
        return {
            "project": "APEX Algo AI",
            "mode": self.state.mode,
            "liveOrdersAllowed": False,
            "killSwitch": self.state.killed,
            "broker": broker,
            "risk": self.risk_snapshot(),
        }


trading_engine = TradingEngine()
