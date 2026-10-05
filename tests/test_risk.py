"""Risk management — the maths that decides how much money is on the line."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from omega.config import RiskConfig
from omega.core.types import (
    AccountState,
    CloseReason,
    Position,
    Regime,
    Side,
    Signal,
    SignalDirection,
    SymbolSpec,
    Trade,
)
from omega.risk import RiskManager, build_stop_plan, manage_position, position_size

UTC = timezone.utc


@pytest.fixture
def spec() -> SymbolSpec:
    """Standard 5-digit EURUSD: 1 lot = 100k, $1 per 0.00001 move."""
    return SymbolSpec(
        symbol="EURUSD", digits=5, point=1e-5, tick_size=1e-5, tick_value=1.0,
        contract_size=100_000, min_lot=0.01, max_lot=100.0, lot_step=0.01,
        commission_per_lot=0.0,
    )


@pytest.fixture
def account() -> AccountState:
    return AccountState(balance=10_000, equity=10_000, free_margin=10_000)


def make_signal(direction=SignalDirection.LONG, score=0.5, confidence=0.8,
                price=1.1000, atr=0.0010, **meta) -> Signal:
    return Signal(
        time=datetime(2024, 1, 3, 10, 0, tzinfo=UTC),
        symbol="EURUSD",
        direction=direction,
        score=score,
        confidence=confidence,
        regime=Regime.TREND_UP,
        atr=atr,
        price=price,
        meta={"risk_multiplier": 1.0, **meta},
    )


# --------------------------------------------------------------------------- #
# Position sizing
# --------------------------------------------------------------------------- #
def test_risk_percent_is_respected_exactly(spec):
    """1% of 10,000 = $100 risk over a 20-pip stop = 0.50 lots."""
    cfg = RiskConfig(risk_per_trade_pct=1.0, scale_risk_with_confidence=False)
    result = position_size(spec, cfg, 10_000, stop_distance=0.0020, entry_price=1.10)

    assert result.lots == pytest.approx(0.50)
    assert result.risk_money == pytest.approx(100.0, abs=0.5)
    assert result.risk_pct == pytest.approx(1.0, abs=0.01)
    assert result.stop_pips == pytest.approx(20.0)


@pytest.mark.parametrize(
    "risk_pct,stop_pips,expected_lots",
    [
        (0.5, 20, 0.25),
        (1.0, 10, 1.00),
        (2.0, 40, 0.50),
        (0.25, 25, 0.10),
    ],
)
def test_sizing_scales_with_risk_and_stop(spec, risk_pct, stop_pips, expected_lots):
    cfg = RiskConfig(risk_per_trade_pct=risk_pct, scale_risk_with_confidence=False)
    result = position_size(
        spec, cfg, 10_000, stop_distance=stop_pips * spec.pip, entry_price=1.10
    )
    assert result.lots == pytest.approx(expected_lots, abs=0.011)


def test_wider_stop_gives_smaller_position(spec):
    cfg = RiskConfig(risk_per_trade_pct=1.0, scale_risk_with_confidence=False)
    tight = position_size(spec, cfg, 10_000, 0.0010, 1.10)
    wide = position_size(spec, cfg, 10_000, 0.0040, 1.10)
    assert tight.lots > wide.lots
    # money at risk stays constant — that is the entire point
    assert tight.risk_money == pytest.approx(wide.risk_money, rel=0.05)


def test_commission_is_absorbed_into_the_risk_budget():
    spec = SymbolSpec(symbol="EURUSD", commission_per_lot=10.0)
    cfg = RiskConfig(risk_per_trade_pct=1.0, scale_risk_with_confidence=False)
    result = position_size(spec, cfg, 10_000, 0.0020, 1.10)
    # total cost of being wrong (stop + commission) must not exceed the budget
    assert result.risk_money <= 100.5


def test_confidence_scales_risk(spec):
    cfg = RiskConfig(risk_per_trade_pct=1.0, scale_risk_with_confidence=True,
                     min_risk_multiplier=0.5, max_risk_multiplier=1.5)
    low = position_size(spec, cfg, 10_000, 0.0020, 1.10, confidence=0.0)
    high = position_size(spec, cfg, 10_000, 0.0020, 1.10, confidence=1.0)
    assert high.lots > low.lots * 2.5


def test_size_is_floored_to_the_lot_step(spec):
    spec.lot_step = 0.1
    cfg = RiskConfig(risk_per_trade_pct=1.0, scale_risk_with_confidence=False)
    result = position_size(spec, cfg, 10_000, 0.0017, 1.10)
    assert (result.lots * 10) % 1 == pytest.approx(0.0)


def test_tiny_account_refuses_to_trade(spec):
    cfg = RiskConfig(risk_per_trade_pct=0.5, scale_risk_with_confidence=False)
    result = position_size(spec, cfg, 50, 0.0030, 1.10)
    assert result.lots == 0
    assert not result.ok
    assert "minimum" in " ".join(result.notes)


def test_margin_cap_shrinks_the_position(spec):
    cfg = RiskConfig(risk_per_trade_pct=5.0, scale_risk_with_confidence=False)
    result = position_size(spec, cfg, 100_000, 0.0010, 1.10, free_margin=500.0)
    assert result.margin_required <= 500.0 + 1e-6


def test_jpy_pair_sizing(spec):
    """3-digit JPY quotes: pip = 0.01, so the maths must not assume 0.0001."""
    jpy = SymbolSpec(symbol="USDJPY", digits=3, point=0.001, tick_size=0.001,
                     tick_value=0.667, contract_size=100_000, commission_per_lot=0.0)
    cfg = RiskConfig(risk_per_trade_pct=1.0, scale_risk_with_confidence=False)
    result = position_size(jpy, cfg, 10_000, stop_distance=0.20, entry_price=150.0)
    assert result.stop_pips == pytest.approx(20.0)
    assert result.risk_money == pytest.approx(100.0, rel=0.05)


# --------------------------------------------------------------------------- #
# Stops
# --------------------------------------------------------------------------- #
def test_atr_stop_and_rr_target(spec):
    cfg = RiskConfig(stop_mode="atr", atr_stop_mult=2.0, target_mode="rr",
                     risk_reward=2.0, min_stop_pips=1)
    plan = build_stop_plan(make_signal(atr=0.0010), spec, cfg, 1.1000)
    assert plan.stop_loss == pytest.approx(1.0980, abs=1e-6)   # 20 pips
    assert plan.take_profit == pytest.approx(1.1040, abs=1e-6)  # 2R


def test_short_stop_sits_above_entry(spec):
    cfg = RiskConfig(stop_mode="atr", atr_stop_mult=2.0, min_stop_pips=1)
    plan = build_stop_plan(
        make_signal(direction=SignalDirection.SHORT, atr=0.0010), spec, cfg, 1.1000
    )
    assert plan.stop_loss > 1.1000
    assert plan.take_profit < 1.1000


def test_stop_is_clamped_to_the_pip_band(spec):
    cfg = RiskConfig(stop_mode="atr", atr_stop_mult=2.0, min_stop_pips=10,
                     max_stop_pips=30)
    tiny = build_stop_plan(make_signal(atr=0.00001), spec, cfg, 1.10)
    huge = build_stop_plan(make_signal(atr=0.01), spec, cfg, 1.10)
    assert tiny.stop_pips == pytest.approx(10.0)
    assert huge.stop_pips == pytest.approx(30.0)


def test_structure_stop_uses_the_swing(spec):
    cfg = RiskConfig(stop_mode="structure", min_stop_pips=1)
    sig = make_signal(atr=0.0005, swing_low=1.0970)
    plan = build_stop_plan(sig, spec, cfg, 1.1000)
    assert plan.stop_loss < 1.0970  # below the swing, plus the ATR buffer


def test_trailing_stop_only_ratchets_forward(spec):
    cfg = RiskConfig(use_trailing_stop=True, trail_mode="atr", trail_atr_mult=2.0,
                     trail_activate_r=1.0, breakeven_at_r=0)
    pos = Position(ticket=1, symbol="EURUSD", side=Side.BUY, lots=0.1,
                   entry_price=1.1000, open_time=datetime.now(UTC),
                   stop_loss=1.0980, initial_stop=1.0980)

    new_stop, _ = manage_position(pos, 1.1040, 0.0010, spec, cfg)
    assert new_stop is not None and new_stop > 1.0980
    pos.stop_loss = new_stop

    # price pulls back — the stop must not loosen
    again, _ = manage_position(pos, 1.1025, 0.0010, spec, cfg)
    assert again is None or again >= pos.stop_loss


def test_breakeven_moves_the_stop_to_entry(spec):
    cfg = RiskConfig(breakeven_at_r=1.0, breakeven_offset_pips=1.0,
                     use_trailing_stop=False)
    pos = Position(ticket=1, symbol="EURUSD", side=Side.BUY, lots=0.1,
                   entry_price=1.1000, open_time=datetime.now(UTC),
                   stop_loss=1.0980, initial_stop=1.0980)
    new_stop, reason = manage_position(pos, 1.1020, 0.0010, spec, cfg)
    assert new_stop == pytest.approx(1.1001, abs=1e-6)
    assert "break-even" in reason


def test_stop_is_never_placed_through_the_market(spec):
    cfg = RiskConfig(use_trailing_stop=True, trail_mode="atr", trail_atr_mult=0.0001,
                     trail_activate_r=0.1)
    pos = Position(ticket=1, symbol="EURUSD", side=Side.BUY, lots=0.1,
                   entry_price=1.1000, open_time=datetime.now(UTC),
                   stop_loss=1.0980, initial_stop=1.0980)
    new_stop, _ = manage_position(pos, 1.1050, 0.0010, spec, cfg)
    assert new_stop is None or new_stop < 1.1050


# --------------------------------------------------------------------------- #
# The manager's guards
# --------------------------------------------------------------------------- #
def test_approves_a_clean_signal(spec, account):
    rm = RiskManager(RiskConfig(), 10_000)
    rm.on_bar(datetime(2024, 1, 3, 10, tzinfo=UTC), account)
    decision = rm.evaluate(make_signal(), spec, account, [])
    assert decision.approved
    assert decision.lots > 0
    assert decision.stop_loss is not None


def test_rejects_when_a_strategy_veto_is_present(spec, account):
    rm = RiskManager(RiskConfig(), 10_000)
    sig = make_signal()
    sig.vetoes.append("session: closed")
    assert not rm.evaluate(sig, spec, account, []).approved


def test_max_open_positions(spec, account):
    cfg = RiskConfig(max_open_positions=1, max_positions_per_symbol=5)
    rm = RiskManager(cfg, 10_000)
    open_pos = [Position(ticket=1, symbol="GBPUSD", side=Side.BUY, lots=0.1,
                         entry_price=1.27, open_time=datetime.now(UTC))]
    decision = rm.evaluate(make_signal(), spec, account, open_pos)
    assert not decision.approved
    assert "max open positions" in decision.reason


def test_one_position_per_symbol(spec, account):
    rm = RiskManager(RiskConfig(max_positions_per_symbol=1), 10_000)
    existing = [Position(ticket=1, symbol="EURUSD", side=Side.BUY, lots=0.1,
                         entry_price=1.10, open_time=datetime.now(UTC))]
    assert not rm.evaluate(make_signal(), spec, account, existing).approved


def test_currency_concentration_limit(spec, account):
    rm = RiskManager(RiskConfig(max_currency_exposure=1, max_positions_per_symbol=5),
                     10_000)
    existing = [Position(ticket=1, symbol="GBPUSD", side=Side.BUY, lots=0.1,
                         entry_price=1.27, open_time=datetime.now(UTC))]
    decision = rm.evaluate(make_signal(), spec, account, existing)
    assert not decision.approved
    assert "USD" in decision.reason


def test_daily_loss_limit_stops_trading(spec, account):
    rm = RiskManager(RiskConfig(max_daily_loss_pct=2.0), 10_000)
    now = datetime(2024, 1, 3, 10, tzinfo=UTC)
    rm.on_bar(now, account)
    rm.on_trade_closed(_losing_trade(-250.0, now), now)
    decision = rm.evaluate(make_signal(), spec, account, [], now=now)
    assert not decision.approved
    assert "daily loss limit" in decision.reason


def test_drawdown_kill_switch(spec):
    rm = RiskManager(RiskConfig(max_drawdown_pct=10.0), 10_000)
    now = datetime(2024, 1, 3, 10, tzinfo=UTC)
    rm.on_bar(now, AccountState(balance=10_000, equity=10_000, free_margin=10_000))
    rm.on_bar(now, AccountState(balance=8_900, equity=8_900, free_margin=8_900))
    assert rm.state.halted
    decision = rm.evaluate(
        make_signal(), spec,
        AccountState(balance=8_900, equity=8_900, free_margin=8_900), [], now=now
    )
    assert not decision.approved
    assert "halted" in decision.reason


def test_losing_streak_triggers_cooldown(spec, account):
    cfg = RiskConfig(max_consecutive_losses=3, cooldown_minutes_after_streak=60)
    rm = RiskManager(cfg, 10_000)
    now = datetime(2024, 1, 3, 10, tzinfo=UTC)
    rm.on_bar(now, account)
    for _ in range(3):
        rm.on_trade_closed(_losing_trade(-20.0, now), now)
    decision = rm.evaluate(make_signal(), spec, account, [], now=now)
    assert not decision.approved
    assert "cool-down" in decision.reason

    # ...and it expires
    later = now + timedelta(minutes=61)
    rm.on_bar(later, account)
    assert rm.evaluate(make_signal(), spec, account, [], now=later).approved


def test_streak_reduces_size_before_it_blocks(spec, account):
    cfg = RiskConfig(max_consecutive_losses=10, reduce_risk_after_loss=True,
                     loss_risk_multiplier=0.5, scale_risk_with_confidence=False)
    rm = RiskManager(cfg, 10_000)
    now = datetime(2024, 1, 3, 10, tzinfo=UTC)
    rm.on_bar(now, account)
    full = rm.evaluate(make_signal(), spec, account, [], now=now)
    for _ in range(2):
        rm.on_trade_closed(_losing_trade(-10.0, now), now)
    reduced = rm.evaluate(make_signal(), spec, account, [], now=now)
    assert reduced.lots < full.lots


def test_portfolio_risk_cap_trims_or_blocks(spec, account):
    cfg = RiskConfig(risk_per_trade_pct=2.0, max_total_risk_pct=2.5,
                     max_positions_per_symbol=5, max_currency_exposure=9,
                     scale_risk_with_confidence=False)
    rm = RiskManager(cfg, 10_000)
    now = datetime(2024, 1, 3, 10, tzinfo=UTC)
    rm.on_bar(now, account)
    # an open trade already risking ~2%
    existing = Position(ticket=1, symbol="GBPUSD", side=Side.BUY, lots=1.0,
                        entry_price=1.2700, open_time=now, stop_loss=1.2680,
                        initial_stop=1.2680,
                        meta={"spec": SymbolSpec(symbol="GBPUSD")})
    decision = rm.evaluate(make_signal(), spec, account, [existing], now=now)
    assert decision.lots == 0 or decision.sizing.risk_pct <= 0.6


def test_open_risk_ignores_stops_already_in_profit(spec, account):
    rm = RiskManager(RiskConfig(), 10_000)
    protected = Position(ticket=1, symbol="EURUSD", side=Side.BUY, lots=1.0,
                         entry_price=1.1000, open_time=datetime.now(UTC),
                         stop_loss=1.1020, initial_stop=1.0980,
                         meta={"spec": spec})
    assert rm.open_risk_pct([protected], account) == 0.0


def test_manual_halt_and_resume(spec, account):
    rm = RiskManager(RiskConfig(), 10_000)
    now = datetime(2024, 1, 3, 10, tzinfo=UTC)
    rm.on_bar(now, account)
    rm.halt("testing")
    assert not rm.evaluate(make_signal(), spec, account, [], now=now).approved
    rm.resume()
    assert rm.evaluate(make_signal(), spec, account, [], now=now).approved


def _losing_trade(pnl: float, when: datetime) -> Trade:
    return Trade(
        ticket=1, symbol="EURUSD", side=Side.BUY, lots=0.1,
        entry_price=1.10, exit_price=1.09, open_time=when, close_time=when,
        pnl=pnl, reason=CloseReason.STOP_LOSS, r_multiple=-1.0,
    )


# --------------------------------------------------------------------------- #
# Notional and margin for USD-base pairs
# --------------------------------------------------------------------------- #
def test_notional_per_lot_does_not_multiply_price_for_usd_base_pairs():
    """One lot of USDJPY is already 100,000 USD.

    Multiplying by the JPY price overstates the notional 114x, the margin
    guard then caps the position, and the pair trades at a twentieth of its
    configured risk. This went unnoticed across 482 USDJPY trades.
    """
    from omega.data.synthetic import default_spec

    usdjpy = default_spec("USDJPY")
    assert usdjpy.notional_per_lot(114.10) == pytest.approx(100_000.0)

    eurusd = default_spec("EURUSD")
    assert eurusd.notional_per_lot(1.10) == pytest.approx(110_000.0)


def test_usd_base_pairs_get_their_full_configured_risk():
    from omega.config import RiskConfig
    from omega.data.synthetic import default_spec
    from omega.risk.sizing import position_size

    cfg = RiskConfig()
    cfg.scale_risk_with_confidence = False

    def risk_pct_for(symbol, price, stop):
        spec = default_spec(symbol)
        spec.commission_per_lot = 6.0
        return position_size(spec=spec, cfg=cfg, equity=10_000.0,
                             stop_distance=stop, entry_price=price,
                             free_margin=10_000.0).risk_pct

    # Every major should land near the configured 1%, JPY included.
    assert risk_pct_for("EURUSD", 1.1000, 0.0030) == pytest.approx(1.0, abs=0.1)
    assert risk_pct_for("USDJPY", 114.10, 0.375) == pytest.approx(1.0, abs=0.1)
    assert risk_pct_for("USDCAD", 1.4600, 0.0040) == pytest.approx(1.0, abs=0.1)


def test_margin_guard_still_caps_an_oversized_position():
    """The fix must not disable the guard it was hiding in."""
    from omega.config import RiskConfig
    from omega.data.synthetic import default_spec
    from omega.risk.sizing import position_size

    cfg = RiskConfig()
    cfg.scale_risk_with_confidence = False
    cfg.risk_per_trade_pct = 50.0          # absurd on purpose
    spec = default_spec("EURUSD")
    result = position_size(spec=spec, cfg=cfg, equity=100_000.0,
                           stop_distance=0.0005, entry_price=1.10,
                           free_margin=4_000.0)
    assert result.margin_required <= 4_000.0 + 1e-6
    assert any("margin-capped" in n for n in result.notes)
