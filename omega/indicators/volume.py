"""Volume / flow indicators.

FX brokers report *tick volume* rather than traded size; it is still a solid
proxy for activity and participation, which is all these indicators need.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .trend import ema, rma, sma


def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    direction = np.sign(close.diff().fillna(0.0))
    return (direction * volume).fillna(0.0).cumsum()


def money_flow_index(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    volume: pd.Series,
    period: int = 14,
) -> pd.Series:
    tp = (high + low + close) / 3.0
    raw_flow = tp * volume
    up = raw_flow.where(tp > tp.shift(1), 0.0)
    down = raw_flow.where(tp < tp.shift(1), 0.0)
    pos = up.rolling(period).sum()
    neg = down.rolling(period).sum()
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = pos / neg.replace(0.0, np.nan)
    return 100.0 - (100.0 / (1.0 + ratio))


def chaikin_money_flow(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    volume: pd.Series,
    period: int = 20,
) -> pd.Series:
    rng = (high - low).replace(0.0, np.nan)
    mfm = ((close - low) - (high - close)) / rng
    mfv = mfm * volume
    return mfv.rolling(period).sum() / volume.rolling(period).sum().replace(0.0, np.nan)


def volume_oscillator(volume: pd.Series, fast: int = 5, slow: int = 20) -> pd.Series:
    f = sma(volume, fast)
    s = sma(volume, slow)
    return 100.0 * (f - s) / s.replace(0.0, np.nan)


def force_index(close: pd.Series, volume: pd.Series, period: int = 13) -> pd.Series:
    return ema(close.diff() * volume, period)


def vwap(
    high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series
) -> pd.Series:
    """Session-anchored VWAP (resets each calendar day)."""
    tp = (high + low + close) / 3.0
    idx = pd.DatetimeIndex(close.index)
    day = pd.Series(idx.date, index=close.index)
    pv = (tp * volume).groupby(day).cumsum()
    vv = volume.groupby(day).cumsum().replace(0.0, np.nan)
    return pv / vv


def relative_volume(volume: pd.Series, period: int = 20) -> pd.Series:
    """Current volume vs its rolling average (1.0 == normal)."""
    return volume / sma(volume, period).replace(0.0, np.nan)
