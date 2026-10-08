"""CSV journal of observed bid/ask spreads.

Every backtest in this project (all eight experiments) assumed a fixed
spread, because tick data does not exist here.  Experiment 8 measured what
that assumption costs: on M1 with structural stops, execution cost -- not
signal quality -- decided the outcome.

This recorder turns paper trading into the data collection the backtests
never had.  One row per closed bar and per submitted order:

    timestamp,symbol,bid,ask,spread_points,context

Review it with ``omega spreads <path>``.
"""
from __future__ import annotations

import csv
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

COLUMNS = ("timestamp", "symbol", "bid", "ask", "spread_points", "context")


class SpreadRecorder:
    """Appends observed spreads to a CSV file, crash-safe per row.

    The file is opened per write: volume is one row per closed bar per
    symbol (a few hundred rows a day), so simplicity beats buffering, and a
    killed process never loses the log.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._has_header = self.path.exists() and self.path.stat().st_size > 0

    def record(
        self,
        timestamp: datetime,
        symbol: str,
        bid: float,
        ask: float,
        spread_points: float,
        context: str = "bar",
    ) -> None:
        """Append one observation.  ``context`` is 'bar' or 'order'."""
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        row = (
            timestamp.isoformat(),
            symbol.upper(),
            f"{float(bid):.6g}",
            f"{float(ask):.6g}",
            f"{float(spread_points):.1f}",
            context,
        )
        with self._lock:
            new_file = not self._has_header
            with self.path.open("a", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                if new_file:
                    w.writerow(COLUMNS)
                    self._has_header = True
                w.writerow(row)

    def read(self) -> list[dict]:
        """Read the log back (used by tests and ``omega spreads``)."""
        if not self.path.exists():
            return []
        with self.path.open("r", newline="", encoding="utf-8") as fh:
            return [dict(r) for r in csv.DictReader(fh)]

    def close(self) -> None:  # kept for API symmetry with other feeds
        pass


def default_path(storage_path: str | Path) -> Path:
    """``runtime/spreads.csv`` next to the journal, whatever its directory."""
    return Path(storage_path).with_name("spreads.csv")
