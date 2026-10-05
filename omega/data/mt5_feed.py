"""MetaTrader 5 data feed.

Requires the official ``MetaTrader5`` package and a running MT5 terminal
(Windows, or Wine/VPS). Import of this module is safe anywhere — the hard
dependency is only resolved when you actually connect.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

import pandas as pd

from ..config import MT5Config
from ..core.types import SymbolSpec, Tick
from .base import DataFeed, sanitize
from .timeframes import normalize

log = logging.getLogger(__name__)

_MT5: Any = None


def mt5_module() -> Any:
    """Import MetaTrader5 lazily with a helpful error message."""
    global _MT5
    if _MT5 is None:
        try:
            import MetaTrader5 as mt5  # type: ignore
        except ImportError as exc:  # pragma: no cover - platform dependent
            raise ImportError(
                "The MetaTrader5 package is required for live/paper trading on MT5.\n"
                "  pip install MetaTrader5   (Windows only)\n"
                "On Linux/macOS use execution.mode=backtest with data.source=csv, "
                "or run the bot on a Windows VPS next to the terminal."
            ) from exc
        _MT5 = mt5
    return _MT5


class MT5Connection:
    """Reference-counted connection to the local MT5 terminal."""

    _instance: Optional["MT5Connection"] = None

    def __init__(self, cfg: MT5Config) -> None:
        self.cfg = cfg
        self.connected = False

    @classmethod
    def shared(cls, cfg: MT5Config) -> "MT5Connection":
        if cls._instance is None:
            cls._instance = cls(cfg)
        return cls._instance

    def connect(self) -> None:
        if self.connected:
            return
        mt5 = mt5_module()
        kwargs: dict[str, Any] = {"timeout": self.cfg.timeout_ms,
                                  "portable": self.cfg.portable}
        if self.cfg.terminal_path:
            kwargs["path"] = self.cfg.terminal_path
        if self.cfg.login:
            kwargs.update(login=int(self.cfg.login), password=self.cfg.password,
                          server=self.cfg.server)

        if not mt5.initialize(**kwargs):
            raise ConnectionError(f"MT5 initialize() failed: {mt5.last_error()}")

        info = mt5.account_info()
        if info is None:
            mt5.shutdown()
            raise ConnectionError(f"MT5 account_info() failed: {mt5.last_error()}")
        self.connected = True
        log.info(
            "Connected to MT5 — account %s on %s (%s, leverage 1:%s)",
            info.login, info.server, info.currency, info.leverage,
        )

    def shutdown(self) -> None:
        if self.connected:
            mt5_module().shutdown()
            self.connected = False

    def ensure(self) -> Any:
        if not self.connected:
            self.connect()
        return mt5_module()


class MT5Feed(DataFeed):
    name = "mt5"

    def __init__(self, cfg: MT5Config) -> None:
        self.cfg = cfg
        self.conn = MT5Connection.shared(cfg)

    def connect(self) -> None:
        self.conn.connect()

    def close(self) -> None:
        self.conn.shutdown()

    # -- DataFeed -----------------------------------------------------------
    def candles(
        self,
        symbol: str,
        timeframe: str,
        count: int = 1_000,
        end: Optional[datetime] = None,
    ) -> pd.DataFrame:
        mt5 = self.conn.ensure()
        from .timeframes import to_mt5

        tf = to_mt5(normalize(timeframe))
        self._select(symbol)

        if end is None:
            rates = mt5.copy_rates_from_pos(symbol, tf, 0, int(count))
        else:
            rates = mt5.copy_rates_from(symbol, tf, end, int(count))

        if rates is None or len(rates) == 0:
            raise RuntimeError(
                f"MT5 returned no bars for {symbol} {timeframe}: {mt5.last_error()}"
            )

        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        if "tick_volume" in df.columns:
            df["volume"] = df["tick_volume"]
        return sanitize(df)

    def tick(self, symbol: str) -> Tick:
        mt5 = self.conn.ensure()
        self._select(symbol)
        t = mt5.symbol_info_tick(symbol)
        if t is None:
            raise RuntimeError(f"No tick for {symbol}: {mt5.last_error()}")
        return Tick(
            time=datetime.fromtimestamp(t.time, tz=timezone.utc),
            bid=float(t.bid),
            ask=float(t.ask),
        )

    def symbol_spec(self, symbol: str) -> SymbolSpec:
        mt5 = self.conn.ensure()
        info = self._select(symbol)
        digits = int(info.digits)
        return SymbolSpec(
            symbol=symbol,
            digits=digits,
            point=float(info.point),
            tick_size=float(info.trade_tick_size or info.point),
            tick_value=float(info.trade_tick_value or 1.0),
            contract_size=float(info.trade_contract_size or 100_000.0),
            min_lot=float(info.volume_min),
            max_lot=float(info.volume_max),
            lot_step=float(info.volume_step),
            margin_rate=float(getattr(info, "margin_initial", 0.0) or 0.0333),
            swap_long=float(getattr(info, "swap_long", 0.0) or 0.0),
            swap_short=float(getattr(info, "swap_short", 0.0) or 0.0),
            base=str(getattr(info, "currency_base", "") or ""),
            quote=str(getattr(info, "currency_profit", "") or ""),
        )

    def spread_points(self, symbol: str) -> float:
        mt5 = self.conn.ensure()
        info = self._select(symbol)
        return float(getattr(info, "spread", 0) or 0)

    # -- internals ----------------------------------------------------------
    def _select(self, symbol: str) -> Any:
        mt5 = self.conn.ensure()
        info = mt5.symbol_info(symbol)
        if info is None:
            raise ValueError(f"Symbol {symbol!r} not found in Market Watch")
        if not info.visible and not mt5.symbol_select(symbol, True):
            raise RuntimeError(f"Could not select {symbol} in Market Watch")
        return mt5.symbol_info(symbol)
