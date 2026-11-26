from datetime import UTC, datetime
from uuid import uuid4

from apex_forex.domain import MarketTick, PaperOrderRequest, PaperTrade, Side
from apex_forex.risk import RiskEngine


class PaperBroker:
    """Deterministic simulation only. No broker order endpoint exists in this class."""

    def __init__(self, starting_balance: float, risk: RiskEngine,
                 slippage_ticks: int = 1, fee_per_order: float = 20):
        self.starting_balance = starting_balance
        self.balance = starting_balance
        self.risk = risk
        self.slippage_ticks = max(0, slippage_ticks)
        self.fee_per_order = max(0, fee_per_order)
        self.trades: list[PaperTrade] = []
        self.realized_daily_pnl = 0.0

    def restore_trades(self, trades: list[PaperTrade]) -> None:
        """Restore the durable ledger before accepting new paper orders."""
        self.trades = trades
        closed = [trade for trade in trades if trade.status == "CLOSED"]
        self.balance = self.starting_balance + sum(trade.net_pnl for trade in closed)
        today = datetime.now(UTC).date()
        self.realized_daily_pnl = sum(
            trade.net_pnl for trade in closed
            if trade.closed_at and trade.closed_at.astimezone(UTC).date() == today
        )

    @property
    def open_trades(self) -> list[PaperTrade]:
        return [trade for trade in self.trades if trade.status == "OPEN"]

    def open_trade(self, order: PaperOrderRequest) -> PaperTrade:
        decision = self.risk.evaluate(order, self.open_trades, self.realized_daily_pnl)
        if not decision.allowed:
            raise ValueError(decision.reason)
        slip = self.slippage_ticks * order.tick_size
        fill = order.entry_price + slip if order.side == Side.LONG else order.entry_price - slip
        trade = PaperTrade(
            id=str(uuid4()), instrument_key=order.instrument_key, side=order.side,
            quantity=order.quantity, entry_price=round(fill, 8), stop_loss=order.stop_loss,
            target=order.target, tick_size=order.tick_size, strategy=order.strategy,
            fees=self.fee_per_order,
        )
        self.trades.append(trade)
        return trade

    def on_tick(self, tick: MarketTick) -> list[PaperTrade]:
        closed = []
        for trade in self.open_trades:
            if tick.instrument_key != trade.instrument_key:
                continue
            executable_price = (
                tick.bid if trade.side == Side.LONG and tick.bid is not None else
                tick.ask if trade.side == Side.SHORT and tick.ask is not None else tick.ltp
            )
            hit_sl = executable_price <= trade.stop_loss if trade.side == Side.LONG else executable_price >= trade.stop_loss
            hit_target = executable_price >= trade.target if trade.side == Side.LONG else executable_price <= trade.target
            if hit_sl or hit_target:
                slip = self.slippage_ticks * trade.tick_size
                exit_price = executable_price - slip if trade.side == Side.LONG else executable_price + slip
                self.close_trade(trade.id, exit_price)
                closed.append(trade)
        return closed

    def close_trade(self, trade_id: str, exit_price: float) -> PaperTrade:
        trade = next((t for t in self.open_trades if t.id == trade_id), None)
        if trade is None:
            raise ValueError("Open paper trade not found")
        direction = 1 if trade.side == Side.LONG else -1
        trade.exit_price = exit_price
        trade.gross_pnl = round((exit_price - trade.entry_price) * direction * trade.quantity, 2)
        trade.fees += self.fee_per_order
        trade.net_pnl = round(trade.gross_pnl - trade.fees, 2)
        trade.status = "CLOSED"
        trade.closed_at = datetime.now(UTC)
        self.realized_daily_pnl += trade.net_pnl
        self.balance += trade.net_pnl
        return trade
