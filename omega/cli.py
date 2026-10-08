"""Omega-Trader command line.

    omega backtest  -c config/config.yaml --report reports/run.json
    omega paper     -c config/config.yaml --dashboard
    omega live      -c config/config.yaml          # requires confirm_live
    omega dashboard -c config/config.yaml
    omega optimize  -c config/config.yaml --param risk.risk_per_trade_pct=0.5,1,2
    omega signal    -c config/config.yaml          # one-shot market read
    omega init      config/config.yaml             # write a starter config
"""

from __future__ import annotations

import argparse
import itertools
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .config import AppConfig
from .logging_setup import setup_logging

log = logging.getLogger("omega.cli")

BANNER = r"""
   ___                               _____             _
  / _ \  _ __ ___   ___  __ _  __ _ |_   _|_ _ ___  __| | ___ _ __
 | | | || '_ ` _ \ / _ \/ _` |/ _` |  | |/ _` / _ \/ _` |/ _ \ '__|
 | |_| || | | | | |  __/ (_| | (_| |  | | (_| |  __/ (_| |  __/ |
  \___/ |_| |_| |_|\___|\__, |\__,_|  |_|\__,_|\___|\__,_|\___|_|
                        |___/   multi-indicator FX ensemble
"""


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def load_config(path: Optional[str], overrides: Sequence[str] = ()) -> AppConfig:
    cfg = AppConfig.load(path) if path else AppConfig()
    for item in overrides:
        key, _, value = item.partition("=")
        if not _:
            raise SystemExit(f"--set expects key=value, got {item!r}")
        _apply_override(cfg, key.strip(), value.strip())
    return cfg


def _apply_override(cfg: AppConfig, dotted: str, raw: str) -> None:
    """Apply ``risk.risk_per_trade_pct=0.5`` style overrides."""
    try:
        cfg.set(dotted, raw)
    except KeyError as exc:
        raise SystemExit(str(exc).strip('"')) from exc


def _coerce(raw: str, like: Any) -> Any:
    if isinstance(like, bool):
        return raw.lower() in ("1", "true", "yes", "on")
    if isinstance(like, int) and not isinstance(like, bool):
        return int(float(raw))
    if isinstance(like, float):
        return float(raw)
    if isinstance(like, list):
        return [p.strip() for p in raw.split(",") if p.strip()]
    return raw


def validate_or_die(cfg: AppConfig, strict: bool = False) -> None:
    problems = cfg.validate()
    if not problems:
        return
    fatal = [p for p in problems if "very aggressive" not in p]
    for p in problems:
        (log.error if p in fatal else log.warning)("config: %s", p)
    if fatal or strict:
        if fatal:
            raise SystemExit("Fix the configuration problems above and try again.")


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
def cmd_backtest(args: argparse.Namespace) -> int:
    from .engine import Backtester

    cfg = load_config(args.config, args.set)
    cfg.execution.mode = "backtest"
    if args.symbol:
        from .config import SymbolConfig

        cfg.symbols = [SymbolConfig(name=s.upper()) for s in args.symbol]
    if args.timeframe:
        cfg.strategy.timeframe = args.timeframe
    if args.bars:
        cfg.data.history_bars = args.bars
        cfg.data.synthetic_bars = max(cfg.data.synthetic_bars, args.bars)
    validate_or_die(cfg)

    setup_logging(cfg.logging)
    print(BANNER)
    log.info(
        "Backtesting %s on %s | risk %.2f%%/trade | data: %s",
        ", ".join(s.name for s in cfg.active_symbols),
        cfg.strategy.timeframe,
        cfg.risk.risk_per_trade_pct,
        cfg.data.source,
    )

    result = Backtester(cfg).run(progress=True)
    print("\n" + "═" * 72)
    print(result.performance.summary())
    print("═" * 72 + "\n")

    if args.trades:
        for trade in result.trades[-args.trades:]:
            d = trade.to_dict()
            print(
                f"  {d['close_time'][:16]}  {d['symbol']:8s} {d['side']:4s} "
                f"{d['lots']:>5.2f}  {d['entry_price']:>10} -> {d['exit_price']:<10} "
                f"{d['reason']:<14} {d['r_multiple']:+6.2f}R  {d['pnl']:+9.2f}"
            )
        print()

    if args.report:
        path = Path(args.report)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result.to_dict(), indent=2, default=str),
                        encoding="utf-8")
        log.info("Report written to %s", path)

    return 0


