"""Strategy layer: features, regimes, sessions and the ensemble vote."""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from omega.config import IndicatorConfig, RegimeConfig, SessionConfig, StrategyConfig
from omega.core.types import Regime, SignalDirection, SymbolSpec
from omega.data.synthetic import SyntheticFeed, default_spec
from omega.strategy import EnsembleStrategy, classify, is_open, weights_for
from omega.strategy.features import build_features, build_htf_features

UTC = timezone.utc


@pytest.fixture(scope="module")
def market() -> pd.DataFrame:
    return SyntheticFeed(seed=11, bars=1_500).candles("EURUSD", "M15", 1_500)


@pytest.fixture(scope="module")
def spec() -> SymbolSpec:
    return default_spec("EURUSD")


def trending_frame(direction: int = 1, n: int = 600) -> pd.DataFrame:
    """A clean, unambiguous trend — any trend follower must see it."""
    idx = pd.date_range("2024-01-01", periods=n, freq="15min", tz="UTC")
    rng = np.random.default_rng(3)
    drift = direction * 0.00025
    close = 1.10 + np.cumsum(drift + rng.standard_normal(n) * 0.00008)
    high = close + 0.00012
    low = close - 0.00012
    open_ = np.concatenate([[close[0]], close[:-1]])
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close,
         "volume": rng.integers(300, 900, n).astype(float),
         "spread": np.full(n, 8.0)},
        index=idx,
    )


# --------------------------------------------------------------------------- #
# Features
# --------------------------------------------------------------------------- #
def test_feature_frame_has_every_block_input(market):
    f = build_features(market, IndicatorConfig())
    required = [
        "atr", "atr_pct", "ema_fast", "ema_slow", "adx", "supertrend_dir",
        "rsi", "stoch_k", "macd_hist_atr", "bb_pct_b", "squeeze", "chop",
        "mfi", "cmf", "obv_slope", "structure_bias", "breakout", "candle",
    ]
    for col in required:
        assert col in f.columns, f"missing feature {col}"
    assert len(f) == len(market)


def test_htf_features_do_not_leak_backwards(market):
    """An H4 value may only appear on M15 bars *after* that H4 bar closed."""
    htf = build_htf_features(market, None, "H4", IndicatorConfig())
    assert len(htf) == len(market)
    assert htf.index.equals(market.index)

    # truncating the series must not change earlier HTF values
    cut = 800
    partial = build_htf_features(market.iloc[:cut], None, "H4", IndicatorConfig())
    pd.testing.assert_frame_equal(
        htf.iloc[:cut].tail(200), partial.tail(200), check_exact=False, rtol=1e-9
    )


# --------------------------------------------------------------------------- #
# Regimes
# --------------------------------------------------------------------------- #
def test_regime_labels_are_valid(market):
    f = build_features(market, IndicatorConfig())
    labels = classify(f, RegimeConfig())
    assert set(labels.unique()) <= {r.value for r in Regime}


def test_strong_trend_is_classified_as_trending():
    f = build_features(trending_frame(1), IndicatorConfig())
    labels = classify(f, RegimeConfig())
    tail = labels.tail(200)
    assert (tail == Regime.TREND_UP.value).mean() > 0.3


def test_regime_reweights_the_blocks():
    cfg = RegimeConfig()
    base = StrategyConfig().weights
    trend_w = weights_for(Regime.TREND_UP, base, cfg)
    range_w = weights_for(Regime.RANGE, base, cfg)
    assert trend_w["trend"] > range_w["trend"]
    assert range_w["momentum"] > trend_w["momentum"]


# --------------------------------------------------------------------------- #
# Sessions
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "when,expected",
    [
        (datetime(2024, 1, 3, 10, 0, tzinfo=UTC), True),    # Wed, London
        (datetime(2024, 1, 3, 3, 0, tzinfo=UTC), False),    # Wed, Asia
        (datetime(2024, 1, 6, 10, 0, tzinfo=UTC), False),   # Saturday
        (datetime(2024, 1, 5, 20, 0, tzinfo=UTC), False),   # Friday evening
    ],
)
def test_session_window(when, expected):
    allowed, _ = is_open(when, SessionConfig())
    assert allowed is expected


