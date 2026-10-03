import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import floor
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from .core import SessionLocal, settings
from .models import AuditLog, Trade
from .strategy import adaptive_learner, evaluate_signal, select_option

IST = ZoneInfo(settings.timezone)


@dataclass
class RuntimeState:
    mode: str = settings.trading_mode
    killed: bool = False
    automation_running: bool = False
    last_cycle_at: str | None = None
    last_decision: dict | None = None
    last_error: str | None = None


class TradingEngine:
    def __init__(self):
        self.state = RuntimeState()

    def _audit(self, event: str, detail: str | dict) -> None:
        payload = detail if isinstance(detail, str) else json.dumps(detail, default=str)
        with SessionLocal() as db:
            db.add(AuditLog(event=event, detail=payload))
            db.commit()

    def set_mode(self, mode: str) -> None:
        if mode == "LIVE":
            raise ValueError("LIVE mode is intentionally unavailable")
        if self.state.killed and mode not in {"KILLED", "OFF"}:
            raise ValueError("Kill switch is active; reset it first")
        self.state.mode = mode
        self._audit("mode.changed", {"mode": mode})

    def kill_switch(self, reason: str) -> None:
        self.state.killed = True
        self.state.mode = "KILLED"
        self._audit("risk.kill_switch", {"reason": reason})

    def reset_kill_switch(self) -> None:
        self.state.killed = False
        self.state.mode = "OFF"
        self._audit("risk.kill_switch_reset", {"mode": "OFF"})

    def _as_ist(self, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(IST)

    def _closed_rows(self, db) -> list[Trade]:
        return list(db.execute(select(Trade).where(Trade.status != "OPEN").order_by(Trade.id.asc())).scalars().all())

    def _today_count(self, db) -> int:
        today = datetime.now(IST).date()
        return sum(1 for t in db.execute(select(Trade)).scalars().all() if self._as_ist(t.opened_at).date() == today)

    def _open_rows(self, db) -> list[Trade]:
        return list(db.execute(select(Trade).where(Trade.status == "OPEN").order_by(Trade.id.asc())).scalars().all())

    def _open_count(self, db) -> int:
        return db.scalar(select(func.count()).select_from(Trade).where(Trade.status == "OPEN")) or 0

    def _period_pnl(self, db, period: str) -> float:
        now = datetime.now(IST)
        rows = self._closed_rows(db)
        values = []
        for trade in rows:
            closed = self._as_ist(trade.closed_at)
            if not closed:
                continue
            if period == "day" and closed.date() == now.date():
                values.append(trade.pnl)
            elif period == "week" and closed.isocalendar()[:2] == now.isocalendar()[:2]:
                values.append(trade.pnl)
            elif period == "month" and (closed.year, closed.month) == (now.year, now.month):
                values.append(trade.pnl)
        return float(sum(values))

    def _all_realized_pnl(self, db) -> float:
        return float(sum(t.pnl for t in self._closed_rows(db)))

    def _effective_capital(self, db) -> float:
        # CAPITAL is the hard maximum allocation. Profits do not automatically increase it.
        # Losses reduce available paper equity until the user changes CAPITAL explicitly.
        realized = self._all_realized_pnl(db)
        return max(0.0, min(settings.capital, settings.capital + min(realized, 0.0)))

    def _limits(self, db) -> dict:
        capital = self._effective_capital(db)
        if settings.dynamic_risk_limits:
            return {
                "perTrade": capital * settings.risk_per_trade_pct / 100,
                "daily": capital * settings.max_daily_loss_pct / 100,
                "weekly": capital * settings.max_weekly_loss_pct / 100,
                "monthly": capital * settings.max_monthly_drawdown_pct / 100,
            }
        return {
            "perTrade": settings.max_risk_per_trade,
            "daily": settings.max_daily_loss,
            "weekly": settings.max_weekly_loss,
            "monthly": settings.max_monthly_drawdown,
        }

    def _quantity_for_risk(self, entry: float, stop: float, lot_size: int, db=None) -> int:
        owns_db = db is None
        db = db or SessionLocal()
        try:
            risk_per_unit = abs(entry - stop)
            if risk_per_unit <= 0 or lot_size <= 0:
                return 0
            capital = self._effective_capital(db)
            limits = self._limits(db)
            deployable = capital * settings.capital_usage_pct / 100
            lots_by_risk = floor(limits["perTrade"] / (risk_per_unit * lot_size))
            lots_by_cash = floor(deployable / (entry * lot_size))
            return max(0, min(lots_by_risk, lots_by_cash)) * lot_size
        finally:
            if owns_db:
                db.close()

    def performance_snapshot(self) -> dict:
        with SessionLocal() as db:
            rows = self._closed_rows(db)
            pnls = [float(t.pnl) for t in rows]
            wins = [p for p in pnls if p > 0]
            losses = [p for p in pnls if p < 0]
            gross_win = sum(wins)
            gross_loss = abs(sum(losses))
            equity = settings.capital
            peak = equity
            max_dd = 0.0
            for pnl in pnls:
                equity += pnl
                peak = max(peak, equity)
                max_dd = max(max_dd, peak - equity)
            return {
                "trades": len(pnls),
                "wins": len(wins),
                "losses": len(losses),
                "winRate": round(len(wins) / len(pnls) * 100, 2) if pnls else 0.0,
                "netPnl": round(sum(pnls), 2),
                "averageWin": round(gross_win / len(wins), 2) if wins else 0.0,
                "averageLoss": round(sum(losses) / len(losses), 2) if losses else 0.0,
                "expectancy": round(sum(pnls) / len(pnls), 2) if pnls else 0.0,
                "profitFactor": round(gross_win / gross_loss, 3) if gross_loss else (999.0 if gross_win else 0.0),
                "maxDrawdown": round(max_dd, 2),
            }

    def risk_snapshot(self) -> dict:
        with SessionLocal() as db:
            capital = self._effective_capital(db)
            limits = self._limits(db)
            day_pnl = self._period_pnl(db, "day")
            week_pnl = self._period_pnl(db, "week")
            month_pnl = self._period_pnl(db, "month")
            return {
                "configuredCapital": settings.capital,
                "effectiveCapital": round(capital, 2),
                "minimumCapital": settings.min_trading_capital,
                "capitalUsagePct": settings.capital_usage_pct,
                "dynamicLimits": settings.dynamic_risk_limits,
                "riskPerTrade": round(limits["perTrade"], 2),
                "maxDailyLoss": round(limits["daily"], 2),
                "maxWeeklyLoss": round(limits["weekly"], 2),
                "maxMonthlyDrawdown": round(limits["monthly"], 2),
                "maxTradesPerDay": settings.max_trades_per_day,
                "maxConcurrentPositions": settings.max_concurrent_positions,
                "tradesToday": self._today_count(db),
                "openPositions": self._open_count(db),
                "dailyPnl": round(day_pnl, 2),
                "weeklyPnl": round(week_pnl, 2),
                "monthlyPnl": round(month_pnl, 2),
                "dailyLocked": day_pnl <= -limits["daily"],
                "weeklyLocked": week_pnl <= -limits["weekly"],
                "monthlyLocked": month_pnl <= -limits["monthly"],
                "killSwitch": self.state.killed,
            }

    def _authorize(self, db) -> None:
        if self.state.mode != "PAPER":
            raise ValueError("System must be in PAPER mode")
        if self.state.killed:
            raise ValueError("Kill switch is active")
        capital = self._effective_capital(db)
        if capital < settings.min_trading_capital:
            raise ValueError("Effective capital is below MIN_TRADING_CAPITAL")
        if self._open_count(db) >= settings.max_concurrent_positions:
            raise ValueError("Concurrent position limit reached")
        if self._today_count(db) >= settings.max_trades_per_day:
            raise ValueError("Daily trade-count limit reached")
        limits = self._limits(db)
        if self._period_pnl(db, "day") <= -limits["daily"]:
            raise ValueError("Daily loss lock reached")
        if self._period_pnl(db, "week") <= -limits["weekly"]:
            raise ValueError("Weekly loss lock reached")
        if self._period_pnl(db, "month") <= -limits["monthly"]:
            raise ValueError("Monthly loss lock reached")

    def _market_phase(self) -> dict:
        now = datetime.now(IST)
        if now.weekday() >= 5:
            return {"tradable": False, "newTrades": False, "forceExit": False, "reason": "weekend", "now": now.isoformat()}
        hm = now.strftime("%H:%M")
        if hm < settings.trade_start_time:
            return {"tradable": False, "newTrades": False, "forceExit": False, "reason": "before entry window", "now": now.isoformat()}
        if hm >= settings.force_exit_time:
            return {"tradable": True, "newTrades": False, "forceExit": True, "reason": "force-exit window", "now": now.isoformat()}
        if hm >= settings.stop_new_trade_time:
            return {"tradable": True, "newTrades": False, "forceExit": False, "reason": "new entries stopped", "now": now.isoformat()}
        return {"tradable": True, "newTrades": True, "forceExit": False, "reason": "entry window", "now": now.isoformat()}

    def open_paper_trade(self, payload: dict) -> dict:
        with SessionLocal() as db:
            self._authorize(db)
            entry, stop, target = float(payload["entry"]), float(payload["stop"]), float(payload["target"])
            lot_size = int(payload["lot_size"])
            if not (0 < stop < entry < target):
                raise ValueError("Option-buying trade requires 0 < stop < entry < target")
            allowed_qty = self._quantity_for_risk(entry, stop, lot_size, db)
            requested = payload.get("quantity")
            qty = int(requested or allowed_qty)
            if qty <= 0 or qty % lot_size != 0:
                raise ValueError("Quantity must be one or more valid whole lots")
            if qty > allowed_qty:
                raise ValueError(f"Risk/capital budget permits at most {allowed_qty} units")

            trade = Trade(
                symbol=payload["symbol"], direction=payload["direction"], entry=entry, stop=stop,
                target=target, quantity=qty, lot_size=lot_size, current_price=entry,
                pnl=0, status="OPEN", reason=payload.get("reason", "{}"),
            )
            db.add(trade)
            db.commit(); db.refresh(trade)
            self._audit("trade.opened", {"tradeId": trade.id, "symbol": trade.symbol, "qty": qty})
            return self._trade_dict(trade)

    def mark_trade(self, trade_id: int, ltp: float, force_exit: bool = False) -> dict:
        with SessionLocal() as db:
            trade = db.get(Trade, trade_id)
            if not trade or trade.status != "OPEN":
                raise ValueError("Open trade not found")
            trade.current_price = float(ltp)
            trade.pnl = round((trade.current_price - trade.entry) * trade.quantity, 2)

            # Simple protected trailing: at 50% of target distance move stop to breakeven,
            # at 75% protect 25% of reward distance. No averaging down.
            reward_distance = max(0.01, trade.target - trade.entry)
            progress = (trade.current_price - trade.entry) / reward_distance
            if progress >= 0.75:
                trade.stop = max(trade.stop, trade.entry + reward_distance * 0.25)
            elif progress >= 0.50:
                trade.stop = max(trade.stop, trade.entry)

            if force_exit:
                trade.status = "TIME_EXIT"
                trade.closed_at = datetime.now(timezone.utc)
            elif trade.current_price <= trade.stop:
                trade.status = "STOPPED"
                trade.closed_at = datetime.now(timezone.utc)
            elif trade.current_price >= trade.target:
                trade.status = "TARGET"
                trade.closed_at = datetime.now(timezone.utc)
            db.commit(); db.refresh(trade)
            if trade.status != "OPEN":
                self._audit("trade.closed", {"tradeId": trade.id, "status": trade.status, "pnl": trade.pnl})
            return self._trade_dict(trade)

    def list_trades(self, limit: int = 100) -> list[dict]:
        with SessionLocal() as db:
            rows = db.execute(select(Trade).order_by(Trade.id.desc()).limit(limit)).scalars().all()
            return [self._trade_dict(t) for t in rows]

    def _update_open_trades(self, provider, phase: dict) -> list[dict]:
        actions = []
        with SessionLocal() as db:
            open_rows = self._open_rows(db)
        for trade in open_rows:
            try:
                ltp = provider.quote_ltp(trade.symbol)
                result = self.mark_trade(trade.id, ltp, force_exit=phase["forceExit"])
                if result["status"] != "OPEN":
                    sandbox = provider.place_sandbox_order(trade.symbol, trade.quantity, "SELL", f"apex-exit-{trade.id}")
                    actions.append({"exit": result, "sandbox": sandbox})
            except Exception as exc:
                self._audit("trade.monitor_error", {"tradeId": trade.id, "error": str(exc)})
        return actions

    def automation_cycle(self, provider) -> dict:
        self.state.last_cycle_at = datetime.now(timezone.utc).isoformat()
        self.state.last_error = None
        phase = self._market_phase()
        decision: dict = {"phase": phase, "action": "NO_TRADE"}
        try:
            adaptive_learner.learn()
            exits = self._update_open_trades(provider, phase) if provider.market_ready else []
            decision["exits"] = exits

            if self.state.mode != "PAPER" or not settings.auto_trading_enabled:
                decision["reason"] = "automation disabled or mode is not PAPER"
                self.state.last_decision = decision
                return decision
            if not provider.market_ready:
                decision["reason"] = "market data token missing"
                self.state.last_decision = decision
                return decision
            if not phase["newTrades"]:
                decision["reason"] = phase["reason"]
                self.state.last_decision = decision
                return decision
            with SessionLocal() as db:
                self._authorize(db)
                capital = self._effective_capital(db)
                deployable = capital * settings.capital_usage_pct / 100

            arm = adaptive_learner.choose_arm()
            ranked = []
            for name, key in settings.underlying_keys.items():
                candles = provider.intraday_candles(key)
                if not provider.candles_fresh(candles):
                    ranked.append({"action": "NO_TRADE", "score": 0.0, "reason": "stale/missing market data", "index": name, "underlyingKey": key})
                    continue
                signal = evaluate_signal(candles, arm)
                signal.update({"index": name, "underlyingKey": key})
                ranked.append(signal)
            ranked.sort(key=lambda x: x.get("score", 0), reverse=True)
            best = ranked[0] if ranked else {"action": "NO_TRADE", "score": 0}
            decision["signals"] = ranked
            if best.get("action") not in {"CE", "PE"}:
                decision["reason"] = "no strategy setup passed threshold"
                self.state.last_decision = decision
                return decision

            expiry = provider.nearest_expiry(best["underlyingKey"])
            if not expiry:
                decision["reason"] = "no current option expiry available"
                self.state.last_decision = decision
                return decision
            chain = provider.option_chain(best["underlyingKey"], expiry)
            option = select_option(chain, best["action"], best["underlyingPrice"], deployable)
            if not option:
                decision["reason"] = "no liquid/affordable option contract passed filters"
                self.state.last_decision = decision
                return decision

            # Lot size is resolved from the option-contract master, never hard-coded.
            contracts = provider.option_contracts(best["underlyingKey"])
            contract = next((x for x in contracts if x.get("instrument_key") == option["instrumentKey"]), None)
            if not contract:
                decision["reason"] = "selected contract missing from instrument master"
                self.state.last_decision = decision
                return decision
            lot_size = int(contract.get("lot_size") or contract.get("minimum_lot") or 0)
            if lot_size <= 0:
                decision["reason"] = "invalid contract lot size"
                self.state.last_decision = decision
                return decision

            entry = float(option["ltp"])
            stop_distance = max(entry * settings.option_stop_pct / 100, 0.05)
            stop = round(max(0.05, entry - stop_distance), 2)
            target = round(entry + stop_distance * settings.reward_risk_ratio, 2)
            with SessionLocal() as db:
                qty = self._quantity_for_risk(entry, stop, lot_size, db)
                risk_limit = self._limits(db)["perTrade"]
            if qty <= 0:
                decision["reason"] = "one valid lot does not fit capital/risk budget"
                self.state.last_decision = decision
                return decision

            initial_risk = round((entry - stop) * qty, 2)
            meta = {
                "strategy": arm.name,
                "signalScore": best["score"],
                "index": best["index"],
                "underlyingKey": best["underlyingKey"],
                "expiry": expiry,
                "strike": option["strike"],
                "selectionScore": option["selectionScore"],
                "spreadPct": option["spreadPct"],
                "delta": option["delta"],
                "initialRisk": initial_risk,
                "riskLimit": round(risk_limit, 2),
            }
            sandbox = provider.place_sandbox_order(option["instrumentKey"], qty, "BUY", f"apex-entry-{best['index'].lower()}")
            trade = self.open_paper_trade({
                "symbol": option["instrumentKey"], "direction": best["action"], "entry": entry,
                "stop": stop, "target": target, "lot_size": lot_size, "quantity": qty,
                "reason": json.dumps(meta),
            })
            decision.update({"action": best["action"], "trade": trade, "option": option, "sandbox": sandbox, "meta": meta})
            self._audit("automation.trade_decision", decision)
        except ValueError as exc:
            decision["reason"] = str(exc)
        except Exception as exc:
            self.state.last_error = str(exc)
            decision["reason"] = f"safe failure: {exc}"
            self._audit("automation.error", {"error": str(exc)})
        self.state.last_decision = decision
        return decision

    @staticmethod
    def _trade_dict(t: Trade) -> dict:
        try:
            meta = json.loads(t.reason or "{}")
        except json.JSONDecodeError:
            meta = {"note": t.reason}
        return {
            "id": t.id, "symbol": t.symbol, "direction": t.direction, "entry": t.entry,
            "stop": t.stop, "target": t.target, "quantity": t.quantity, "lotSize": t.lot_size,
            "currentPrice": t.current_price, "pnl": t.pnl, "status": t.status, "meta": meta,
            "openedAt": t.opened_at.isoformat() if t.opened_at else None,
            "closedAt": t.closed_at.isoformat() if t.closed_at else None,
        }

    def status(self, broker: dict, market: dict | None = None) -> dict:
        return {
            "project": "APEX Algo AI",
            "mode": self.state.mode,
            "liveOrdersAllowed": False,
            "killSwitch": self.state.killed,
            "automation": {
                "enabled": settings.auto_trading_enabled,
                "running": self.state.automation_running,
                "lastCycleAt": self.state.last_cycle_at,
                "lastDecision": self.state.last_decision,
                "lastError": self.state.last_error,
                "tradeWindow": f"{settings.trade_start_time}-{settings.stop_new_trade_time}",
                "forceExit": settings.force_exit_time,
            },
            "broker": broker,
            "market": market or {},
            "risk": self.risk_snapshot(),
            "performance": self.performance_snapshot(),
            "learning": adaptive_learner.snapshot(),
        }


trading_engine = TradingEngine()
