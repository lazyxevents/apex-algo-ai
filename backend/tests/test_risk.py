from app.trading import TradingEngine


def test_quantity_is_whole_lot():
    engine = TradingEngine()
    qty = engine._quantity_for_risk(entry=100, stop=98, lot_size=25)
    assert qty % 25 == 0
    assert qty <= 100
