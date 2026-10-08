"""The spread journal -- paper trading as data collection (experiment 8)."""

from __future__ import annotations

from datetime import datetime, timezone

from omega.cli import main as cli_main
from omega.data.spread_recorder import SpreadRecorder, default_path

UTC = timezone.utc


def test_recorder_creates_header_and_appends(tmp_path):
    rec = SpreadRecorder(tmp_path / "spreads.csv")
    ts = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
    rec.record(ts, "EURUSD", 1.1000, 1.10012, 12.0, "bar")
    rec.record(ts, "gbpusd", 1.2500, 1.25018, 18.0, "order")

    rows = rec.read()
    assert len(rows) == 2
    assert rows[0]["symbol"] == "EURUSD"          # upper-cased
    assert rows[1]["symbol"] == "GBPUSD"
    assert rows[0]["context"] == "bar"
    assert rows[1]["context"] == "order"
    assert float(rows[0]["spread_points"]) == 12.0


def test_recorder_reopens_and_keeps_rows(tmp_path):
    path = tmp_path / "spreads.csv"
    ts = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
    SpreadRecorder(path).record(ts, "EURUSD", 1.0, 1.0001, 10.0)
    SpreadRecorder(path).record(ts, "EURUSD", 1.0, 1.0002, 20.0)  # no dup header
    rows = SpreadRecorder(path).read()
    assert len(rows) == 2
    assert rows[0]["spread_points"] == "10.0"


def test_recorder_naive_timestamp_is_assumed_utc(tmp_path):
    rec = SpreadRecorder(tmp_path / "s.csv")
    rec.record(datetime(2026, 10, 8, 12, 0), "EURUSD", 1.0, 1.0001, 10.0)
    assert rec.read()[0]["timestamp"].endswith("+00:00")


def test_default_path_sits_beside_the_journal():
    assert default_path("runtime/omega.sqlite").name == "spreads.csv"
    assert default_path("/x/y/journal.sqlite").parent.name == "y"


def test_cli_spreads_summarises_a_log(tmp_path, capsys):
    rec = SpreadRecorder(tmp_path / "spreads.csv")
    for hour in (12, 13):
        for pts in (10.0, 12.0, 14.0, 40.0):     # one news spike per hour
            ts = datetime(2026, 10, 8, hour, 30, tzinfo=UTC)
            rec.record(ts, "EURUSD", 1.1000, 1.1000 + pts * 1e-5, pts, "bar")
    rc = cli_main(["spreads", str(tmp_path / "spreads.csv")])
    out = capsys.readouterr().out
    assert rc == 0
    assert "EURUSD" in out
    assert "observations" in out
    assert "context" in out and "bar" in out
    assert "UTC" in out                        # worst-hours table present


def test_paper_trader_writes_the_spread_journal(tmp_path):
    """LiveTrader with execution.record_spreads journals every closed bar."""
    from omega.config import AppConfig, SymbolConfig
    from omega.engine import LiveTrader

    cfg = AppConfig()
    cfg.execution.mode = "paper"
    cfg.execution.record_spreads = True
    cfg.execution.poll_seconds = 0.01
    cfg.data.source = "synthetic"
    cfg.data.synthetic_seed = 5
    cfg.data.synthetic_bars = 1_200
    cfg.data.history_bars = 1_200
    cfg.data.synthetic_stream = True
    cfg.data.synthetic_speed = 50_000
    cfg.strategy.warmup_bars = 300
    cfg.logging.to_file = False
    cfg.storage.path = str(tmp_path / "journal.sqlite")
    cfg.symbols = [SymbolConfig(name="EURUSD")]

    trader = LiveTrader(cfg).setup()
    assert trader.spread_recorder is not None
    trader.run(max_iterations=12)

    rows = trader.spread_recorder.read()
    assert len(rows) >= 10
    assert all(r["symbol"] == "EURUSD" for r in rows)
    # 'bar' rows come from LiveTrader.step, 'order' rows from TradingCore's
    # submission hook -- both are expected during a run that trades.
    assert {r["context"] for r in rows} <= {"bar", "order"}
    assert any(r["context"] == "bar" for r in rows)
    assert all(float(r["ask"]) > float(r["bid"]) for r in rows)
