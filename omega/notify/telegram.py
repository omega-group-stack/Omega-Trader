"""Telegram notifications and remote control.

Two independent background threads, both daemons, neither of which can block
or crash the trading loop:

``sender``
    Drains an outbound queue. Trading-thread calls are a queue ``put`` and
    return immediately, so a hanging Telegram API call costs a few hundred
    bytes of RAM rather than a missed bar. Failed sends are retried with
    exponential backoff and the queue is bounded, so a long outage degrades
    into dropped messages instead of unbounded memory growth.

``poller``
    Long-polls ``getUpdates`` for commands (``/status``, ``/halt``, ...).
    Only enabled when ``allow_commands`` is true, and it ignores every chat
    except the configured one — otherwise anyone who guesses your bot's
    name could flatten your book.

Implemented with ``urllib`` from the standard library on purpose: a trading
bot that must stay up for weeks should not grow a dependency tree for the
sake of four HTTP calls.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from ..core.types import Trade
from .base import Notifier

log = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/{method}"

#: Telegram hard-limits message bodies; leave room for the trailing newline.
MAX_LEN = 4000


class TelegramNotifier(Notifier):
    """Push notifications to a Telegram chat, optionally accepting commands."""

    name = "telegram"

    def __init__(self, cfg, events=None) -> None:
        self.cfg = cfg
        self.events = events
        self.token = (cfg.bot_token or os.environ.get("TELEGRAM_BOT_TOKEN", "")).strip()
        self.chat_id = str(cfg.chat_id or os.environ.get("TELEGRAM_CHAT_ID", "")).strip()
        self.enabled = bool(self.token and self.chat_id)

        self._queue: "queue.Queue[Optional[tuple]]" = queue.Queue(maxsize=cfg.queue_size)
        self._sender: Optional[threading.Thread] = None
        self._poller: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._trader = None
        self._offset = 0
        self._last_summary: Optional[date] = None
        self._last_heartbeat = 0.0
        self.sent = 0
        self.dropped = 0
        self.failures = 0

    @property
    def configured(self) -> bool:
        return self.enabled

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    def start(self, trader=None) -> "TelegramNotifier":
        if not self.enabled:
            return self
        self._trader = trader
        self._stop.clear()

        self._sender = threading.Thread(target=self._sender_loop,
                                        name="omega-telegram-send", daemon=True)
        self._sender.start()

        if self.cfg.allow_commands and trader is not None:
            # Skip whatever piled up while the bot was down, otherwise a
            # /flatten sent yesterday executes at startup today.
            self._drain_pending_updates()
            self._poller = threading.Thread(target=self._poll_loop,
                                            name="omega-telegram-poll", daemon=True)
            self._poller.start()
            log.info("Telegram remote control enabled for chat %s", self.chat_id)
        else:
            log.info("Telegram notifications enabled for chat %s "
                     "(commands disabled)", self.chat_id)
        return self

    def stop(self) -> None:
        self._stop.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:  # pragma: no cover
            pass
        for thread in (self._sender, self._poller):
            if thread and thread.is_alive():
                thread.join(timeout=5.0)

    # ------------------------------------------------------------------ #
    # Outbound
    # ------------------------------------------------------------------ #
    def send(self, text: str, silent: bool = False) -> bool:
        """Queue a message. Returns False if the queue was full (dropped)."""
        if not self.enabled:
            return False
        if len(text) > MAX_LEN:
            text = text[: MAX_LEN - 20] + "\n… (truncated)"
        try:
            self._queue.put_nowait((text, silent))
            return True
        except queue.Full:
            self.dropped += 1
            log.warning("Telegram queue full — dropped a message (%d total)",
                        self.dropped)
            return False

    def send_now(self, text: str, silent: bool = False) -> bool:
        """Blocking send. Used by the CLI test command, never by the loop."""
        return self._deliver(text, silent)

    def _sender_loop(self) -> None:
        while not self._stop.is_set():
            try:
                item = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if item is None:
                break
            text, silent = item
            self._deliver(text, silent)
            # Telegram throttles aggressively per chat; a small floor keeps a
            # burst of exits from tripping 429s.
            time.sleep(max(0.0, self.cfg.min_interval_seconds))

    def _deliver(self, text: str, silent: bool = False) -> bool:
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "disable_notification": silent,
        }
        delay = 1.0
        for attempt in range(self.cfg.max_retries):
            try:
                self._api("sendMessage", payload)
                self.sent += 1
                return True
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", "ignore")[:300]
                if exc.code == 429:
                    retry_after = _retry_after(body) or delay
                    log.warning("Telegram rate limited; sleeping %.0fs", retry_after)
                    if self._stop.wait(retry_after):
                        return False
                    continue
                if 400 <= exc.code < 500:
                    # Bad token, wrong chat id, malformed HTML — retrying will
                    # not help and would spam the log forever.
                    self.failures += 1
                    log.error("Telegram rejected the message (%d): %s", exc.code, body)
                    return False
                log.warning("Telegram %d on attempt %d: %s", exc.code, attempt + 1, body)
            except Exception as exc:
                log.warning("Telegram send failed (attempt %d/%d): %s",
                            attempt + 1, self.cfg.max_retries, exc)
            if self._stop.wait(delay):
                return False
            delay = min(delay * 2, 30.0)
        self.failures += 1
        return False

    def _api(self, method: str, payload: Dict[str, Any], timeout: Optional[float] = None) -> dict:
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            API.format(token=self.token, method=method),
            data=data,
            headers={"Content-Type": "application/json",
                     "User-Agent": "Omega-Trader/1.0"},
        )
        with urllib.request.urlopen(
            request, timeout=timeout or self.cfg.timeout_seconds
        ) as response:
            return json.loads(response.read().decode("utf-8"))

    # ------------------------------------------------------------------ #
    # Semantic events
    # ------------------------------------------------------------------ #
    def _wants(self, key: str) -> bool:
        return bool(getattr(self.events, f"on_{key}", True)) if self.events else True

    def trade_opened(self, symbol: str, side: str, lots: float, price: float,
                     stop: float, target: float, risk_pct: float,
                     score: float, confidence: float, regime: str,
                     reasons: Optional[List[str]] = None) -> None:
        if not self._wants("entry"):
            return
        arrow = "🟢 LONG" if side.upper() in ("BUY", "LONG") else "🔴 SHORT"
        risk_pips = abs(price - stop)
        rr = abs(target - price) / risk_pips if risk_pips > 0 and target else 0.0
        lines = [
            f"<b>{arrow} {_esc(symbol)}</b>",
            f"entry <code>{price:.5f}</code>   size <code>{lots:.2f}</code> lots",
            f"stop <code>{stop:.5f}</code>   target <code>{target:.5f}</code>"
            + (f"   ({rr:.1f}R)" if rr else ""),
            f"risk <b>{risk_pct:.2f}%</b> of equity",
            f"score <b>{score:+.2f}</b>   confidence {confidence:.0%}   "
            f"regime <i>{_esc(regime)}</i>",
        ]
        if reasons:
            lines.append("")
            lines += [f"• {_esc(r)}" for r in reasons[:5]]
        self.send("\n".join(lines))

    def trade_closed(self, trade: Trade, balance: float) -> None:
        if not self._wants("exit"):
            return
        won = trade.pnl > 0
        mark = "✅" if won else ("➖" if abs(trade.pnl) < 1e-9 else "❌")
        held = ""
        try:
            minutes = (trade.close_time - trade.open_time).total_seconds() / 60
            held = f"   held {_duration(minutes)}"
        except Exception:  # pragma: no cover
            pass
        self.send(
            f"<b>{mark} CLOSED {_esc(trade.symbol)} {_esc(trade.side.value)}</b>\n"
            f"{_esc(trade.entry_price)} → {_esc(trade.exit_price)}   "
            f"{trade.lots:.2f} lots{held}\n"
            f"P&amp;L <b>{trade.pnl:+,.2f}</b>  ({trade.r_multiple:+.2f}R)   "
            f"reason <i>{_esc(trade.reason.value)}</i>\n"
            f"balance <b>{balance:,.2f}</b>",
            silent=not won and abs(trade.r_multiple) < 1.0,
        )

    def halted(self, reason: str, snapshot: dict) -> None:
        if not self._wants("halt"):
            return
        risk = snapshot.get("risk", {})
        self.send(
            f"🛑 <b>TRADING HALTED</b>\n{_esc(reason)}\n\n"
            f"equity <b>{snapshot.get('account', {}).get('equity', 0):,.2f}</b>   "
            f"day {risk.get('day_pnl_pct', 0):+.2f}%   "
            f"drawdown {risk.get('drawdown_pct', 0):.2f}%\n"
            f"open positions: {len(snapshot.get('positions', []))}\n\n"
            f"Send /resume to clear the guards."
        )

    def error(self, message: str) -> None:
        if not self._wants("error"):
            return
        self.send(f"⚠️ <b>Error</b>\n<code>{_esc(message)[:600]}</code>")

    def event(self, kind: str, message: str) -> None:
        if kind in ("start", "stop") and self._wants("lifecycle"):
            icon = "▶️" if kind == "start" else "⏹"
            self.send(f"{icon} <b>Omega-Trader {_esc(kind)}</b> — {_esc(message)}")

    def heartbeat(self, snapshot: dict) -> None:
        interval = getattr(self.events, "heartbeat_minutes", 0) if self.events else 0
        if not interval:
            return
        now = time.time()
        if now - self._last_heartbeat < interval * 60:
            return
        self._last_heartbeat = now
        self.send(self._status_text(snapshot), silent=True)

    def daily_summary(self, snapshot: dict) -> None:
        if not self._wants("daily_summary"):
            return
        today = datetime.now(timezone.utc).date()
        if self._last_summary == today:
            return
        hour = getattr(self.events, "daily_summary_hour", 21) if self.events else 21
        if datetime.now(timezone.utc).hour < hour:
            return
        self._last_summary = today

        perf = snapshot.get("performance", {})
        risk = snapshot.get("risk", {})
        account = snapshot.get("account", {})
        self.send(
            f"📊 <b>Daily summary — {today}</b>\n"
            f"equity <b>{account.get('equity', 0):,.2f}</b>   "
            f"day {risk.get('day_pnl_pct', 0):+.2f}%\n"
            f"trades today: {risk.get('trades_today', 0)}   "
            f"open: {len(snapshot.get('positions', []))}\n"
            f"since start: {perf.get('return_pct', 0):+.2f}%   "
            f"PF {perf.get('profit_factor', 0):.2f}   "
            f"maxDD {perf.get('max_drawdown_pct', 0):.2f}%\n"
            f"{perf.get('trades', 0)} trades   "
            f"win {perf.get('win_rate_pct', 0):.1f}%   "
            f"expectancy {perf.get('expectancy_r', 0):+.3f}R"
        )

    # ------------------------------------------------------------------ #
    # Inbound commands
    # ------------------------------------------------------------------ #
    def _drain_pending_updates(self) -> None:
        try:
            data = self._api("getUpdates", {"timeout": 0, "offset": -1}, timeout=15)
            results = data.get("result") or []
            if results:
                self._offset = results[-1]["update_id"] + 1
        except Exception as exc:
            log.debug("could not drain pending updates: %s", exc)

    def _poll_loop(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                data = self._api(
                    "getUpdates",
                    {"timeout": int(self.cfg.poll_timeout_seconds),
                     "offset": self._offset,
                     "allowed_updates": ["message"]},
                    timeout=self.cfg.poll_timeout_seconds + 15,
                )
                backoff = 1.0
                for update in data.get("result", []):
                    self._offset = update["update_id"] + 1
                    self._handle_update(update)
            except Exception as exc:
                if self._stop.is_set():
                    break
                log.warning("Telegram poll failed: %s (retry in %.0fs)", exc, backoff)
                if self._stop.wait(backoff):
                    break
                backoff = min(backoff * 2, 60.0)

    def _handle_update(self, update: dict) -> None:
        message = update.get("message") or {}
        chat = str((message.get("chat") or {}).get("id", ""))
        text = (message.get("text") or "").strip()
        if not text:
            return
        if chat != self.chat_id:
            log.warning("Ignoring Telegram command from unauthorised chat %s", chat)
            return

        command, _, argument = text.partition(" ")
        command = command.split("@")[0].lstrip("/").lower()
        try:
            self.send(self._run_command(command, argument.strip()))
        except Exception as exc:  # pragma: no cover - defensive
            log.exception("command %r failed", command)
            self.send(f"⚠️ <code>{_esc(exc)}</code>")

    def _run_command(self, command: str, argument: str) -> str:
        trader = self._trader
        if trader is None:
            return "Bot is not attached to a trader."

        if command in ("start", "help"):
            return (
                "<b>Omega-Trader</b>\n"
                "/status — equity, risk, open positions\n"
                "/positions — open positions in detail\n"
                "/signal — current ensemble read per symbol\n"
                "/trades — last 5 closed trades\n"
                "/risk &lt;pct&gt; — change risk per trade\n"
                "/halt — stop opening new trades\n"
                "/resume — clear the risk guards\n"
                "/flatten — close everything now\n"
                "/stop — stop the trading loop"
            )
        if command == "status":
            return self._status_text(trader.snapshot())
        if command == "positions":
            return self._positions_text(trader.snapshot())
        if command == "signal":
            return self._signal_text(trader.snapshot())
        if command == "trades":
            return self._trades_text(trader.snapshot())
        if command == "halt":
            trader.halt("halted from Telegram")
            return "🛑 Halted. No new entries until /resume."
        if command == "resume":
            trader.resume()
            return "▶️ Risk guards cleared — trading re-enabled."
        if command == "flatten":
            if not self.cfg.allow_flatten:
                return ("Refusing: notifications.telegram.allow_flatten is false.")
            closed = trader.flatten()
            return f"Closed {closed} position(s)."
        if command == "stop":
            trader.stop()
            return "⏹ Trading loop stopped."
        if command == "risk":
            if not argument:
                return (f"Risk per trade is "
                        f"<b>{trader.cfg.risk.risk_per_trade_pct:.2f}%</b>. "
                        f"Send <code>/risk 0.5</code> to change it.")
            try:
                value = float(argument.replace("%", "").strip())
            except ValueError:
                return f"Could not read {_esc(argument)!r} as a number."
            applied = trader.set_risk_pct(value)
            return f"Risk per trade set to <b>{applied:.2f}%</b>."
        return f"Unknown command /{_esc(command)}. Send /help."

    # ------------------------------------------------------------------ #
    # Renderers
    # ------------------------------------------------------------------ #
    def _status_text(self, snap: dict) -> str:
        account = snap.get("account", {})
        risk = snap.get("risk", {})
        perf = snap.get("performance", {})
        state = "🟢 running" if snap.get("running") else "⏸ stopped"
        if risk.get("halted"):
            state = f"🛑 halted — {_esc(risk.get('halt_reason', ''))}"
        return (
            f"<b>Omega-Trader</b> {state}\n"
            f"mode <code>{_esc(snap.get('mode'))}</code>   "
            f"feed <code>{_esc(snap.get('feed'))}</code>   "
            f"bars {snap.get('bars_processed', 0)}\n\n"
            f"balance <b>{account.get('balance', 0):,.2f}</b>   "
            f"equity <b>{account.get('equity', 0):,.2f}</b>\n"
            f"day {risk.get('day_pnl_pct', 0):+.2f}% "
            f"(limit {risk.get('max_daily_loss_pct', 0):.1f}%)   "
            f"DD {risk.get('drawdown_pct', 0):.2f}% "
            f"(limit {risk.get('max_drawdown_pct', 0):.1f}%)\n"
            f"risk/trade <b>{risk.get('risk_per_trade_pct', 0):.2f}%</b>   "
            f"open risk {risk.get('open_risk_pct', 0):.2f}%\n"
            f"positions {len(snap.get('positions', []))}   "
            f"trades today {risk.get('trades_today', 0)}   "
            f"streak {risk.get('consecutive_losses', 0)}L\n\n"
            f"since start <b>{perf.get('return_pct', 0):+.2f}%</b>   "
            f"PF {perf.get('profit_factor', 0):.2f}   "
            f"{perf.get('trades', 0)} trades"
        )

    def _positions_text(self, snap: dict) -> str:
        positions = snap.get("positions", [])
        if not positions:
            return "No open positions."
        lines = [f"<b>{len(positions)} open position(s)</b>"]
        for p in positions:
            lines.append(
                f"\n<b>{_esc(p.get('symbol'))} {_esc(p.get('side'))}</b> "
                f"{p.get('lots', 0):.2f} lots\n"
                f"  entry <code>{p.get('entry_price')}</code>  "
                f"now <code>{p.get('current_price')}</code>\n"
                f"  SL <code>{p.get('stop_loss')}</code>  "
                f"TP <code>{p.get('take_profit')}</code>\n"
                f"  P&amp;L <b>{p.get('pnl', 0):+,.2f}</b> "
                f"({p.get('r_multiple', 0):+.2f}R)"
            )
        return "".join(lines)

    def _signal_text(self, snap: dict) -> str:
        signals = snap.get("signals", {})
        if not signals:
            return "No signals yet — the strategy is still warming up."
        lines = []
        for symbol, s in signals.items():
            lines.append(
                f"<b>{_esc(symbol)}</b> → <b>{_esc(s.get('direction'))}</b>  "
                f"score {s.get('score', 0):+.3f}  "
                f"conf {s.get('confidence', 0):.0%}  "
                f"<i>{_esc(s.get('regime'))}</i>"
            )
            for component in s.get("components", []):
                lines.append(
                    f"   {_esc(component.get('name')):<11} "
                    f"{component.get('score', 0):+.2f} "
                    f"×{component.get('weight', 0):.2f}"
                )
            for veto in (s.get("vetoes") or [])[:4]:
                lines.append(f"   ⛔ {_esc(veto)}")
        return "\n".join(lines)

    def _trades_text(self, snap: dict) -> str:
        trades = snap.get("trades", [])[-5:]
        if not trades:
            return "No closed trades yet."
        lines = ["<b>Last closed trades</b>"]
        for t in reversed(trades):
            mark = "✅" if t.get("pnl", 0) > 0 else "❌"
            lines.append(
                f"{mark} {_esc(t.get('symbol'))} {_esc(t.get('side'))} "
                f"{t.get('pnl', 0):+,.2f} ({t.get('r_multiple', 0):+.2f}R) "
                f"<i>{_esc(t.get('reason'))}</i>"
            )
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _esc(value: Any) -> str:
    """Escape the three characters Telegram's HTML parse mode cares about."""
    return (str(value).replace("&", "&amp;")
            .replace("<", "&lt;").replace(">", "&gt;"))


def _duration(minutes: float) -> str:
    if minutes < 60:
        return f"{minutes:.0f}m"
    if minutes < 60 * 24:
        return f"{minutes / 60:.1f}h"
    return f"{minutes / 1440:.1f}d"


def _retry_after(body: str) -> Optional[float]:
    try:
        return float(json.loads(body).get("parameters", {}).get("retry_after", 0)) or None
    except Exception:
        return None