def test_sessions_can_be_disabled():
    allowed, _ = is_open(datetime(2024, 1, 6, 3, 0, tzinfo=UTC),
                         SessionConfig(enabled=False))
    assert allowed


# --------------------------------------------------------------------------- #
# The ensemble
# --------------------------------------------------------------------------- #
def test_scores_are_bounded(market, spec):
    st = EnsembleStrategy(StrategyConfig(), spec).prepare(market)
    for block in ("trend", "momentum", "volatility", "volume", "structure", "htf_bias"):
        assert st.scores[block].between(-1.0, 1.0).all()
    assert st.scores["ensemble"].between(-1.0, 1.0).all()
    assert st.scores["confidence"].between(0.0, 1.0).all()


def test_uptrend_produces_a_long_bias(spec):
    st = EnsembleStrategy(StrategyConfig(), spec).prepare(trending_frame(1))
    assert st.scores["trend"].tail(150).mean() > 0.25
    assert st.scores["ensemble"].tail(150).mean() > 0.1


def test_downtrend_produces_a_short_bias(spec):
    st = EnsembleStrategy(StrategyConfig(), spec).prepare(trending_frame(-1))
    assert st.scores["trend"].tail(150).mean() < -0.25
    assert st.scores["ensemble"].tail(150).mean() < -0.1


def test_signal_shape(market, spec):
    st = EnsembleStrategy(StrategyConfig(), spec).prepare(market)
    sig = st.latest()
    assert len(sig.components) == 6
    assert sig.direction in tuple(SignalDirection)
    assert -1 <= sig.score <= 1
    assert sig.to_dict()["symbol"] == "EURUSD"


def test_threshold_gates_the_direction(market, spec):
    cfg = StrategyConfig(entry_threshold=0.99, min_agreeing_blocks=0,
                         min_confidence=0.0)
    st = EnsembleStrategy(cfg, spec).prepare(market)
    directions = {st.signal_at(i).direction for i in range(400, len(market))}
    assert directions == {SignalDirection.FLAT}


def test_disabling_shorts_vetoes_them(market, spec):
    cfg = StrategyConfig(allow_shorts=False, entry_threshold=0.1,
                         min_confidence=0.0, min_agreeing_blocks=0,
                         require_htf_alignment=False)
    st = EnsembleStrategy(cfg, spec).prepare(market)
    for i in range(400, len(market)):
        sig = st.signal_at(i)
        if sig.direction is SignalDirection.SHORT:
            assert sig.vetoes, "short should have been vetoed"


def test_warmup_bars_are_vetoed(market, spec):
    cfg = StrategyConfig(warmup_bars=250)
    st = EnsembleStrategy(cfg, spec).prepare(market)
    assert any("warm-up" in v for v in st.signal_at(100).vetoes)
    assert not any("warm-up" in v for v in st.signal_at(400).vetoes)


def test_wide_spread_vetoes_entries(market, spec):
    cfg = StrategyConfig(entry_threshold=0.05, min_confidence=0.0,
                         min_agreeing_blocks=0)
    st = EnsembleStrategy(cfg, spec).prepare(market)
    st.max_spread_points = 0.5  # absurdly tight — everything should be blocked
    blocked = [st.signal_at(i) for i in range(400, 450)]
    assert all(any("spread" in v for v in s.vetoes) for s in blocked)


def test_prepare_is_deterministic(market, spec):
    a = EnsembleStrategy(StrategyConfig(), spec).prepare(market).scores["ensemble"]
    b = EnsembleStrategy(StrategyConfig(), spec).prepare(market).scores["ensemble"]
    pd.testing.assert_series_equal(a, b)


def test_scores_do_not_change_when_future_bars_are_added(market, spec):
    """The decisive look-ahead test for the whole strategy layer."""
    cut = 1_000
    full = EnsembleStrategy(StrategyConfig(), spec).prepare(market)
    partial = EnsembleStrategy(StrategyConfig(), spec).prepare(market.iloc[:cut])

    # compare the last 200 bars that both runs can see
    a = full.scores["ensemble"].iloc[cut - 200:cut]
    b = partial.scores["ensemble"].iloc[-200:]
    np.testing.assert_allclose(a.to_numpy(), b.to_numpy(), rtol=1e-9, atol=1e-12)
