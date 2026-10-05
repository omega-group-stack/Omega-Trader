"""Data-feed abstraction.

Every feed returns a tz-aware, time-indexed OHLCV DataFrame with the columns
``open, high, low, close, volume, spread`` sorted ascending and free of
duplicate timestamps. Downstream code can rely on that contract.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional

import pandas as pd

from ..core.types import SymbolSpec
from .timeframes import pandas_rule

REQUIRED_COLUMNS = ("open", "high", "low", "close", "volume", "spread")


class DataFeed(ABC):
    """Source of historical and (optionally) streaming market data."""

    name: str = "feed"

    @abstractmethod
    def candles(
        self,
        symbol: str,
        timeframe: str,
        count: int = 1_000,
        end: Optional[datetime] = None,
    ) -> pd.DataFrame:
        """Return the last ``count`` closed bars at or before ``end``."""

    def symbol_spec(self, symbol: str) -> Optional[SymbolSpec]:
        """Broker contract spec, when the feed knows it."""
        return None

    def connect(self) -> None:
        return None

    def close(self) -> None:
        return None

    # -- convenience --------------------------------------------------------
    def __enter__(self) -> "DataFeed":
        self.connect()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def sanitize(df: pd.DataFrame) -> pd.DataFrame:
    """Enforce the OHLCV contract: columns, dtypes, ordering, sanity."""
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=list(REQUIRED_COLUMNS))

    out = df.copy()
    out.columns = [str(c).strip().lower() for c in out.columns]

    aliases = {
        "date": "time", "datetime": "time", "timestamp": "time",
        "o": "open", "h": "high", "l": "low", "c": "close",
        "vol": "volume", "tick_volume": "volume", "tickvol": "volume",
        "real_volume": "real_volume",
    }
    out = out.rename(columns={k: v for k, v in aliases.items() if k in out.columns})

    if "time" in out.columns:
        out["time"] = pd.to_datetime(out["time"], utc=True, errors="coerce")
        out = out.dropna(subset=["time"]).set_index("time")
    else:
        out.index = pd.to_datetime(out.index, utc=True, errors="coerce")
        out = out[out.index.notna()]

    for col in REQUIRED_COLUMNS:
        if col not in out.columns:
            out[col] = 0.0
        out[col] = pd.to_numeric(out[col], errors="coerce")

    out = out[list(REQUIRED_COLUMNS)]
    out = out.dropna(subset=["open", "high", "low", "close"])
    out = out[~out.index.duplicated(keep="last")].sort_index()

    # guard against corrupt bars where high/low do not bracket open/close
    out["high"] = out[["high", "open", "close"]].max(axis=1)
    out["low"] = out[["low", "open", "close"]].min(axis=1)
    out["volume"] = out["volume"].fillna(0.0)
    out["spread"] = out["spread"].fillna(0.0)
    out.index.name = "time"
    return out


def resample(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Aggregate an OHLCV frame up to a higher timeframe."""
    rule = pandas_rule(timeframe)
    agg = {
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
        "spread": "mean",
    }
    out = df.resample(rule, label="left", closed="left").agg(agg)
    return out.dropna(subset=["open", "high", "low", "close"])
