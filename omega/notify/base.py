"""Notifier interface.

The trading loop must never block on, or be killed by, a notification. Every
implementation here therefore swallows its own errors and does its network I/O
off the trading thread. A failed Telegram call must cost you a message, never
a position.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

from ..core.types import Trade

if TYPE_CHECKING:  # pragma: no cover
    from ..config import AppConfig

log = logging.getLogger(__name__)


class Notifier:
    """Base class. Subclasses override :meth:`send` at minimum."""

    name = "notifier"
    enabled = False

    # -- lifecycle ------------------------------------------------------- #
    def start(self, trader=None) -> "Notifier":
        """Begin background work. ``trader`` enables remote commands."""
        return self

    def stop(self) -> None:
        return None

    # -- primitives ------------------------------------------------------ #
    def send(self, text: str, silent: bool = False) -> bool:
        raise NotImplementedError

    # -- semantic events -------------------------------------------------- #
    def event(self, kind: str, message: str) -> None:
        """Generic event from the trading loop (already human-readable)."""

    def trade_opened(self, symbol: str, side: str, lots: float, price: float,
                     stop: float, target: float, risk_pct: float,
                     score: float, confidence: float, regime: str,
                     reasons: Optional[list] = None) -> None:
        """A new position was opened."""

    def trade_closed(self, trade: Trade, balance: float) -> None:
        """A position was closed."""

    def halted(self, reason: str, snapshot: dict) -> None:
        """A risk guard stopped trading."""

    def error(self, message: str) -> None:
        """Something went wrong in the loop."""

    def daily_summary(self, snapshot: dict) -> None:
        """End-of-day roll-up."""

    def heartbeat(self, snapshot: dict) -> None:
        """Periodic 'still alive' ping."""


class NullNotifier(Notifier):
    """Does nothing. The default, so notifications are strictly opt-in."""

    name = "none"
    enabled = False

    def send(self, text: str, silent: bool = False) -> bool:
        return False


def build_notifier(cfg: "AppConfig") -> Notifier:
    """Construct the notifier described by ``cfg.notifications``."""
    nc = getattr(cfg, "notifications", None)
    if nc is None or not nc.enabled:
        return NullNotifier()

    provider = (nc.provider or "telegram").lower()
    if provider in ("none", "null", ""):
        return NullNotifier()
    if provider == "telegram":
        from .telegram import TelegramNotifier

        notifier = TelegramNotifier(nc.telegram, events=nc)
        if not notifier.configured:
            log.warning(
                "notifications.enabled is true but the Telegram bot token or "
                "chat id is missing — notifications are OFF. Set "
                "notifications.telegram.bot_token and .chat_id (or the "
                "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID environment variables)."
            )
            return NullNotifier()
        return notifier

    log.warning("Unknown notification provider %r — notifications are OFF", provider)
    return NullNotifier()
