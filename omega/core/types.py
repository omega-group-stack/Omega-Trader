"""Core domain types shared across the whole Omega-Trader stack.

Everything here is deliberately dependency-free (stdlib only) so that the data,
strategy, risk and execution layers can all import it without cycles.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #
class Side(str, Enum):
    """Direction of an order / position."""

    BUY = "BUY"
    SELL = "SELL"

    @property
    def sign(self) -> int:
        return 1 if self is Side.BUY else -1

    @property
    def opposite(self) -> "Side":
        return Side.SELL if self is Side.BUY else Side.BUY


class SignalDirection(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    FLAT = "FLAT"

    @property
    def sign(self) -> int:
        if self is SignalDirection.LONG:
            return 1
        if self is SignalDirection.SHORT:
            return -1
        return 0

    def to_side(self) -> Optional[Side]:
        if self is SignalDirection.LONG:
            return Side.BUY
        if self is SignalDirection.SHORT:
            return Side.SELL
        return None


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"


class Regime(str, Enum):
    """Market regime classification used to re-weight the signal ensemble."""

    TREND_UP = "TREND_UP"
    TREND_DOWN = "TREND_DOWN"
    RANGE = "RANGE"
    VOLATILE = "VOLATILE"
    QUIET = "QUIET"

    @property
    def is_trending(self) -> bool:
        return self in (Regime.TREND_UP, Regime.TREND_DOWN)


class CloseReason(str, Enum):
    STOP_LOSS = "STOP_LOSS"
    TAKE_PROFIT = "TAKE_PROFIT"
    TRAILING_STOP = "TRAILING_STOP"
    SIGNAL_FLIP = "SIGNAL_FLIP"
    SIGNAL_EXIT = "SIGNAL_EXIT"
    PARTIAL_TP = "PARTIAL_TP"
    TIME_STOP = "TIME_STOP"
    RISK_HALT = "RISK_HALT"
    SESSION_END = "SESSION_END"
    MANUAL = "MANUAL"
    END_OF_DATA = "END_OF_DATA"


# --------------------------------------------------------------------------- #
# Market data
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class Candle:
    """A single OHLCV bar. ``volume`` is tick-volume for most FX brokers."""

    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    spread: float = 0.0  # in points, as reported by the broker

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def body(self) -> float:
        return self.close - self.open

    @property
    def is_bull(self) -> bool:
        return self.close >= self.open


@dataclass(slots=True)
class Tick:
    time: datetime
    bid: float
    ask: float

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0

    @property
    def spread(self) -> float:
        return self.ask - self.bid


@dataclass(slots=True)
class SymbolSpec:
    """Broker contract specification — the source of truth for position sizing.

    For a 5-digit EURUSD on a 100k standard lot:
        digits=5, point=0.00001, tick_size=0.00001, tick_value=1.0,
        contract_size=100_000, pip=0.0001
    """

    symbol: str
    digits: int = 5
    point: float = 0.00001
    tick_size: float = 0.00001
    tick_value: float = 1.0          # account-currency P/L for 1 lot per tick_size move
    contract_size: float = 100_000.0
    min_lot: float = 0.01
    max_lot: float = 100.0
    lot_step: float = 0.01
    margin_rate: float = 0.0333      # fraction of notional required (1:30 leverage)
    commission_per_lot: float = 7.0  # round-turn, account currency
    swap_long: float = 0.0           # points per lot per night
    swap_short: float = 0.0
    base: str = ""
    quote: str = ""

    def __post_init__(self) -> None:
        if not self.base or not self.quote:
            raw = "".join(ch for ch in self.symbol.upper() if ch.isalpha())
            if len(raw) >= 6:
                self.base = self.base or raw[:3]
                self.quote = self.quote or raw[3:6]

    @property
    def pip(self) -> float:
        """One pip in price terms (10 points on 3/5-digit quotes)."""
        return self.point * 10 if self.digits in (3, 5) else self.point

    @property
    def is_jpy(self) -> bool:
        return self.quote == "JPY"

    def currencies(self) -> tuple[str, str]:
        return self.base, self.quote

    def normalize_price(self, price: float) -> float:
        return round(price, self.digits)

    def normalize_lots(self, lots: float) -> float:
        """Floor to the broker's lot step and clamp into [min_lot, max_lot]."""
        if lots <= 0 or not math.isfinite(lots):
            return 0.0
        step = self.lot_step or 0.01
        stepped = math.floor(round(lots / step, 8)) * step
        stepped = round(stepped, 8)
        if stepped < self.min_lot:
            return 0.0
        return round(min(stepped, self.max_lot), 8)

    def price_to_pips(self, price_distance: float) -> float:
        return price_distance / self.pip if self.pip else 0.0

    def pips_to_price(self, pips: float) -> float:
        return pips * self.pip

    def money_per_lot(self, price_distance: float) -> float:
        """Account-currency P/L for 1.0 lot over ``price_distance`` of price."""
        if self.tick_size <= 0:
            return 0.0
        return abs(price_distance) / self.tick_size * self.tick_value


# --------------------------------------------------------------------------- #
# Strategy output
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class ComponentScore:
    """One indicator family's vote, normalised to [-1, +1]."""

    name: str
    score: float
    weight: float
    detail: str = ""

    @property
    def contribution(self) -> float:
        return self.score * self.weight


