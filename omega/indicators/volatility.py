"""Volatility / range indicators."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .trend import ema, rma, sma, true_range


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    return rma(true_range(high, low, close), period)


def natr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """ATR as a percentage of price — comparable across symbols."""
    return 100.0 * atr(high, low, close, period) / close.replace(0.0, np.nan)


def bollinger(series: pd.Series, period: int = 20, std: float = 2.0) -> pd.DataFrame:
    mid = sma(series, period)
    dev = series.rolling(period).std(ddof=0)
    upper = mid + std * dev
    lower = mid - std * dev
    width = (upper - lower) / mid.replace(0.0, np.nan)
    pct_b = (series - lower) / (upper - lower).replace(0.0, np.nan)
    return pd.DataFrame(
        {"mid": mid, "upper": upper, "lower": lower, "bandwidth": width, "pct_b": pct_b}
    )


def keltner(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 20,
    mult: float = 1.5,
) -> pd.DataFrame:
    mid = ema(close, period)
    rng = atr(high, low, close, period)
    return pd.DataFrame(
        {"mid": mid, "upper": mid + mult * rng, "lower": mid - mult * rng}
    )


def donchian(high: pd.Series, low: pd.Series, period: int = 20) -> pd.DataFrame:
    upper = high.rolling(period).max()
    lower = low.rolling(period).min()
    return pd.DataFrame({"upper": upper, "lower": lower, "mid": (upper + lower) / 2.0})


def squeeze(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 20
) -> pd.Series:
    """True when Bollinger Bands sit inside Keltner Channels (energy coiling)."""
    bb = bollinger(close, period)
    kc = keltner(high, low, close, period)
    return (bb["upper"] < kc["upper"]) & (bb["lower"] > kc["lower"])


def choppiness(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14
) -> pd.Series:
    """Choppiness Index: ~100 = sideways chop, ~0 = strong directional move."""
    tr_sum = true_range(high, low, close).rolling(period).sum()
    rng = high.rolling(period).max() - low.rolling(period).min()
    with np.errstate(divide="ignore", invalid="ignore"):
        val = 100.0 * np.log10((tr_sum / rng.replace(0.0, np.nan))) / np.log10(period)
    return val


def rolling_percentile(series: pd.Series, window: int = 200) -> pd.Series:
    """Percentile rank of the latest value inside its rolling window, in [0, 1]."""
    return series.rolling(window, min_periods=max(10, window // 10)).apply(
        lambda w: float((w <= w[-1]).mean()), raw=True
    )


def realized_volatility(close: pd.Series, period: int = 20) -> pd.Series:
    return close.pct_change().rolling(period).std(ddof=0) * np.sqrt(period)
