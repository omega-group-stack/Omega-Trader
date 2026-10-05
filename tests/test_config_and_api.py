"""Configuration loading/validation and the dashboard HTTP API."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from omega.cli import build_parser, load_config
from omega.config import AppConfig, RiskConfig
from omega.dashboard.app import create_app
from omega.engine import LiveTrader

REPO_ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
def test_defaults_are_sane_and_valid():
    cfg = AppConfig()
    assert cfg.validate() == []
    assert cfg.risk.risk_per_trade_pct == 1.0
    assert cfg.execution.mode == "paper"
    assert cfg.execution.confirm_live is False


def test_shipped_config_file_parses_and_validates():
    cfg = AppConfig.load(REPO_ROOT / "config" / "config.yaml")
    assert cfg.validate() == []
    assert cfg.active_symbols
    assert cfg.strategy.timeframe == "M15"
    assert isinstance(cfg.risk, RiskConfig)
    assert isinstance(cfg.strategy.indicators.rsi_period, int)


def test_nested_overrides_are_typed(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text(yaml.safe_dump({
        "risk": {"risk_per_trade_pct": 0.35, "max_open_positions": 2},
        "strategy": {"timeframe": "H1", "indicators": {"rsi_period": 21}},
        "symbols": [{"name": "GBPUSD", "weight": 0.5}, "USDJPY"],
    }))
    cfg = AppConfig.load(path)
    assert cfg.risk.risk_per_trade_pct == 0.35
    assert cfg.risk.max_open_positions == 2
    assert cfg.strategy.timeframe == "H1"
    assert cfg.strategy.indicators.rsi_period == 21
    assert [s.name for s in cfg.symbols] == ["GBPUSD", "USDJPY"]
    assert cfg.symbols[0].weight == 0.5
    # untouched defaults survive
    assert cfg.risk.max_drawdown_pct == 15.0


def test_environment_variables_are_expanded(tmp_path, monkeypatch):
    monkeypatch.setenv("MT5_LOGIN", "123456")
    path = tmp_path / "c.yaml"
    path.write_text(yaml.safe_dump({
        "mt5": {"login": "${MT5_LOGIN}", "server": "${MISSING:-DemoServer}"}
    }))
    cfg = AppConfig.load(path)
    assert str(cfg.mt5.login) == "123456"
    assert cfg.mt5.server == "DemoServer"


def test_unknown_keys_are_ignored(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text(yaml.safe_dump({"risk": {"future_feature": 1}, "nonsense": 2}))
    assert AppConfig.load(path).validate() == []


@pytest.mark.parametrize("patch,needle", [
    ({"risk_per_trade_pct": 0}, "must be in (0, 100]"),
    ({"risk_per_trade_pct": 9}, "aggressive"),
    ({"max_total_risk_pct": 0.1}, "max_total_risk_pct"),
    ({"atr_stop_mult": 0}, "atr_stop_mult"),
])
def test_validation_catches_dangerous_settings(patch, needle):
    cfg = AppConfig()
    for key, value in patch.items():
        setattr(cfg.risk, key, value)
    problems = " ".join(cfg.validate())
    assert needle in problems


def test_live_mode_requires_the_interlock():
    cfg = AppConfig()
    cfg.execution.mode = "live"
    assert any("confirm_live" in p for p in cfg.validate())


def test_live_broker_refuses_without_confirmation():
    from omega.execution import build_broker

    cfg = AppConfig()
    cfg.execution.mode = "live"
    with pytest.raises(RuntimeError, match="confirm_live"):
        build_broker(cfg)


def test_config_roundtrip(tmp_path):
    cfg = AppConfig()
    cfg.risk.risk_per_trade_pct = 0.75
    out = tmp_path / "dump.yaml"
    cfg.dump(out)
    assert AppConfig.load(out).risk.risk_per_trade_pct == 0.75


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def test_cli_set_overrides():
    cfg = load_config(None, ["risk.risk_per_trade_pct=0.25",
                             "strategy.timeframe=H1",
                             "execution.confirm_live=true"])
    assert cfg.risk.risk_per_trade_pct == 0.25
    assert cfg.strategy.timeframe == "H1"
    assert cfg.execution.confirm_live is True


def test_cli_rejects_unknown_keys():
    with pytest.raises(SystemExit):
        load_config(None, ["risk.not_a_real_key=1"])


def test_cli_parser_exposes_every_command():
    parser = build_parser()
    for command in ("backtest", "paper", "live", "dashboard", "signal",
                    "optimize", "init"):
        args = parser.parse_args([command] if command != "init"
                                 else ["init", "x.yaml"])
        assert hasattr(args, "func")


# --------------------------------------------------------------------------- #
# Dashboard API
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def client():
    cfg = AppConfig()
    cfg.execution.mode = "paper"
    cfg.data.synthetic_bars = 900
    cfg.data.history_bars = 900
    cfg.logging.to_file = False
    trader = LiveTrader(cfg).setup()
    return TestClient(create_app(trader, cfg))


def test_health(client):
    body = client.get("/api/health").json()
    assert body["ok"] is True
    assert body["mode"] == "paper"


def test_state_payload_is_complete(client):
    body = client.get("/api/state").json()
    for key in ("account", "risk", "positions", "signals", "performance",
                "trades", "events", "symbols"):
        assert key in body, f"missing {key}"
    assert body["risk"]["risk_per_trade_pct"] == 1.0
    signal = body["signals"]["EURUSD"]
    assert len(signal["components"]) == 6


def test_candles_endpoint(client):
    body = client.get("/api/candles?symbol=EURUSD&limit=50").json()
    assert body["symbol"] == "EURUSD"
    assert len(body["candles"]) == 50
    first = body["candles"][0]
    for key in ("time", "open", "high", "low", "close"):
        assert key in first


def test_config_endpoint(client):
    body = client.get("/api/config").json()
    assert "risk" in body and "strategy" in body
    assert body["strategy"]["entry_threshold"] > 0


def test_risk_can_be_changed_through_the_api(client):
    out = client.post("/api/control", json={"action": "risk", "risk_pct": 0.4}).json()
    assert out["ok"] and out["risk_pct"] == pytest.approx(0.4)
    assert client.get("/api/state").json()["risk"]["risk_per_trade_pct"] == 0.4
    client.post("/api/control", json={"action": "risk", "risk_pct": 1.0})


def test_halt_and_resume_through_the_api(client):
    client.post("/api/control", json={"action": "halt"})
    assert client.get("/api/state").json()["risk"]["halted"] is True
    client.post("/api/control", json={"action": "resume"})
    assert client.get("/api/state").json()["risk"]["halted"] is False


def test_flatten_is_idempotent_when_flat(client):
    out = client.post("/api/control", json={"action": "flatten"}).json()
    assert out["ok"]


def test_unknown_action_is_rejected(client):
    assert client.post("/api/control", json={"action": "launch"}).status_code == 400


def test_dashboard_html_is_served(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Omega-Trader" in response.text
