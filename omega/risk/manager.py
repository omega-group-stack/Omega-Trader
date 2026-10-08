"""The risk manager — Omega's circuit breaker.

The strategy decides *what* to trade; this module decides *whether* and *how
much*, and it has absolute veto power. Every guard below is checked before a
single lot is sent:

* per-trade risk %            — the headline knob (``risk.risk_per_trade_pct``)
* total open risk %           — sum of the open positions' remaining risk
* max open positions          — overall and per symbol
* currency concentration      — e.g. no more than N trades touching USD
* free-margin floor
* daily / weekly loss limits  — stops trading for the rest of the period
* maximum drawdown            — hard kill-switch from the equity peak
* losing-streak cool-down     — pause, then resume at reduced size
* daily trade count & profit target
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional

from ..config import RiskConfig
from ..core.types import (
    AccountState,
    Position,
    Signal,
    SignalDirection,
    SymbolSpec,
    Trade,
)
from .sizing import SizingResult, position_size
from .stops import StopPlan, build_stop_plan

log = logging.getLogger(__name__)


@dataclass(slots=True)
class RiskDecision:
    """Outcome of a pre-trade risk check."""

    approved: bool
    lots: float = 0.0
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    reason: str = ""
    sizing: Optional[SizingResult] = None
    plan: Optional[StopPlan] = None
    warnings: List[str] = field(default_factory=list)
    # Round-trip cost (spread + commission) as a fraction of the stop
    # distance -- the number the cost guardian (experiment 8) decides on.
    cost_fraction: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "approved": self.approved,
            "lots": round(self.lots, 4),
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "reason": self.reason,
            "risk_pct": round(self.sizing.risk_pct, 3) if self.sizing else 0.0,
            "risk_money": round(self.sizing.risk_money, 2) if self.sizing else 0.0,
            "stop_pips": round(self.sizing.stop_pips, 1) if self.sizing else 0.0,
            "cost_fraction": (round(self.cost_fraction, 3)
                              if self.cost_fraction is not None else None),
            "warnings": self.warnings,
        }


@dataclass
class RiskState:
    """Rolling counters the guards are evaluated against."""

    equity_peak: float = 0.0
    day: Optional[date] = None
    week: Optional[tuple[int, int]] = None
    day_start_equity: float = 0.0
    week_start_equity: float = 0.0
    day_realized: float = 0.0
    week_realized: float = 0.0
    trades_today: int = 0
    consecutive_losses: int = 0
    wins_since_streak: int = 0
    cooldown_until: Optional[datetime] = None
    halted: bool = False
    halt_reason: str = ""
    last_exit_time: Dict[str, datetime] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "equity_peak": round(self.equity_peak, 2),
            "day_realized": round(self.day_realized, 2),
            "week_realized": round(self.week_realized, 2),
            "trades_today": self.trades_today,
            "consecutive_losses": self.consecutive_losses,
            "cooldown_until": self.cooldown_until.isoformat() if self.cooldown_until else None,
            "halted": self.halted,
            "halt_reason": self.halt_reason,
        }


class RiskManager:
    """Stateful gatekeeper. One instance per running bot."""

    def __init__(self, cfg: RiskConfig, starting_equity: float) -> None:
        self.cfg = cfg
        self.state = RiskState(
            equity_peak=starting_equity,
            day_start_equity=starting_equity,
            week_start_equity=starting_equity,
        )

    # ------------------------------------------------------------------ #
    # Clock / accounting
    # ------------------------------------------------------------------ #
    def on_bar(self, now: datetime, account: AccountState) -> None:
        """Roll day/week counters and update the equity peak."""
        today = now.date()
        iso = now.isocalendar()
        week = (iso[0], iso[1])

        if self.state.day != today:
            self.state.day = today
            self.state.day_start_equity = account.equity
            self.state.day_realized = 0.0
            self.state.trades_today = 0
        if self.state.week != week:
            self.state.week = week
            self.state.week_start_equity = account.equity
            self.state.week_realized = 0.0

        self.state.equity_peak = max(self.state.equity_peak, account.equity)

        if self.state.cooldown_until and now >= self.state.cooldown_until:
            self.state.cooldown_until = None

        self._check_drawdown(account)

    def on_trade_closed(self, trade: Trade, now: Optional[datetime] = None) -> None:
        """Feed realised P/L back into the guards."""
        now = now or trade.close_time
        self.state.day_realized += trade.pnl
        self.state.week_realized += trade.pnl
        self.state.last_exit_time[trade.symbol] = now

        if trade.pnl < 0:
            self.state.consecutive_losses += 1
            self.state.wins_since_streak = 0
            if (
                self.cfg.max_consecutive_losses
                and self.state.consecutive_losses >= self.cfg.max_consecutive_losses
            ):
                minutes = self.cfg.cooldown_minutes_after_streak
                if minutes > 0:
                    self.state.cooldown_until = now + timedelta(minutes=minutes)
                    log.warning(
                        "Losing streak of %d — cooling down until %s",
                        self.state.consecutive_losses, self.state.cooldown_until,
                    )
        else:
            self.state.wins_since_streak += 1
            if self.state.wins_since_streak >= max(1, self.cfg.recovery_wins_needed):
                self.state.consecutive_losses = 0

    def on_trade_opened(self) -> None:
        self.state.trades_today += 1

    # ------------------------------------------------------------------ #
    # Pre-trade checks
    # ------------------------------------------------------------------ #
    def evaluate(
        self,
        signal: Signal,
        spec: SymbolSpec,
        account: AccountState,
        positions: Iterable[Position],
        *,
        entry_price: Optional[float] = None,
        symbol_weight: float = 1.0,
        now: Optional[datetime] = None,
    ) -> RiskDecision:
        """Full pre-trade gate: returns an approved size or a refusal reason."""
        cfg = self.cfg
        now = now or signal.time
        positions = list(positions)
        warnings: List[str] = []

        if signal.direction is SignalDirection.FLAT:
            return RiskDecision(False, reason="no directional signal")
        if signal.vetoes:
            return RiskDecision(False, reason=f"strategy veto: {signal.vetoes[0]}")

        # --- account-level halts -------------------------------------------
        blocked = self._account_blocks(account, now)
        if blocked:
            return RiskDecision(False, reason=blocked)

        # --- exposure limits -------------------------------------------------
        exposure = self._exposure_blocks(signal, spec, positions, account)
        if exposure:
            return RiskDecision(False, reason=exposure)

        # --- stops ------------------------------------------------------------
        price = entry_price if entry_price is not None else signal.price
        plan = build_stop_plan(signal, spec, cfg, price)
        if plan.note:
            warnings.append(plan.note)

        # --- cost guardian (experiment 8) -------------------------------------
        # A trade whose round-trip cost eats too much of its stop distance
        # is refused before any sizing work: with a 1.5-pip stop against a
        # 1.2-pip spread, even perfect foresight lost money.
        cost_fraction = self._cost_fraction(signal, spec, plan.stop_distance)
        if cfg.max_cost_fraction > 0 and cost_fraction > cfg.max_cost_fraction:
            return RiskDecision(
                False,
                reason=(
                    f"cost veto: round-trip cost {cost_fraction:.0%} of stop "
                    f"distance exceeds max_cost_fraction={cfg.max_cost_fraction:.0%}"
                ),
                plan=plan,
                warnings=warnings,
                cost_fraction=cost_fraction,
            )
        if cfg.max_cost_fraction > 0 and cost_fraction >= 0.5 * cfg.max_cost_fraction:
            warnings.append(
                f"round-trip cost is {cost_fraction:.0%} of the stop distance"
            )

        # --- sizing -----------------------------------------------------------
        equity = account.equity if cfg.risk_base == "equity" else account.balance
        throttle = float(signal.meta.get("risk_multiplier", 1.0)) * self._streak_multiplier()

        headroom = self._risk_headroom_pct(positions, account)
        sizing = position_size(
            spec,
            cfg,
            equity,
            plan.stop_distance,
            price,
            confidence=signal.confidence,
            risk_multiplier=throttle,
            symbol_weight=symbol_weight,
            free_margin=account.free_margin or None,
        )
        warnings.extend(sizing.notes)

        if sizing.lots <= 0:
            return RiskDecision(
                False,
                reason=sizing.notes[-1] if sizing.notes else "position size rounds to zero",
                sizing=sizing,
                plan=plan,
                warnings=warnings,
            )

        # Trim so the *portfolio* risk cap is respected.
        if sizing.risk_pct > headroom:
            scale = headroom / sizing.risk_pct if sizing.risk_pct > 0 else 0.0
            trimmed = spec.normalize_lots(sizing.lots * scale)
            if trimmed <= 0:
                return RiskDecision(
                    False,
                    reason=(
                        f"portfolio risk cap reached "
                        f"({cfg.max_total_risk_pct:.2f}% total, {headroom:.2f}% free)"
                    ),
                    sizing=sizing,
                    plan=plan,
                    warnings=warnings,
                )
            warnings.append(
                f"trimmed {sizing.lots:.2f} -> {trimmed:.2f} lots to respect "
                f"max_total_risk_pct={cfg.max_total_risk_pct:.2f}%"
            )
            sizing.lots = trimmed
            sizing.risk_money = trimmed * (sizing.loss_per_lot + spec.commission_per_lot)
            sizing.risk_pct = (sizing.risk_money / equity * 100.0) if equity else 0.0

        return RiskDecision(
            approved=True,
            lots=sizing.lots,
            stop_loss=plan.stop_loss,
            take_profit=plan.take_profit,
            reason=(
                f"risking {sizing.risk_pct:.2f}% ({sizing.risk_money:.2f} "
                f"{account.currency}) over {sizing.stop_pips:.1f} pips"
            ),
            sizing=sizing,
            plan=plan,
            warnings=warnings,
            cost_fraction=cost_fraction,
        )

    # ------------------------------------------------------------------ #
    # Guard implementations
    # ------------------------------------------------------------------ #
    def _cost_fraction(
        self, signal: Signal, spec: SymbolSpec, stop_distance: float
    ) -> float:
        """Round-trip cost (spread + commission) as a fraction of stop distance.

        Prefers the spread the feed actually reported in ``signal.meta``; when
        none was reported it assumes ``typical_spread_points`` -- optimistic
        spreads are how backtests lie.
        """
        if stop_distance is None or stop_distance <= 0:
            return float("inf")
        pts = float(signal.meta.get("spread_points") or 0.0)
        if pts <= 0:
            pts = self.cfg.typical_spread_points
        cost_money = (spec.money_per_lot(pts * spec.point)
                      + spec.commission_per_lot)
        risk_money = spec.money_per_lot(stop_distance)
        if risk_money <= 0:
            return float("inf")
        return cost_money / risk_money

    def _account_blocks(self, account: AccountState, now: datetime) -> str:
        cfg, st = self.cfg, self.state

        if st.halted:
            return f"trading halted: {st.halt_reason}"
        if st.cooldown_until and now < st.cooldown_until:
            return (
                f"cool-down after {st.consecutive_losses} losses "
                f"(until {st.cooldown_until:%H:%M})"
            )
        if cfg.max_daily_trades and st.trades_today >= cfg.max_daily_trades:
            return f"daily trade cap reached ({cfg.max_daily_trades})"

        day_pnl_pct = self._pct(st.day_realized, st.day_start_equity)
        if cfg.max_daily_loss_pct and day_pnl_pct <= -abs(cfg.max_daily_loss_pct):
            return (
                f"daily loss limit hit ({day_pnl_pct:.2f}% of "
                f"{st.day_start_equity:.0f})"
            )
        week_pnl_pct = self._pct(st.week_realized, st.week_start_equity)
        if cfg.max_weekly_loss_pct and week_pnl_pct <= -abs(cfg.max_weekly_loss_pct):
            return f"weekly loss limit hit ({week_pnl_pct:.2f}%)"

        if cfg.daily_profit_target_pct and day_pnl_pct >= cfg.daily_profit_target_pct:
            return f"daily profit target reached (+{day_pnl_pct:.2f}%) — banking it"

        if account.margin > 0 and account.margin_level < 100.0 + cfg.min_free_margin_pct:
            return f"margin level {account.margin_level:.0f}% too low"
        if account.equity <= 0:
            return "account equity depleted"
        return ""

    def _exposure_blocks(
        self,
        signal: Signal,
        spec: SymbolSpec,
        positions: List[Position],
        account: AccountState,
    ) -> str:
        cfg = self.cfg
        if cfg.max_open_positions and len(positions) >= cfg.max_open_positions:
            return f"max open positions reached ({cfg.max_open_positions})"

        same = [p for p in positions if p.symbol.upper() == signal.symbol.upper()]
        if cfg.max_positions_per_symbol and len(same) >= cfg.max_positions_per_symbol:
            return f"already {len(same)} position(s) on {signal.symbol}"

        if cfg.max_currency_exposure:
            counts: Dict[str, int] = defaultdict(int)
            for p in positions:
                raw = "".join(ch for ch in p.symbol.upper() if ch.isalpha())
                for cur in {raw[:3], raw[3:6]} - {""}:
                    counts[cur] += 1
            for cur in {spec.base, spec.quote} - {""}:
                if counts[cur] >= cfg.max_currency_exposure:
                    return (
                        f"currency concentration: already {counts[cur]} open "
                        f"trades exposed to {cur}"
                    )
        return ""

    def _risk_headroom_pct(
        self, positions: List[Position], account: AccountState
    ) -> float:
        """Remaining portfolio risk budget, in % of equity."""
        cap = self.cfg.max_total_risk_pct or self.cfg.risk_per_trade_pct
        used = self.open_risk_pct(positions, account)
        return max(0.0, cap - used)

    def open_risk_pct(
        self, positions: Iterable[Position], account: AccountState
    ) -> float:
        """Sum of what the open positions would lose if every stop were hit."""
        if account.equity <= 0:
            return 0.0
        total = 0.0
        for p in positions:
            if p.stop_loss is None:
                continue
            spec = p.meta.get("spec")
            distance = abs(p.entry_price - p.stop_loss)
            if isinstance(spec, SymbolSpec):
                money = spec.money_per_lot(distance) * p.lots
            else:  # conservative generic fallback (100k contract)
                money = distance * 100_000.0 * p.lots
            # a stop already in profit contributes no risk
            if (p.side.sign > 0 and p.stop_loss >= p.entry_price) or (
                p.side.sign < 0 and p.stop_loss <= p.entry_price
            ):
                money = 0.0
            total += money
        return total / account.equity * 100.0

    def _streak_multiplier(self) -> float:
        cfg, st = self.cfg, self.state
        if cfg.reduce_risk_after_loss and st.consecutive_losses >= 2:
            return max(0.1, cfg.loss_risk_multiplier)
        return 1.0

    def _check_drawdown(self, account: AccountState) -> None:
        cfg, st = self.cfg, self.state
        if not cfg.max_drawdown_pct or st.equity_peak <= 0:
            return
        dd = (st.equity_peak - account.equity) / st.equity_peak * 100.0
        if dd >= cfg.max_drawdown_pct and not st.halted:
            st.halted = True
            st.halt_reason = (
                f"max drawdown {dd:.2f}% >= {cfg.max_drawdown_pct:.2f}% "
                f"(peak {st.equity_peak:.2f} -> equity {account.equity:.2f})"
            )
            log.critical("KILL SWITCH — %s", st.halt_reason)

    # ------------------------------------------------------------------ #
    # Manual controls
    # ------------------------------------------------------------------ #
    def halt(self, reason: str = "manual halt") -> None:
        self.state.halted = True
        self.state.halt_reason = reason

    def resume(self) -> None:
        self.state.halted = False
        self.state.halt_reason = ""
        self.state.cooldown_until = None

    def cooldown_active(self, symbol: str, now: datetime, bars_delta: timedelta) -> bool:
        """True while the per-symbol re-entry cool-down is still running."""
        last = self.state.last_exit_time.get(symbol)
        return bool(last and now < last + bars_delta)

    @staticmethod
    def _pct(amount: float, base: float) -> float:
        return (amount / base * 100.0) if base > 0 else 0.0

    def snapshot(self, account: AccountState, positions: Iterable[Position]) -> dict:
        positions = list(positions)
        st = self.state
        dd = 0.0
        if st.equity_peak > 0:
            dd = (st.equity_peak - account.equity) / st.equity_peak * 100.0
        return {
            **st.to_dict(),
            "drawdown_pct": round(dd, 2),
            "open_risk_pct": round(self.open_risk_pct(positions, account), 3),
            "risk_headroom_pct": round(self._risk_headroom_pct(positions, account), 3),
            "day_pnl_pct": round(self._pct(st.day_realized, st.day_start_equity), 3),
            "week_pnl_pct": round(self._pct(st.week_realized, st.week_start_equity), 3),
            "risk_per_trade_pct": self.cfg.risk_per_trade_pct,
            "max_daily_loss_pct": self.cfg.max_daily_loss_pct,
            "max_drawdown_pct": self.cfg.max_drawdown_pct,
        }
