from dataclasses import dataclass
from apex_forex.domain import PaperOrderRequest, Side, PaperTrade


@dataclass
class RiskDecision:
    allowed: bool
    reason: str


class RiskEngine:
    def __init__(self, max_risk_per_trade: float, max_daily_loss: float, max_open_positions: int):
        self.max_risk_per_trade = max_risk_per_trade
        self.max_daily_loss = max_daily_loss
        self.max_open_positions = max_open_positions

    def evaluate(self, order: PaperOrderRequest, open_trades: list[PaperTrade],
                 realized_daily_pnl: float) -> RiskDecision:
        if len(open_trades) >= self.max_open_positions:
            return RiskDecision(False, "Maximum open paper positions reached")
        if realized_daily_pnl <= -abs(self.max_daily_loss):
            return RiskDecision(False, "Daily loss limit reached; trading locked")
        if order.side == Side.LONG and not (order.stop_loss < order.entry_price < order.target):
            return RiskDecision(False, "LONG requires stop_loss < entry < target")
        if order.side == Side.SHORT and not (order.target < order.entry_price < order.stop_loss):
            return RiskDecision(False, "SHORT requires target < entry < stop_loss")
        risk_per_unit = abs(order.entry_price - order.stop_loss)
        if risk_per_unit * order.quantity > self.max_risk_per_trade:
            return RiskDecision(False, "Trade risk exceeds configured per-trade limit")
        return RiskDecision(True, "Risk checks passed")
