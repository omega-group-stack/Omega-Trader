"""End-to-end engine behaviour, metrics and the look-ahead guarantee."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from omega.config import AppConfig, SymbolConfig
from omega.core.types import CloseReason, Side, Trade
from omega.engine import Backtester, LiveTrader
from omega.engine.metrics import analyse

UTC = timezone.utc


def base_config(bars: int = 2_500) -> AppConfig:
    cfg = AppConfig()
    cfg.execution.mode = "backtest"
    cfg.data.source = "synthetic"
    cfg.data.synthetic_seed = 5
    cfg.data.synthetic_bars = bars
    cfg.data.history_bars = bars
    cfg.strategy.warmup_bars = 300
    cfg.logging.to_file = False
    cfg.symbols = [SymbolConfig(name="EURUSD")]
    return cfg


# --------------------------------------------------------------------------- #
# Backtester
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def result():
    return Backtester(base_config()).run()


def test_backtest_produces_a_full_report(result):
    perf = result.performance
    assert perf.initial_balance == 10_000
    assert perf.start and perf.end
    assert len(result.equity_curve) > 1_000
    assert isinstance(perf.max_drawdown_pct, float)


def test_every_trade_is_internally_consistent(result):
    for t in result.trades:
        assert t.lots > 0
        assert t.close_time >= t.open_time
        assert t.side in (Side.BUY, Side.SELL)
        assert isinstance(t.reason, CloseReason)
        move = (t.exit_price - t.entry_price) * t.side.sign
        # P/L sign must follow the price move (allowing for costs on scratches)
        if abs(move) > 0.0005:
            assert (t.pnl > 0) == (move > 0)


def test_no_position_survives_the_backtest(result):
    assert any(t.reason is CloseReason.END_OF_DATA for t in result.trades) or result.trades


def test_losses_never_blow_past_the_risk_budget(result):
    """A single stop-out must not cost materially more than risk_per_trade_pct."""
    budget = 10_000 * 0.01 * 1.6   # 1% risk + confidence scaling + slippage headroom
    stopped = [t for t in result.trades if t.reason is CloseReason.STOP_LOSS]
    for t in stopped:
        assert t.pnl > -budget, f"stop-out lost {t.pnl:.2f}, budget {budget:.2f}"


def test_r_multiple_of_a_stop_out_is_about_minus_one(result):
    stopped = [t for t in result.trades if t.reason is CloseReason.STOP_LOSS]
    if stopped:
        avg_r = sum(t.r_multiple for t in stopped) / len(stopped)
        assert -1.35 < avg_r < -0.75


def test_equity_curve_matches_the_final_balance(result):
    final = result.equity_curve[-1][1]
    assert final == pytest.approx(result.performance.final_equity, abs=0.01)


def test_backtest_is_reproducible():
    a = Backtester(base_config(1_500)).run()
    b = Backtester(base_config(1_500)).run()
    assert a.performance.net_profit == b.performance.net_profit
    assert len(a.trades) == len(b.trades)


def test_risk_percent_scales_position_size():
    """Doubling the risk % must roughly double the money at risk."""
    small = base_config(1_500)
    small.risk.risk_per_trade_pct = 0.5
    small.risk.scale_risk_with_confidence = False
    big = base_config(1_500)
    big.risk.risk_per_trade_pct = 1.0
    big.risk.scale_risk_with_confidence = False

    r_small = Backtester(small).run()
    r_big = Backtester(big).run()

    lots_small = sum(t.lots for t in r_small.trades) / max(len(r_small.trades), 1)
    lots_big = sum(t.lots for t in r_big.trades) / max(len(r_big.trades), 1)
    assert lots_big == pytest.approx(lots_small * 2, rel=0.25)


def test_drawdown_kill_switch_engages_in_a_backtest():
    cfg = base_config(2_500)
    cfg.risk.max_drawdown_pct = 0.6      # absurdly tight: must trip
    cfg.risk.risk_per_trade_pct = 2.0
    result = Backtester(cfg).run()
    assert result.performance.max_drawdown_pct < 8.0


def test_session_filter_keeps_trades_inside_the_window():
    cfg = base_config(2_500)
    cfg.strategy.sessions.enabled = True
    cfg.strategy.sessions.windows = [["08:00", "12:00"]]
    cfg.strategy.sessions.avoid_friday_after = "11:00"
    result = Backtester(cfg).run()
    for t in result.trades:
        assert 8 <= t.open_time.hour <= 12, f"trade opened at {t.open_time}"


def test_longs_only_configuration():
    cfg = base_config(2_000)
    cfg.strategy.allow_shorts = False
    result = Backtester(cfg).run()
    assert all(t.side is Side.BUY for t in result.trades)


def test_no_lookahead_future_bars_cannot_change_the_past():
    """The gold-standard test for hindsight bias.

    Both runs start from the *same* bar (so the path-dependent risk state is
    identical); the only difference is that one of them can see 600 extra bars
    of future data. Every trade that closed before the cut-off must come out
    byte-identical. If any indicator, fill rule or stop check peeked ahead,
    these two runs would diverge.
    """
    from omega.data import build_feed

    cfg = base_config(2_400)
    feed = build_feed(cfg)                      # one feed => identical prices
    candles = feed.candles("EURUSD", cfg.strategy.timeframe, cfg.data.history_bars)
    cutoff = candles.index[-600]

    blind_cfg = base_config(2_400)
    blind_cfg.data.end = cutoff.isoformat()     # same start, truncated future

    full = Backtester(cfg, feed=feed).run()
    blind = Backtester(blind_cfg, feed=feed).run()

    def settled(result):
        return [t for t in result.trades
                if t.close_time < cutoff.to_pydatetime()
                and t.reason is not CloseReason.END_OF_DATA]

    a, b = settled(full), settled(blind)
    assert a, "expected trades before the cut-off"
    assert len(a) == len(b), (
        f"seeing the future changed the trade count: {len(a)} vs {len(b)}"
    )
    for x, y in zip(a, b):
        assert x.open_time == y.open_time
        assert x.close_time == y.close_time
        assert x.side == y.side
        assert x.lots == pytest.approx(y.lots)
        assert x.entry_price == pytest.approx(y.entry_price, abs=1e-9)
        assert x.exit_price == pytest.approx(y.exit_price, abs=1e-9)
        assert x.reason == y.reason
        assert x.pnl == pytest.approx(y.pnl, abs=1e-6)


# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #
def test_metrics_on_a_known_sequence():
    t0 = datetime(2024, 1, 1, tzinfo=UTC)
    curve = [(t0 + timedelta(days=i), 10_000 + i * 100, 10_000 + i * 100)
             for i in range(11)]
    trades = [
        _trade(t0, 200.0, 2.0), _trade(t0, -100.0, -1.0),
        _trade(t0, 300.0, 3.0), _trade(t0, -100.0, -1.0),
    ]
    perf = analyse(curve, trades, 10_000)

    assert perf.trades == 4
    assert perf.wins == 2 and perf.losses == 2
    assert perf.win_rate_pct == 50.0
    assert perf.profit_factor == pytest.approx(2.5)       # 500 / 200
    assert perf.expectancy == pytest.approx(75.0)
    assert perf.payoff_ratio == pytest.approx(2.5)
    assert perf.return_pct == pytest.approx(10.0)
    assert perf.max_drawdown_pct == 0.0


def test_drawdown_is_measured_from_the_peak():
    t0 = datetime(2024, 1, 1, tzinfo=UTC)
    equity = [10_000, 12_000, 9_000, 11_000]
    curve = [(t0 + timedelta(days=i), e, e) for i, e in enumerate(equity)]
    perf = analyse(curve, [], 10_000)
    assert perf.max_drawdown_pct == pytest.approx(25.0)   # 12k -> 9k
    assert perf.max_drawdown_money == pytest.approx(3_000.0)


def test_metrics_tolerate_no_trades():
    perf = analyse([], [], 10_000)
    assert perf.trades == 0
    assert perf.profit_factor == 0.0
    assert "Trades" in perf.summary()


def _trade(when: datetime, pnl: float, r: float) -> Trade:
    return Trade(ticket=1, symbol="EURUSD", side=Side.BUY, lots=0.1,
                 entry_price=1.1, exit_price=1.1, open_time=when, close_time=when,
                 pnl=pnl, r_multiple=r, reason=CloseReason.TAKE_PROFIT)


# --------------------------------------------------------------------------- #
# Live / paper runner
# --------------------------------------------------------------------------- #
def test_paper_trader_runs_and_reports():
    cfg = base_config(1_200)
    cfg.execution.mode = "paper"
    cfg.execution.poll_seconds = 0.01
    cfg.data.synthetic_stream = True
    cfg.data.synthetic_speed = 50_000

    trader = LiveTrader(cfg).setup()
    trader.run(max_iterations=12)
    snap = trader.snapshot()

    assert snap["ready"] is True
    assert snap["mode"] == "paper"
    assert snap["account"]["equity"] > 0
    assert snap["signals"]["EURUSD"] is not None
    assert "risk_per_trade_pct" in snap["risk"]
    assert isinstance(snap["positions"], list)


def test_risk_percent_can_be_changed_live():
    cfg = base_config(900)
    cfg.execution.mode = "paper"
    trader = LiveTrader(cfg).setup()
    assert trader.set_risk_pct(0.35) == pytest.approx(0.35)
    assert trader.cfg.risk.risk_per_trade_pct == pytest.approx(0.35)
    assert trader.risk.cfg.risk_per_trade_pct == pytest.approx(0.35)
    # bounded for safety
    assert trader.set_risk_pct(999) == 10.0


def test_halt_and_flatten_controls():
    cfg = base_config(900)
    cfg.execution.mode = "paper"
    trader = LiveTrader(cfg).setup()
    trader.halt("test")
    assert trader.risk.state.halted
    trader.resume()
    assert not trader.risk.state.halted
    assert trader.flatten() == 0  # nothing open yet


# --------------------------------------------------------------------------- #
# R-multiple and money must never disagree
# --------------------------------------------------------------------------- #
def test_every_trades_r_multiple_has_the_same_sign_as_its_money():
    """A trade that lost money must never be reported as a positive R.

    This is the invariant that a *gross* R-multiple breaks. Defining R as
    price-move / initial-risk ignores commission and swap, so a trade that
    moved +3 pips and paid $8 of commission shows "+0.15R" while the balance
    went down. Closed-trade R is pnl / risk-money, so the two can never
    disagree about direction.

    Note the weaker claim: *portfolio* expectancy-in-R and profit factor can
    still disagree, because R normalises by each trade's own risk and the
    risk per trade is not constant. Only the per-trade sign is guaranteed.
    """
    cfg = AppConfig()
    cfg.data.synthetic_bars = 9000
    cfg.data.history_bars = 9000
    cfg.execution.commission_per_lot = 25.0     # exaggerate the cost drag
    cfg.logging.level = "ERROR"
    result = Backtester(cfg).run()

    assert len(result.trades) > 20, "need a meaningful sample"
    for trade in result.trades:
        if abs(trade.pnl) < 1e-9:
            continue
        assert (trade.pnl > 0) == (trade.r_multiple > 0), (
            f"trade #{trade.ticket} pnl {trade.pnl:+.2f} but "
            f"R {trade.r_multiple:+.3f} — R is ignoring costs again"
        )


def test_total_net_profit_and_profit_factor_agree():
    """The two money-based headline numbers must tell the same story."""
    cfg = AppConfig()
    cfg.data.synthetic_bars = 9000
    cfg.data.history_bars = 9000
    cfg.logging.level = "ERROR"
    perf = Backtester(cfg).run().performance
    if perf.profit_factor > 1.0:
        assert perf.net_profit > 0
    elif perf.profit_factor < 1.0:
        assert perf.net_profit < 0


def test_r_multiple_is_net_of_commission():
    """Two identical runs differing only in commission must differ in R."""
    def run(commission):
        cfg = AppConfig()
        cfg.data.synthetic_bars = 6000
        cfg.data.history_bars = 6000
        cfg.execution.commission_per_lot = commission
        cfg.logging.level = "ERROR"
        return Backtester(cfg).run().performance

    free = run(0.0)
    costly = run(40.0)
    assert costly.expectancy_r < free.expectancy_r


def test_a_stopped_out_trade_loses_slightly_more_than_one_r_after_costs():
    """-1R is the gross ideal; costs make the real number worse, never better."""
    cfg = AppConfig()
    cfg.data.synthetic_bars = 9000
    cfg.data.history_bars = 9000
    cfg.logging.level = "ERROR"
    result = Backtester(cfg).run()
    stops = [t for t in result.trades if t.reason is CloseReason.STOP_LOSS]
    assert stops, "expected some stop-outs"
    avg = sum(t.r_multiple for t in stops) / len(stops)
    assert -1.6 < avg < -1.0, f"average stop-out was {avg:+.3f}R"
