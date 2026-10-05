"""CSV data feed — for broker exports, Dukascopy/HistData dumps, etc.

Expected layout inside ``csv_dir``::

    data/EURUSD_M15.csv
    data/GBPUSD_H1.csv

Any of these column namings work (case-insensitive):
``time|date|datetime|timestamp``, ``open|o``, ``high|h``, ``low|l``,
``close|c``, ``volume|vol|tick_volume``, ``spread``.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from ..core.types import SymbolSpec
from .base import DataFeed, resample, sanitize
from .synthetic import default_spec
from .timeframes import minutes as tf_minutes
from .timeframes import normalize


class CsvFeed(DataFeed):
    name = "csv"

    def __init__(self, directory: str = "data") -> None:
        self.directory = Path(directory)
        self._cache: dict[str, pd.DataFrame] = {}

    def candles(
        self,
        symbol: str,
        timeframe: str,
        count: int = 1_000,
        end: Optional[datetime] = None,
    ) -> pd.DataFrame:
        tf = normalize(timeframe)
        df = self._load(symbol, tf)
        if end is not None:
            df = df[df.index <= pd.Timestamp(end, tz="UTC")]
        return df.tail(count).copy()

    def symbol_spec(self, symbol: str) -> SymbolSpec:
        return default_spec(symbol)

    # -- internals ----------------------------------------------------------
    def _load(self, symbol: str, timeframe: str) -> pd.DataFrame:
        key = f"{symbol.upper()}_{timeframe}"
        if key in self._cache:
            return self._cache[key]

        path = self._resolve(symbol, timeframe)
        if path is None:
            raise FileNotFoundError(
                f"No CSV for {symbol} {timeframe} in {self.directory.resolve()}. "
                f"Expected e.g. {self.directory}/{symbol.upper()}_{timeframe}.csv"
            )
        raw = pd.read_csv(path)
        df = sanitize(raw)

        # If the file holds a finer timeframe, aggregate it up.
        source_tf = self._timeframe_of(path)
        if source_tf and tf_minutes(source_tf) < tf_minutes(timeframe):
            df = resample(df, timeframe)

        self._cache[key] = df
        return df

    def _resolve(self, symbol: str, timeframe: str) -> Optional[Path]:
        sym = symbol.upper()
        candidates = [
            self.directory / f"{sym}_{timeframe}.csv",
            self.directory / f"{sym}-{timeframe}.csv",
            self.directory / f"{sym}{timeframe}.csv",
            self.directory / sym / f"{timeframe}.csv",
            self.directory / f"{sym}.csv",
        ]
        for c in candidates:
            if c.exists():
                return c
        matches = sorted(self.directory.glob(f"{sym}*.csv"))
        return matches[0] if matches else None

    @staticmethod
    def _timeframe_of(path: Path) -> Optional[str]:
        stem = path.stem.upper().replace("-", "_")
        for token in reversed(stem.split("_")):
            try:
                return normalize(token)
            except ValueError:
                continue
        return None
