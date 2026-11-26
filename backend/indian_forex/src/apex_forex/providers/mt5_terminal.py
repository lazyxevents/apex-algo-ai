"""Read-only MetaTrader 5 Python terminal adapter.

Requires Windows, the official MetaTrader5 Python package and a locally installed
MT5 terminal. Deliberately exposes no order_send method.
"""
from datetime import datetime
from typing import Any


class MT5TerminalError(RuntimeError):
    pass


class MT5TerminalClient:
    def __init__(self, terminal_path: str | None = None, timeout_ms: int = 60_000):
        self.terminal_path = terminal_path
        self.timeout_ms = timeout_ms
        self._mt5 = None

    def _module(self):
        if self._mt5 is None:
            try:
                import MetaTrader5 as mt5
            except ImportError as exc:
                raise MT5TerminalError(
                    "Install the optional 'mt5' extra on a Windows machine with MT5 terminal"
                ) from exc
            self._mt5 = mt5
        return self._mt5

    def connect(self) -> dict[str, Any]:
        mt5 = self._module()
        kwargs = {"timeout": self.timeout_ms}
        if self.terminal_path:
            ok = mt5.initialize(self.terminal_path, **kwargs)
        else:
            ok = mt5.initialize(**kwargs)
        if not ok:
            raise MT5TerminalError(f"MT5 initialize failed: {mt5.last_error()}")
        terminal = mt5.terminal_info()
        if terminal is None:
            raise MT5TerminalError(f"MT5 terminal_info failed: {mt5.last_error()}")
        return {
            "connected": bool(getattr(terminal, "connected", False)),
            "trade_allowed": bool(getattr(terminal, "trade_allowed", False)),
            "company": str(getattr(terminal, "company", "")),
            "name": str(getattr(terminal, "name", "")),
            "path": str(getattr(terminal, "path", "")),
            "version": mt5.version(),
        }

    def disconnect(self) -> None:
        if self._mt5 is not None:
            self._mt5.shutdown()

    def account_snapshot(self) -> dict[str, Any]:
        mt5 = self._require_connected()
        account = mt5.account_info()
        if account is None:
            raise MT5TerminalError(f"MT5 account_info failed: {mt5.last_error()}")
        fields = (
            "login", "server", "currency", "leverage", "balance", "equity",
            "margin", "margin_free", "margin_level", "trade_allowed",
        )
        return {field: getattr(account, field, None) for field in fields}

    def forex_symbols(self) -> list[dict[str, Any]]:
        mt5 = self._require_connected()
        symbols = mt5.symbols_get()
        if symbols is None:
            raise MT5TerminalError(f"MT5 symbols_get failed: {mt5.last_error()}")
        allowed_modes = {
            mt5.SYMBOL_CALC_MODE_FOREX,
            mt5.SYMBOL_CALC_MODE_FOREX_NO_LEVERAGE,
        }
        result = []
        for symbol in symbols:
            if getattr(symbol, "trade_calc_mode", None) not in allowed_modes:
                continue
            result.append({
                "name": symbol.name,
                "base_currency": getattr(symbol, "currency_base", ""),
                "quote_currency": getattr(symbol, "currency_profit", ""),
                "digits": getattr(symbol, "digits", None),
                "point": getattr(symbol, "point", None),
                "trade_tick_size": getattr(symbol, "trade_tick_size", None),
                "volume_min": getattr(symbol, "volume_min", None),
                "volume_max": getattr(symbol, "volume_max", None),
                "volume_step": getattr(symbol, "volume_step", None),
                "visible": bool(getattr(symbol, "visible", False)),
            })
        return result

    def ticks_range(self, symbol: str, start: datetime, end: datetime) -> list[dict[str, Any]]:
        mt5 = self._require_connected()
        ticks = mt5.copy_ticks_range(symbol, start, end, mt5.COPY_TICKS_ALL)
        if ticks is None:
            raise MT5TerminalError(f"MT5 copy_ticks_range failed: {mt5.last_error()}")
        return [self._record(row) for row in ticks]

    def candles_range(
        self, symbol: str, timeframe: int, start: datetime, end: datetime,
    ) -> list[dict[str, Any]]:
        mt5 = self._require_connected()
        rates = mt5.copy_rates_range(symbol, timeframe, start, end)
        if rates is None:
            raise MT5TerminalError(f"MT5 copy_rates_range failed: {mt5.last_error()}")
        return [self._record(row) for row in rates]

    def _require_connected(self):
        mt5 = self._module()
        if mt5.terminal_info() is None:
            raise MT5TerminalError("MT5 terminal is not connected")
        return mt5

    @staticmethod
    def _record(row) -> dict[str, Any]:
        names = getattr(getattr(row, "dtype", None), "names", None)
        if names:
            return {
                name: item.item() if hasattr(item, "item") else item
                for name, item in zip(names, row)
            }
        if hasattr(row, "_asdict"):
            return row._asdict()
        return dict(row)
