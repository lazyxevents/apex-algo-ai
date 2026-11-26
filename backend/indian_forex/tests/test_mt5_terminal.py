from datetime import datetime, timezone
from types import SimpleNamespace

from apex_forex.providers.mt5_terminal import MT5TerminalClient


class FakeMT5:
    SYMBOL_CALC_MODE_FOREX = 0
    SYMBOL_CALC_MODE_FOREX_NO_LEVERAGE = 1
    COPY_TICKS_ALL = 0

    def __init__(self):
        self.initialized = False

    def initialize(self, *args, **kwargs):
        self.initialized = True
        return True

    def terminal_info(self):
        return SimpleNamespace(connected=True, trade_allowed=False, company="Demo", name="MT5", path="C:/MT5")

    def version(self):
        return (500, 1, "test")

    def account_info(self):
        return SimpleNamespace(login=123, server="Demo", currency="USD", leverage=100,
                               balance=1000, equity=1000, margin=0, margin_free=1000,
                               margin_level=0, trade_allowed=False)

    def symbols_get(self):
        return [
            SimpleNamespace(name="EURUSD", trade_calc_mode=0, currency_base="EUR",
                            currency_profit="USD", digits=5, point=0.00001,
                            trade_tick_size=0.00001, volume_min=0.01, volume_max=100,
                            volume_step=0.01, visible=True),
            SimpleNamespace(name="XAUUSD", trade_calc_mode=2, currency_base="XAU",
                            currency_profit="USD", digits=2, point=0.01,
                            trade_tick_size=0.01, volume_min=0.01, volume_max=100,
                            volume_step=0.01, visible=True),
        ]

    def copy_ticks_range(self, *args):
        return [{"time": 1, "bid": 1.1, "ask": 1.1001}]

    def copy_rates_range(self, *args):
        return [{"time": 1, "open": 1.1, "high": 1.2, "low": 1.0, "close": 1.15}]

    def last_error(self):
        return (0, "ok")

    def shutdown(self):
        self.initialized = False


def test_mt5_terminal_client_filters_to_forex_and_reads_account(monkeypatch):
    import sys
    fake = FakeMT5()
    monkeypatch.setitem(sys.modules, "MetaTrader5", fake)
    client = MT5TerminalClient()
    assert client.connect()["connected"]
    assert client.account_snapshot()["currency"] == "USD"
    symbols = client.forex_symbols()
    assert [symbol["name"] for symbol in symbols] == ["EURUSD"]
    assert client.ticks_range("EURUSD", datetime.now(timezone.utc), datetime.now(timezone.utc))
    assert client.candles_range("EURUSD", 1, datetime.now(timezone.utc), datetime.now(timezone.utc))
    client.disconnect()