def cmd_paper(args: argparse.Namespace) -> int:
    return _run_live(args, mode="paper")


def cmd_live(args: argparse.Namespace) -> int:
    return _run_live(args, mode="live")


def _run_live(args: argparse.Namespace, mode: str) -> int:
    from .engine import LiveTrader

    cfg = load_config(args.config, args.set)
    cfg.execution.mode = mode
    if args.symbol:
        from .config import SymbolConfig

        cfg.symbols = [SymbolConfig(name=s.upper()) for s in args.symbol]
    if getattr(args, "risk", None):
        cfg.risk.risk_per_trade_pct = args.risk
    validate_or_die(cfg)

    setup_logging(cfg.logging)
    print(BANNER)

    if mode == "live":
        log.warning("=" * 70)
        log.warning("LIVE TRADING — real money is at risk on account %s", cfg.mt5.login)
        log.warning("Risk per trade: %.2f%% | daily stop: %.2f%% | kill switch: %.2f%%",
                    cfg.risk.risk_per_trade_pct, cfg.risk.max_daily_loss_pct,
                    cfg.risk.max_drawdown_pct)
        log.warning("=" * 70)

    trader = LiveTrader(cfg).setup()

    if args.dashboard or getattr(args, "dashboard_only", False):
        from .dashboard.app import create_app
        import uvicorn

        trader.start()
        app = create_app(trader, cfg)

        host = cfg.dashboard.host
        if not cfg.dashboard.auth_token and host not in ("127.0.0.1", "localhost"):
            log.warning("=" * 70)
            log.warning("DASHBOARD IS UNAUTHENTICATED and bound to %s", host)
            log.warning("Anyone who can reach port %s can flatten your positions,",
                        cfg.dashboard.port)
            log.warning("change your risk percentage and halt the bot.")
            log.warning("Fix it with EITHER of:")
            log.warning("  --set dashboard.auth_token=$(openssl rand -hex 24)")
            log.warning("  --set dashboard.host=127.0.0.1   (then use an SSH tunnel)")
            log.warning("=" * 70)
        elif cfg.dashboard.auth_token:
            log.info("Dashboard authentication is ON "
                     "(open /?token=... once, then the cookie carries it).")

        log.info("Dashboard on http://%s:%s", host, cfg.dashboard.port)
        try:
            uvicorn.run(app, host=cfg.dashboard.host, port=cfg.dashboard.port,
                        log_level="warning", access_log=False)
        finally:
            trader.stop()
        return 0

    try:
        trader.run(max_iterations=args.iterations or 0)
    except KeyboardInterrupt:
        log.info("Interrupted — shutting down")
    finally:
        trader.stop()
        if args.flatten_on_exit:
            trader.flatten()
        snap = trader.snapshot()
        if snap.get("ready"):
            print("\n" + "═" * 72)
            print(f"  Final equity : {snap['account']['equity']:,.2f} "
                  f"{snap['account']['currency']}")
            print(f"  Trades       : {snap['performance']['trades']}  "
                  f"(PF {snap['performance']['profit_factor']}, "
                  f"win rate {snap['performance']['win_rate_pct']}%)")
            print("═" * 72)
    return 0


def cmd_dashboard(args: argparse.Namespace) -> int:
    args.dashboard = True
    args.iterations = 0
    args.flatten_on_exit = False
    return _run_live(args, mode=getattr(args, "mode", "paper"))


