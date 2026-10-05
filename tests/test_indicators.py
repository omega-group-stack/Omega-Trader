"""Indicator correctness — checked against hand-computable cases."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from omega import indicators as ind


@pytest.fixture
def ohlcv() -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=300, freq="15min", tz="UTC")
    rng = np.random.default_rng(42)
    close = 1.10 + np.cumsum(rng.standard_normal(300)) * 0.0004
    high = close + np.abs(rng.standard_normal(300)) * 0.0003
    low = close - np.abs(rng.standard_normal(300)) * 0.0003
    open_ = np.concatenate([[close[0]], close[:-1]])
    volume = rng.integers(100, 1000, 300).astype(float)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


def test_sma_matches_manual_mean(ohlcv):
    out = ind.sma(ohlcv["close"], 10)
    assert out.iloc[:9].isna().all()
    assert out.iloc[9] == pytest.approx(ohlcv["close"].iloc[:10].mean())


def test_ema_is_recursive_and_seeded():
    s = pd.Series([1.0] * 50)
    assert ind.ema(s, 10).iloc[-1] == pytest.approx(1.0)


def test_rsi_bounds_and_extremes():
    rising = pd.Series(np.arange(1, 60, dtype=float))
    assert ind.rsi(rising, 14).iloc[-1] == pytest.approx(100.0, abs=1e-6)

    falling = pd.Series(np.arange(60, 1, -1, dtype=float))
    assert ind.rsi(falling, 14).iloc[-1] == pytest.approx(0.0, abs=1e-6)


def test_rsi_stays_in_range(ohlcv):
    r = ind.rsi(ohlcv["close"], 14).dropna()
    assert ((r >= 0) & (r <= 100)).all()


def test_true_range_includes_gaps():
    high = pd.Series([10.0, 20.0])
    low = pd.Series([9.0, 19.0])
    close = pd.Series([9.5, 19.5])
    # second bar gapped up: TR = high - prev_close = 20 - 9.5
    assert ind.true_range(high, low, close).iloc[1] == pytest.approx(10.5)


def test_atr_is_positive(ohlcv):
    atr = ind.atr(ohlcv["high"], ohlcv["low"], ohlcv["close"], 14).dropna()
    assert (atr > 0).all()


def test_adx_components_in_range(ohlcv):
    out = ind.adx(ohlcv["high"], ohlcv["low"], ohlcv["close"], 14).dropna()
    assert ((out["adx"] >= 0) & (out["adx"] <= 100)).all()
    assert ((out["plus_di"] >= 0) & (out["minus_di"] >= 0)).all()


def test_supertrend_direction_is_binary(ohlcv):
    st = ind.supertrend(ohlcv["high"], ohlcv["low"], ohlcv["close"])
    assert set(np.unique(st["direction"])) <= {-1.0, 1.0}


def test_supertrend_follows_a_strong_uptrend():
    n = 120
    idx = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    close = pd.Series(np.linspace(1.0, 1.5, n), index=idx)
    high = close + 0.002
    low = close - 0.002
    st = ind.supertrend(high, low, close, 10, 3.0)
    assert st["direction"].iloc[-1] == 1.0
    assert st["supertrend"].iloc[-1] < close.iloc[-1]


def test_bollinger_bands_bracket_price(ohlcv):
    bb = ind.bollinger(ohlcv["close"], 20, 2.0).dropna()
    assert (bb["upper"] >= bb["mid"]).all()
    assert (bb["mid"] >= bb["lower"]).all()


def test_choppiness_is_low_in_a_straight_trend():
    n = 100
    close = pd.Series(np.linspace(1.0, 1.4, n))
    high = close + 0.0005
    low = close - 0.0005
    chop = ind.choppiness(high, low, close, 14).dropna()
    assert chop.iloc[-1] < 40  # directional, not chop


def test_rolling_percentile_bounds(ohlcv):
    atr = ind.atr(ohlcv["high"], ohlcv["low"], ohlcv["close"], 14)
    pct = ind.rolling_percentile(atr, 50).dropna()
    assert ((pct >= 0) & (pct <= 1)).all()


def test_obv_direction():
    close = pd.Series([1.0, 2.0, 3.0])
    volume = pd.Series([10.0, 10.0, 10.0])
    assert ind.obv(close, volume).iloc[-1] == pytest.approx(20.0)


def test_candle_signal_detects_bullish_engulfing():
    open_ = pd.Series([1.10, 1.0950])
    close = pd.Series([1.0960, 1.1050])
    high = pd.Series([1.1005, 1.1055])
    low = pd.Series([1.0955, 1.0945])
    assert ind.candle_signal(open_, high, low, close).iloc[-1] > 0.5


def test_swing_levels_have_no_lookahead(ohlcv):
    """A pivot may only be known ``right`` bars after it printed."""
    levels = ind.last_swing_levels(ohlcv["high"], ohlcv["low"], 3, 3)
    for i in range(50, len(ohlcv)):
        known_high = levels["swing_high"].iloc[i]
        if pd.isna(known_high):
            continue
        # the level must exist somewhere in the *past* highs
        assert known_high <= ohlcv["high"].iloc[: i + 1].max() + 1e-12


def test_indicators_never_read_the_future(ohlcv):
    """Truncating the series must not change earlier indicator values."""
    cut = 200
    full = ind.rsi(ohlcv["close"], 14).iloc[:cut]
    partial = ind.rsi(ohlcv["close"].iloc[:cut], 14)
    pd.testing.assert_series_equal(full, partial)

    full_atr = ind.atr(ohlcv["high"], ohlcv["low"], ohlcv["close"], 14).iloc[:cut]
    part_atr = ind.atr(
        ohlcv["high"].iloc[:cut], ohlcv["low"].iloc[:cut], ohlcv["close"].iloc[:cut], 14
    )
    pd.testing.assert_series_equal(full_atr, part_atr)
