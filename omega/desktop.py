"""Desktop launcher — the entry point the Windows installer puts on the Start menu.

Double-clicking the shortcut should land a non-technical user on a working
dashboard with no terminal, no Python and no YAML editing. That means this
module owns the things a CLI user would otherwise do by hand:

* pick a writable home directory outside Program Files, because the install
  directory is read-only for a normal user account;
* write a starter ``config.yaml`` there on first run;
* choose a port that is actually free, rather than failing on "address
  already in use";
* mint an auth token and open the browser already holding it;
* keep the trader and the web server shutting down together.

It deliberately starts in **paper** mode against the synthetic feed. A first
run must succeed on a machine with no broker, no MetaTrader terminal and no
market data; trading real money is an explicit choice made later in the
config file, and `execution.confirm_live` still guards it.
"""

from __future__ import annotations

import argparse
import logging
import os
import secrets
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Optional

log = logging.getLogger("omega.desktop")

APP_NAME = "OmegaTrader"
DEFAULT_PORT = 8765
#: Loopback only. The dashboard can flatten positions and change risk, so it
#: must never be reachable from the network just because someone launched the
#: desktop app on a laptop in a cafe.
HOST = "127.0.0.1"


# --------------------------------------------------------------------------- #
# Locations
# --------------------------------------------------------------------------- #
def app_home() -> Path:
    """Writable per-user directory for config, logs, data and reports.

    Program Files is not writable by a standard user, so nothing the app
    creates at runtime may live next to the executable. ``OMEGA_HOME``
    overrides this, which is what the build's self-test uses.
    """
    override = os.environ.get("OMEGA_HOME")
    if override:
        return Path(override).expanduser()

    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        root = Path(base) if base else Path.home() / "AppData" / "Local"
        return root / APP_NAME

    return Path.home() / ".omega-trader"


def bundled_file(relative: str) -> Optional[Path]:
    """Locate a file shipped inside the bundle (or the source tree)."""
    roots = []
    meipass = getattr(sys, "_MEIPASS", None)      # set by PyInstaller
    if meipass:
        roots.append(Path(meipass))
    roots.append(Path(__file__).resolve().parent.parent)

    for root in roots:
        candidate = root / relative
        if candidate.exists():
            return candidate
    return None


def ensure_home() -> Path:
    """Create the home directory tree and a starter config if absent."""
    home = app_home()
    for sub in ("", "logs", "data", "reports", "runtime"):
        (home / sub).mkdir(parents=True, exist_ok=True)

    config_path = home / "config.yaml"
    if not config_path.exists():
        template = bundled_file("config/config.yaml")
        if template is not None:
            config_path.write_text(template.read_text(encoding="utf-8"),
                                   encoding="utf-8")
        else:                                      # pragma: no cover
            from .config import AppConfig
            AppConfig().dump(config_path)
    return home


# --------------------------------------------------------------------------- #
# Networking
# --------------------------------------------------------------------------- #
def free_port(preferred: int = DEFAULT_PORT, attempts: int = 50) -> int:
    """First free TCP port at or after ``preferred``.

    Users relaunch the app without noticing the old one is still running, and
    "[Errno 98] address already in use" is not an acceptable thing to show
    them.
    """
    for offset in range(attempts):
        candidate = preferred + offset
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((HOST, candidate))
                return candidate
            except OSError:
                continue
    raise RuntimeError(
        f"no free port in {preferred}..{preferred + attempts - 1}"
    )


def wait_until_serving(port: int, timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.4)
            if sock.connect_ex((HOST, port)) == 0:
                return True
        time.sleep(0.2)
    return False


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
def build_config(home: Path, port: int, token: str, mode: str):
    """Load the user's config, then force the desktop-specific settings."""
    from .config import AppConfig

    config_path = home / "config.yaml"
    cfg = AppConfig.load(config_path) if config_path.exists() else AppConfig()

    cfg.execution.mode = mode
    cfg.dashboard.enabled = True
    cfg.dashboard.host = HOST
    cfg.dashboard.port = port
    # The browser is opened by us with the token in the query string, so this
    # costs the user nothing and closes the door on any other local process
    # (or a web page doing cross-origin POSTs at 127.0.0.1) driving the bot.
    cfg.dashboard.auth_token = token

    cfg.logging.to_file = True
    cfg.logging.dir = str(home / "logs")
    if getattr(cfg.data, "csv_dir", None) in (None, "", "data"):
        cfg.data.csv_dir = str(home / "data")
    if getattr(cfg.data, "cache_dir", None) in (None, "", "runtime/cache"):
        cfg.data.cache_dir = str(home / "runtime" / "cache")
    if cfg.storage.path in ("", "runtime/omega.sqlite"):
        cfg.storage.path = str(home / "runtime" / "omega.sqlite")
    return cfg


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #
BANNER = r"""
   ___                               _____             _
  / _ \  _ __ ___   ___  __ _  __ _ |_   _|_ _ ___  __| | ___ _ __
 | | | || '_ ` _ \ / _ \/ _` |/ _` |  | |/ _` / _ \/ _` |/ _ \ '__|
 | |_| || | | | | |  __/ (_| | (_| |  | | (_| |  __/ (_| |  __/ |
  \___/ |_| |_| |_|\___|\__, |\__,_|  |_|\__,_|\___|\__,_|\___|_|
                        |___/   multi-indicator FX ensemble
"""