def cmd_signal(args: argparse.Namespace) -> int:
    """One-shot: what does the ensemble think right now?"""
    from .data import build_feed
    from .data.base import resample
    from .strategy import EnsembleStrategy

    cfg = load_config(args.config, args.set)
    setup_logging(cfg.logging)

    feed = build_feed(cfg)
    feed.connect()
    symbols = args.symbol or [s.name for s in cfg.active_symbols]

    for name in symbols:
        sym = name.upper()
        df = feed.candles(sym, cfg.strategy.timeframe, cfg.data.history_bars)
        spec = feed.symbol_spec(sym)
        strategy = EnsembleStrategy(cfg.strategy, spec).prepare(
            df, resample(df, cfg.strategy.htf_timeframe)
        )
        signal = strategy.latest(sym)

        print(f"\n┌─ {sym} · {cfg.strategy.timeframe} · {signal.time:%Y-%m-%d %H:%M} UTC")
        print(f"│  price {signal.price:.5f}   ATR {spec.price_to_pips(signal.atr):.1f} pips"
              f"   regime {signal.regime.value}")
        print(f"│  score {signal.score:+.3f}   confidence {signal.confidence:.2f}"
              f"   ⇒ {signal.direction.value}")
        print("│")
        for c in signal.components:
            bar_len = int(abs(c.score) * 18)
            bar = ("█" * bar_len).rjust(18) if c.score < 0 else ("█" * bar_len).ljust(18)
            side = "◀" if c.score < 0 else "▶"
            print(f"│  {c.name:<11} {c.score:+.2f} w{c.weight:.2f} {side} {bar}  {c.detail}")
        print("│")
        for r in signal.reasons:
            print(f"│  · {r}")
        for v in signal.vetoes:
            print(f"│  ⛔ {v}")
        print("└" + "─" * 70)

    feed.close()
    return 0


def cmd_optimize(args: argparse.Namespace) -> int:
    """Brute-force parameter sweep with a walk-forward-friendly report."""
    from .engine import Backtester

    cfg_base = load_config(args.config, args.set)
    cfg_base.execution.mode = "backtest"
    setup_logging(cfg_base.logging)

    grid: Dict[str, List[str]] = {}
    for spec in args.param:
        key, _, values = spec.partition("=")
        if not _:
            raise SystemExit(f"--param expects key=v1,v2,v3 (got {spec!r})")
        grid[key.strip()] = [v.strip() for v in values.split(",") if v.strip()]
    if not grid:
        raise SystemExit("at least one --param is required")

    keys = list(grid)
    combos = list(itertools.product(*(grid[k] for k in keys)))
    log.info("Sweeping %d combination(s) over %s", len(combos), ", ".join(keys))

    rows = []
    for n, combo in enumerate(combos, 1):
        cfg = load_config(args.config, args.set)
        cfg.execution.mode = "backtest"
        for key, value in zip(keys, combo):
            _apply_override(cfg, key, value)
        try:
            result = Backtester(cfg).run()
        except Exception as exc:
            log.warning("  [%d/%d] %s failed: %s", n, len(combos), combo, exc)
            continue
        p = result.performance
        rows.append({
            **{k: v for k, v in zip(keys, combo)},
            "trades": p.trades,
            "return_pct": p.return_pct,
            "max_dd_pct": p.max_drawdown_pct,
            "profit_factor": p.profit_factor,
            "sharpe": p.sharpe,
            "expectancy_r": p.expectancy_r,
            "win_rate": p.win_rate_pct,
            "score": _fitness(p),
        })
        log.info("  [%d/%d] %s -> ret %+.2f%% PF %.2f DD %.2f%% (%d trades)",
                 n, len(combos), dict(zip(keys, combo)),
                 p.return_pct, p.profit_factor, p.max_drawdown_pct, p.trades)

    if not rows:
        log.error("No successful runs")
        return 1

    rows.sort(key=lambda r: r["score"], reverse=True)
    width = max(len(k) for k in keys) + 2

    print("\n" + "═" * 92)
    print("  RANKED RESULTS  (fitness = risk-adjusted return, penalised for few trades)")
    print("═" * 92)
    header = "".join(k.split(".")[-1][:width - 1].ljust(width) for k in keys)
    print(f"  {header}{'trades':>8}{'return%':>10}{'maxDD%':>9}"
          f"{'PF':>7}{'Sharpe':>8}{'expR':>8}{'fitness':>9}")
    print("  " + "─" * 88)
    for r in rows[:args.top]:
        line = "".join(str(r[k])[:width - 1].ljust(width) for k in keys)
        print(f"  {line}{r['trades']:>8}{r['return_pct']:>10.2f}{r['max_dd_pct']:>9.2f}"
              f"{r['profit_factor']:>7.2f}{r['sharpe']:>8.2f}{r['expectancy_r']:>8.3f}"
              f"{r['score']:>9.3f}")
    print("═" * 92)
    print("\n  ⚠  A good score here is NOT proof of an edge. Re-test the winner on\n"
          "     data it has never seen before trusting it with money.\n")

    if args.report:
        path = Path(args.report)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
        log.info("Sweep written to %s", path)
    return 0


