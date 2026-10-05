"""Live MetaTrader 5 broker.

Only reachable when ``execution.mode == "live"`` *and*
``execution.confirm_live == true`` — a deliberate two-key interlock so a real
account can never be traded by accident.

Every order carries the configured ``magic`` number, so Omega only ever
manages its own positions and happily coexists with manual trading or other
EAs on the same account.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ..config import MT5Config
from ..core.types import (
    AccountState,
    CloseReason,
    OrderRequest,
    OrderResult,
    Position,
    Side,
    SymbolSpec,
    Trade,
)
from ..data.mt5_feed import MT5Connection, mt5_module
from .base import Broker

log = logging.getLogger(__name__)


class MT5Broker(Broker):
    name = "mt5"
    supports_partial_close = True

    def __init__(self, cfg: MT5Config, deviation_points: Optional[int] = None) -> None:
        self.cfg = cfg
        self.conn = MT5Connection.shared(cfg)
        self.magic = int(cfg.magic)
        self.deviation = int(
            deviation_points if deviation_points is not None else cfg.deviation_points
        )
        self._specs: Dict[str, SymbolSpec] = {}
        self._closed: List[Trade] = []

    # -- lifecycle ----------------------------------------------------------
    def connect(self) -> None:
        self.conn.connect()

    def disconnect(self) -> None:
        self.conn.shutdown()

    # -- state ---------------------------------------------------------------
    def account(self) -> AccountState:
        mt5 = self.conn.ensure()
        info = mt5.account_info()
        if info is None:
            raise ConnectionError(f"MT5 account_info failed: {mt5.last_error()}")
        return AccountState(
            balance=float(info.balance),
            equity=float(info.equity),
            margin=float(info.margin),
            free_margin=float(info.margin_free),
            currency=str(info.currency),
            leverage=int(info.leverage),
            server_time=datetime.now(timezone.utc),
        )

    def positions(self, symbol: Optional[str] = None) -> List[Position]:
        mt5 = self.conn.ensure()
        raw = mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()
        if raw is None:
            return []
        out: List[Position] = []
        for p in raw:
            if self.magic and int(p.magic) != self.magic:
                continue  # not ours — leave it alone
            spec = self.symbol_spec(p.symbol)
            out.append(
                Position(
                    ticket=int(p.ticket),
                    symbol=str(p.symbol),
                    side=Side.BUY if p.type == mt5.POSITION_TYPE_BUY else Side.SELL,
                    lots=float(p.volume),
                    entry_price=float(p.price_open),
                    open_time=datetime.fromtimestamp(p.time, tz=timezone.utc),
                    stop_loss=float(p.sl) or None,
                    take_profit=float(p.tp) or None,
                    swap=float(p.swap),
                    comment=str(p.comment),
                    magic=int(p.magic),
                    meta={"spec": spec, "mt5_profit": float(p.profit)},
                )
            )
        return out

    def symbol_spec(self, symbol: str) -> SymbolSpec:
        sym = symbol.upper()
        if sym not in self._specs:
            from ..data.mt5_feed import MT5Feed

            self._specs[sym] = MT5Feed(self.cfg).symbol_spec(symbol)
        return self._specs[sym]

    def closed_trades(self) -> List[Trade]:
        return list(self._closed)

    def tick(self, symbol: str) -> tuple[float, float]:
        """``(bid, ask)`` straight from the terminal."""
        mt5 = self.conn.ensure()
        t = mt5.symbol_info_tick(symbol)
        if t is None:
            raise RuntimeError(f"No tick for {symbol}: {mt5.last_error()}")
        return float(t.bid), float(t.ask)

    # -- trading ---------------------------------------------------------------
    def market_order(self, request: OrderRequest) -> OrderResult:
        mt5 = self.conn.ensure()
        spec = self.symbol_spec(request.symbol)
        lots = spec.normalize_lots(request.lots)
        if lots <= 0:
            return OrderResult(False, message=f"invalid lot size {request.lots}")

        bid, ask = self.tick(request.symbol)
        is_buy = request.side is Side.BUY
        price = ask if is_buy else bid

        req: Dict[str, Any] = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": request.symbol,
            "volume": float(lots),
            "type": mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL,
            "price": float(price),
            "deviation": self.deviation,
            "magic": self.magic,
            "comment": request.comment[:31],
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self._filling_mode(request.symbol),
        }
        if request.stop_loss:
            req["sl"] = float(spec.normalize_price(request.stop_loss))
        if request.take_profit:
            req["tp"] = float(spec.normalize_price(request.take_profit))

        result = mt5.order_send(req)
        return self._interpret(result, lots, "market order")

    def modify_position(
        self,
        ticket: int,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
    ) -> bool:
        mt5 = self.conn.ensure()
        pos = self.position_by_ticket(ticket)
        if pos is None:
            return False
        spec = self.symbol_spec(pos.symbol)
        req = {
            "action": mt5.TRADE_ACTION_SLTP,
            "symbol": pos.symbol,
            "position": int(ticket),
            "sl": float(spec.normalize_price(stop_loss if stop_loss is not None
                                             else (pos.stop_loss or 0.0))),
            "tp": float(spec.normalize_price(take_profit if take_profit is not None
                                             else (pos.take_profit or 0.0))),
            "magic": self.magic,
        }
        result = mt5.order_send(req)
        ok = result is not None and result.retcode == mt5.TRADE_RETCODE_DONE
        if not ok:
            log.warning("modify #%s failed: %s", ticket, getattr(result, "comment", result))
        return ok

    def close_position(
        self,
        ticket: int,
        lots: Optional[float] = None,
        reason: CloseReason = CloseReason.MANUAL,
    ) -> OrderResult:
        mt5 = self.conn.ensure()
        pos = self.position_by_ticket(ticket)
        if pos is None:
            return OrderResult(False, message=f"position {ticket} not found")

        spec = self.symbol_spec(pos.symbol)
        volume = spec.normalize_lots(min(lots, pos.lots)) if lots else pos.lots
        if volume <= 0:
            return OrderResult(False, message="nothing to close")

        bid, ask = self.tick(pos.symbol)
        is_buy = pos.side is Side.BUY
        req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": pos.symbol,
            "volume": float(volume),
            "type": mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY,
            "position": int(ticket),
            "price": float(bid if is_buy else ask),
            "deviation": self.deviation,
            "magic": self.magic,
            "comment": f"omega {reason.value}"[:31],
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self._filling_mode(pos.symbol),
        }
        result = mt5.order_send(req)
        out = self._interpret(result, volume, f"close ({reason.value})")
        if out.ok:
            self._closed.append(
                Trade(
                    ticket=ticket,
                    symbol=pos.symbol,
                    side=pos.side,
                    lots=volume,
                    entry_price=pos.entry_price,
                    exit_price=out.price,
                    open_time=pos.open_time,
                    close_time=datetime.now(timezone.utc),
                    pnl=float(pos.meta.get("mt5_profit", 0.0)),
                    swap=pos.swap,
                    reason=reason,
                    r_multiple=pos.r_multiple(out.price),
                )
            )
        return out

    # -- internals ---------------------------------------------------------------
    def _filling_mode(self, symbol: str) -> int:
        """Pick a filling mode the symbol actually supports."""
        mt5 = self.conn.ensure()
        info = mt5.symbol_info(symbol)
        modes = int(getattr(info, "filling_mode", 0) or 0)
        if modes & 1:
            return mt5.ORDER_FILLING_FOK
        if modes & 2:
            return mt5.ORDER_FILLING_IOC
        return mt5.ORDER_FILLING_RETURN

    def _interpret(self, result: Any, lots: float, what: str) -> OrderResult:
        mt5 = self.conn.ensure()
        if result is None:
            err = mt5.last_error()
            log.error("%s failed — no result: %s", what, err)
            return OrderResult(False, message=f"{what} failed: {err}")
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            log.error("%s rejected: %s (%s)", what, result.retcode, result.comment)
            return OrderResult(
                False,
                message=f"{what} rejected [{result.retcode}]: {result.comment}",
                raw=result,
            )
        return OrderResult(
            True,
            ticket=int(getattr(result, "order", 0) or getattr(result, "deal", 0)),
            price=float(result.price),
            lots=float(getattr(result, "volume", lots)),
            message="filled",
            raw=result,
        )
