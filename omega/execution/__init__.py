"""Order routing: simulated (backtest/paper) and live MetaTrader 5."""

from __future__ import annotations

from typing import Dict, Optional

from ..config import AppConfig
from ..core.types import SymbolSpec
from .base import Broker
from .simulated import SimulatedBroker

__all__ = ["Broker", "SimulatedBroker", "build_broker"]


def build_broker(
    cfg: AppConfig, specs: Optional[Dict[str, SymbolSpec]] = None
) -> Broker:
    """Create the broker named by ``cfg.execution.mode``."""
    mode = (cfg.execution.mode or "paper").lower()
    if mode == "live":
        if not cfg.execution.confirm_live:
            raise RuntimeError(
                "Refusing to trade live: set execution.confirm_live=true in your "
                "config (and make sure you have paper-traded the same settings first)."
            )
        from .mt5_broker import MT5Broker  # lazy: Windows-only dependency

        return MT5Broker(cfg.mt5)
    if mode in ("paper", "backtest"):
        return SimulatedBroker(cfg.execution, cfg.account, specs)
    raise ValueError(
        f"Unknown execution.mode={cfg.execution.mode!r} (backtest | paper | live)"
    )