def _fitness(p) -> float:
    """Rank by return per unit of drawdown, penalising thin samples."""
    if p.trades < 10 or p.max_drawdown_pct <= 0:
        return -999.0
    sample_penalty = min(1.0, p.trades / 50.0)
    return round(
        (p.return_pct / p.max_drawdown_pct) * sample_penalty
        + 0.5 * p.sharpe
        + 2.0 * p.expectancy_r,
        4,
    )


def cmd_init(args: argparse.Namespace) -> int:
    path = Path(args.path)
    if path.exists() and not args.force:
        raise SystemExit(f"{path} already exists (use --force to overwrite)")
    template = Path(__file__).parent.parent / "config" / "config.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    if template.exists():
        path.write_text(template.read_text(encoding="utf-8"), encoding="utf-8")
    else:  # pragma: no cover
        AppConfig().dump(path)
    print(f"Wrote starter config to {path}")
    print("Next:  omega backtest -c " + str(path))
    return 0


def cmd_notify(args: argparse.Namespace) -> int:
    """Send a test message / verify the Telegram wiring end to end."""
    from omega.notify.telegram import TelegramNotifier

    cfg = load_config(args.config, args.set)
    tc = cfg.notifications.telegram
    notifier = TelegramNotifier(tc, events=cfg.notifications)

    if not notifier.configured:
        print("Telegram is not configured.\n")
        print("  1. Talk to @BotFather on Telegram, send /newbot, copy the token.")
        print("  2. Send any message to your new bot, then open")
        print("     https://api.telegram.org/bot<TOKEN>/getUpdates")
        print("     and copy message.chat.id.")
        print("  3. Export them (recommended — keeps secrets out of the YAML):")
        print("       export TELEGRAM_BOT_TOKEN=123456:ABC...")
        print("       export TELEGRAM_CHAT_ID=987654321")
        print("     or set notifications.telegram.bot_token / .chat_id.")
        print("  4. Then:  omega notify test")
        return 1

    text = args.message or (
        "✅ <b>Omega-Trader</b> is wired up.\n"
        f"mode <code>{cfg.execution.mode}</code>   "
        f"symbols <code>{', '.join(s.name for s in cfg.active_symbols)}</code>   "
        f"risk <b>{cfg.risk.risk_per_trade_pct:.2f}%</b>/trade\n"
        "Send /help once the bot is running."
    )
    ok = notifier.send_now(text)
    print("Sent." if ok else "Failed — see the log above.")
    return 0 if ok else 1


def cmd_walkforward(args: argparse.Namespace) -> int:
    """Rolling optimise-then-test study. The only honest backtest number."""
    from omega.engine.walkforward import walk_forward

    cfg = load_config(args.config, args.set)
    if args.symbol:
        for sym in cfg.symbols:
            sym.enabled = sym.name.upper() in {s.upper() for s in args.symbol}
    validate_or_die(cfg)

    grid: dict = {}
    for item in args.param:
        key, _, values = item.partition("=")
        if not _:
            raise SystemExit(f"--param expects key=v1,v2, got {item!r}")
        grid[key.strip()] = [float(v) for v in values.split(",")]
    if not grid:
        grid = {"strategy.entry_threshold": [0.28, 0.40, 0.52]}
        log.info("No --param given; using default grid %s", grid)

    result = walk_forward(
        cfg, grid,
        is_months=args.is_months, oos_months=args.oos_months,
        step_months=args.step_months, workers=args.workers,
        checkpoint=args.json,
    )
    print(result.summary())

    if args.json:
        import json
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(result.to_dict(), indent=2),
                                   encoding="utf-8")
        print(f"\nWrote {args.json}")
    return 0


