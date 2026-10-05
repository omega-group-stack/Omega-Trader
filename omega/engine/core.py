"""The trading core — one bar, one decision.

This is the single place where "what should the bot do right now?" is answered.
Backtest, paper and live all call :meth:`TradingCore.on_bar`, which is exactly
why a backtest is a faithful rehearsal of live behaviour rather than a
different program that happens to share some indicators.

Order of operations on every closed bar:

1. roll the risk manager's day/week counters and refresh the equity peak
2. **manage open positions** — break-even, trailing stop, partial take-profit,
   signal-decay exit, time stop, weekend flatten
3. **consider a new entry** — only if the ensemble is actionable, the risk
   manager approves, and no cool-down is running
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from ..config import AppConfig
from ..core.types import (
    Candle,
    CloseReason,
    OrderRequest,
    Position,
    Side,
    Signal,
    SignalDirection,
    SymbolSpec,
    Trade,
)
from ..data.timeframes import delta as tf_delta
from ..risk import RiskManager, manage_position, partial_close_lots
from ..strategy import EnsembleStrategy
from ..strategy.sessions import should_flatten_for_weekend

log = logging.getLogger(__name__)


@dataclass
class BarOutcome:
    """Everything the core did on one bar — used for logging and the UI."""

    time: datetime
    symbol: str
    signal: Optional[Signal] = None
    opened: Optional[Position] = None
    closed: List[Trade] = field(default_factory=list)
    actions: List[str] = field(default_factory=list)
    rejected: str = ""


class TradingCore:
    """Strategy + risk + broker, wired together."""

    def __init__(
        self,
        cfg: AppConfig,
        broker,
        risk: RiskManager,
        strategies: Dict[str, EnsembleStrategy],
    ) -> None:
        self.cfg = cfg
        self.broker = broker
        self.risk = risk
        self.strategies = strategies
        self.bar_delta = tf_delta(cfg.strategy.timeframe)
        self._last_signal: Dict[str, Signal] = {}

    # ------------------------------------------------------------------ #
    def on_bar(self, symbol: str, bar_index: int, bar: Candle) -> BarOutcome:
        """Process one closed bar for ``symbol``."""
        sym = symbol.upper()
        strategy = self.strategies[sym]
        spec = self.broker.symbol_spec(sym)
        account = self.broker.account()
        outcome = BarOutcome(time=bar.time, symbol=sym)

        self.risk.on_bar(bar.time, account)

        signal = strategy.signal_at(bar_index, sym)
        self._last_signal[sym] = signal
        outcome.signal = signal

        # 1. manage what is already open
        outcome.closed.extend(self._manage_open(sym, spec, bar, signal, outcome))

        # 2. look for a new entry
        if not outcome.closed or self.cfg.strategy.reverse_on_flip:
            self._consider_entry(sym, spec, bar, signal, outcome)

        return outcome

    # ------------------------------------------------------------------ #
    # Position management
    # ------------------------------------------------------------------ #
    def _manage_open(
        self,
        symbol: str,
        spec: SymbolSpec,
        bar: Candle,
        signal: Signal,
        outcome: BarOutcome,
    ) -> List[Trade]:
        cfg = self.cfg
        closed: List[Trade] = []
        price = bar.close
        atr = signal.atr

        for pos in self.broker.positions(symbol):
            # --- hard exits ---------------------------------------------------
            if self.risk.state.halted:
                closed += self._close(pos, CloseReason.RISK_HALT, outcome)
                continue

            if should_flatten_for_weekend(bar.time, cfg.strategy.sessions):
                closed += self._close(pos, CloseReason.SESSION_END, outcome)
                continue

            if cfg.risk.time_stop_bars and pos.bars_held >= cfg.risk.time_stop_bars:
                closed += self._close(pos, CloseReason.TIME_STOP, outcome)
                continue

            # --- signal-based exits --------------------------------------------
            aligned = signal.score * pos.side.sign
            if cfg.strategy.reverse_on_flip and aligned <= -cfg.strategy.flip_threshold:
                closed += self._close(pos, CloseReason.SIGNAL_FLIP, outcome)
                continue
            # Bank a decayed thesis only once the trade has paid for itself.
            # Exiting at +0.05R every time the score wobbles is how a system
            # ends up with tiny winners and full-sized losers.
            if (
                aligned < cfg.strategy.exit_threshold
                and pos.r_multiple(price) >= cfg.strategy.exit_min_r
            ):
                closed += self._close(pos, CloseReason.SIGNAL_EXIT, outcome)
                continue

            # --- partial take-profit --------------------------------------------
            part = partial_close_lots(pos, price, spec, cfg.risk)
            if part > 0:
                result = self.broker.close_position(
                    pos.ticket, lots=part, reason=CloseReason.PARTIAL_TP
                )
                if result.ok:
                    outcome.actions.append(
                        f"partial TP {part:.2f} lots at "
                        f"+{pos.r_multiple(price):.2f}R on #{pos.ticket}"
                    )
                    if isinstance(result.raw, Trade):
                        closed.append(result.raw)
                        self.risk.on_trade_closed(result.raw, bar.time)
                    still = self.broker.position_by_ticket(pos.ticket)
                    if still is not None:
                        still.partials_done = pos.partials_done + 1
                        pos = still

            # --- stop management -------------------------------------------------
            new_stop, reason = manage_position(
                pos, price, atr, spec, cfg.risk,
                highest=pos.entry_price + max(pos.max_favourable, 0.0) * pos.side.sign
                if pos.side is Side.BUY else None,
                lowest=pos.entry_price - max(pos.max_favourable, 0.0)
                if pos.side is Side.SELL else None,
            )
            if new_stop is not None:
                if self.broker.modify_position(pos.ticket, stop_loss=new_stop):
                    live = self.broker.position_by_ticket(pos.ticket)
                    if live is not None and "break-even" in reason:
                        live.breakeven_done = True
                    outcome.actions.append(
                        f"stop -> {new_stop:.5f} on #{pos.ticket} ({reason})"
                    )
        return closed

    def _close(self, pos: Position, reason: CloseReason, outcome: BarOutcome) -> List[Trade]:
        result = self.broker.close_position(pos.ticket, reason=reason)
        if not result.ok:
            log.warning("close #%s failed: %s", pos.ticket, result.message)
            return []
        outcome.actions.append(
            f"closed #{pos.ticket} {pos.symbol} {pos.side.value} ({reason.value})"
        )
        if isinstance(result.raw, Trade):
            self.risk.on_trade_closed(result.raw, outcome.time)
            return [result.raw]
        return []

    # ------------------------------------------------------------------ #
    # Entries
    # ------------------------------------------------------------------ #
    def _consider_entry(
        self,
        symbol: str,
        spec: SymbolSpec,
        bar: Candle,
        signal: Signal,
        outcome: BarOutcome,
    ) -> None:
        cfg = self.cfg
        if not signal.is_actionable:
            if signal.vetoes:
                outcome.rejected = signal.vetoes[0]
            return

        existing = self.broker.positions(symbol)
        if existing and not cfg.strategy.allow_pyramiding:
            same_way = any(p.side.sign == signal.direction.sign for p in existing)
            if same_way:
                outcome.rejected = "already in the trade"
                return

        cooldown = self.bar_delta * max(0, cfg.strategy.cooldown_bars_after_exit)
        if cooldown and self.risk.cooldown_active(symbol, bar.time, cooldown):
            outcome.rejected = "re-entry cool-down"
            return

        account = self.broker.account()
        sym_cfg = cfg.symbol(symbol)
        decision = self.risk.evaluate(
            signal,
            spec,
            account,
            self.broker.positions(),
            entry_price=bar.close,
            symbol_weight=sym_cfg.weight,
            now=bar.time,
        )
        if not decision.approved:
            outcome.rejected = decision.reason
            return

        side = signal.direction.to_side()
        assert side is not None
        request = OrderRequest(
            symbol=symbol,
            side=side,
            lots=decision.lots,
            stop_loss=decision.stop_loss,
            take_profit=decision.take_profit,
            comment=f"omega {signal.regime.value[:8]}",
            magic=cfg.mt5.magic,
            meta={
                "entry_score": signal.score,
                "confidence": signal.confidence,
                "regime": signal.regime.value,
                "spec": spec,
            },
        )
        result = self.broker.market_order(request)
        if not result.ok:
            outcome.rejected = result.message
            log.warning("order rejected: %s", result.message)
            return

        self.risk.on_trade_opened()
        pos = self.broker.position_by_ticket(result.ticket) if result.ticket else None
        outcome.opened = pos
        outcome.actions.append(
            f"OPEN {side.value} {decision.lots:.2f} {symbol} @ {result.price:.5f} "
            f"| SL {decision.stop_loss:.5f} TP "
            f"{decision.take_profit:.5f} | {decision.reason}"
            if decision.take_profit
            else f"OPEN {side.value} {decision.lots:.2f} {symbol} @ {result.price:.5f} "
                 f"| SL {decision.stop_loss:.5f} | {decision.reason}"
        )
        log.info("%s | %s", bar.time, outcome.actions[-1])

    # ------------------------------------------------------------------ #
    def last_signal(self, symbol: str) -> Optional[Signal]:
        return self._last_signal.get(symbol.upper())
