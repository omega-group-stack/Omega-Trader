"""Price-action / market-structure features.

These capture what an experienced discretionary trader reads off a bare chart:
swing points, breakouts, pullback depth and candlestick intent.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .volatility import atr


def swing_high(high: pd.Series, left: int = 3, right: int = 3) -> pd.Series:
    """Boolean series marking confirmed pivot highs (right bars look-ahead-safe
    only when shifted; callers use :func:`last_swing_levels`)."""
    roll = high.rolling(left + right + 1, center=True).max()
    return (high == roll) & high.notna()


def swing_low(low: pd.Series, left: int = 3, right: int = 3) -> pd.Series:
    roll = low.rolling(left + right + 1, center=True).min()
    return (low == roll) & low.notna()


def last_swing_levels(
    high: pd.Series, low: pd.Series, left: int = 3, right: int = 3
) -> pd.DataFrame:
    """Most recent *confirmed* swing high/low at each bar (no look-ahead).

    A pivot at index ``i`` only becomes known at ``i + right``, so the result is
    shifted accordingly before forward-filling.
    """
    sh = swing_high(high, left, right).shift(right).fillna(False).astype(bool)
    sl = swing_low(low, left, right).shift(right).fillna(False).astype(bool)
    last_high = high.shift(right).where(sh).ffill()
    last_low = low.shift(right).where(sl).ffill()
    return pd.DataFrame({"swing_high": last_high, "swing_low": last_low})


def structure_bias(
    high: pd.Series, low: pd.Series, left: int = 3, right: int = 3, depth: int = 4
) -> pd.Series:
    """+1 for a sequence of higher-highs/higher-lows, -1 for lower-highs/lows."""
    lv = last_swing_levels(high, low, left, right)
    hh = lv["swing_high"].diff().rolling(depth).mean()
    hl = lv["swing_low"].diff().rolling(depth).mean()
    up = (hh > 0) & (hl > 0)
    down = (hh < 0) & (hl < 0)
    return pd.Series(np.where(up, 1.0, np.where(down, -1.0, 0.0)), index=high.index)


def breakout_strength(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 20
) -> pd.Series:
    """Distance beyond the N-bar range, expressed in ATR units (signed)."""
    prior_high = high.rolling(period).max().shift(1)
    prior_low = low.rolling(period).min().shift(1)
    rng = atr(high, low, close, 14).replace(0.0, np.nan)
    up = (close - prior_high) / rng
    down = (close - prior_low) / rng
    return pd.Series(
        np.where(close > prior_high, up, np.where(close < prior_low, down, 0.0)),
        index=close.index,
    )


def pullback_depth(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 20
) -> pd.Series:
    """Where price sits inside the recent range: 0 = range low, 1 = range high."""
    hi = high.rolling(period).max()
    lo = low.rolling(period).min()
    return (close - lo) / (hi - lo).replace(0.0, np.nan)


def candle_signal(
    open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series
) -> pd.Series:
    """Composite candlestick intent in [-1, +1].

    Blends engulfing patterns, pin bars (rejection wicks) and body dominance.
    """
    body = (close - open_).abs()
    rng = (high - low).replace(0.0, np.nan)
    upper_wick = high - close.combine(open_, max)
    lower_wick = close.combine(open_, min) - low
    bull = (close > open_).astype(float)

    body_ratio = (body / rng).fillna(0.0)
    direction = np.sign(close - open_)

    prev_body_low = open_.shift(1).combine(close.shift(1), min)
    prev_body_high = open_.shift(1).combine(close.shift(1), max)
    bull_engulf = (bull == 1) & (close > prev_body_high) & (open_ < prev_body_low)
    bear_engulf = (bull == 0) & (close < prev_body_low) & (open_ > prev_body_high)

    pin_bull = (lower_wick > 2 * body) & (lower_wick / rng > 0.55)
    pin_bear = (upper_wick > 2 * body) & (upper_wick / rng > 0.55)

    score = 0.5 * direction * body_ratio
    score = score + np.where(bull_engulf, 0.5, 0.0) - np.where(bear_engulf, 0.5, 0.0)
    score = score + np.where(pin_bull, 0.4, 0.0) - np.where(pin_bear, 0.4, 0.0)
    return pd.Series(np.clip(score, -1.0, 1.0), index=close.index).fillna(0.0)


def gap_signal(open_: pd.Series, close: pd.Series, high: pd.Series, low: pd.Series,
               atr_period: int = 14) -> pd.Series:
    """Opening gap measured in ATR units (rare in FX, big when it happens)."""
    rng = atr(high, low, close, atr_period).replace(0.0, np.nan)
    return ((open_ - close.shift(1)) / rng).fillna(0.0)
