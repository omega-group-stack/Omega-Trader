"""Walk-forward machinery.

The value of this module is entirely in its honesty, so the tests are mostly
about leakage: the out-of-sample segments must not overlap the in-sample ones,
the chosen parameters must actually be applied, and the headline diagnostics
must say "overfit" when the data says overfit.
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from omega.config import AppConfig
from omega.engine import metrics
from omega.engine.walkforward import (
    Fold,
    WalkForwardResult,
    _median,
    fitness,
    walk_forward,
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def make_fold(i, is_ret, oos_ret, params=None, trades=50):
    base = pd.Timestamp("2015-01-01", tz="UTC") + pd.DateOffset(years=i)
    return Fold(
        index=i, is_start=base, is_end=base + pd.DateOffset(years=2),
        oos_start=base + pd.DateOffset(years=2),
        oos_end=base + pd.DateOffset(years=3),
        params=params or {"strategy.entry_threshold": 0.3},
        is_return_pct=is_ret, oos_return_pct=oos_ret, oos_trades=trades,
    )


def small_cfg(bars=5000):
    cfg = AppConfig()
    cfg.data.source = "synthetic"
    cfg.data.synthetic_bars = bars
    cfg.data.history_bars = bars
    cfg.strategy.warmup_bars = 120
    cfg.logging.level = "ERROR"
    return cfg


# --------------------------------------------------------------------------- #
# Diagnostics
# --------------------------------------------------------------------------- #
def test_efficiency_of_one_means_oos_matched_in_sample():
    r = WalkForwardResult(folds=[make_fold(i, 10.0, 10.0) for i in range(5)])
    assert r.efficiency == pytest.approx(1.0)


def test_efficiency_is_near_zero_for_a_curve_fit_strategy():
    """Great in-sample, nothing out-of-sample — the classic overfit signature."""
    r = WalkForwardResult(folds=[make_fold(i, 40.0, 0.2) for i in range(5)])
    assert 0 < r.efficiency < 0.1


def test_efficiency_is_negative_when_oos_loses():
    r = WalkForwardResult(folds=[make_fold(i, 30.0, -8.0) for i in range(5)])
    assert r.efficiency < 0


def test_efficiency_is_zero_rather_than_infinite_when_in_sample_is_flat():
    r = WalkForwardResult(folds=[make_fold(i, 0.0, 5.0) for i in range(3)])
    assert r.efficiency == 0.0


def test_consistency_counts_profitable_out_of_sample_folds():
    folds = [make_fold(0, 10, 5), make_fold(1, 10, -2),
             make_fold(2, 10, 3), make_fold(3, 10, -1)]
    assert WalkForwardResult(folds=folds).consistency == 0.5


def test_parameter_stability_is_zero_when_the_optimiser_never_moves():
    folds = [make_fold(i, 10, 1, {"strategy.entry_threshold": 0.4})
             for i in range(4)]
    assert WalkForwardResult(folds=folds).parameter_stability[
        "strategy.entry_threshold"] == 0.0


def test_parameter_stability_rises_when_the_optimiser_thrashes():
    values = [0.1, 0.9, 0.2, 0.8]
    folds = [make_fold(i, 10, 1, {"strategy.entry_threshold": v})
             for i, v in enumerate(values)]
    cv = WalkForwardResult(folds=folds).parameter_stability["strategy.entry_threshold"]
    assert cv > 0.5


def test_empty_result_does_not_divide_by_zero():
    r = WalkForwardResult()
    assert r.efficiency == 0.0
    assert r.consistency == 0.0
    assert r.parameter_stability == {}


@pytest.mark.parametrize("values,expected", [
    ([], 0.0), ([3], 3), ([1, 3], 2), ([5, 1, 3], 3), ([4, 1, 3, 2], 2.5),
])
def test_median(values, expected):
    assert _median(values) == expected


# --------------------------------------------------------------------------- #
# Fitness
# --------------------------------------------------------------------------- #
def test_fitness_rejects_samples_that_are_too_small_to_mean_anything():
    perf = metrics.Performance(trades=5, return_pct=500.0, sharpe=9.0)
    assert fitness(perf, min_trades=20) == -999.0


def test_fitness_prefers_the_same_return_with_less_drawdown():
    calm = metrics.Performance(trades=100, return_pct=20.0, max_drawdown_pct=5.0)
    wild = metrics.Performance(trades=100, return_pct=20.0, max_drawdown_pct=40.0)
    assert fitness(calm) > fitness(wild)


def test_fitness_prefers_more_trades_at_equal_quality():
    few = metrics.Performance(trades=25, return_pct=10.0, max_drawdown_pct=5.0)
    many = metrics.Performance(trades=200, return_pct=10.0, max_drawdown_pct=5.0)
    assert fitness(many) > fitness(few)


def test_fitness_rewards_positive_expectancy():
    good = metrics.Performance(trades=100, return_pct=10.0,
                               max_drawdown_pct=5.0, expectancy_r=0.2)
    poor = metrics.Performance(trades=100, return_pct=10.0,
                               max_drawdown_pct=5.0, expectancy_r=-0.2)
    assert fitness(good) > fitness(poor)


# --------------------------------------------------------------------------- #
# Serialisation
# --------------------------------------------------------------------------- #
def test_fold_round_trips_to_json():
    payload = make_fold(1, 12.5, -3.25).to_dict()
    assert json.loads(json.dumps(payload))["oos_return_pct"] == -3.25
    assert payload["is_start"] == "2016-01-01"   # make_fold(1) offsets a year


def test_summary_renders_without_a_performance_object():
    text = WalkForwardResult(folds=[make_fold(0, 5, 2)]).summary()
    assert "WALK-FORWARD" in text and "Efficiency" in text


# --------------------------------------------------------------------------- #
# End-to-end on synthetic data (single worker, so it stays fast)
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def study():
    cfg = small_cfg(bars=12_000)
    return walk_forward(
        cfg,
        {"strategy.entry_threshold": [0.30, 0.45]},
        is_months=2, oos_months=1, workers=1, progress=False,
    )


def test_study_produces_folds(study):
    assert len(study.folds) >= 2


def test_out_of_sample_never_overlaps_its_own_in_sample(study):
    for fold in study.folds:
        assert fold.oos_start >= fold.is_end


def test_folds_roll_strictly_forward(study):
    starts = [f.oos_start for f in study.folds]
    assert starts == sorted(starts)
    ends = [f.oos_end for f in study.folds]
    assert ends == sorted(ends)


def test_the_balance_is_carried_from_one_fold_into_the_next(study):
    for previous, current in zip(study.folds, study.folds[1:]):
        assert current.start_balance == pytest.approx(previous.end_balance)


def test_first_fold_starts_from_the_configured_balance(study):
    assert study.folds[0].start_balance == pytest.approx(study.initial_balance)


def test_chosen_params_come_from_the_grid(study):
    for fold in study.folds:
        assert fold.params["strategy.entry_threshold"] in (0.30, 0.45)


def test_stitched_performance_matches_the_last_fold_balance(study):
    assert study.performance is not None
    assert study.performance.final_balance == pytest.approx(
        study.folds[-1].end_balance, abs=1.0
    )


def test_stitched_trade_count_matches_the_sum_of_folds(study):
    assert study.performance.trades == sum(f.oos_trades for f in study.folds)


def test_equity_curve_is_chronological(study):
    times = [t for t, _, _ in study.equity_curve]
    assert times == sorted(times)


def test_summary_mentions_every_fold(study):
    text = study.summary()
    for fold in study.folds:
        assert str(fold.oos_start.date()) in text


def test_result_is_json_serialisable(study):
    assert json.loads(json.dumps(study.to_dict()))["folds"]


# --------------------------------------------------------------------------- #
# Guard rails
# --------------------------------------------------------------------------- #
def test_history_too_short_is_an_explicit_error_not_an_empty_result():
    cfg = small_cfg(bars=3_000)
    with pytest.raises(ValueError, match="too short"):
        walk_forward(cfg, {"strategy.entry_threshold": [0.3]},
                     is_months=120, oos_months=60, workers=1, progress=False)


def test_an_empty_grid_still_runs_the_baseline():
    cfg = small_cfg(bars=12_000)
    result = walk_forward(cfg, {}, is_months=2, oos_months=1,
                          workers=1, progress=False)
    assert result.folds and result.folds[0].params == {}


def test_checkpoint_is_written_after_each_fold(tmp_path):
    path = tmp_path / "nested" / "wf.json"
    cfg = small_cfg(bars=12_000)
    result = walk_forward(cfg, {"strategy.entry_threshold": [0.35]},
                          is_months=2, oos_months=1, workers=1,
                          progress=False, checkpoint=str(path))
    saved = json.loads(path.read_text())
    assert len(saved["folds"]) == len(result.folds)


def test_a_different_grid_value_actually_changes_the_run():
    """Guards against the override silently not being applied."""
    cfg = small_cfg(bars=12_000)
    loose = walk_forward(cfg, {"strategy.entry_threshold": [0.20]},
                         is_months=2, oos_months=1, workers=1, progress=False)
    tight = walk_forward(cfg, {"strategy.entry_threshold": [0.80]},
                         is_months=2, oos_months=1, workers=1, progress=False)
    assert sum(f.oos_trades for f in loose.folds) > \
        sum(f.oos_trades for f in tight.folds)
