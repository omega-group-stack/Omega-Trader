"""Timeframe helpers shared by every data source."""

from __future__ import annotations

from datetime import timedelta
from typing import Dict

# Canonical name -> minutes
TIMEFRAME_MINUTES: Dict[str, int] = {
    "M1": 1,
    "M2": 2,
    "M3": 3,
    "M4": 4,
    "M5": 5,
    "M6": 6,
    "M10": 10,
    "M12": 12,
    "M15": 15,
    "M20": 20,
    "M30": 30,
    "H1": 60,
    "H2": 120,
    "H3": 180,
    "H4": 240,
    "H6": 360,
    "H8": 480,
    "H12": 720,
    "D1": 1_440,
    "W1": 10_080,
    "MN1": 43_200,
}

# pandas resample rule per timeframe
PANDAS_RULE: Dict[str, str] = {
    name: (f"{mins}min" if mins < 1_440 else f"{mins // 1_440}D")
    for name, mins in TIMEFRAME_MINUTES.items()
}


def normalize(timeframe: str) -> str:
    tf = timeframe.strip().upper()
    if tf not in TIMEFRAME_MINUTES:
        raise ValueError(
            f"Unknown timeframe {timeframe!r}. Known: {', '.join(TIMEFRAME_MINUTES)}"
        )
    return tf


def minutes(timeframe: str) -> int:
    return TIMEFRAME_MINUTES[normalize(timeframe)]


def delta(timeframe: str) -> timedelta:
    return timedelta(minutes=minutes(timeframe))


def pandas_rule(timeframe: str) -> str:
    return PANDAS_RULE[normalize(timeframe)]


def ratio(higher: str, lower: str) -> int:
    """How many ``lower`` bars fit into one ``higher`` bar."""
    return max(1, minutes(higher) // minutes(lower))


def to_mt5(timeframe: str):  # pragma: no cover - requires MetaTrader5
    """Translate to the MetaTrader5 TIMEFRAME_* constant."""
    import MetaTrader5 as mt5  # type: ignore

    tf = normalize(timeframe)
    const = getattr(mt5, f"TIMEFRAME_{tf}", None)
    if const is None:
        raise ValueError(f"MetaTrader5 does not support timeframe {tf}")
    return const
