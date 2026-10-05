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
