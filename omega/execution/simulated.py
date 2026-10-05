"""Simulated broker — powers both ``backtest`` and ``paper`` modes.

Realism matters more than speed here, so the simulation models:

* **bid/ask spread** — buys fill at the ask, sells at the bid, and exits cross
  the spread again
* **slippage** — configurable points against you on every market fill
* **commission** — charged per lot, round-turn
* **swap / rollover** — accrued at the daily roll, tripled on Wednesdays
* **intrabar stop & target hits** — detected from the bar's high/low, with the
  conservative assumption that if a bar could have hit *both* the stop and the
  target, the **stop** came first
* **margin** — positions are rejected when free margin is insufficient
"""

from __future__ import annotations

import itertools
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

from ..config import AccountConfig, ExecutionConfig
from .base import Broker
from ..core.types import (
    AccountState,
    Candle,
    CloseReason,
    OrderRequest,
    OrderResult,
    Position,
    Side,
    SymbolSpec,
    Trade,
)

log = logging.getLogger(__name__)


class SimulatedBroker(Broker):
    """An account simulator that fills orders against supplied bars."""

    name = "simulated"
    supports_partial_close = True

    def __init__(
        self,
        exec_cfg: ExecutionConfig,
        account_cfg: AccountConfig,
        specs: Optional[Dict[str, SymbolSpec]] = None,
    ) -> None:
        self.cfg = exec_cfg
        self.account_cfg = account_cfg
        self.specs: Dict[str, SymbolSpec] = {
            k.upper(): v for k, v in (specs or {}).items()
        }
        self.balance = float(account_cfg.initial_balance)
        self.currency = account_cfg.currency
        self.leverage = int(account_cfg.leverage)

        self._tickets = itertools.count(1_000_001)
        self._positions: Dict[int, Position] = {}
        self._trades: List[Trade] = []
        self._bars: Dict[str, Candle] = {}
        self._last_roll: Dict[int, datetime] = {}
        self.equity_curve: List[tuple[datetime, float, float]] = []  # (t, equity, balance)
        self.now: datetime = datetime.now(timezone.utc)

    # ------------------------------------------------------------------ #
    # Broker interface
    # ------------------------------------------------------------------ #
    def account(self) -> AccountState:
        equity = self.balance + sum(
            self._unrealized(p) for p in self._positions.values()
        )
        margin = sum(self._margin_of(p) for p in self._positions.values())
        return AccountState(
            balance=round(self.balance, 2),
            equity=round(equity, 2),
            margin=round(margin, 2),
            free_margin=round(equity - margin, 2),
            currency=self.currency,
            leverage=self.leverage,
            server_time=self.now,
        )

    def positions(self, symbol: Optional[str] = None) -> List[Position]:
        out = list(self._positions.values())
        if symbol:
            out = [p for p in out if p.symbol.upper() == symbol.upper()]
        return sorted(out, key=lambda p: p.open_time)

    def closed_trades(self) -> List[Trade]:
        return list(self._trades)

    def symbol_spec(self, symbol: str) -> SymbolSpec:
        sym = symbol.upper()
        if sym not in self.specs:
            from ..data.synthetic import default_spec

            self.specs[sym] = default_spec(sym)
        return self.specs[sym]

    def register_spec(self, spec: SymbolSpec) -> None:
        self.specs[spec.symbol.upper()] = spec

    # ------------------------------------------------------------------ #
    # Market data plumbing
    # ------------------------------------------------------------------ #
    def set_bar(self, symbol: str, bar: Candle) -> None:
        """Make ``bar`` the current market for ``symbol``."""
        self._bars[symbol.upper()] = bar
        self.now = bar.time

    def current_bar(self, symbol: str) -> Optional[Candle]:
        return self._bars.get(symbol.upper())

    def mid_price(self, symbol: str) -> float:
        bar = self._bars.get(symbol.upper())
        return bar.close if bar else 0.0

    def spread_price(self, symbol: str) -> float:
        """Current spread in price units."""
        spec = self.symbol_spec(symbol)
        bar = self._bars.get(symbol.upper())
        points = (bar.spread if bar and bar.spread else self.cfg.spread_points)
        return float(points) * spec.point

    def bid_ask(self, symbol: str, reference: Optional[float] = None) -> tuple[float, float]:
        mid = reference if reference is not None else self.mid_price(symbol)
        half = self.spread_price(symbol) / 2.0
        return mid - half, mid + half

    # ------------------------------------------------------------------ #
    # Order execution
    # ------------------------------------------------------------------ #
    def market_order(
        self, request: OrderRequest, reference_price: Optional[float] = None
    ) -> OrderResult:
        spec = self.symbol_spec(request.symbol)
        lots = spec.normalize_lots(request.lots)
        if lots <= 0:
            return OrderResult(False, message=f"invalid lot size {request.lots}")

        bid, ask = self.bid_ask(request.symbol, reference_price)
        slip = self.cfg.slippage_points * spec.point
        price = (ask + slip) if request.side is Side.BUY else (bid - slip)
        price = spec.normalize_price(price)
        if price <= 0:
            return OrderResult(False, message="no market price available")

        # margin check
        required = lots * spec.contract_size * price * spec.margin_rate
        acct = self.account()
        if required > acct.free_margin:
            return OrderResult(
                False,
                message=(
                    f"insufficient margin: need {required:.2f}, "
                    f"free {acct.free_margin:.2f}"
                ),
            )

        commission = lots * spec.commission_per_lot
        self.balance -= commission

        ticket = next(self._tickets)
        pos = Position(
            ticket=ticket,
            symbol=spec.symbol,
            side=request.side,
            lots=lots,
            entry_price=price,
            open_time=self.now,
            stop_loss=spec.normalize_price(request.stop_loss) if request.stop_loss else None,
            take_profit=spec.normalize_price(request.take_profit) if request.take_profit else None,
            initial_lots=lots,
            commission=commission,
            comment=request.comment,
            magic=request.magic,
            meta={**request.meta, "spec": spec},
        )
        self._positions[ticket] = pos
        self._last_roll[ticket] = self.now
        log.debug(
            "OPEN #%s %s %s %.2f @ %.5f sl=%s tp=%s",
            ticket, request.side.value, spec.symbol, lots, price,
            pos.stop_loss, pos.take_profit,
        )
        return OrderResult(True, ticket=ticket, price=price, lots=lots,
                           message="filled")

    def modify_position(
        self,
        ticket: int,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
    ) -> bool:
        pos = self._positions.get(ticket)
        if pos is None:
            return False
        spec = self.symbol_spec(pos.symbol)
        if stop_loss is not None:
            pos.stop_loss = spec.normalize_price(stop_loss)
        if take_profit is not None:
            pos.take_profit = spec.normalize_price(take_profit)
        return True

    def close_position(
        self,
        ticket: int,
        lots: Optional[float] = None,
        reason: CloseReason = CloseReason.MANUAL,
        price: Optional[float] = None,
    ) -> OrderResult:
        pos = self._positions.get(ticket)
        if pos is None:
            return OrderResult(False, message=f"position {ticket} not found")

        spec = self.symbol_spec(pos.symbol)
        bid, ask = self.bid_ask(pos.symbol, price)
        slip = self.cfg.slippage_points * spec.point
        exit_price = (bid - slip) if pos.side is Side.BUY else (ask + slip)
        if price is not None and reason in (
            CloseReason.STOP_LOSS, CloseReason.TAKE_PROFIT, CloseReason.TRAILING_STOP
        ):
            # the stop/target level itself is the fill (plus slippage on stops)
            exit_price = price - pos.side.sign * (
                slip if reason is not CloseReason.TAKE_PROFIT else 0.0
            )
        exit_price = spec.normalize_price(exit_price)

        close_lots = spec.normalize_lots(lots) if lots else pos.lots
        close_lots = min(close_lots, pos.lots)
        if close_lots <= 0:
            return OrderResult(False, message="nothing to close")

        move = (exit_price - pos.entry_price) * pos.side.sign
        gross = spec.money_per_lot(move) * close_lots * (1 if move >= 0 else -1)
        commission = close_lots * spec.commission_per_lot * 0.0  # charged on entry
        swap_share = pos.swap * (close_lots / pos.lots) if pos.lots else 0.0
        pnl = gross + swap_share

        self.balance += pnl
        partial = close_lots < pos.lots

        trade = Trade(
            ticket=pos.ticket,
            symbol=pos.symbol,
            side=pos.side,
            lots=close_lots,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            open_time=pos.open_time,
            close_time=self.now,
            pnl=round(pnl - (pos.commission * close_lots / max(pos.initial_lots, 1e-9)), 6),
            commission=round(pos.commission * close_lots / max(pos.initial_lots, 1e-9), 4),
            swap=round(swap_share, 4),
            reason=reason,
            r_multiple=round(pos.r_multiple(exit_price), 4),
            mae=pos.max_adverse,
            mfe=pos.max_favourable,
            bars_held=pos.bars_held,
            entry_score=float(pos.meta.get("entry_score", 0.0)),
            regime=str(pos.meta.get("regime", "")),
            balance_after=round(self.balance, 2),
            meta={"partial": partial},
        )
        self._trades.append(trade)

        if partial:
            pos.lots = spec.normalize_lots(pos.lots - close_lots)
            pos.swap -= swap_share
            pos.realized_pnl += pnl
            pos.partials_done += 1
        else:
            self._positions.pop(ticket, None)
            self._last_roll.pop(ticket, None)

        log.debug(
            "CLOSE #%s %s %.2f @ %.5f pnl=%.2f (%s)",
            ticket, pos.symbol, close_lots, exit_price, pnl, reason.value,
        )
        return OrderResult(True, ticket=ticket, price=exit_price, lots=close_lots,
                           message=reason.value, raw=trade)

    def close_all(self, reason: CloseReason = CloseReason.MANUAL) -> List[OrderResult]:
        return [self.close_position(t, reason=reason) for t in list(self._positions)]

    # ------------------------------------------------------------------ #
    # Bar processing — stops, targets, swap, bookkeeping
    # ------------------------------------------------------------------ #
    def process_bar(self, symbol: str, bar: Candle) -> List[Trade]:
        """Advance the simulation by one bar and settle any stop/target hits."""
        self.set_bar(symbol, bar)
        closed: List[Trade] = []
        spec = self.symbol_spec(symbol)
        half_spread = self.spread_price(symbol) / 2.0

        for ticket in list(self._positions):
            pos = self._positions.get(ticket)
            if pos is None or pos.symbol.upper() != symbol.upper():
                continue

            pos.bars_held += 1
            self._accrue_swap(pos, spec, bar.time)

            # Excursions are measured on the price the position would exit at.
            sign = pos.side.sign
            best = bar.high if sign > 0 else bar.low
            worst = bar.low if sign > 0 else bar.high
            pos.max_favourable = max(pos.max_favourable, (best - pos.entry_price) * sign)
            pos.max_adverse = min(pos.max_adverse, (worst - pos.entry_price) * sign)

            hit = self._detect_hit(pos, bar, half_spread)
            if hit is None:
                continue
            level, reason = hit
            result = self.close_position(ticket, reason=reason, price=level)
            if result.ok and isinstance(result.raw, Trade):
                closed.append(result.raw)

        self._record_equity()
        return closed

    def _detect_hit(
        self, pos: Position, bar: Candle, half_spread: float
    ) -> Optional[tuple[float, CloseReason]]:
        """Did this bar touch the stop or the target? (stop wins ties)."""
        sign = pos.side.sign
        # Exits cross the spread: a long exits on the bid, a short on the ask.
        low = bar.low - (half_spread if sign > 0 else -half_spread)
        high = bar.high - (half_spread if sign > 0 else -half_spread)

        stop_hit = (
            pos.stop_loss is not None
            and ((sign > 0 and low <= pos.stop_loss) or (sign < 0 and high >= pos.stop_loss))
        )
        tp_hit = (
            pos.take_profit is not None
            and ((sign > 0 and high >= pos.take_profit) or (sign < 0 and low <= pos.take_profit))
        )

        if stop_hit and tp_hit:
            if self.cfg.stop_first_on_ambiguous_bar:
                return pos.stop_loss, _stop_reason(pos)  # type: ignore[arg-type]
            return pos.take_profit, CloseReason.TAKE_PROFIT  # type: ignore[return-value]
        if stop_hit:
            return pos.stop_loss, _stop_reason(pos)  # type: ignore[arg-type]
        if tp_hit:
            return pos.take_profit, CloseReason.TAKE_PROFIT  # type: ignore[return-value]
        return None

    def _accrue_swap(self, pos: Position, spec: SymbolSpec, now: datetime) -> None:
        if not self.cfg.swap_enabled:
            return
        last = self._last_roll.get(pos.ticket, pos.open_time)
        if now.date() <= last.date():
            return
        days = (now.date() - last.date()).days
        # Wednesday rollover carries triple swap
        multiplier = 3 if now.weekday() == 2 else 1
        points = spec.swap_long if pos.side is Side.BUY else spec.swap_short
        pos.swap += points * spec.point / spec.tick_size * spec.tick_value * pos.lots \
            * days * multiplier
        self._last_roll[pos.ticket] = now

    def _record_equity(self) -> None:
        acct = self.account()
        self.equity_curve.append((self.now, acct.equity, acct.balance))

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    def _unrealized(self, pos: Position) -> float:
        spec = self.symbol_spec(pos.symbol)
        bar = self._bars.get(pos.symbol.upper())
        if bar is None:
            return pos.swap
        half = self.spread_price(pos.symbol) / 2.0
        exit_price = bar.close - pos.side.sign * half
        move = (exit_price - pos.entry_price) * pos.side.sign
        gross = spec.money_per_lot(move) * pos.lots
        return (gross if move >= 0 else -gross) + pos.swap

    def _margin_of(self, pos: Position) -> float:
        spec = self.symbol_spec(pos.symbol)
        price = self.mid_price(pos.symbol) or pos.entry_price
        return pos.lots * spec.contract_size * price * spec.margin_rate


def _stop_reason(pos: Position) -> CloseReason:
    """Distinguish a trailing stop-out from the original protective stop."""
    if pos.initial_stop is not None and pos.stop_loss is not None:
        moved = abs(pos.stop_loss - pos.initial_stop) > 1e-12
        if moved:
            return CloseReason.TRAILING_STOP
    return CloseReason.STOP_LOSS
