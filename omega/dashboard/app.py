"""FastAPI dashboard.

Serves a dependency-free single-page UI plus a small JSON API over a running
:class:`~omega.engine.live.LiveTrader`. Everything the bot knows — account,
risk budget, the six ensemble blocks, open positions, trades and the event
log — is visible here, and the kill-switch is one click away.
"""

from __future__ import annotations

import hmac
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ..config import AppConfig
from ..core.types import CloseReason
from ..engine.live import LiveTrader

log = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).parent / "static"

_LOGIN_HTML = """<!doctype html><html><head><meta charset="utf-8">
<title>Omega-Trader — token required</title>
<style>
 body{background:#0d1117;color:#c9d1d9;font:15px/1.6 ui-sans-serif,system-ui,sans-serif;
      display:grid;place-items:center;height:100vh;margin:0}
 .card{background:#161b22;border:1px solid #30363d;border-radius:10px;
       padding:28px 32px;max-width:420px}
 h1{font-size:18px;margin:0 0 12px} code{color:#58a6ff}
 input{width:100%;padding:9px;margin:12px 0;background:#0d1117;color:#c9d1d9;
       border:1px solid #30363d;border-radius:6px;font-family:ui-monospace,monospace}
 button{padding:9px 18px;background:#238636;color:#fff;border:0;border-radius:6px;
        cursor:pointer;font-weight:600}
</style></head><body><div class="card">
<h1>🔒 Access token required</h1>
<p>This dashboard can move real money, so it is behind a token.</p>
<form onsubmit="location.search='?token='+encodeURIComponent(this.t.value);return false">
  <input name="t" type="password" placeholder="dashboard.auth_token" autofocus>
  <button type="submit">Unlock</button>
</form>
<p style="color:#8b949e;font-size:13px;margin-bottom:0">
API clients may send <code>X-Omega-Token</code> instead.</p>
</div></body></html>"""


def _bearer(header: str) -> str:
    parts = header.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return ""


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
    # Authentication
    # ------------------------------------------------------------------ #
    # /api/control can flatten your book and change your risk. If the server
    # is reachable by anything other than your own machine, a token is the
    # bare minimum. Empty token = open, and the startup banner says so loudly.
    token = (cfg.dashboard.auth_token or "").strip()
    if token:
        @app.middleware("http")
        async def require_token(request: Request, call_next):
            if request.url.path in ("/api/health", "/login") or \
                    request.url.path.startswith("/static"):
                return await call_next(request)

            supplied = (
                request.headers.get("x-omega-token")
                or _bearer(request.headers.get("authorization", ""))
                or request.query_params.get("token")
                or request.cookies.get("omega_token")
                or ""
            )
            # Constant-time compare: a naive == leaks the token one byte at a
            # time to anyone who can measure response latency.
            if not hmac.compare_digest(supplied, token):
                if request.url.path == "/":
                    return HTMLResponse(_LOGIN_HTML, status_code=401)
                return JSONResponse({"detail": "unauthorised"}, status_code=401)

            response = await call_next(request)
            # Refresh the cookie so a ?token=... link only has to be used once.
            if request.query_params.get("token") == token:
                response.set_cookie("omega_token", token, httponly=True,
                                    samesite="lax", max_age=7 * 24 * 3600)
            return response

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