def cmd_data(args: argparse.Namespace) -> int:
    """Download or inspect historical price files."""
    from omega.data import download as dl

    if args.data_command == "describe":
        for path in args.paths:
            info = dl.describe(path)
            print(f"\n{info['path']}")
            print(f"  bars        {info['bars']:,}")
            print(f"  range       {info['start']}  ->  {info['end']}"
                  f"   ({info['years']:.2f} years)")
            print(f"  median gap  {info['median_gap']}"
                  f"   (gaps > 3x median: {info['large_gaps']})")
            print(f"  duplicates  {info['duplicate_times']}"
                  f"   zero-volume bars: {info['zero_volume_bars']}")
            print(f"  spread col  {'yes' if info['has_spread'] else 'no'}")
            print(f"  price range {info['price_range'][0]} .. {info['price_range'][1]}")
        return 0

    timeframes = args.timeframe or ["M15", "H1", "H4", "D1"]
    written = []
    for tf in timeframes:
        written.append(
            dl.download_github_csv(
                args.symbol, tf, out_dir=args.out,
                repo=args.repo, path_template=args.path_template,
            )
        )
    print(f"\nDownloaded {len(written)} file(s) into {args.out}/")
    for path in written:
        info = dl.describe(path)
        print(f"  {path.name:<18} {info['bars']:>8,} bars  "
              f"{str(info['start'])[:10]} -> {str(info['end'])[:10]}")
    print("\nUse it with:")
    print(f"  omega backtest --set data.source=csv --set data.csv_dir={args.out}")
    return 0


def cmd_spreads(args: argparse.Namespace) -> int:
    """Summarise a spread journal written by paper/live trading."""
    import pandas as pd

    from omega.data.spread_recorder import default_path

    if args.path:
        path = Path(args.path)
    else:
        storage = "runtime/omega.sqlite"
        if args.config:
            storage = load_config(args.config).storage.path
        path = default_path(storage)
    if not path.exists():
        print(f"No spread journal at {path}")
        print("Enable it with: omega paper --set execution.record_spreads=true")
        return 1

    df = pd.read_csv(path)
    if df.empty:
        print(f"{path} is empty")
        return 0
    ts = pd.to_datetime(df["timestamp"], utc=True)
    df["hour"] = ts.dt.hour
    # 1 pip = 10 points on 3/5-digit quotes (the usual MT5 case).
    df["pips"] = df["spread_points"] / 10.0

    print(f"\n{path}  ({len(df):,} observations, "
          f"{ts.min()} .. {ts.max()})")
    print(f"\n{'symbol':<8} {'n':>7} {'median':>8} {'p90':>8} {'p99':>8} "
          f"{'max':>8}   (points / pips)")
    for sym, g in df.groupby("symbol"):
        q = g["spread_points"].quantile([0.5, 0.9, 0.99])
        print(f"{sym:<8} {len(g):>7,} {q[0.5]:>8.1f} {q[0.9]:>8.1f} "
              f"{q[0.99]:>8.1f} {g['spread_points'].max():>8.1f}   "
              f"({q[0.5] / 10:.1f} / {g['spread_points'].max() / 10:.1f} pips)")

    if "context" in df.columns:
        for ctx, g in df.groupby("context"):
            print(f"context {ctx:<6} n={len(g):>7,}  "
                  f"median {g['spread_points'].median():.1f} pts")

    print("\nworst hours by median spread (all symbols):")
    by_hour = df.groupby("hour")["spread_points"].median().sort_values(
        ascending=False
    )
    for hour, med in by_hour.head(5).items():
        print(f"  {hour:02d}:00 UTC   median {med:.1f} pts "
              f"({med / 10:.1f} pips)")
    print("\nBacktest assumption to compare against: risk.typical_spread_points")
    return 0


