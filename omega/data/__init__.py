"""Market-data sources."""

from __future__ import annotations

from ..config import AppConfig
from .base import DataFeed, resample, sanitize
from .csv_feed import CsvFeed
from .synthetic import SyntheticFeed, default_spec
from .timeframes import delta, minutes, normalize, pandas_rule, ratio

__all__ = [
    "DataFeed", "CsvFeed", "SyntheticFeed", "build_feed", "default_spec",
    "sanitize", "resample", "minutes", "delta", "normalize", "pandas_rule", "ratio",
]


def build_feed(cfg: AppConfig) -> DataFeed:
    """Instantiate the feed named by ``cfg.data.source``."""
    source = (cfg.data.source or "synthetic").lower()
    if source == "mt5":
        from .mt5_feed import MT5Feed  # imported lazily: Windows-only dependency

        return MT5Feed(cfg.mt5)
    if source == "csv":
        return CsvFeed(cfg.data.csv_dir)
    if source == "synthetic":
        return SyntheticFeed(
            seed=cfg.data.synthetic_seed,
            bars=cfg.data.synthetic_bars,
            stream=cfg.data.synthetic_stream,
            speed=cfg.data.synthetic_speed,
        )
    raise ValueError(f"Unknown data.source={cfg.data.source!r} (mt5 | csv | synthetic)")
