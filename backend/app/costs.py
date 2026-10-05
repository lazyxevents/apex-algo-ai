from __future__ import annotations

from .core import settings


def estimate_option_charges(entry: float, exit_price: float, quantity: int, exchange: str = "NSE", synthetic: bool = False) -> dict:
    entry = max(0.0, float(entry))
    exit_price = max(0.0, float(exit_price))
    quantity = max(0, int(quantity))
    buy_turnover = entry * quantity
    sell_turnover = exit_price * quantity
    total_turnover = buy_turnover + sell_turnover

    if synthetic:
        cost = total_turnover * settings.synthetic_roundtrip_cost_pct / 100
        return {
            "model": "normalized_synthetic",
            "brokerage": 0.0,
            "stt": 0.0,
            "transaction": 0.0,
            "sebi": 0.0,
            "gst": 0.0,
            "stamp": 0.0,
            "total": round(cost, 2),
        }

    exchange = exchange.upper()
    transaction_rate = settings.bse_option_transaction_pct if exchange == "BSE" else settings.nse_option_transaction_pct
    brokerage = settings.option_brokerage_per_order * 2
    stt = sell_turnover * settings.option_stt_sell_pct / 100
    transaction = total_turnover * transaction_rate / 100
    sebi = total_turnover * settings.sebi_turnover_pct / 100
    stamp = buy_turnover * settings.option_stamp_buy_pct / 100
    gst = (brokerage + transaction + sebi) * settings.gst_pct / 100
    total = brokerage + stt + transaction + sebi + stamp + gst
    return {
        "model": "zerodha_estimate_2026",
        "brokerage": round(brokerage, 2),
        "stt": round(stt, 2),
        "transaction": round(transaction, 2),
        "sebi": round(sebi, 2),
        "gst": round(gst, 2),
        "stamp": round(stamp, 2),
        "total": round(total, 2),
    }
