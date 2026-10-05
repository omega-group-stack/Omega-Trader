"""Trend / direction indicators. Pure pandas — no TA-Lib dependency."""

from __future__ import annotations

import numpy as np
import pandas as pd


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period, min_periods=period).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def wma(series: pd.Series, period: int) -> pd.Series:
    weights = np.arange(1, period + 1, dtype=float)
    return series.rolling(period).apply(
        lambda w: float(np.dot(w, weights) / weights.sum()), raw=True
    )


def hma(series: pd.Series, period: int) -> pd.Series:
    """Hull MA — fast and smooth, great for slope reads."""
    half = max(1, period // 2)
    sqrt_p = max(1, int(np.sqrt(period)))
    return wma(2 * wma(series, half) - wma(series, period), sqrt_p)


def dema(series: pd.Series, period: int) -> pd.Series:
    e1 = ema(series, period)
    return 2 * e1 - ema(e1, period)


def rma(series: pd.Series, period: int) -> pd.Series:
    """Wilder's smoothing (used by ATR/ADX/RSI)."""
    return series.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    return pd.concat(
        [(high - low).abs(), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)


def adx(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14
) -> pd.DataFrame:
    """Average Directional Index with +DI / -DI (Wilder)."""
    up = high.diff()
    down = -low.diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=high.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=high.index)

    atr_ = rma(true_range(high, low, close), period)
    with np.errstate(divide="ignore", invalid="ignore"):
        plus_di = 100.0 * rma(plus_dm, period) / atr_
        minus_di = 100.0 * rma(minus_dm, period) / atr_
        dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    return pd.DataFrame(
        {"adx": rma(dx.fillna(0.0), period), "plus_di": plus_di, "minus_di": minus_di}
    )


def supertrend(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 10,
    multiplier: float = 3.0,
) -> pd.DataFrame:
    """Supertrend: ATR bands that flip with trend. Returns line + direction."""
    atr_ = rma(true_range(high, low, close), period)
    hl2 = (high + low) / 2.0
    upper = hl2 + multiplier * atr_
    lower = hl2 - multiplier * atr_

    n = len(close)
    final_upper = np.full(n, np.nan)
    final_lower = np.full(n, np.nan)
    direction = np.ones(n)  # +1 uptrend, -1 downtrend

    c = close.to_numpy(dtype=float)
    u = upper.to_numpy(dtype=float)
    lo = lower.to_numpy(dtype=float)

    for i in range(n):
        if i == 0 or np.isnan(u[i]) or np.isnan(final_upper[i - 1]):
            final_upper[i] = u[i]
            final_lower[i] = lo[i]
            direction[i] = 1.0
            continue
        final_upper[i] = (
            min(u[i], final_upper[i - 1]) if c[i - 1] <= final_upper[i - 1] else u[i]
        )
        final_lower[i] = (
            max(lo[i], final_lower[i - 1]) if c[i - 1] >= final_lower[i - 1] else lo[i]
        )
        if direction[i - 1] == 1.0:
            direction[i] = -1.0 if c[i] < final_lower[i] else 1.0
        else:
            direction[i] = 1.0 if c[i] > final_upper[i] else -1.0

    line = np.where(direction == 1.0, final_lower, final_upper)
    return pd.DataFrame(
        {"supertrend": line, "direction": direction}, index=close.index
    )


def parabolic_sar(
    high: pd.Series, low: pd.Series, step: float = 0.02, max_step: float = 0.2
) -> pd.Series:
    """Classic Wilder Parabolic SAR."""
    h = high.to_numpy(dtype=float)
    l = low.to_numpy(dtype=float)
    n = len(h)
    sar = np.full(n, np.nan)
    if n < 2:
        return pd.Series(sar, index=high.index)

    bull = True
    af = step
    ep = h[0]
    sar[0] = l[0]

    for i in range(1, n):
        prev = sar[i - 1]
        sar[i] = prev + af * (ep - prev)
        if bull:
            sar[i] = min(sar[i], l[i - 1], l[max(0, i - 2)])
            if h[i] > ep:
                ep, af = h[i], min(af + step, max_step)
            if l[i] < sar[i]:
                bull, sar[i], ep, af = False, ep, l[i], step
        else:
            sar[i] = max(sar[i], h[i - 1], h[max(0, i - 2)])
            if l[i] < ep:
                ep, af = l[i], min(af + step, max_step)
            if h[i] > sar[i]:
                bull, sar[i], ep, af = True, ep, h[i], step
    return pd.Series(sar, index=high.index)


def linreg_slope(series: pd.Series, period: int = 20) -> pd.Series:
    """Normalised slope of a rolling linear regression (per-bar % of price)."""
    x = np.arange(period, dtype=float)
    x_mean = x.mean()
    denom = float(((x - x_mean) ** 2).sum())

    def _slope(window: np.ndarray) -> float:
        y_mean = window.mean()
        return float(((x - x_mean) * (window - y_mean)).sum() / denom)

    raw = series.rolling(period).apply(_slope, raw=True)
    return raw / series.replace(0.0, np.nan)


def ichimoku(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    conversion: int = 9,
    base: int = 26,
    span_b: int = 52,
) -> pd.DataFrame:
    conv = (high.rolling(conversion).max() + low.rolling(conversion).min()) / 2
    bas = (high.rolling(base).max() + low.rolling(base).min()) / 2
    span_a = ((conv + bas) / 2).shift(base)
    spb = ((high.rolling(span_b).max() + low.rolling(span_b).min()) / 2).shift(base)
    return pd.DataFrame(
        {"tenkan": conv, "kijun": bas, "senkou_a": span_a, "senkou_b": spb,
         "chikou": close.shift(-base)}
    )
