"""SQLite trade journal.

Survives restarts and gives you something to analyse later. Writes are
best-effort: a journal failure must never take the trading loop down.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from ..core.types import AccountState, Trade

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    mode        TEXT NOT NULL,
    symbols     TEXT NOT NULL,
    timeframe   TEXT NOT NULL,
    config      TEXT
);
CREATE TABLE IF NOT EXISTS trades (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      INTEGER,
    ticket      INTEGER,
    symbol      TEXT,
    side        TEXT,
    lots        REAL,
    entry_price REAL,
    exit_price  REAL,
    open_time   TEXT,
    close_time  TEXT,
    pnl         REAL,
    commission  REAL,
    swap        REAL,
    reason      TEXT,
    r_multiple  REAL,
    bars_held   INTEGER,
    entry_score REAL,
    regime      TEXT,
    balance_after REAL
);
CREATE TABLE IF NOT EXISTS equity (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id   INTEGER,
    time     TEXT,
    equity   REAL,
    balance  REAL
);
CREATE TABLE IF NOT EXISTS events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id  INTEGER,
    time    TEXT,
    kind    TEXT,
    message TEXT
);
CREATE INDEX IF NOT EXISTS idx_trades_run ON trades(run_id);
CREATE INDEX IF NOT EXISTS idx_equity_run ON equity(run_id);
"""


class Journal:
    """Thread-safe, append-only run journal."""

    def __init__(self, path: str = "runtime/omega.sqlite") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self.run_id: Optional[int] = None

    # ------------------------------------------------------------------ #
    def start_run(
        self, mode: str, symbols: Iterable[str], timeframe: str, config: Dict[str, Any]
    ) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO runs (started_at, mode, symbols, timeframe, config) "
                "VALUES (?,?,?,?,?)",
                (
                    datetime.now(timezone.utc).isoformat(),
                    mode,
                    ",".join(symbols),
                    timeframe,
                    json.dumps(config, default=str)[:200_000],
                ),
            )
            self._conn.commit()
            self.run_id = int(cur.lastrowid or 0)
            return self.run_id

    def record_trade(self, trade: Trade) -> None:
        self._safe(
            "INSERT INTO trades (run_id, ticket, symbol, side, lots, entry_price, "
            "exit_price, open_time, close_time, pnl, commission, swap, reason, "
            "r_multiple, bars_held, entry_score, regime, balance_after) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                self.run_id, trade.ticket, trade.symbol, trade.side.value, trade.lots,
                trade.entry_price, trade.exit_price, trade.open_time.isoformat(),
                trade.close_time.isoformat(), trade.pnl, trade.commission, trade.swap,
                trade.reason.value, trade.r_multiple, trade.bars_held,
                trade.entry_score, trade.regime, trade.balance_after,
            ),
        )

    def record_equity(self, when: datetime, account: AccountState) -> None:
        self._safe(
            "INSERT INTO equity (run_id, time, equity, balance) VALUES (?,?,?,?)",
            (self.run_id, when.isoformat(), account.equity, account.balance),
        )

    def record_event(self, kind: str, message: str, when: Optional[datetime] = None) -> None:
        self._safe(
            "INSERT INTO events (run_id, time, kind, message) VALUES (?,?,?,?)",
            (
                self.run_id,
                (when or datetime.now(timezone.utc)).isoformat(),
                kind,
                message[:2_000],
            ),
        )

    # ------------------------------------------------------------------ #
    def trades(self, run_id: Optional[int] = None, limit: int = 1_000) -> List[dict]:
        rid = run_id if run_id is not None else self.run_id
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM trades WHERE run_id IS ? ORDER BY id DESC LIMIT ?",
                (rid, limit),
            )
            cols = [c[0] for c in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    def runs(self, limit: int = 50) -> List[dict]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT id, started_at, mode, symbols, timeframe FROM runs "
                "ORDER BY id DESC LIMIT ?",
                (limit,),
            )
            cols = [c[0] for c in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.commit()
                self._conn.close()
            except sqlite3.Error:
                pass

    # ------------------------------------------------------------------ #
    def _safe(self, sql: str, params: tuple) -> None:
        try:
            with self._lock:
                self._conn.execute(sql, params)
                self._conn.commit()
        except sqlite3.Error as exc:  # journalling must never break trading
            log.warning("journal write failed: %s", exc)
