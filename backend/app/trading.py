import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from math import floor
from time import sleep
from threading import Lock
from zoneinfo import ZoneInfo
from urllib.parse import quote

from sqlalchemy import func, select

from .core import SessionLocal, settings
from .llm_research import ollama_research
from .llm_advisor import ollama_advisor
from .learning_worker import learning_worker
from .market_research import build_market_research
from .models import AuditLog, Trade
from .paper_costs import estimate_paper_costs
from .strategy import adaptive_learner, evaluate_signal, market_context, select_option

IST = ZoneInfo(settings.timezone)


@dataclass
class RuntimeState:
    mode: str = settings.trading_mode
    killed: bool = False
    automation_running: bool = False
    position_monitor_running: bool = False
    last_cycle_at: str | None = None
    last_position_update_at: str | None = None
    last_premarket_date: str | None = None
    market_research: dict | None = None
    last_decision: dict | None = None
    last_error: str | None = None


class TradingEngine:
    def __init__(self):
        self.state = RuntimeState()
        self._position_lock = Lock()

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

    @staticmethod
    def _meta(trade: Trade) -> dict:
        try:
            return json.loads(trade.reason or "{}")
        except json.JSONDecodeError:
            return {"note": trade.reason}

    def _closed_rows(self, db) -> list[Trade]:
        return list(db.execute(select(Trade).where(Trade.status != "OPEN").order_by(Trade.id.asc())).scalars().all())

    def _open_rows(self, db) -> list[Trade]:
        return list(db.execute(select(Trade).where(Trade.status == "OPEN").order_by(Trade.id.asc())).scalars().all())

    def _today_count(self, db) -> int:
        today = datetime.now(IST).date()
        return sum(1 for t in db.execute(select(Trade)).scalars().all() if self._as_ist(t.opened_at).date() == today)

    def _open_count(self, db) -> int:
        return db.scalar(select(func.count()).select_from(Trade).where(Trade.status == "OPEN")) or 0

    def _period_pnl(self, db, period: str) -> float:
        now = datetime.now(IST)
        values = []
        for trade in self._closed_rows(db):
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

    def _apply_cap_ceiling(self, capital: float) -> float:
        if settings.max_deployable_capital > 0:
            return min(capital, settings.max_deployable_capital)
        return capital

    def _effective_capital(self, db) -> float:
        realized = self._all_realized_pnl(db)
        if settings.auto_compound_profits:
            equity = settings.capital + realized
        else:
            equity = settings.capital + min(realized, 0.0)
        return max(0.0, self._apply_cap_ceiling(equity))

    def _month_start_equity(self, db) -> float:
        now = datetime.now(IST)
        month_start = datetime(now.year, now.month, 1, tzinfo=IST)
        pnl_before_month = 0.0
        for trade in self._closed_rows(db):
            closed = self._as_ist(trade.closed_at)
            if closed and closed < month_start:
                pnl_before_month += trade.pnl
        if settings.auto_compound_profits:
            equity = settings.capital + pnl_before_month
        else:
            equity = settings.capital + min(pnl_before_month, 0.0)
        return max(0.0, self._apply_cap_ceiling(equity))

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

    def _monthly_target(self, db) -> dict:
        month_start_equity = self._month_start_equity(db)
        month_pnl = self._period_pnl(db, "month")
        target_amount = month_start_equity * settings.monthly_profit_target_pct / 100
        reached = bool(settings.monthly_target_lock and target_amount > 0 and month_pnl >= target_amount)
        return {
            "monthStartEquity": round(month_start_equity, 2),
            "targetPct": settings.monthly_profit_target_pct,
            "targetAmount": round(target_amount, 2),
            "monthPnl": round(month_pnl, 2),
            "remaining": round(max(0.0, target_amount - month_pnl), 2),
            "reached": reached,
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
            wins, losses = [p for p in pnls if p > 0], [p for p in pnls if p < 0]
            gross_win, gross_loss = sum(wins), abs(sum(losses))
            equity = settings.capital
            peak, max_dd = equity, 0.0
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
            target = self._monthly_target(db)
            return {
                "configuredCapital": settings.capital,
                "effectiveCapital": round(capital, 2),
                "minimumCapital": settings.min_trading_capital,
                "capitalUsagePct": settings.capital_usage_pct,
                "autoCompoundProfits": settings.auto_compound_profits,
                "maxDeployableCapital": settings.max_deployable_capital,
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
                "monthlyLossLocked": month_pnl <= -limits["monthly"],
                "monthlyTarget": target,
                "monthlyTargetLocked": target["reached"],
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
        if self._monthly_target(db)["reached"]:
            raise ValueError("Monthly profit target reached; trading locked until next month")
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
            qty = int(payload.get("quantity") or allowed_qty)
            if qty <= 0 or qty % lot_size != 0:
                raise ValueError("Quantity must be one or more valid whole lots")
            if qty > allowed_qty:
                raise ValueError(f"Risk/capital budget permits at most {allowed_qty} units")
            trade = Trade(
                symbol=payload["symbol"],
                direction=payload["direction"],
                entry=entry,
                stop=stop,
                target=target,
                quantity=qty,
                lot_size=lot_size,
                current_price=entry,
                pnl=0,
                status="OPEN",
                reason=payload.get("reason", "{}"),
            )
            db.add(trade)
            db.commit()
            db.refresh(trade)
            self._audit("trade.opened", {"tradeId": trade.id, "symbol": trade.symbol, "qty": qty})
            return self._trade_dict(trade)

    def mark_trade(self, trade_id: int, ltp: float, force_status: str | None = None) -> dict:
        with SessionLocal() as db:
            trade = db.get(Trade, trade_id)
            if not trade or trade.status != "OPEN":
                raise ValueError("Open trade not found")
            trade.current_price = float(ltp)
            meta = self._meta(trade)
            gross_pnl = (trade.current_price - trade.entry) * trade.quantity
            exchange = "BSE" if str(meta.get("index") or "").upper() == "SENSEX" else "NSE"
            costs = estimate_paper_costs(
                trade.entry,
                trade.current_price,
                trade.quantity,
                exchange=exchange,
                synthetic=bool(meta.get("syntheticDemo")),
            )
            trade.pnl = round(gross_pnl - float(costs["total"]), 2)
            meta["grossPnl"] = round(gross_pnl, 2)
            meta["estimatedCharges"] = float(costs["total"])
            meta["costModel"] = costs
            trade.reason = json.dumps(meta, default=str)
            reward_distance = max(0.01, trade.target - trade.entry)
            progress = (trade.current_price - trade.entry) / reward_distance
            if progress >= 0.75:
                trade.stop = max(trade.stop, trade.entry + reward_distance * 0.25)
            elif progress >= 0.50:
                trade.stop = max(trade.stop, trade.entry)

            if force_status:
                trade.status = force_status
                trade.closed_at = datetime.now(timezone.utc)
            elif trade.current_price <= trade.stop:
                trade.status = "STOPPED"
                trade.closed_at = datetime.now(timezone.utc)
            elif trade.current_price >= trade.target:
                trade.status = "TARGET"
                trade.closed_at = datetime.now(timezone.utc)
            db.commit()
            db.refresh(trade)
            if trade.status != "OPEN":
                self._audit("trade.closed", {"tradeId": trade.id, "status": trade.status, "pnl": trade.pnl})
            return self._trade_dict(trade)

    def _trade_ltp(self, provider, trade: Trade) -> float:
        meta = self._meta(trade)
        if meta.get("syntheticDemo"):
            if not hasattr(provider, "synthetic_option_ltp"):
                raise RuntimeError("Synthetic paper trade requires a synthetic-capable market provider")
            return float(provider.synthetic_option_ltp(meta))
        return float(provider.quote_ltp(trade.symbol))

    def _best_exit_price(self, provider, trade: Trade) -> tuple[float, bool, str | None]:
        last_error = None
        attempts = max(1, settings.force_exit_retry_count)
        for attempt in range(attempts):
            try:
                return self._trade_ltp(provider, trade), True, None
            except Exception as exc:
                last_error = str(exc)
                if attempt + 1 < attempts:
                    sleep(max(0.0, settings.force_exit_retry_delay_seconds))
        return float(trade.current_price or trade.entry), False, last_error

    def force_flatten(self, provider, reason: str = "manual emergency flatten") -> list[dict]:
        with SessionLocal() as db:
            open_rows = self._open_rows(db)
        results = []
        for trade in open_rows:
            price, fresh, quote_error = (
                self._best_exit_price(provider, trade)
                if provider.market_ready
                else (float(trade.current_price or trade.entry), False, "market data unavailable")
            )
            closed = self.mark_trade(trade.id, price, force_status="FORCED_EXIT")
            sandbox = provider.sandbox_exit_with_retry(trade.symbol, trade.quantity, f"apex-flat-{trade.id}")
            item = {
                "trade": closed,
                "freshExitPrice": fresh,
                "quoteError": quote_error,
                "sandbox": sandbox,
                "reason": reason,
            }
            results.append(item)
            self._audit("risk.force_flatten", item)
        return results

    def list_trades(self, limit: int = 100) -> list[dict]:
        with SessionLocal() as db:
            rows = db.execute(select(Trade).order_by(Trade.id.desc()).limit(limit)).scalars().all()
            return [self._trade_dict(t) for t in rows]

    def _update_open_trades(self, provider, phase: dict) -> list[dict]:
        if self.state.killed:
            return self.force_flatten(provider, "kill switch")
        if phase["forceExit"]:
            return self.force_flatten(provider, "scheduled force exit")
        actions = []
        with SessionLocal() as db:
            open_rows = self._open_rows(db)
        for trade in open_rows:
            try:
                ltp = self._trade_ltp(provider, trade)
                result = self.mark_trade(trade.id, ltp)
                if result["status"] != "OPEN":
                    sandbox = provider.sandbox_exit_with_retry(
                        trade.symbol,
                        trade.quantity,
                        f"apex-exit-{trade.id}",
                    )
                    actions.append({"exit": result, "sandbox": sandbox})
            except Exception as exc:
                self._audit("trade.monitor_error", {"tradeId": trade.id, "error": str(exc)})
        return actions

    def monitor_open_positions(self, provider, phase: dict | None = None) -> list[dict]:
        """Refresh open paper positions without running a full strategy scan."""
        with self._position_lock:
            self.state.last_position_update_at = datetime.now(timezone.utc).isoformat()
            return self._update_open_trades(provider, phase or self._market_phase())

    def run_premarket_research(self, provider, force: bool = False) -> dict:
        today = datetime.now(IST).date().isoformat()
        if not force and self.state.last_premarket_date == today and self.state.market_research:
            return self.state.market_research
        if not provider.market_ready:
            result = {"status": "market_data_unavailable", "runDate": today}
            self.state.market_research = result
            return result
        analytics = build_market_research(provider)
        news = ollama_research.news_research()
        llm_summary = ollama_research.summarize(analytics, news)
        result = {
            "status": "completed",
            "runDate": today,
            "scheduledTime": settings.premarket_research_time,
            "analytics": analytics,
            "news": news,
            "llm": llm_summary,
        }
        self.state.last_premarket_date = today
        self.state.market_research = result
        self._audit("research.premarket", result)
        return result

    def _scan_one(self, provider, name: str, key: str, capital: float) -> dict:
        candles = provider.intraday_candles(key)
        if not provider.candles_fresh(candles):
            return {
                "action": "NO_TRADE",
                "score": 0.0,
                "reason": "stale/missing market data",
                "index": name,
                "underlyingKey": key,
            }
        context = market_context(candles)
        arm = adaptive_learner.choose_arm(context, capital)
        signal = evaluate_signal(candles, arm)
        signal.update({"index": name, "underlyingKey": key, "chosenStrategy": arm.name})
        return signal

    def _real_option_candidate(self, provider, best: dict, deployable: float) -> tuple[dict | None, int, str | None]:
        expiry = provider.nearest_expiry(best["underlyingKey"])
        if not expiry:
            return None, 0, None
        chain = provider.option_chain(best["underlyingKey"], expiry)
        option = select_option(chain, best["action"], best["underlyingPrice"], deployable)
        if not option:
            return None, 0, expiry
        contracts = provider.option_contracts(best["underlyingKey"])
        contract = next((x for x in contracts if x.get("instrument_key") == option["instrumentKey"]), None)
        if not contract:
            return None, 0, expiry
        lot_size = int(contract.get("lot_size") or contract.get("minimum_lot") or 0)
        return option, lot_size, expiry

    def automation_cycle(self, provider) -> dict:
        self.state.last_cycle_at = datetime.now(timezone.utc).isoformat()
        self.state.last_error = None
        phase = self._market_phase()
        decision: dict = {"phase": phase, "action": "NO_TRADE", "provider": provider.status()}
        try:
            adaptive_learner.learn()
            now_ist = datetime.now(IST)
            if (
                settings.premarket_research_enabled
                and provider.market_ready
                and now_ist.weekday() < 5
                and now_ist.strftime("%H:%M") >= settings.premarket_research_time
                and self.state.last_premarket_date != now_ist.date().isoformat()
            ):
                decision["premarketResearch"] = self.run_premarket_research(provider)

            if (
                provider.market_ready
                and now_ist.weekday() < 5
                and now_ist.strftime("%H:%M") >= settings.daily_research_time
            ):
                decision["research"] = adaptive_learner.daily_research(provider)

            exits = (
                self.monitor_open_positions(provider, phase)
                if provider.market_ready or self.state.killed or phase["forceExit"]
                else []
            )
            decision["exits"] = exits

            risk = self.risk_snapshot()
            if risk["monthlyTargetLocked"]:
                if risk["openPositions"]:
                    decision["monthlyTargetFlatten"] = self.force_flatten(provider, "monthly profit target reached")
                self.state.mode = "SAFE"
                decision["reason"] = "monthly profit target reached; SAFE lock until next month"
                self.state.last_decision = decision
                return decision
            if self.state.killed:
                decision["reason"] = "kill switch active"
                self.state.last_decision = decision
                return decision
            if self.state.mode != "PAPER" or not settings.auto_trading_enabled:
                decision["reason"] = "automation disabled or mode is not PAPER"
                self.state.last_decision = decision
                return decision
            if not provider.market_ready:
                decision["reason"] = "market data provider is not ready"
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

            ranked = []
            keys = settings.underlying_keys
            with ThreadPoolExecutor(max_workers=len(keys)) as pool:
                futures = {
                    pool.submit(self._scan_one, provider, name, key, capital): name
                    for name, key in keys.items()
                }
                for future in as_completed(futures):
                    try:
                        ranked.append(future.result())
                    except Exception as exc:
                        ranked.append({
                            "action": "NO_TRADE",
                            "score": 0.0,
                            "reason": f"scan failure: {exc}",
                            "index": futures[future],
                        })
            ranked.sort(key=lambda x: x.get("score", 0), reverse=True)
            decision["signals"] = ranked
            best = ranked[0] if ranked else {"action": "NO_TRADE", "score": 0}
            if best.get("action") not in {"CE", "PE"}:
                decision["reason"] = "no strategy setup passed score + candlestick confirmation"
                self.state.last_decision = decision
                return decision

            synthetic_demo = not bool(getattr(provider, "supports_option_chain", True))
            expiry = None
            if synthetic_demo:
                option = provider.synthetic_option_candidate(
                    best["index"],
                    best["underlyingKey"],
                    best["action"],
                    best["underlyingPrice"],
                )
                lot_size = int(option.get("lotSize") or 1)
            else:
                option, lot_size, expiry = self._real_option_candidate(provider, best, deployable)
                if not option:
                    decision["reason"] = "no liquid/affordable option contract passed filters"
                    self.state.last_decision = decision
                    return decision
                if lot_size <= 0:
                    decision["reason"] = "selected option has invalid/missing exchange lot size"
                    self.state.last_decision = decision
                    return decision

            entry = float(option["ltp"])
            strategy_name = str(best.get("chosenStrategy") or best.get("strategy") or "")
            is_scalp = "SCALP" in strategy_name.upper()
            delta = max(0.05, abs(float(option.get("delta") or settings.yfinance_synthetic_delta)))
            previous_low = float(best.get("previousLow") or best["underlyingPrice"])
            previous_high = float(best.get("previousHigh") or best["underlyingPrice"])
            spot = float(best["underlyingPrice"])
            if best["action"] == "CE":
                structure_distance = max(0.0, spot - previous_low) * delta
            else:
                structure_distance = max(0.0, previous_high - spot) * delta
            fallback_stop = max(entry * settings.option_stop_pct / 100, 0.05)
            stop_distance = structure_distance if structure_distance > 0.05 else fallback_stop
            stop_distance = min(stop_distance, settings.scalp_max_stop_points) if is_scalp else stop_distance
            stop = round(max(0.05, entry - stop_distance), 2)
            if is_scalp:
                target = round(entry + settings.scalp_target_points, 2)
            else:
                target = round(entry + settings.swing_runner_target_points, 2)
            with SessionLocal() as db:
                qty = self._quantity_for_risk(entry, stop, lot_size, db)
                risk_limit = self._limits(db)["perTrade"]
            if qty <= 0:
                decision["reason"] = "one valid paper unit/lot does not fit capital/risk budget"
                self.state.last_decision = decision
                return decision

            initial_risk = round((entry - stop) * qty, 2)
            context = best.get("context") or {}
            chart_symbol = "BSE:SENSEX" if best["index"] == "SENSEX" else ("NSE:BANKNIFTY" if best["index"] == "BANKNIFTY" else "NSE:NIFTY")
            chart_url = f"https://www.tradingview.com/chart/?symbol={quote(chart_symbol, safe='')}"
            llm_review = ollama_advisor.analyze_trade({
                "index": best["index"],
                "direction": best["action"],
                "signalScore": best["score"],
                "strategy": strategy_name,
                "context": context,
                "patterns": best.get("patterns"),
                "underlyingPrice": best["underlyingPrice"],
                "previousCandleLow": previous_low,
                "previousCandleHigh": previous_high,
                "entry": entry,
                "stop": stop,
                "target": target,
                "quantity": qty,
                "lotSize": lot_size,
                "paperOnly": True,
            })
            meta = {
                "strategy": strategy_name,
                "tradeStyle": "SCALP" if is_scalp else "SWING",
                "stopModel": "previous-candle structure mapped to option premium via delta; fallback percentage stop",
                "previousCandleLow": previous_low,
                "previousCandleHigh": previous_high,
                "structureStopDistance": round(stop_distance, 2),
                "firstTarget": round(entry + (settings.scalp_target_points if is_scalp else settings.swing_first_target_points), 2),
                "runnerTarget": target,
                "partialBookPct": 0.0 if is_scalp else settings.swing_partial_pct,
                "pullbackEntryPoints": settings.scalp_entry_pullback_points if is_scalp else 0.0,
                "chartSymbol": chart_symbol,
                "chartUrl": chart_url,
                "ollamaReview": llm_review,
                "signalScore": best["score"],
                "index": best["index"],
                "underlyingKey": best["underlyingKey"],
                "contextKey": context.get("key", "UNKNOWN"),
                "context": context,
                "patterns": best.get("patterns"),
                "direction": best["action"],
                "expiry": expiry,
                "strike": option["strike"],
                "selectionScore": option["selectionScore"],
                "spreadPct": option["spreadPct"],
                "delta": option["delta"],
                "initialRisk": initial_risk,
                "riskLimit": round(risk_limit, 2),
                "capitalAtEntry": round(capital, 2),
                "syntheticDemo": synthetic_demo,
            }
            if synthetic_demo:
                meta.update({
                    "underlyingEntry": float(option["underlyingEntry"]),
                    "syntheticEntryPremium": entry,
                    "syntheticModel": "entryPremium + directional underlying move × fixed delta",
                    "syntheticWarning": "Strategy demo only; not real NSE option premium, Greeks, spread or exchange lot execution.",
                })

            sandbox = provider.place_sandbox_order(
                option["instrumentKey"],
                qty,
                "BUY",
                f"apex-entry-{best['index'].lower()}",
            )
            meta["sandboxEntryOrderId"] = sandbox.get("orderId")
            trade = self.open_paper_trade({
                "symbol": option["instrumentKey"],
                "direction": best["action"],
                "entry": entry,
                "stop": stop,
                "target": target,
                "lot_size": lot_size,
                "quantity": qty,
                "reason": json.dumps(meta),
            })
            decision.update({
                "action": best["action"],
                "trade": trade,
                "option": option,
                "sandbox": sandbox,
                "meta": meta,
            })
            if synthetic_demo:
                decision["warning"] = "Yahoo demo uses synthetic option premium and unit sizing; do not interpret as real options execution."
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
            "id": t.id,
            "symbol": t.symbol,
            "direction": t.direction,
            "entry": t.entry,
            "stop": t.stop,
            "target": t.target,
            "quantity": t.quantity,
            "lotSize": t.lot_size,
            "currentPrice": t.current_price,
            "pnl": t.pnl,
            "grossPnl": meta.get("grossPnl", t.pnl),
            "estimatedCharges": meta.get("estimatedCharges", 0.0),
            "costModel": meta.get("costModel", {}),
            "status": t.status,
            "meta": meta,
            "openedAt": t.opened_at.isoformat() if t.opened_at else None,
            "closedAt": t.closed_at.isoformat() if t.closed_at else None,
            "chartUrl": meta.get("chartUrl"),
            "chartSymbol": meta.get("chartSymbol"),
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
                "positionMonitor": {
                    "running": self.state.position_monitor_running,
                    "intervalSeconds": settings.position_monitor_interval_seconds,
                    "lastUpdateAt": self.state.last_position_update_at,
                },
                "lastDecision": self.state.last_decision,
                "lastError": self.state.last_error,
                "tradeWindow": f"{settings.trade_start_time}-{settings.stop_new_trade_time}",
                "forceExit": settings.force_exit_time,
                "researchTime": settings.daily_research_time,
                "premarketResearchTime": settings.premarket_research_time,
            },
            "marketResearch": self.state.market_research or {},
            "broker": broker,
            "market": market or {},
            "risk": self.risk_snapshot(),
            "performance": self.performance_snapshot(),
            "learning": adaptive_learner.snapshot(),
            "learningWorker": learning_worker.snapshot(),
            "ollama": {
                "enabled": settings.ollama_enabled,
                "configured": ollama_advisor.configured,
                "model": settings.ollama_model,
                "webSearchEnabled": settings.ollama_web_search_enabled,
                "role": "advisory research/review only; hard risk remains deterministic",
            },
        }


trading_engine = TradingEngine()