def run(mode: str = "paper", open_browser: bool = True,
        selftest: bool = False) -> int:
    """Start the trader plus dashboard. Returns a process exit code."""
    import uvicorn

    from .dashboard.app import create_app
    from .engine import LiveTrader
    from .logging_setup import setup_logging

    home = ensure_home()
    port = free_port()
    token = secrets.token_hex(24)
    cfg = build_config(home, port, token, mode)

    setup_logging(cfg.logging)
    url = f"http://{HOST}:{port}/?token={token}"

    print(BANNER)
    print(f"  Mode      : {mode}")
    print(f"  Settings  : {home / 'config.yaml'}")
    print(f"  Logs      : {home / 'logs'}")
    print(f"  Dashboard : {url}")
    print()
    print("  Keep this window open while the bot runs.")
    print("  Closing it stops the bot.")
    print()
    sys.stdout.flush()

    trader = LiveTrader(cfg).setup()
    trader.start()
    app = create_app(trader, cfg)

    server = uvicorn.Server(uvicorn.Config(
        app, host=HOST, port=port, log_level="warning", access_log=False,
    ))
    thread = threading.Thread(target=server.run, name="omega-dashboard",
                              daemon=True)
    thread.start()

    serving = wait_until_serving(port)
    if not serving:
        log.error("dashboard did not start listening on port %s", port)
        trader.stop()
        return 1

    if selftest:
        code = _selftest(port, token)
        server.should_exit = True
        thread.join(timeout=15)
        trader.stop()
        return code

    if open_browser:
        try:
            webbrowser.open(url)
        except Exception as exc:                    # pragma: no cover
            log.warning("could not open a browser automatically: %s", exc)
            print(f"  Open this address manually: {url}")

    try:
        while thread.is_alive():
            thread.join(timeout=0.5)
    except KeyboardInterrupt:
        print("\n  Stopping…")
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        trader.stop()
        print("  Stopped.")
    return 0


def _selftest(port: int, token: str) -> int:
    """Prove a packaged build actually boots. Run by CI on a real Windows box.

    A green unit-test suite says the code is right; it says nothing about
    whether PyInstaller collected uvicorn's protocol modules or the dashboard's
    static files. This does.
    """
    import json
    import urllib.error
    import urllib.request

    def get(path: str, with_token: bool):
        req = urllib.request.Request(f"http://{HOST}:{port}{path}")
        if with_token:
            req.add_header("X-Omega-Token", token)
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, resp.read()

    failures = []

    try:
        status, body = get("/api/health", with_token=False)
        if status != 200:
            failures.append(f"/api/health returned {status}")
        else:
            print(f"  [ok] /api/health -> {json.loads(body)}")
    except Exception as exc:
        failures.append(f"/api/health raised {exc!r}")

    try:
        status, body = get("/api/state", with_token=True)
        if status != 200:
            failures.append(f"/api/state returned {status}")
        else:
            snap = json.loads(body)
            print(f"  [ok] /api/state -> mode={snap.get('mode')} "
                  f"symbols={snap.get('symbols')}")
    except Exception as exc:
        failures.append(f"/api/state raised {exc!r}")

    # The static bundle is the thing PyInstaller is most likely to drop.
    try:
        status, body = get("/", with_token=True)
        if status != 200 or b"<" not in body[:512]:
            failures.append(f"/ returned {status}, {len(body)} bytes")
        else:
            print(f"  [ok] / -> {len(body)} bytes of HTML")
    except Exception as exc:
        failures.append(f"/ raised {exc!r}")

    # Unauthenticated control must be refused.
    try:
        req = urllib.request.Request(
            f"http://{HOST}:{port}/api/control",
            data=json.dumps({"action": "halt"}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            failures.append(f"unauthenticated control allowed ({resp.status})")
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            print(f"  [ok] unauthenticated /api/control -> {exc.code}")
        else:
            failures.append(f"unauthenticated control returned {exc.code}")
    except Exception as exc:
        failures.append(f"control probe raised {exc!r}")

    if failures:
        print("\n  SELF-TEST FAILED")
        for item in failures:
            print(f"    - {item}")
        return 1
    print("\n  SELF-TEST PASSED")
    return 0


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="omega-desktop",
        description="Run Omega-Trader with its dashboard as a desktop app.",
    )
    parser.add_argument("--mode", default="paper",
                        choices=["paper", "backtest", "live"],
                        help="execution mode (default: paper)")
    parser.add_argument("--no-browser", action="store_true",
                        help="do not open a browser window")
    parser.add_argument("--selftest", action="store_true",
                        help="boot, probe the dashboard, exit (used by CI)")
    parser.add_argument("--home", help="override the settings directory")
    args = parser.parse_args(argv)

    if args.home:
        os.environ["OMEGA_HOME"] = args.home

    try:
        return run(mode=args.mode,
                   open_browser=not args.no_browser and not args.selftest,
                   selftest=args.selftest)
    except KeyboardInterrupt:                       # pragma: no cover
        return 0
    except Exception as exc:
        logging.getLogger("omega").exception("fatal error")
        print(f"\n  Omega-Trader could not start: {exc}\n")
        if sys.platform == "win32" and not args.selftest:
            # A double-clicked shortcut closes instantly on error otherwise,
            # taking the only copy of the message with it.
            try:
                input("  Press Enter to close… ")
            except EOFError:
                pass
        return 1


if __name__ == "__main__":                          # pragma: no cover
    raise SystemExit(main())
