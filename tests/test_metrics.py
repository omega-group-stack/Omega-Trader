"""Risk-weighted expectancy.

`expectancy_r` is an unweighted mean of per-trade R, which assumes every
trade risked the same amount. Under percent-of-equity sizing it never does.
These tests pin the weighted version that tells you what the account
actually earned per unit of risk.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from omega.core.types import CloseReason, Side, Trade
from omega.engine.metrics import analyse

START = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _trade(pnl: float, r_multiple: float, n: int = 0) -> Trade:
    when = START + timedelta(days=n)
    return Trade(
        ticket=1000 + n, symbol="EURUSD", side=Side.BUY, lots=0.1,
        entry_price=1.1, exit_price=1.1, open_time=when, close_time=when,
        pnl=pnl, r_multiple=r_multiple, reason=CloseReason.TAKE_PROFIT,
    )


def _flat_curve():
    return [(START + timedelta(days=i), 10_000.0, 10_000.0) for i in range(5)]


def test_risk_weighted_expectancy_exposes_oversized_losers():
    """Two trades, +1R and -1R, but the loser was twice the size.

    The unweighted mean says break-even. The account is down. This is the
    exact bias that made a six-pair out-of-sample panel look like +0.111R
    when it actually earned -0.010R.
    """
    trades = [
        _trade(pnl=+100.0, r_multiple=+1.0, n=0),      # risked 100
        _trade(pnl=-200.0, r_multiple=-1.0, n=1),      # risked 200
    ]
    perf = analyse(_flat_curve(), trades, 10_000.0)

    assert perf.expectancy_r == pytest.approx(0.0)
    assert perf.expectancy_r_weighted == pytest.approx(-100 / 300, abs=1e-3)


def test_the_two_expectancies_agree_when_every_trade_is_the_same_size():
    trades = [
        _trade(pnl=+150.0, r_multiple=+1.5, n=0),
        _trade(pnl=-100.0, r_multiple=-1.0, n=1),
        _trade(pnl=+50.0, r_multiple=+0.5, n=2),
    ]
    perf = analyse(_flat_curve(), trades, 10_000.0)
    assert perf.expectancy_r == pytest.approx(perf.expectancy_r_weighted, abs=1e-3)


def test_oversized_winners_push_the_weighted_number_the_other_way():
    trades = [
        _trade(pnl=+200.0, r_multiple=+1.0, n=0),      # risked 200
        _trade(pnl=-100.0, r_multiple=-1.0, n=1),      # risked 100
    ]
    perf = analyse(_flat_curve(), trades, 10_000.0)
    assert perf.expectancy_r == pytest.approx(0.0)
    assert perf.expectancy_r_weighted == pytest.approx(100 / 300, abs=1e-3)


def test_risk_weighted_expectancy_ignores_trades_with_no_measurable_risk():
    trades = [
        _trade(pnl=+100.0, r_multiple=+1.0, n=0),
        _trade(pnl=-30.0, r_multiple=0.0, n=1),        # R undefined
    ]
    perf = analyse(_flat_curve(), trades, 10_000.0)
    assert perf.expectancy_r_weighted == pytest.approx(1.0)


def test_no_trades_is_zero_not_a_crash():
    perf = analyse(_flat_curve(), [], 10_000.0)
    assert perf.expectancy_r_weighted == 0.0


def test_it_is_reported_in_the_summary():
    trades = [_trade(pnl=+100.0, r_multiple=+1.0, n=0),
              _trade(pnl=-200.0, r_multiple=-1.0, n=1)]
    text = analyse(_flat_curve(), trades, 10_000.0).summary()
    assert "risk-weighted" in text


# --------------------------------------------------------------------------- #
# Partial exits must not be counted as independent trades
# --------------------------------------------------------------------------- #
def _leg(ticket: int, pnl: float, r_multiple: float, reason: CloseReason,
         n: int = 0) -> Trade:
    when = START + timedelta(days=n)
    return Trade(
        ticket=ticket, symbol="EURUSD", side=Side.BUY, lots=0.1,
        entry_price=1.1, exit_price=1.1, open_time=when, close_time=when,
        pnl=pnl, r_multiple=r_multiple, reason=reason,
    )


def test_a_partial_exit_does_not_become_a_second_trade():
    """A winner must not get two rows while a loser gets one.

    One position takes a partial at +1.5R and runs the rest to +1.8R; the
    other goes straight to its stop. Honestly measured that is one win and
    one loss. Counted per record it is two wins and one loss, and the mean
    R jumps from roughly +0.3 to +0.75 without a single extra pip earned.
    """
    trades = [
        _leg(1, pnl=+75.0, r_multiple=+1.5, reason=CloseReason.PARTIAL_TP, n=0),
        _leg(1, pnl=+90.0, r_multiple=+1.8, reason=CloseReason.TAKE_PROFIT, n=1),
        _leg(2, pnl=-100.0, r_multiple=-1.0, reason=CloseReason.STOP_LOSS, n=2),
    ]
    perf = analyse(_flat_curve(), trades, 10_000.0)

    assert perf.trades == 3          # three records
    assert perf.positions == 2       # but only two positions
    assert perf.partial_exits == 1

    # Position 1 risked 50 on the partial and 50 on the remainder: +165/100.
    # Position 2: -100/100. Mean of +1.65 and -1.00.
    assert perf.expectancy_r == pytest.approx(0.325, abs=1e-3)

    # The biased per-record figure is kept visible, and it is much rosier.
    assert perf.expectancy_r_per_record == pytest.approx(0.767, abs=1e-3)
    assert perf.expectancy_r < perf.expectancy_r_per_record


def test_position_expectancy_agrees_with_the_account_when_risk_is_equal():
    """With partials present, per-position R must still track the account."""
    trades = [
        _leg(1, pnl=+75.0, r_multiple=+1.5, reason=CloseReason.PARTIAL_TP, n=0),
        _leg(1, pnl=+90.0, r_multiple=+1.8, reason=CloseReason.TAKE_PROFIT, n=1),
        _leg(2, pnl=-100.0, r_multiple=-1.0, reason=CloseReason.STOP_LOSS, n=2),
        _leg(3, pnl=-100.0, r_multiple=-1.0, reason=CloseReason.STOP_LOSS, n=3),
    ]
    perf = analyse(_flat_curve(), trades, 10_000.0)

    # Every position risked 100; the account made -35 on 300 risked.
    assert perf.expectancy_r_weighted == pytest.approx(-35.0 / 300.0, abs=1e-3)
    assert perf.expectancy_r == pytest.approx(-35.0 / 300.0, abs=1e-3)
    # Per record it looks far better than the account did.
    assert perf.expectancy_r_per_record > perf.expectancy_r_weighted


def test_without_partials_nothing_is_grouped():
    """No partials means records and positions are the same thing."""
    trades = [_trade(pnl=+100.0, r_multiple=+1.0, n=i) for i in range(4)]
    perf = analyse(_flat_curve(), trades, 10_000.0)

    assert perf.positions == perf.trades == 4
    assert perf.partial_exits == 0
    assert perf.expectancy_r == pytest.approx(perf.expectancy_r_per_record)
