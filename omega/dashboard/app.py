"""FastAPI dashboard.

Serves a dependency-free single-page UI plus a small JSON API over a running
:class:`~omega.engine.live.LiveTrader`. Everything the bot knows — account,
risk budget, the six ensemble blocks, open positions, trades and the event
log — is visible here, and the kill-switch is one click away.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ..config import AppConfig
from ..core.types import CloseReason
from ..engine.live import LiveTrader

log = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).parent / "static"


def create_app(trader: LiveTrader, cfg: Optional[AppConfig] = None) -> FastAPI:
    """Build the dashboard app around an existing trader."""
    cfg = cfg or trader.cfg
    app = FastAPI(title=cfg.dashboard.title, version="1.0.0", docs_url="/api/docs")

    # The dashboard is served through a proxy in hosted environments, so the
    # browser's origin is not the server's. Allow it.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.state.trader = trader
    app.state.cfg = cfg

    # ------------------------------------------------------------------ #
    # API
    # ------------------------------------------------------------------ #
    @app.get("/api/state")
    def state() -> Dict[str, Any]:
        snap = trader.snapshot()
        snap["title"] = cfg.dashboard.title
        snap["refresh_ms"] = cfg.dashboard.refresh_ms
        return snap

    @app.get("/api/equity")
    def equity() -> Dict[str, Any]:
        return {"points": trader.equity_series()}

    @app.get("/api/candles")
    def candles(symbol: str = "", limit: int = 300) -> Dict[str, Any]:
        sym = (symbol or (trader.symbols[0] if trader.symbols else "")).upper()
        if not sym:
            raise HTTPException(404, "no symbols configured")
        return {"symbol": sym, "candles": trader.candles(sym, limit)}

    @app.get("/api/config")
    def configuration() -> Dict[str, Any]:
        return {
            "strategy": {
                "timeframe": cfg.strategy.timeframe,
                "htf_timeframe": cfg.strategy.htf_timeframe,
                "entry_threshold": cfg.strategy.entry_threshold,
                "exit_threshold": cfg.strategy.exit_threshold,
                "min_confidence": cfg.strategy.min_confidence,
                "min_agreeing_blocks": cfg.strategy.min_agreeing_blocks,
                "weights": cfg.strategy.weights.__dict__,
            },
            "risk": cfg.risk.__dict__,
            "execution": cfg.execution.__dict__,
        }

    @app.post("/api/control")
    def control(payload: Dict[str, Any]) -> Dict[str, Any]:
        action = str(payload.get("action", "")).lower()

        if action == "start":
            trader.start()
            return {"ok": True, "message": "trading loop started"}
        if action == "stop":
            trader.stop()
            return {"ok": True, "message": "trading loop stopped"}
        if action == "flatten":
            n = trader.flatten(CloseReason.MANUAL)
            return {"ok": True, "message": f"closed {n} position(s)"}
        if action == "halt":
            trader.halt("halted from dashboard")
            return {"ok": True, "message": "trading halted"}
        if action == "resume":
            trader.resume()
            return {"ok": True, "message": "trading resumed"}
        if action == "risk":
            try:
                pct = float(payload.get("risk_pct", cfg.risk.risk_per_trade_pct))
            except (TypeError, ValueError):
                raise HTTPException(400, "risk_pct must be a number")
            applied = trader.set_risk_pct(pct)
            return {"ok": True, "message": f"risk per trade = {applied:.2f}%",
                    "risk_pct": applied}

        raise HTTPException(400, f"unknown action {action!r}")

    @app.get("/api/health")
    def health() -> Dict[str, Any]:
        return {
            "ok": True,
            "running": trader.running,
            "mode": cfg.execution.mode,
            "error": trader.error,
        }

    # ------------------------------------------------------------------ #
    # UI
    # ------------------------------------------------------------------ #
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/")
    def index() -> FileResponse:
        html = STATIC_DIR / "index.html"
        if not html.exists():  # pragma: no cover
            return JSONResponse({"error": "dashboard assets missing"}, status_code=500)
        return FileResponse(str(html))

    return app


def serve(trader: LiveTrader, cfg: Optional[AppConfig] = None) -> None:
    """Blocking: start the trader and serve the dashboard."""
    import uvicorn

    cfg = cfg or trader.cfg
    app = create_app(trader, cfg)
    uvicorn.run(
        app,
        host=cfg.dashboard.host,
        port=cfg.dashboard.port,
        log_level=cfg.logging.level.lower(),
        access_log=False,
    )
