"""Live / paper trading runner.

Polls the feed, detects *closed* bars and drives the exact same
:class:`~omega.engine.core.TradingCore` the backtester uses. The only
difference between paper and live is which broker is plugged in.

The runner is thread-safe enough to be driven by the dashboard: call
:meth:`start` to run it in a background thread, :meth:`stop` to wind it down,
and :meth:`snapshot` from anywhere to read its state.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Deque, Dict, List, Optional

import pandas as pd

from ..config import AppConfig
from ..core.types import AccountState, Candle, CloseReason, SymbolSpec, Trade
from ..data import DataFeed, build_feed
from ..data.base import resample
from ..data.timeframes import delta as tf_delta
from ..execution import build_broker
from ..execution.simulated import SimulatedBroker
from ..risk import RiskManager
from ..strategy import EnsembleStrategy
from .core import BarOutcome, TradingCore
from .metrics import analyse

log = logging.getLogger(__name__)


class LiveTrader:
    """Runs Omega against a live (or simulated-live) market."""

    def __init__(
        self,
        cfg: AppConfig,
        feed: Optional[DataFeed] = None,
        broker=None,
    ) -> None:
        self.cfg = cfg
        self.feed = feed or build_feed(cfg)
        self.symbols = [s.name.upper() for s in cfg.active_symbols]
        self.specs: Dict[str, SymbolSpec] = {}
        self.strategies: Dict[str, EnsembleStrategy] = {}
        self.data: Dict[str, pd.DataFrame] = {}
        self._last_bar: Dict[str, datetime] = {}

        self.broker = broker
        self.risk: Optional[RiskManager] = None
        self.core: Optional[TradingCore] = None

        self.events: Deque[dict] = deque(maxlen=500)
        self.started_at: Optional[datetime] = None
        self.last_tick: Optional[datetime] = None
        self.bars_processed = 0
        self.error: str = ""

        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self.running = False

    # ------------------------------------------------------------------ #
    # Setup
    # ------------------------------------------------------------------ #
    def setup(self) -> "LiveTrader":
        cfg = self.cfg
        self.feed.connect()

        for sym in self.symbols:
            spec = self.feed.symbol_spec(sym)
            if spec is None:
                from ..data.synthetic import default_spec

                spec = default_spec(sym)
            self._apply_overrides(spec, sym)
            self.specs[sym] = spec

        if self.broker is None:
            self.broker = build_broker(cfg, self.specs)
        self.broker.connect()
        if isinstance(self.broker, SimulatedBroker):
            for spec in self.specs.values():
                self.broker.register_spec(spec)

        account = self.broker.account()
        self.risk = RiskManager(cfg.risk, account.equity or cfg.account.initial_balance)

        for sym in self.symbols:
            df = self._fetch(sym)
            self.data[sym] = df
            strategy = EnsembleStrategy(cfg.strategy, self.specs[sym])
            strategy.max_spread_points = float(  # type: ignore[attr-defined]
                cfg.symbol(sym).max_spread_points or 0.0
            )
            strategy.prepare(df, resample(df, cfg.strategy.htf_timeframe))
            self.strategies[sym] = strategy
            self._last_bar[sym] = df.index[-1].to_pydatetime()

        self.core = TradingCore(cfg, self.broker, self.risk, self.strategies)

        # Evaluate (without trading) so the dashboard has a signal immediately.
        for sym in self.symbols:
            self.core._last_signal[sym] = self.strategies[sym].latest(sym)
            if isinstance(self.broker, SimulatedBroker):
                row = self.data[sym].iloc[-1]
                self.broker.set_bar(sym, Candle(
                    time=self.data[sym].index[-1].to_pydatetime(),
                    open=float(row["open"]), high=float(row["high"]),
                    low=float(row["low"]), close=float(row["close"]),
                    volume=float(row["volume"]), spread=float(row["spread"]),
                ))

        self._log_event(
            "ready",
            f"{cfg.execution.mode.upper()} mode on {', '.join(self.symbols)} "
            f"@ {cfg.strategy.timeframe} | risk {cfg.risk.risk_per_trade_pct}%/trade",
        )
        return self

    def _apply_overrides(self, spec: SymbolSpec, symbol: str) -> None:
        sc = self.cfg.symbol(symbol)
        for attr in ("digits", "contract_size", "tick_value", "tick_size",
                     "min_lot", "max_lot", "lot_step", "commission_per_lot"):
            value = getattr(sc, attr, None)
            if value is not None:
                setattr(spec, attr, value)
        if sc.commission_per_lot is None:
            spec.commission_per_lot = self.cfg.execution.commission_per_lot

    def _fetch(self, symbol: str) -> pd.DataFrame:
        return self.feed.candles(
            symbol, self.cfg.strategy.timeframe, self.cfg.data.history_bars
        )

    # ------------------------------------------------------------------ #
    # Main loop
    # ------------------------------------------------------------------ #
    def step(self) -> List[BarOutcome]:
        """Check every symbol for a newly closed bar and act on it."""
        if self.core is None or self.broker is None:
            raise RuntimeError("LiveTrader.setup() must be called first")

        outcomes: List[BarOutcome] = []
        self.last_tick = datetime.now(timezone.utc)

        with self._lock:
            for sym in self.symbols:
                try:
                    df = self._fetch(sym)
                except Exception as exc:  # feed hiccup — try again next poll
                    self.error = f"{sym}: {exc}"
                    log.warning("feed error on %s: %s", sym, exc)
                    continue

                if df.empty:
                    continue

                # Only the *second to last* bar is guaranteed closed when the
                # feed includes a forming bar; MT5 copy_rates_from_pos(0) does.
                closed_index = df.index[-1].to_pydatetime()
                if closed_index <= self._last_bar.get(sym, datetime.min.replace(
                    tzinfo=timezone.utc
                )):
                    continue

                self.data[sym] = df
                strategy = self.strategies[sym]
                strategy.prepare(df, resample(df, self.cfg.strategy.htf_timeframe))
                self._last_bar[sym] = closed_index
                self.bars_processed += 1

                row = df.iloc[-1]
                bar = Candle(
                    time=closed_index,
                    open=float(row["open"]), high=float(row["high"]),
                    low=float(row["low"]), close=float(row["close"]),
                    volume=float(row["volume"]), spread=float(row["spread"]),
                )

                # In paper mode the simulated broker needs the bar to settle
                # stops and targets; a live broker does that server-side.
                if isinstance(self.broker, SimulatedBroker):
                    for trade in self.broker.process_bar(sym, bar):
                        self.risk.on_trade_closed(trade)  # type: ignore[union-attr]
                        self._log_trade(trade)

                outcome = self.core.on_bar(sym, len(df) - 1, bar)
                outcomes.append(outcome)
                self._record(outcome)
                self.error = ""

        return outcomes

    def run(self, max_iterations: int = 0) -> None:
        """Blocking loop. Use :meth:`start` for the background version."""
        if self.core is None:
            self.setup()
        self.running = True
        self.started_at = datetime.now(timezone.utc)
        self._log_event("start", "trading loop started")
        iterations = 0
        try:
            while not self._stop.is_set():
                try:
                    self.step()
                except Exception as exc:  # never let one bad bar kill the bot
                    self.error = str(exc)
                    log.exception("step failed: %s", exc)
                    self._log_event("error", str(exc))
                iterations += 1
                if max_iterations and iterations >= max_iterations:
                    break
                self._stop.wait(max(0.2, self.cfg.execution.poll_seconds))
        finally:
            self.running = False
            self._log_event("stop", "trading loop stopped")

    def start(self) -> None:
        """Run the loop in a daemon thread."""
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self.run, name="omega-live", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        self.running = False

    # ------------------------------------------------------------------ #
    # Manual controls (wired to the dashboard)
    # ------------------------------------------------------------------ #
    def flatten(self, reason: CloseReason = CloseReason.MANUAL) -> int:
        if self.broker is None:
            return 0
        with self._lock:
            results = self.broker.close_all(reason)
            closed = sum(1 for r in results if r.ok)
            for r in results:
                if r.ok and isinstance(r.raw, Trade) and self.risk:
                    self.risk.on_trade_closed(r.raw)
        self._log_event("flatten", f"closed {closed} position(s) — {reason.value}")
        return closed

    def halt(self, reason: str = "manual halt") -> None:
        if self.risk:
            self.risk.halt(reason)
        self._log_event("halt", reason)

    def resume(self) -> None:
        if self.risk:
            self.risk.resume()
        self._log_event("resume", "risk guards reset — trading re-enabled")

    def set_risk_pct(self, pct: float) -> float:
        """Change the per-trade risk live (bounded for safety)."""
        pct = max(0.01, min(float(pct), 10.0))
        self.cfg.risk.risk_per_trade_pct = pct
        if self.risk:
            self.risk.cfg.risk_per_trade_pct = pct
        self._log_event("risk", f"risk per trade set to {pct:.2f}%")
        return pct

    # ------------------------------------------------------------------ #
    # Reporting
    # ------------------------------------------------------------------ #
    def snapshot(self) -> dict:
        """Full state for the dashboard / CLI status line."""
        if self.broker is None or self.risk is None:
            return {"ready": False, "mode": self.cfg.execution.mode}

        account = self.broker.account()
        positions = self.broker.positions()
        trades = self.broker.closed_trades()
        equity_curve = getattr(self.broker, "equity_curve", [])
        perf = analyse(equity_curve, trades, self.cfg.account.initial_balance)

        return {
            "ready": True,
            "running": self.running,
            "mode": self.cfg.execution.mode,
            "broker": getattr(self.broker, "name", "broker"),
            "feed": getattr(self.feed, "name", "feed"),
            "symbols": self.symbols,
            "timeframe": self.cfg.strategy.timeframe,
            "htf": self.cfg.strategy.htf_timeframe,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "last_tick": self.last_tick.isoformat() if self.last_tick else None,
            "bars_processed": self.bars_processed,
            "error": self.error,
            "account": account.to_dict(),
            "risk": self.risk.snapshot(account, positions),
            "positions": [self._position_dict(p, account) for p in positions],
            "signals": {
                sym: (self.core.last_signal(sym).to_dict()
                      if self.core and self.core.last_signal(sym) else None)
                for sym in self.symbols
            },
            "performance": perf.to_dict(),
            "trades": [t.to_dict() for t in trades[-100:]],
            "events": list(self.events)[-80:],
        }

    def equity_series(self) -> List[dict]:
        curve = getattr(self.broker, "equity_curve", [])
        return [
            {"time": t.isoformat(), "equity": round(e, 2), "balance": round(b, 2)}
            for t, e, b in curve
        ]

    def candles(self, symbol: str, limit: int = 300) -> List[dict]:
        df = self.data.get(symbol.upper())
        if df is None or df.empty:
            return []
        tail = df.tail(limit)
        strategy = self.strategies.get(symbol.upper())
        extras: Dict[str, pd.Series] = {}
        if strategy is not None and not strategy.features.empty:
            feats = strategy.features.tail(limit)
            for col in ("ema_fast", "ema_slow", "ema_base", "supertrend"):
                if col in feats:
                    extras[col] = feats[col]
            if not strategy.scores.empty:
                extras["score"] = strategy.scores["ensemble"].tail(limit)

        out = []
        for ts, row in tail.iterrows():
            item = {
                "time": ts.isoformat(),
                "open": float(row["open"]), "high": float(row["high"]),
                "low": float(row["low"]), "close": float(row["close"]),
                "volume": float(row["volume"]),
            }
            for key, series in extras.items():
                value = series.get(ts)
                item[key] = None if value is None or pd.isna(value) else round(float(value), 6)
            out.append(item)
        return out

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    def _position_dict(self, p, account: AccountState) -> dict:
        spec = self.specs.get(p.symbol.upper()) or self.broker.symbol_spec(p.symbol)
        price = (
            self.broker.mid_price(p.symbol)
            if isinstance(self.broker, SimulatedBroker)
            else p.entry_price
        )
        price = price or p.entry_price
        return {
            "ticket": p.ticket,
            "symbol": p.symbol,
            "side": p.side.value,
            "lots": round(p.lots, 2),
            "entry_price": p.entry_price,
            "price": round(price, spec.digits),
            "stop_loss": p.stop_loss,
            "take_profit": p.take_profit,
            "open_time": p.open_time.isoformat(),
            "bars_held": p.bars_held,
            "pnl": round(p.unrealized_pnl(price, spec), 2),
            "r_multiple": round(p.r_multiple(price), 2),
            "risk_pips": round(spec.price_to_pips(p.risk_distance), 1),
        }

    def _record(self, outcome: BarOutcome) -> None:
        for action in outcome.actions:
            kind = "entry" if action.startswith("OPEN") else (
                "exit" if "closed" in action else "manage"
            )
            self._log_event(kind, action, outcome.time)

    def _log_trade(self, trade: Trade) -> None:
        self._log_event(
            "exit",
            f"closed #{trade.ticket} {trade.symbol} {trade.side.value} "
            f"({trade.reason.value}) pnl {trade.pnl:+.2f} [{trade.r_multiple:+.2f}R]",
            trade.close_time,
        )

    def _log_event(self, kind: str, message: str, when: Optional[datetime] = None) -> None:
        self.events.append(
            {
                "time": (when or datetime.now(timezone.utc)).isoformat(),
                "kind": kind,
                "message": message,
            }
        )
        log.info("[%s] %s", kind, message)
