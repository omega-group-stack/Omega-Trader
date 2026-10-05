"""Momentum / oscillator indicators."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .trend import ema, rma, sma


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's Relative Strength Index."""
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = rma(gain, period)
    avg_loss = rma(loss, period)
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = avg_gain / avg_loss
    out = 100.0 - (100.0 / (1.0 + rs))
    return out.where(avg_loss != 0, 100.0).where(avg_gain != 0, out)


def stoch_rsi(series: pd.Series, period: int = 14, smooth: int = 3) -> pd.Series:
    r = rsi(series, period)
    lo = r.rolling(period).min()
    hi = r.rolling(period).max()
    raw = (r - lo) / (hi - lo).replace(0.0, np.nan)
    return (raw * 100.0).rolling(smooth).mean()


def stochastic(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    k_period: int = 14,
    d_period: int = 3,
    smooth: int = 3,
) -> pd.DataFrame:
    lowest = low.rolling(k_period).min()
    highest = high.rolling(k_period).max()
    raw_k = 100.0 * (close - lowest) / (highest - lowest).replace(0.0, np.nan)
    k = raw_k.rolling(smooth).mean()
    return pd.DataFrame({"k": k, "d": k.rolling(d_period).mean()})


def macd(
    series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> pd.DataFrame:
    line = ema(series, fast) - ema(series, slow)
    sig = ema(line, signal)
    return pd.DataFrame({"macd": line, "signal": sig, "hist": line - sig})


def cci(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 20) -> pd.Series:
    tp = (high + low + close) / 3.0
    ma = sma(tp, period)
    md = (tp - ma).abs().rolling(period).mean()
    return (tp - ma) / (0.015 * md.replace(0.0, np.nan))


def roc(series: pd.Series, period: int = 10) -> pd.Series:
    return series.pct_change(period) * 100.0


def williams_r(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14
) -> pd.Series:
    highest = high.rolling(period).max()
    lowest = low.rolling(period).min()
    return -100.0 * (highest - close) / (highest - lowest).replace(0.0, np.nan)


def awesome_oscillator(high: pd.Series, low: pd.Series) -> pd.Series:
    hl2 = (high + low) / 2.0
    return sma(hl2, 5) - sma(hl2, 34)


def momentum_divergence(
    price: pd.Series, oscillator: pd.Series, lookback: int = 20
) -> pd.Series:
    """+1 bullish divergence, -1 bearish divergence, 0 none.

    Compares the slope of price extremes against the oscillator's extremes over
    a rolling window — a cheap but effective classic divergence read.
    """
    price_low = price.rolling(lookback).min()
    price_high = price.rolling(lookback).max()
    osc_at_low = oscillator.rolling(lookback).min()
    osc_at_high = oscillator.rolling(lookback).max()

    bullish = (price <= price_low * 1.0005) & (oscillator > osc_at_low * 1.02)
    bearish = (price >= price_high * 0.9995) & (oscillator < osc_at_high * 0.98)
    return pd.Series(
        np.where(bullish, 1.0, np.where(bearish, -1.0, 0.0)), index=price.index
    )
