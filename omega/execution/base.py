"""Broker abstraction.

Backtest, paper and live trading all speak this interface, which is what makes
a backtest a faithful rehearsal: the engine never knows which one it is driving.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from ..core.types import (
    AccountState,
    CloseReason,
    OrderRequest,
    OrderResult,
    Position,
    SymbolSpec,
    Trade,
)


class Broker(ABC):
    """Minimum surface the engine needs to trade an account."""

    name: str = "broker"
    supports_partial_close: bool = True

    # -- lifecycle ----------------------------------------------------------
    def connect(self) -> None:
        return None

    def disconnect(self) -> None:
        return None

    # -- state ---------------------------------------------------------------
    @abstractmethod
    def account(self) -> AccountState: ...

    @abstractmethod
    def positions(self, symbol: Optional[str] = None) -> List[Position]: ...

    @abstractmethod
    def symbol_spec(self, symbol: str) -> SymbolSpec: ...

    @abstractmethod
    def closed_trades(self) -> List[Trade]: ...

    # -- trading --------------------------------------------------------------
    @abstractmethod
    def market_order(self, request: OrderRequest) -> OrderResult: ...

    @abstractmethod
    def modify_position(
        self,
        ticket: int,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
    ) -> bool: ...

    @abstractmethod
    def close_position(
        self,
        ticket: int,
        lots: Optional[float] = None,
        reason: CloseReason = CloseReason.MANUAL,
    ) -> OrderResult: ...

    # -- convenience -----------------------------------------------------------
    def close_all(self, reason: CloseReason = CloseReason.MANUAL) -> List[OrderResult]:
        return [self.close_position(p.ticket, reason=reason) for p in self.positions()]

    def position_by_ticket(self, ticket: int) -> Optional[Position]:
        for p in self.positions():
            if p.ticket == ticket:
                return p
        return None

    def __enter__(self) -> "Broker":
        self.connect()
        return self

    def __exit__(self, *exc: object) -> None:
        self.disconnect()