@dataclass(slots=True)
class Signal:
    """The ensemble's decision for one bar."""

    time: datetime
    symbol: str
    direction: SignalDirection = SignalDirection.FLAT
    score: float = 0.0            # weighted ensemble score, [-1, +1]
    confidence: float = 0.0       # [0, 1] — agreement-adjusted strength
    regime: Regime = Regime.RANGE
    components: List[ComponentScore] = field(default_factory=list)
    atr: float = 0.0
    price: float = 0.0
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    reasons: List[str] = field(default_factory=list)
    vetoes: List[str] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_actionable(self) -> bool:
        return self.direction is not SignalDirection.FLAT and not self.vetoes

    def to_dict(self) -> Dict[str, Any]:
        return {
            "time": self.time.isoformat(),
            "symbol": self.symbol,
            "direction": self.direction.value,
            "score": round(self.score, 4),
            "confidence": round(self.confidence, 4),
            "regime": self.regime.value,
            "price": self.price,
            "atr": self.atr,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "components": [
                {
                    "name": c.name,
                    "score": round(c.score, 4),
                    "weight": round(c.weight, 4),
                    "contribution": round(c.contribution, 4),
                    "detail": c.detail,
                }
                for c in self.components
            ],
            "reasons": self.reasons,
            "vetoes": self.vetoes,
        }


# --------------------------------------------------------------------------- #
# Orders / positions / trades
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class OrderRequest:
    symbol: str
    side: Side
    lots: float
    order_type: OrderType = OrderType.MARKET
    price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    comment: str = "omega"
    magic: int = 920_112
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class OrderResult:
    ok: bool
    ticket: Optional[int] = None
    price: float = 0.0
    lots: float = 0.0
    message: str = ""
    raw: Any = None


@dataclass(slots=True)
class Position:
    ticket: int
    symbol: str
    side: Side
    lots: float
    entry_price: float
    open_time: datetime
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    initial_stop: Optional[float] = None
    initial_lots: float = 0.0
    commission: float = 0.0
    swap: float = 0.0
    comment: str = "omega"
    magic: int = 920_112
    # bookkeeping used by the risk/trade managers
    partials_done: int = 0
    breakeven_done: bool = False
    bars_held: int = 0
    max_favourable: float = 0.0   # best unrealised price excursion (price units)
    max_adverse: float = 0.0
    realized_pnl: float = 0.0     # from partial closes
    meta: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.initial_lots:
            self.initial_lots = self.lots
        if self.initial_stop is None:
            self.initial_stop = self.stop_loss

    @property
    def risk_distance(self) -> float:
        """Original stop distance in price units (1R)."""
        if self.initial_stop is None:
            return 0.0
        return abs(self.entry_price - self.initial_stop)

    def unrealized_price_move(self, price: float) -> float:
        return (price - self.entry_price) * self.side.sign

    def unrealized_pnl(self, price: float, spec: SymbolSpec) -> float:
        move = self.unrealized_price_move(price)
        return math.copysign(spec.money_per_lot(move) * self.lots, move) + self.swap

    def r_multiple(self, price: float) -> float:
        rd = self.risk_distance
        if rd <= 0:
            return 0.0
        return self.unrealized_price_move(price) / rd


@dataclass(slots=True)
class Trade:
    """A closed (or partially closed) position record."""

    ticket: int
    symbol: str
    side: Side
    lots: float
    entry_price: float
    exit_price: float
    open_time: datetime
    close_time: datetime
    pnl: float
    commission: float = 0.0
    swap: float = 0.0
    reason: CloseReason = CloseReason.MANUAL
    r_multiple: float = 0.0
    mae: float = 0.0          # max adverse excursion, price units
    mfe: float = 0.0          # max favourable excursion
    bars_held: int = 0
    entry_score: float = 0.0
    regime: str = ""
    balance_after: float = 0.0
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def net_pnl(self) -> float:
        return self.pnl

    @property
    def is_win(self) -> bool:
        return self.pnl > 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ticket": self.ticket,
            "symbol": self.symbol,
            "side": self.side.value,
            "lots": round(self.lots, 4),
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "open_time": self.open_time.isoformat(),
            "close_time": self.close_time.isoformat(),
            "pnl": round(self.pnl, 2),
            "commission": round(self.commission, 2),
            "swap": round(self.swap, 2),
            "reason": self.reason.value,
            "r_multiple": round(self.r_multiple, 3),
            "bars_held": self.bars_held,
            "entry_score": round(self.entry_score, 3),
            "regime": self.regime,
            "balance_after": round(self.balance_after, 2),
        }


@dataclass(slots=True)
class AccountState:
    balance: float
    equity: float
    margin: float = 0.0
    free_margin: float = 0.0
    currency: str = "USD"
    leverage: int = 30
    server_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def margin_level(self) -> float:
        return (self.equity / self.margin * 100.0) if self.margin > 0 else math.inf

    def to_dict(self) -> Dict[str, Any]:
        return {
            "balance": round(self.balance, 2),
            "equity": round(self.equity, 2),
            "margin": round(self.margin, 2),
            "free_margin": round(self.free_margin, 2),
            "margin_level": None if math.isinf(self.margin_level) else round(self.margin_level, 1),
            "currency": self.currency,
            "leverage": self.leverage,
            "server_time": self.server_time.isoformat(),
        }