# --------------------------------------------------------------------------- #
# Parser
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="omega",
        description="Omega-Trader — multi-indicator automated FX trading bot",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("-c", "--config", help="path to a YAML config file")
        p.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                       help="override any config key, e.g. risk.risk_per_trade_pct=0.5")
        p.add_argument("-s", "--symbol", action="append",
                       help="symbol to trade (repeatable)")

    # backtest
    bt = sub.add_parser("backtest", help="run the strategy over historical data")
    common(bt)
    bt.add_argument("-t", "--timeframe", help="override strategy.timeframe")
    bt.add_argument("-b", "--bars", type=int, help="how many bars to load")
    bt.add_argument("--report", help="write a JSON report here")
    bt.add_argument("--trades", type=int, default=0,
                    help="print the last N trades")
    bt.set_defaults(func=cmd_backtest)

    # paper
    pp = sub.add_parser("paper", help="simulated trading on live data")
    common(pp)
    pp.add_argument("--risk", type=float, help="risk %% per trade")
    pp.add_argument("--dashboard", action="store_true", help="serve the web dashboard")
    pp.add_argument("--iterations", type=int, default=0, help="stop after N loops")
    pp.add_argument("--flatten-on-exit", action="store_true")
    pp.set_defaults(func=cmd_paper)

    # live
    lv = sub.add_parser("live", help="REAL money trading through MetaTrader 5")
    common(lv)
    lv.add_argument("--risk", type=float, help="risk %% per trade")
    lv.add_argument("--dashboard", action="store_true")
    lv.add_argument("--iterations", type=int, default=0)
    lv.add_argument("--flatten-on-exit", action="store_true")
    lv.set_defaults(func=cmd_live)

    # dashboard
    db = sub.add_parser("dashboard", help="paper-trade with the web dashboard")
    common(db)
    db.add_argument("--mode", default="paper", choices=["paper", "live"])
    db.add_argument("--risk", type=float)
    db.set_defaults(func=cmd_dashboard)

    # signal
    sg = sub.add_parser("signal", help="print the current ensemble read")
    common(sg)
    sg.set_defaults(func=cmd_signal)

    # optimize
    op = sub.add_parser("optimize", help="grid-search strategy parameters")
    common(op)
    op.add_argument("--param", action="append", default=[], metavar="KEY=V1,V2",
                    help="parameter grid, repeatable")
    op.add_argument("--top", type=int, default=15, help="rows to print")
    op.add_argument("--report", help="write the full sweep as JSON")
    op.set_defaults(func=cmd_optimize)

    # init
    ini = sub.add_parser("init", help="write a starter config file")
    ini.add_argument("path", nargs="?", default="config/config.yaml")
    ini.add_argument("--force", action="store_true")
    ini.set_defaults(func=cmd_init)

    # notify
    nt = sub.add_parser("notify", help="test the Telegram notification setup")
    nsub = nt.add_subparsers(dest="notify_command", required=True)
    ntt = nsub.add_parser("test", help="send a test message")
    common(ntt)
    ntt.add_argument("-m", "--message", help="custom message to send")
    ntt.set_defaults(func=cmd_notify)

    # walkforward
    wf = sub.add_parser("walkforward",
                        help="rolling out-of-sample study (trustworthy backtest)")
    common(wf)
    wf.add_argument("--param", action="append", default=[], metavar="KEY=V1,V2",
                    help="parameter grid searched in-sample, repeatable")
    wf.add_argument("--is-months", type=int, default=36, dest="is_months",
                    help="in-sample window length in months (default 36)")
    wf.add_argument("--oos-months", type=int, default=12, dest="oos_months",
                    help="out-of-sample window length in months (default 12)")
    wf.add_argument("--step-months", type=int, default=None, dest="step_months",
                    help="roll step in months (default: = --oos-months)")
    wf.add_argument("--workers", type=int, default=2)
    wf.add_argument("--json", help="also write the full result to this path")
    wf.set_defaults(func=cmd_walkforward)

    # data
    dt = sub.add_parser("data", help="download / inspect historical price files")
    dsub = dt.add_subparsers(dest="data_command", required=True)

    dd = dsub.add_parser("download", help="fetch real OHLCV history from GitHub")
    dd.add_argument("symbol", nargs="?", default="EURUSD")
    dd.add_argument("-t", "--timeframe", action="append", default=None,
                    help="timeframe to fetch (repeatable); default M15 H1 H4 D1")
    dd.add_argument("-o", "--out", default="data", help="output directory")
    dd.add_argument("--repo", help="override the source GitHub repository")
    dd.add_argument("--path-template", dest="path_template",
                    help="path inside the repo, e.g. 'fx/{symbol}_{tf}.csv'")
    dd.set_defaults(func=cmd_data)

    ds = dsub.add_parser("describe", help="quality report for local CSV files")
    ds.add_argument("paths", nargs="+")
    ds.set_defaults(func=cmd_data)

    # spreads
    sp = sub.add_parser("spreads",
                        help="summarise a recorded spread journal")
    sp.add_argument("path", nargs="?",
                    help="path to spreads.csv (default: beside the journal)")
    sp.add_argument("-c", "--config", help="config file to read storage.path from")
    sp.set_defaults(func=cmd_spreads)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 130
    except SystemExit:
        raise
    except Exception as exc:
        logging.getLogger("omega").exception("fatal: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
