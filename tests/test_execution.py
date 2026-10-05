"""Simulated broker: fills, costs, stops, targets and margin."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from omega.config import AccountConfig, ExecutionConfig
from omega.core.types import Candle, CloseReason, OrderRequest, Side, SymbolSpec
from omega.execution.simulated import SimulatedBroker

UTC = timezone.utc
T0 = datetime(2024, 1, 3, 10, 0, tzinfo=UTC)


@pytest.fixture
def spec() -> SymbolSpec:
    return SymbolSpec(
        symbol="EURUSD", digits=5, point=1e-5, tick_size=1e-5, tick_value=1.0,
        contract_size=100_000, min_lot=0.01, max_lot=100.0, lot_step=0.01,
        commission_per_lot=0.0, margin_rate=0.0333,
    )


@pytest.fixture
def broker(spec) -> SimulatedBroker:
    cfg = ExecutionConfig(slippage_points=0, spread_points=0, commission_per_lot=0,
                          swap_enabled=False)
    return SimulatedBroker(cfg, AccountConfig(initial_balance=10_000),
                           {"EURUSD": spec})


def bar(o, h, l, c, minute=0, spread=0.0) -> Candle:
    return Candle(time=T0 + timedelta(minutes=minute), open=o, high=h, low=l,
                  close=c, volume=100.0, spread=spread)


def buy(lots=1.0, sl=None, tp=None) -> OrderRequest:
    return OrderRequest(symbol="EURUSD", side=Side.BUY, lots=lots,
                        stop_loss=sl, take_profit=tp)


# --------------------------------------------------------------------------- #
# Fills and P/L
# --------------------------------------------------------------------------- #
def test_market_order_opens_a_position(broker):
    broker.set_bar("EURUSD", bar(1.10, 1.10, 1.10, 1.10))
    result = broker.market_order(buy(0.5))
    assert result.ok
    positions = broker.positions()
    assert len(positions) == 1
    assert positions[0].lots == 0.5
    assert positions[0].side is Side.BUY


def test_profit_is_computed_correctly(broker):
    """1 lot EURUSD, +20 pips = +$200."""
    broker.set_bar("EURUSD", bar(1.10, 1.10, 1.10, 1.10))
    broker.market_order(buy(1.0))
    broker.set_bar("EURUSD", bar(1.1020, 1.1020, 1.1020, 1.1020))
    broker.close_position(broker.positions()[0].ticket)
    assert broker.balance == pytest.approx(10_200.0, abs=0.01)


def test_short_profits_when_price_falls(broker):
    broker.set_bar("EURUSD", bar(1.10, 1.10, 1.10, 1.10))
    broker.market_order(OrderRequest(symbol="EURUSD", side=Side.SELL, lots=1.0))
    broker.set_bar("EURUSD", bar(1.0980, 1.0980, 1.0980, 1.0980))
    broker.close_position(broker.positions()[0].ticket)
    assert broker.balance == pytest.approx(10_200.0, abs=0.01)


def test_spread_is_a_real_cost(spec):
    """Round-trip with a 2-pip spread on 1 lot costs exactly $20."""
    cfg = ExecutionConfig(slippage_points=0, spread_points=20, commission_per_lot=0,
                          swap_enabled=False)
    broker = SimulatedBroker(cfg, AccountConfig(initial_balance=10_000),
                             {"EURUSD": spec})
    broker.set_bar("EURUSD", bar(1.10, 1.10, 1.10, 1.10, spread=20))
    broker.market_order(buy(1.0))       # fills at the ask
    broker.close_position(broker.positions()[0].ticket)  # exits on the bid
    assert broker.balance == pytest.approx(9_980.0, abs=0.5)


def test_commission_is_charged_on_entry(spec):
    spec.commission_per_lot = 7.0
    cfg = ExecutionConfig(slippage_points=0, spread_points=0, swap_enabled=False)
    broker = SimulatedBroker(cfg, AccountConfig(initial_balance=10_000),
                             {"EURUSD": spec})
    broker.set_bar("EURUSD", bar(1.10, 1.10, 1.10, 1.10))
    broker.market_order(buy(2.0))
    assert broker.balance == pytest.approx(10_000 - 14.0)


def test_slippage_worsens_the_fill(spec):
    cfg = ExecutionConfig(slippage_points=10, spread_points=0, commission_per_lot=0,
                          swap_enabled=False)
    broker = SimulatedBroker(cfg, AccountConfig(initial_balance=10_000),
                             {"EURUSD": spec})
    broker.set_bar("EURUSD", bar(1.10, 1.10, 1.10, 1.10))
    result = broker.market_order(buy(1.0))
    assert result.price > 1.10


def test_insufficient_margin_is_rejected(broker):
    broker.set_bar("EURUSD", bar(1.10, 1.10, 1.10, 1.10))
    result = broker.market_order(buy(50.0))  # 5.5m notional on a 10k account
    assert not result.ok
    assert "margin" in result.message


# --------------------------------------------------------------------------- #
# Stops and targets
# --------------------------------------------------------------------------- #
def test_stop_loss_fires_intrabar(broker):
    broker.set_bar("EURUSD", bar(1.10, 1.10, 1.10, 1.10))
    broker.market_order(buy(1.0, sl=1.0980))
    closed = broker.process_bar("EURUSD", bar(1.10, 1.1005, 1.0975, 1.0990, 15))
    assert len(closed) == 1
    assert closed[0].reason is CloseReason.STOP_LOSS
    assert not broker.positions()


def test_take_profit_fires_intrabar(broker):
    broker.set_bar("EURUSD", bar(1.10, 1.10, 1.10, 1.10))
    broker.market_order(buy(1.0, tp=1.1030))
    closed = broker.process_bar("EURUSD", bar(1.10, 1.1035, 1.0995, 1.1020, 15))
    assert len(closed) == 1
    assert closed[0].reason is CloseReason.TAKE_PROFIT
    assert closed[0].pnl > 0


def test_ambiguous_bar_assumes_the_stop_came_first(broker):
    """When a bar touches both levels, the pessimistic outcome is assumed."""
    broker.set_bar("EURUSD", bar(1.10, 1.10, 1.10, 1.10))
    broker.market_order(buy(1.0, sl=1.0980, tp=1.1020))
    closed = broker.process_bar("EURUSD", bar(1.10, 1.1030, 1.0970, 1.1000, 15))
    assert closed[0].reason is CloseReason.STOP_LOSS
    assert closed[0].pnl < 0


def test_stop_moved_into_profit_is_reported_as_a_trailing_stop(broker):
    broker.set_bar("EURUSD", bar(1.10, 1.10, 1.10, 1.10))
    broker.market_order(buy(1.0, sl=1.0980))
    ticket = broker.positions()[0].ticket
    broker.modify_position(ticket, stop_loss=1.1010)
    closed = broker.process_bar("EURUSD", bar(1.1030, 1.1040, 1.1005, 1.1008, 15))
    assert closed[0].reason is CloseReason.TRAILING_STOP
    assert closed[0].pnl > 0


def test_partial_close_leaves_the_rest_running(broker):
    broker.set_bar("EURUSD", bar(1.10, 1.10, 1.10, 1.10))
    broker.market_order(buy(1.0, sl=1.0980))
    ticket = broker.positions()[0].ticket
    broker.set_bar("EURUSD", bar(1.1020, 1.1020, 1.1020, 1.1020, 15))
    result = broker.close_position(ticket, lots=0.4, reason=CloseReason.PARTIAL_TP)
    assert result.ok
    remaining = broker.positions()
    assert len(remaining) == 1
    assert remaining[0].lots == pytest.approx(0.6)
    assert broker.balance == pytest.approx(10_080.0, abs=0.5)


def test_excursions_are_tracked(broker):
    broker.set_bar("EURUSD", bar(1.10, 1.10, 1.10, 1.10))
    broker.market_order(buy(1.0))
    broker.process_bar("EURUSD", bar(1.10, 1.1050, 1.0970, 1.1000, 15))
    pos = broker.positions()[0]
    assert pos.max_favourable == pytest.approx(0.0050, abs=1e-6)
    assert pos.max_adverse == pytest.approx(-0.0030, abs=1e-6)


def test_equity_tracks_open_pnl(broker):
    broker.set_bar("EURUSD", bar(1.10, 1.10, 1.10, 1.10))
    broker.market_order(buy(1.0))
    broker.process_bar("EURUSD", bar(1.10, 1.1010, 1.1000, 1.1010, 15))
    account = broker.account()
    assert account.equity == pytest.approx(10_100.0, abs=1.0)
    assert account.balance == pytest.approx(10_000.0)  # unrealised, not booked


def test_close_all_flattens_everything(broker):
    broker.set_bar("EURUSD", bar(1.10, 1.10, 1.10, 1.10))
    broker.market_order(buy(0.5))
    broker.market_order(buy(0.5))
    broker.close_all(CloseReason.MANUAL)
    assert not broker.positions()
    assert len(broker.closed_trades()) == 2


def test_r_multiple_is_recorded(broker):
    broker.set_bar("EURUSD", bar(1.10, 1.10, 1.10, 1.10))
    broker.market_order(buy(1.0, sl=1.0980, tp=1.1040))   # 1R = 20 pips
    closed = broker.process_bar("EURUSD", bar(1.10, 1.1045, 1.0995, 1.1040, 15))
    assert closed[0].r_multiple == pytest.approx(2.0, abs=0.05)
