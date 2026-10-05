"""Cross-sectional currency strength.

The two things that matter here: the score must actually separate "EUR is
strong against everything" from "EURUSD drifted", and it must not peek at
the future. Everything else is plumbing.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from omega.config import AppConfig
from omega.strategy.strength import (
    MAJORS,
    align,
    build_strength,
    describe,
    pair_spread,
    split_pair,
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def ramp(start: float, drift: float, n: int = 1200, noise: float = 4e-4,
         seed: int = 0) -> pd.Series:
    """A price path with drift *and* noise.

    The noise is not decoration. Strength scores are scaled by each pair's own
    trailing volatility, so a perfectly smooth exponential has zero volatility
    and an undefined score — a noiseless fixture would be testing nothing.
    """
    index = pd.date_range("2020-01-01", periods=n, freq="h", tz="UTC")
    rng = np.random.default_rng(seed)
    steps = drift + rng.normal(0.0, noise, n)
    return pd.Series(start * np.exp(np.cumsum(steps)), index=index)


def flat(start: float, n: int = 1200, seed: int = 0) -> pd.Series:
    return ramp(start, 0.0, n, seed=seed)


# --------------------------------------------------------------------------- #
# Pair parsing
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("symbol,expected", [
    ("EURUSD", ("EUR", "USD")),
    ("eurusd", ("EUR", "USD")),
    ("USDJPY", ("USD", "JPY")),
    ("EURUSD.pro", ("EUR", "USD")),     # broker suffixes are everywhere
    ("EURUSDm", ("EUR", "USD")),
    ("EURUSD_raw", ("EUR", "USD")),
    ("XAUUSD", None),                   # gold is not a currency pair here
    ("USDUSD", None),
    ("EUR", None),
    ("", None),
])
def test_split_pair(symbol, expected):
    assert split_pair(symbol) == expected


def test_all_majors_are_recognised():
    for base in MAJORS:
        for quote in MAJORS:
            if base != quote:
                assert split_pair(base + quote) == (base, quote)


# --------------------------------------------------------------------------- #
# The core claim
# --------------------------------------------------------------------------- #
def test_a_currency_rising_against_everything_ranks_top():
    closes = {
        "EURUSD": ramp(1.10, +3e-4, seed=1),
        "EURGBP": ramp(0.85, +3e-4, seed=2),
        "EURJPY": ramp(120.0, +3e-4, seed=3),
        "GBPUSD": flat(1.30, seed=4),
        "USDJPY": flat(110.0, seed=5),
    }
    strength = build_strength(closes, lookback=24)
    last = strength.iloc[-1]
    assert last.idxmax() == "EUR"


def test_a_currency_falling_against_everything_ranks_bottom():
    closes = {
        "EURUSD": ramp(1.10, +3e-4, seed=1),     # USD weak
        "GBPUSD": ramp(1.30, +3e-4, seed=2),     # USD weak
        "AUDUSD": ramp(0.70, +3e-4, seed=3),     # USD weak
        "USDJPY": ramp(110.0, -3e-4, seed=4),    # USD weak
    }
    strength = build_strength(closes, lookback=24)
    assert strength.iloc[-1].idxmin() == "USD"


def test_an_idiosyncratic_move_does_not_produce_a_strong_score():
    """The whole point: EURUSD alone moving must not look like EUR strength."""
    shared = {
        "GBPUSD": flat(1.30, seed=4), "AUDUSD": flat(0.70, seed=5),
        "USDJPY": flat(110.0, seed=6), "USDCHF": flat(0.95, seed=7),
    }
    eurusd = ramp(1.10, +3e-4, seed=1)

    # EUR climbs against USD, GBP and JPY alike.
    broad = build_strength({**shared, "EURUSD": eurusd,
                            "EURGBP": ramp(0.85, +3e-4, seed=2),
                            "EURJPY": ramp(120.0, +3e-4, seed=3)}, lookback=24)
    # Identical EURUSD path, but EUR goes nowhere against GBP or JPY.
    narrow = build_strength({**shared, "EURUSD": eurusd,
                             "EURGBP": flat(0.85, seed=2),
                             "EURJPY": flat(120.0, seed=3)}, lookback=24)

    assert pair_spread(broad, "EURUSD").iloc[-1] > \
        pair_spread(narrow, "EURUSD").iloc[-1]


def test_scores_are_centred_across_the_cross_section():
    closes = {"EURUSD": ramp(1.10, 2e-4, seed=1),
              "GBPUSD": ramp(1.30, 1e-4, seed=2),
              "USDJPY": ramp(110.0, -1e-4, seed=3),
              "AUDUSD": flat(0.70, seed=4)}
    strength = build_strength(closes, lookback=24)
    row = strength.iloc[-1]
    assert abs(row.sum()) < 1e-9, "cross-section should be mean-zero"


def test_the_same_shape_of_move_scores_the_same_in_calm_and_volatile_regimes():
    """Standardisation exists so one threshold works in both."""
    def spread_for(scale):
        # Drift *and* noise scale together: the same market, louder.
        closes = {
            "EURUSD": ramp(1.10, 3e-4 * scale, noise=4e-4 * scale, seed=1),
            "GBPUSD": ramp(1.30, 1e-4 * scale, noise=4e-4 * scale, seed=2),
            "USDJPY": ramp(110.0, -1e-4 * scale, noise=4e-4 * scale, seed=3),
            "AUDUSD": ramp(0.70, 1e-4 * scale, noise=4e-4 * scale, seed=4),
        }
        return pair_spread(build_strength(closes, lookback=24), "EURUSD").iloc[-1]

    assert spread_for(1.0) == pytest.approx(spread_for(5.0), rel=0.02)


# --------------------------------------------------------------------------- #
# Look-ahead
# --------------------------------------------------------------------------- #
def test_strength_at_time_t_does_not_change_when_the_future_is_removed():
    """The decisive safety property."""
    closes = {
        "EURUSD": ramp(1.10, 2e-4, seed=1), "GBPUSD": ramp(1.30, 1e-4, seed=2),
        "AUDUSD": ramp(0.70, -1e-4, seed=3), "USDJPY": ramp(110.0, 1e-4, seed=4),
        "USDCHF": ramp(0.95, -2e-4, seed=5),
    }
    full = build_strength(closes, lookback=24)
    cut = 900
    truncated = build_strength({k: v.iloc[:cut] for k, v in closes.items()},
                               lookback=24)
    pd.testing.assert_frame_equal(
        full.iloc[:cut], truncated, check_freq=False
    )


def test_align_forward_fills_and_never_back_fills():
    sparse = pd.Series(
        [1.0, 2.0],
        index=pd.DatetimeIndex(["2020-01-01 05:00", "2020-01-01 10:00"], tz="UTC"),
    )
    index = pd.date_range("2020-01-01 00:00", periods=12, freq="h", tz="UTC")
    out = align(sparse, index)
    assert out.loc["2020-01-01 00:00"] == 0.0, "no value yet -> neutral, not 1.0"
    assert out.loc["2020-01-01 07:00"] == 1.0, "carry the last known value"
    assert out.loc["2020-01-01 11:00"] == 2.0


# --------------------------------------------------------------------------- #
# Degradation
# --------------------------------------------------------------------------- #
def test_empty_basket_returns_an_empty_frame():
    assert build_strength({}).empty


def test_a_single_pair_is_not_enough_and_says_so():
    assert build_strength({"EURUSD": ramp(1.10, 1e-4, seed=1)}).empty


def test_unrecognised_symbols_are_skipped_not_fatal():
    closes = {"EURUSD": ramp(1.10, 2e-4, seed=1),
              "GBPUSD": ramp(1.30, 1e-4, seed=2),
              "XAUUSD": ramp(1800.0, 1e-4, seed=3),
              "SPX500": ramp(4000.0, 1e-4, seed=4)}
    strength = build_strength(closes, lookback=24)
    assert set(strength.columns) == {"EUR", "USD", "GBP"}


def test_pair_spread_for_an_absent_currency_is_neutral_not_an_error():
    strength = build_strength({"EURUSD": ramp(1.10, 2e-4, seed=1),
                               "GBPUSD": ramp(1.30, 1e-4, seed=2)}, lookback=24)
    out = pair_spread(strength, "AUDNZD")
    assert (out == 0.0).all()


def test_pair_spread_of_an_unknown_symbol_is_empty():
    strength = build_strength({"EURUSD": ramp(1.10, 2e-4, seed=1),
                               "GBPUSD": ramp(1.30, 1e-4, seed=2)}, lookback=24)
    assert pair_spread(strength, "XAUUSD").empty


def test_duplicate_timestamps_are_collapsed():
    index = pd.DatetimeIndex(["2020-01-01 00:00", "2020-01-01 00:00",
                              "2020-01-01 01:00"], tz="UTC")
    closes = {"EURUSD": pd.Series([1.1, 1.2, 1.3], index=index),
              "GBPUSD": pd.Series([1.3, 1.3, 1.3], index=index)}
    assert not build_strength(closes, lookback=1).index.has_duplicates


def test_describe_is_ordered_and_readable():
    closes = {"EURUSD": ramp(1.10, 3e-4, seed=1), "GBPUSD": flat(1.30, seed=2),
              "USDJPY": flat(110.0, seed=3)}
    text = describe(build_strength(closes, lookback=24))
    assert text.split()[0] == "EUR"


def test_describe_handles_no_data():
    assert "no cross-sectional" in describe(pd.DataFrame())


# --------------------------------------------------------------------------- #
# Wiring into the strategy
# --------------------------------------------------------------------------- #
def test_filter_is_off_by_default():
    assert AppConfig().strategy.min_strength_agreement == 0.0


def test_strategy_without_a_basket_behaves_exactly_as_before():
    """Enabling the config knob must not change anything if no data arrives."""
    from omega.engine.backtester import Backtester

    def run(agreement):
        cfg = AppConfig()
        cfg.data.synthetic_bars = 6000
        cfg.data.history_bars = 6000
        cfg.strategy.min_strength_agreement = agreement
        cfg.strategy.strength_basket = []      # nothing to build from
        cfg.logging.level = "ERROR"
        return Backtester(cfg).run().performance

    assert run(0.0).trades == run(0.5).trades


def test_the_filter_can_only_remove_entries_never_create_them():
    """The invariant that actually holds.

    Note what is *not* asserted here: that enabling the filter lowers the
    backtest's trade count. It does not, reliably — vetoing an early trade
    changes the equity path, which changes when cooldowns and the drawdown
    kill switch fire, and a filtered run that avoids an early halt can end up
    taking *more* trades than the unfiltered one. Measured: 90 trades with the
    filter on versus 72 with it off, on the synthetic feed. The guarantee is
    at the signal level, so that is where it is tested.
    """
    from omega.data.synthetic import SyntheticFeed, default_spec
    from omega.strategy import EnsembleStrategy

    feed = SyntheticFeed(seed=11, bars=6000)
    basket = ("EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCHF")
    closes = {s: feed.candles(s, "M15", 6000)["close"] for s in basket}
    strength = build_strength(closes, 24)
    df = feed.candles("EURUSD", "M15", 6000)

    def entry_bars(agreement):
        cfg = AppConfig().strategy
        cfg.min_strength_agreement = agreement
        strategy = EnsembleStrategy(cfg, default_spec("EURUSD")).prepare(
            df, None, strength=strength if agreement else None, symbol="EURUSD"
        )
        return {i for i in range(400, len(df))
                if strategy.signal_at(i, "EURUSD").is_actionable}

    unfiltered = entry_bars(0.0)
    filtered = entry_bars(0.5)

    assert filtered < unfiltered, "the filter must bite, and only subtract"


def test_veto_reason_is_explained_to_the_user():
    from omega.data.synthetic import SyntheticFeed, default_spec
    from omega.strategy import EnsembleStrategy
    from omega.strategy.strength import build_strength

    feed = SyntheticFeed(seed=3, bars=4000)
    closes = {s: feed.candles(s, "M15", 4000)["close"]
              for s in ("EURUSD", "GBPUSD", "USDJPY", "AUDUSD")}
    strength = build_strength(closes, 24)

    cfg = AppConfig().strategy
    cfg.min_strength_agreement = 5.0        # impossibly strict -> always vetoes
    df = feed.candles("EURUSD", "M15", 4000)
    strategy = EnsembleStrategy(cfg, default_spec("EURUSD")).prepare(
        df, None, strength=strength, symbol="EURUSD"
    )
    vetoed = [strategy.signal_at(i, "EURUSD") for i in range(-50, 0)]
    texts = [v for s in vetoed for v in s.vetoes]
    assert any("cross-section disagrees" in t for t in texts)
