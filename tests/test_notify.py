"""Telegram notifier — delivery, safety and command handling.

No network: a fake ``_api`` records what *would* have been sent. The point of
these tests is that notifications can never take the trading loop down, and
that remote control cannot be driven by a stranger.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from omega.config import AppConfig, NotificationConfig, TelegramConfig
from omega.core.types import CloseReason, Side, Trade
from omega.notify import NullNotifier, build_notifier
from omega.notify.telegram import MAX_LEN, TelegramNotifier, _duration, _esc


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
class FakeTelegram(TelegramNotifier):
    """A notifier whose HTTP layer is a list."""

    def __init__(self, cfg=None, events=None, fail_times=0, raise_type=None):
        cfg = cfg or TelegramConfig(bot_token="T", chat_id="42",
                                    min_interval_seconds=0.0, max_retries=3)
        super().__init__(cfg, events=events or NotificationConfig())
        self.calls = []
        self.fail_times = fail_times
        self.raise_type = raise_type or RuntimeError

    def _api(self, method, payload, timeout=None):
        self.calls.append((method, payload))
        if self.fail_times > 0:
            self.fail_times -= 1
            raise self.raise_type("boom")
        return {"ok": True, "result": []}

    @property
    def messages(self):
        return [p["text"] for m, p in self.calls if m == "sendMessage"]


def make_trade(pnl=120.0, r=1.5, reason=CloseReason.TAKE_PROFIT):
    now = datetime(2024, 3, 1, 12, 0, tzinfo=timezone.utc)
    return Trade(
        ticket=7, symbol="EURUSD", side=Side.BUY, lots=0.4,
        entry_price=1.08500, exit_price=1.08900,
        open_time=now, close_time=now + timedelta(hours=3),
        pnl=pnl, commission=-2.8, swap=0.0, reason=reason, r_multiple=r,
    )


class FakeTrader:
    """Just enough LiveTrader surface for the command handler."""

    def __init__(self):
        self.cfg = AppConfig()
        self.actions = []
        self._risk = 1.0

    def snapshot(self):
        return {
            "running": True, "mode": "paper", "feed": "synthetic",
            "bars_processed": 120,
            "account": {"balance": 10250.0, "equity": 10310.0},
            "risk": {"risk_per_trade_pct": self._risk, "day_pnl_pct": 0.8,
                     "max_daily_loss_pct": 3.0, "drawdown_pct": 1.2,
                     "max_drawdown_pct": 15.0, "open_risk_pct": 1.0,
                     "trades_today": 2, "consecutive_losses": 0,
                     "halted": False, "halt_reason": ""},
            "performance": {"return_pct": 2.5, "profit_factor": 1.3,
                            "trades": 18, "win_rate_pct": 55.0,
                            "expectancy_r": 0.08, "max_drawdown_pct": 1.2},
            "positions": [{"symbol": "EURUSD", "side": "BUY", "lots": 0.3,
                           "entry_price": 1.085, "current_price": 1.0861,
                           "stop_loss": 1.0829, "take_profit": 1.0899,
                           "pnl": 33.0, "r_multiple": 0.5}],
            "trades": [make_trade().to_dict()],
            "signals": {"EURUSD": {"direction": "LONG", "score": 0.41,
                                   "confidence": 0.7, "regime": "trend_up",
                                   "components": [{"name": "trend", "score": 0.6,
                                                   "weight": 1.2}],
                                   "vetoes": ["session: closed"]}},
        }

    def halt(self, reason="x"): self.actions.append(("halt", reason))
    def resume(self): self.actions.append(("resume",))
    def stop(self): self.actions.append(("stop",))
    def flatten(self): self.actions.append(("flatten",)); return 2

    def set_risk_pct(self, pct):
        pct = max(0.01, min(float(pct), 10.0))
        self._risk = pct
        self.actions.append(("risk", pct))
        return pct


# --------------------------------------------------------------------------- #
# Construction / opt-in
# --------------------------------------------------------------------------- #
def test_notifications_are_off_by_default():
    assert build_notifier(AppConfig()).__class__ is NullNotifier


def test_enabled_without_credentials_degrades_to_null_not_a_crash():
    cfg = AppConfig()
    cfg.notifications.enabled = True
    cfg.notifications.telegram.bot_token = ""
    assert isinstance(build_notifier(cfg), NullNotifier)


def test_unknown_provider_degrades_to_null():
    cfg = AppConfig()
    cfg.notifications.enabled = True
    cfg.notifications.provider = "carrier-pigeon"
    assert isinstance(build_notifier(cfg), NullNotifier)


def test_credentials_can_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "env-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "999")
    cfg = AppConfig()
    cfg.notifications.enabled = True
    notifier = build_notifier(cfg)
    assert notifier.configured and notifier.chat_id == "999"


def test_null_notifier_accepts_every_call_silently():
    n = NullNotifier()
    n.trade_opened("EURUSD", "BUY", 0.1, 1.0, 0.9, 1.1, 1.0, 0.3, 0.6, "trend")
    n.trade_closed(make_trade(), 10_000)
    n.halted("x", {})
    n.error("x")
    n.daily_summary({})
    n.heartbeat({})
    assert n.send("anything") is False


# --------------------------------------------------------------------------- #
# Delivery
# --------------------------------------------------------------------------- #
def test_message_is_rendered_and_delivered():
    t = FakeTelegram()
    assert t.send_now("hello") is True
    method, payload = t.calls[0]
    assert method == "sendMessage"
    assert payload["chat_id"] == "42"
    assert payload["text"] == "hello"
    assert payload["parse_mode"] == "HTML"


def test_transient_failures_are_retried_then_succeed():
    t = FakeTelegram(fail_times=2)
    assert t.send_now("hi") is True
    assert len(t.calls) == 3          # two failures, one success
    assert t.failures == 0


def test_permanent_failure_gives_up_and_reports():
    t = FakeTelegram(fail_times=99)
    t.cfg.max_retries = 2
    assert t.send_now("hi") is False
    assert t.failures == 1


def test_a_full_queue_drops_messages_instead_of_blocking():
    t = FakeTelegram(TelegramConfig(bot_token="T", chat_id="42", queue_size=2))
    assert t.send("one") and t.send("two")
    assert t.send("three") is False    # dropped, not blocked
    assert t.dropped == 1


def test_oversized_messages_are_truncated_not_rejected():
    t = FakeTelegram(TelegramConfig(bot_token="T", chat_id="42", queue_size=5))
    t.send("x" * (MAX_LEN * 2))
    text, _ = t._queue.get_nowait()
    assert len(text) <= MAX_LEN
    assert text.endswith("(truncated)")


def test_sender_thread_drains_the_queue_without_the_caller_waiting():
    t = FakeTelegram(TelegramConfig(bot_token="T", chat_id="42",
                                    min_interval_seconds=0.0))
    t.start(trader=None)
    try:
        for i in range(5):
            t.send(f"m{i}")
        deadline = time.time() + 5
        while t.sent < 5 and time.time() < deadline:
            time.sleep(0.02)
        assert t.sent == 5
    finally:
        t.stop()


def test_send_from_a_trading_thread_returns_immediately():
    """The whole design rests on this: queueing must not do network I/O."""
    t = FakeTelegram(TelegramConfig(bot_token="T", chat_id="42", queue_size=50))
    start = time.perf_counter()
    for _ in range(50):
        t.send("tick")
    assert time.perf_counter() - start < 0.1
    assert not t.calls          # nothing sent yet — the sender thread is off


# --------------------------------------------------------------------------- #
# Event rendering
# --------------------------------------------------------------------------- #
def test_entry_notification_contains_the_numbers_that_matter():
    t = FakeTelegram()
    t.enabled = True
    t.send = lambda text, silent=False: t.calls.append(("sendMessage", {"text": text}))
    t.trade_opened("EURUSD", "BUY", 0.45, 1.08500, 1.08300, 1.08860,
                   risk_pct=0.98, score=0.52, confidence=0.71, regime="trend_up",
                   reasons=["score +0.520 clears ±0.28"])
    text = t.messages[0]
    assert "LONG" in text and "EURUSD" in text
    assert "1.08500" in text and "1.08300" in text and "0.45" in text
    assert "0.98%" in text and "+0.52" in text and "71%" in text


def test_exit_notification_reports_pnl_and_r_multiple():
    t = FakeTelegram()
    t.send = lambda text, silent=False: t.calls.append(("sendMessage", {"text": text}))
    t.trade_closed(make_trade(pnl=-80.0, r=-1.0, reason=CloseReason.STOP_LOSS), 9920.0)
    text = t.messages[0]
    assert "❌" in text and "-80.00" in text and "-1.00R" in text
    assert "STOP_LOSS".lower() in text.lower()
    assert "9,920.00" in text


def test_losing_trades_under_one_r_are_sent_silently():
    """Don't buzz someone's phone at 3am for a routine -0.4R scratch."""
    sent = []
    t = FakeTelegram()
    t.send = lambda text, silent=False: sent.append(silent)
    t.trade_closed(make_trade(pnl=-30.0, r=-0.4, reason=CloseReason.SIGNAL_EXIT), 9970)
    t.trade_closed(make_trade(pnl=-150.0, r=-1.6, reason=CloseReason.STOP_LOSS), 9820)
    t.trade_closed(make_trade(pnl=200.0, r=2.1), 10020)
    assert sent == [True, False, False]


@pytest.mark.parametrize("flag,method,args", [
    ("on_entry", "trade_opened", ("EURUSD", "BUY", 0.1, 1.0, 0.99, 1.02, 1.0, 0.3, 0.6, "r")),
    ("on_exit", "trade_closed", (make_trade(), 10_000.0)),
    ("on_halt", "halted", ("limit hit", {})),
    ("on_error", "error", ("kaboom",)),
])
def test_each_event_type_can_be_switched_off(flag, method, args):
    events = NotificationConfig(**{flag: False})
    t = FakeTelegram(events=events)
    sent = []
    t.send = lambda text, silent=False: sent.append(text)
    getattr(t, method)(*args)
    assert sent == []


def test_halt_notification_tells_you_how_to_recover():
    t = FakeTelegram()
    t.send = lambda text, silent=False: t.calls.append(("sendMessage", {"text": text}))
    t.halted("max drawdown 15.1% >= 15.0%", FakeTrader().snapshot())
    text = t.messages[0]
    assert "HALTED" in text and "/resume" in text


def test_daily_summary_fires_once_per_day_after_the_configured_hour():
    events = NotificationConfig(daily_summary_hour=0)   # always "past" the hour
    t = FakeTelegram(events=events)
    sent = []
    t.send = lambda text, silent=False: sent.append(text)
    snap = FakeTrader().snapshot()
    t.daily_summary(snap)
    t.daily_summary(snap)
    t.daily_summary(snap)
    assert len(sent) == 1


def test_daily_summary_waits_for_its_hour():
    events = NotificationConfig(daily_summary_hour=25)  # unreachable
    t = FakeTelegram(events=events)
    sent = []
    t.send = lambda text, silent=False: sent.append(text)
    t.daily_summary(FakeTrader().snapshot())
    assert sent == []


def test_heartbeat_is_rate_limited():
    events = NotificationConfig(heartbeat_minutes=60)
    t = FakeTelegram(events=events)
    sent = []
    t.send = lambda text, silent=False: sent.append(text)
    snap = FakeTrader().snapshot()
    t.heartbeat(snap)
    t.heartbeat(snap)
    assert len(sent) == 1


def test_heartbeat_off_by_default():
    t = FakeTelegram(events=NotificationConfig())
    sent = []
    t.send = lambda text, silent=False: sent.append(text)
    t.heartbeat(FakeTrader().snapshot())
    assert sent == []


# --------------------------------------------------------------------------- #
# Commands & security
# --------------------------------------------------------------------------- #
def test_commands_from_another_chat_are_ignored():
    """Someone who finds your bot must not be able to flatten your book."""
    t = FakeTelegram()
    trader = FakeTrader()
    t._trader = trader
    sent = []
    t.send = lambda text, silent=False: sent.append(text)
    t._handle_update({"update_id": 1, "message": {
        "chat": {"id": 666}, "text": "/flatten"}})
    assert trader.actions == []
    assert sent == []


def test_commands_from_the_configured_chat_are_executed():
    t = FakeTelegram()
    trader = FakeTrader()
    t._trader = trader
    sent = []
    t.send = lambda text, silent=False: sent.append(text)
    t._handle_update({"update_id": 1, "message": {
        "chat": {"id": 42}, "text": "/halt"}})
    assert trader.actions[0][0] == "halt"
    assert "Halted" in sent[0]


@pytest.mark.parametrize("command,expected", [
    ("/status", "Omega-Trader"),
    ("/positions", "EURUSD"),
    ("/signal", "LONG"),
    ("/trades", "EURUSD"),
    ("/help", "/flatten"),
])
def test_read_only_commands_render(command, expected):
    t = FakeTelegram()
    t._trader = FakeTrader()
    assert expected in t._run_command(command.lstrip("/"), "")


def test_risk_command_changes_the_risk_percentage():
    t = FakeTelegram()
    trader = FakeTrader()
    t._trader = trader
    reply = t._run_command("risk", "0.35")
    assert trader._risk == 0.35 and "0.35%" in reply


def test_risk_command_is_clamped_to_a_sane_band():
    t = FakeTelegram()
    trader = FakeTrader()
    t._trader = trader
    t._run_command("risk", "500")
    assert trader._risk == 10.0


def test_risk_command_without_an_argument_reports_the_current_value():
    t = FakeTelegram()
    t._trader = FakeTrader()
    assert "%" in t._run_command("risk", "")


def test_risk_command_rejects_nonsense():
    t = FakeTelegram()
    trader = FakeTrader()
    t._trader = trader
    assert "number" in t._run_command("risk", "banana")
    assert trader.actions == []


def test_flatten_can_be_disabled_in_config():
    t = FakeTelegram(TelegramConfig(bot_token="T", chat_id="42",
                                    allow_flatten=False))
    trader = FakeTrader()
    t._trader = trader
    reply = t._run_command("flatten", "")
    assert "Refusing" in reply and trader.actions == []


def test_unknown_command_is_answered_not_crashed():
    t = FakeTelegram()
    t._trader = FakeTrader()
    assert "Unknown command" in t._run_command("launch_missiles", "")


def test_group_style_command_suffix_is_stripped():
    """In groups Telegram sends '/status@MyBot'."""
    t = FakeTelegram()
    t._trader = FakeTrader()
    sent = []
    t.send = lambda text, silent=False: sent.append(text)
    t._handle_update({"update_id": 1, "message": {
        "chat": {"id": 42}, "text": "/status@OmegaBot"}})
    assert "Omega-Trader" in sent[0]


def test_a_crashing_command_replies_instead_of_killing_the_poller():
    t = FakeTelegram()

    class Exploding(FakeTrader):
        def snapshot(self):
            raise ValueError("database on fire")

    t._trader = Exploding()
    sent = []
    t.send = lambda text, silent=False: sent.append(text)
    t._handle_update({"update_id": 1, "message": {
        "chat": {"id": 42}, "text": "/status"}})
    assert "database on fire" in sent[0]


def test_empty_and_non_text_updates_are_ignored():
    t = FakeTelegram()
    t._trader = FakeTrader()
    sent = []
    t.send = lambda text, silent=False: sent.append(text)
    t._handle_update({"update_id": 1, "message": {"chat": {"id": 42}}})
    t._handle_update({"update_id": 2})
    assert sent == []


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def test_html_special_characters_are_escaped():
    assert _esc("a<b>&c") == "a&lt;b&gt;&amp;c"


@pytest.mark.parametrize("minutes,expected", [
    (12, "12m"), (90, "1.5h"), (60 * 36, "1.5d"),
])
def test_duration_formatting(minutes, expected):
    assert _duration(minutes) == expected


# --------------------------------------------------------------------------- #
# Integration with the trading loop
# --------------------------------------------------------------------------- #
def test_live_trader_never_dies_because_a_notifier_throws():
    from omega.engine.live import LiveTrader

    class Hostile(NullNotifier):
        def start(self, trader=None):
            return self

        def trade_opened(self, *a, **k):
            raise RuntimeError("telegram is down")

        def trade_closed(self, *a, **k):
            raise RuntimeError("telegram is down")

        def event(self, *a, **k):
            raise RuntimeError("telegram is down")

    cfg = AppConfig()
    cfg.data.synthetic_bars = 1500
    cfg.strategy.warmup_bars = 300
    trader = LiveTrader(cfg, notifier=Hostile())
    trader.setup()
    # A notification failure must not propagate out of the periodic pass.
    trader._notify_periodic()
    assert trader.error == ""


def test_live_trader_pushes_entries_and_exits_to_the_notifier():
    from omega.engine.live import LiveTrader

    class Recorder(NullNotifier):
        def __init__(self):
            self.opened, self.closed = [], []

        def start(self, trader=None):
            return self

        def trade_opened(self, **kw):
            self.opened.append(kw)

        def trade_closed(self, trade, balance):
            self.closed.append((trade, balance))

    cfg = AppConfig()
    cfg.data.synthetic_bars = 1500
    cfg.strategy.warmup_bars = 300
    recorder = Recorder()
    trader = LiveTrader(cfg, notifier=recorder).setup()
    trader._notify_closed(make_trade())
    assert len(recorder.closed) == 1
    # Same ticket twice must not double-post.
    trader._notify_closed(make_trade())
    assert len(recorder.closed) == 1
