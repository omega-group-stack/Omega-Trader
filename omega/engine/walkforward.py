"""Walk-forward analysis — the only backtest number worth believing.

A single backtest over the whole history tells you almost nothing once you
have touched the parameters, because you chose those parameters while looking
at that history. Walk-forward analysis fixes the leak structurally:

    |<-- in-sample (optimise) -->|<-- out-of-sample (measure) -->|
                                 |<-- in-sample ------------->|<-- OOS -->|
                                                               ...

Parameters are fitted on the in-sample window and then *frozen* and applied to
the following out-of-sample window, which the optimiser has never seen. Only
the stitched OOS segments are reported. The account balance is carried from
one OOS segment into the next, so the reported curve is what the account would
actually have done if you had re-optimised on that schedule.

Two numbers matter more than the headline return:

``efficiency``
    median(OOS return) / median(IS return). Above ~0.5 the edge survives
    contact with unseen data; near zero or negative, the "edge" was curve fit.

``consistency``
    fraction of folds with a positive OOS return. With a real edge this sits
    well above 0.5; a coin flip is the null hypothesis.
"""

from __future__ import annotations

import logging
import math
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from datetime import timedelta
from itertools import product
from typing import Dict, List, Optional, Sequence, Tuple

import pandas as pd

from ..config import AppConfig
from ..core.types import Trade
from ..data.timeframes import minutes as tf_minutes
from . import metrics
from .backtester import Backtester

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Results
# --------------------------------------------------------------------------- #
@dataclass
class Fold:
    """One in-sample / out-of-sample pair."""

    index: int
    is_start: pd.Timestamp
    is_end: pd.Timestamp
    oos_start: pd.Timestamp
    oos_end: pd.Timestamp
    params: Dict[str, float] = field(default_factory=dict)
    is_return_pct: float = 0.0
    is_fitness: float = float("-inf")
    oos_return_pct: float = 0.0
    oos_trades: int = 0
    oos_profit_factor: float = 0.0
    oos_expectancy_r: float = 0.0
    oos_max_dd_pct: float = 0.0
    start_balance: float = 0.0
    end_balance: float = 0.0

    def to_dict(self) -> dict:
        return {
            "fold": self.index,
            "is_start": self.is_start.date().isoformat(),
            "is_end": self.is_end.date().isoformat(),
            "oos_start": self.oos_start.date().isoformat(),
            "oos_end": self.oos_end.date().isoformat(),
            "params": dict(self.params),
            "is_return_pct": round(self.is_return_pct, 2),
            "oos_return_pct": round(self.oos_return_pct, 2),
            "oos_trades": self.oos_trades,
            "oos_profit_factor": round(self.oos_profit_factor, 3),
            "oos_expectancy_r": round(self.oos_expectancy_r, 4),
            "oos_max_dd_pct": round(self.oos_max_dd_pct, 2),
            "start_balance": round(self.start_balance, 2),
            "end_balance": round(self.end_balance, 2),
        }


@dataclass
class WalkForwardResult:
    folds: List[Fold] = field(default_factory=list)
    trades: List[Trade] = field(default_factory=list)
    equity_curve: List[tuple] = field(default_factory=list)
    performance: Optional[metrics.Performance] = None
    initial_balance: float = 0.0

    # ---- headline diagnostics ------------------------------------------- #
    @property
    def efficiency(self) -> float:
        """median OOS return / median IS return (walk-forward efficiency)."""
        if not self.folds:
            return 0.0
        is_med = _median([f.is_return_pct for f in self.folds])
        oos_med = _median([f.oos_return_pct for f in self.folds])
        if abs(is_med) < 1e-9:
            return 0.0
        return oos_med / is_med

    @property
    def consistency(self) -> float:
        """Fraction of folds whose out-of-sample return was positive."""
        if not self.folds:
            return 0.0
        return sum(1 for f in self.folds if f.oos_return_pct > 0) / len(self.folds)

    @property
    def parameter_stability(self) -> Dict[str, float]:
        """Per-parameter coefficient of variation of the chosen values.

        Low numbers mean the optimiser keeps landing in the same place, which
        is a sign the parameter surface has a real plateau rather than noise
        spikes. Values above ~0.5 mean the "best" setting is unstable and the
        parameter should probably be fixed by reasoning, not by search.
        """
        out: Dict[str, float] = {}
        if not self.folds:
            return out
        for key in self.folds[0].params:
            values = [float(f.params[key]) for f in self.folds]
            mean = sum(values) / len(values)
            if abs(mean) < 1e-12:
                out[key] = 0.0
                continue
            var = sum((v - mean) ** 2 for v in values) / len(values)
            out[key] = math.sqrt(var) / abs(mean)
        return out

    def to_dict(self) -> dict:
        return {
            "efficiency": round(self.efficiency, 3),
            "consistency": round(self.consistency, 3),
            "parameter_stability": {k: round(v, 3)
                                    for k, v in self.parameter_stability.items()},
            "folds": [f.to_dict() for f in self.folds],
            "performance": self.performance.to_dict() if self.performance else {},
        }

    def summary(self) -> str:
        width = 78
        lines = ["", "=" * width, "WALK-FORWARD ANALYSIS — out-of-sample only".center(width),
                 "=" * width]
        header = (f"{'#':>2}  {'in-sample':<21}  {'out-of-sample':<21}  "
                  f"{'IS%':>7}  {'OOS%':>7}  {'trd':>4}  {'PF':>5}")
        lines.append(header)
        lines.append("-" * width)
        for f in self.folds:
            lines.append(
                f"{f.index:>2}  "
                f"{f.is_start.date()}→{f.is_end.date()}  "
                f"{f.oos_start.date()}→{f.oos_end.date()}  "
                f"{f.is_return_pct:>+7.2f}  {f.oos_return_pct:>+7.2f}  "
                f"{f.oos_trades:>4}  {f.oos_profit_factor:>5.2f}"
            )
        lines.append("-" * width)

        perf = self.performance
        if perf:
            lines.append(f"Stitched OOS    : {self.initial_balance:,.2f} -> "
                         f"{perf.final_balance:,.2f}  "
                         f"({perf.return_pct:+.2f}% over {perf.days} days)")
            lines.append(f"CAGR / MaxDD    : {perf.cagr_pct:+.2f}% / {perf.max_drawdown_pct:.2f}%")
            lines.append(f"Sharpe / PF     : {perf.sharpe:.2f} / {perf.profit_factor:.2f}")
            lines.append(f"Trades / Expect.: {perf.trades} / "
                         f"{perf.expectancy_r:+.4f}R")
        lines.append(f"Efficiency      : {self.efficiency:+.2f}   "
                     "(median OOS return / median IS return; >0.5 is healthy)")
        lines.append(f"Consistency     : {self.consistency:.0%} of folds profitable "
                     "out-of-sample")
        stab = self.parameter_stability
        if stab:
            lines.append("Param stability : " + ", ".join(
                f"{k.split('.')[-1]}={v:.2f}" for k, v in stab.items())
                + "   (coefficient of variation; <0.3 = stable)")
        lines.append("=" * width)
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Fitness
# --------------------------------------------------------------------------- #
def fitness(perf: metrics.Performance, min_trades: int = 20) -> float:
    """Score an in-sample run. Deliberately penalises thin, lucky samples.

    Return alone picks the fold that got lucky once with a huge position.
    This blends risk-adjusted return, consistency and sample size so the
    in-sample winner has a chance of still working next year.
    """
    if perf.trades < min_trades:
        return -999.0
    dd = max(perf.max_drawdown_pct, 1.0)
    return (
        (perf.return_pct / dd)
        + 0.5 * perf.sharpe
        + 2.0 * perf.expectancy_r
        + 0.5 * min(1.0, perf.trades / (min_trades * 3))
    )


# --------------------------------------------------------------------------- #
# Worker
# --------------------------------------------------------------------------- #
def _run_window(payload: Tuple[dict, Dict[str, float], str, str, float]) -> dict:
    """Run one backtest in a worker process. Must be top-level for pickling."""
    base, params, start, end, balance = payload
    # Workers are forked and inherit the parent's log handlers, which turns a
    # 78-run grid into tens of thousands of duplicated risk warnings.
    logging.getLogger("omega").setLevel(logging.ERROR)
    cfg = AppConfig.from_dict(base)
    for key, value in params.items():
        cfg.set(key, value)
    cfg.data.start = start
    cfg.data.end = end
    cfg.account.initial_balance = balance
    cfg.logging.level = "ERROR"
    try:
        result = Backtester(cfg).run()
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("window %s..%s failed: %s", start, end, exc)
        return {"ok": False, "error": str(exc)}
    perf = result.performance
    return {
        "ok": True,
        "fitness": fitness(perf),
        "return_pct": perf.return_pct,
        "final_balance": perf.final_balance,
        "trades": perf.trades,
        "profit_factor": perf.profit_factor,
        "expectancy_r": perf.expectancy_r,
        "max_drawdown_pct": perf.max_drawdown_pct,
        "trade_objects": result.trades,
        "equity_curve": [(t.isoformat(), e, b) for t, e, b in result.equity_curve],
    }


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #
def walk_forward(
    cfg: AppConfig,
    grid: Dict[str, Sequence[float]],
    is_months: int = 36,
    oos_months: int = 12,
    step_months: Optional[int] = None,
    workers: int = 2,
    progress: bool = True,
    checkpoint: Optional[str] = None,
) -> WalkForwardResult:
    """Run a rolling walk-forward study.

    ``grid`` maps dotted config keys to the candidate values to try, e.g.
    ``{"strategy.entry_threshold": [0.3, 0.4, 0.5]}``. Keep it small: every
    extra axis multiplies the number of in-sample fits and, worse, increases
    the chance that the in-sample winner is noise.
    """
    step_months = step_months or oos_months
    symbol = cfg.active_symbols[0].name.upper()

    # Discover the available date range without running anything.
    probe = Backtester(cfg)
    frame = probe.feed.candles(symbol, cfg.strategy.timeframe, cfg.data.history_bars)
    if cfg.data.start:
        frame = frame[frame.index >= pd.Timestamp(cfg.data.start, tz="UTC")]
    if cfg.data.end:
        frame = frame[frame.index <= pd.Timestamp(cfg.data.end, tz="UTC")]
    if frame.empty:
        raise ValueError(f"No data for {symbol} {cfg.strategy.timeframe}")

    first, last = frame.index[0], frame.index[-1]

    # Warm-up must be *fed* to the strategy but not traded. Loading this much
    # extra history before each window start means the indicators are already
    # settled when the window's first tradable bar arrives.
    bar_minutes = tf_minutes(cfg.strategy.timeframe)
    warmup = timedelta(minutes=bar_minutes * cfg.strategy.warmup_bars * 1.9)

    base = cfg.to_dict()
    combos = [dict(zip(grid.keys(), values))
              for values in product(*grid.values())] or [{}]

    # ---- lay out the folds ---------------------------------------------- #
    windows: List[Tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp, pd.Timestamp]] = []
    is_start = first + warmup
    while True:
        is_end = is_start + pd.DateOffset(months=is_months)
        oos_end = is_end + pd.DateOffset(months=oos_months)
        if oos_end > last:
            break
        windows.append((is_start, is_end, is_end, min(oos_end, last)))
        is_start = is_start + pd.DateOffset(months=step_months)

    if not windows:
        raise ValueError(
            f"History is too short: {first.date()}..{last.date()} cannot hold a "
            f"{is_months}-month in-sample plus {oos_months}-month out-of-sample window."
        )

    log.info("Walk-forward: %d folds x %d parameter combinations = %d in-sample runs",
             len(windows), len(combos), len(windows) * len(combos))

    out = WalkForwardResult(initial_balance=cfg.account.initial_balance)
    balance = cfg.account.initial_balance
    equity: List[tuple] = []

    executor = ProcessPoolExecutor(max_workers=workers) if workers > 1 else None
    try:
        for n, (is_s, is_e, oos_s, oos_e) in enumerate(windows, start=1):
            fold = Fold(index=n, is_start=is_s, is_end=is_e,
                        oos_start=oos_s, oos_end=oos_e, start_balance=balance)

            # ---- in-sample optimisation ---------------------------------- #
            jobs = [(base, combo, (is_s - warmup).isoformat(), is_e.isoformat(),
                     cfg.account.initial_balance) for combo in combos]
            if executor is not None:
                results = list(executor.map(_run_window, jobs))
            else:
                results = [_run_window(job) for job in jobs]

            best_i, best = -1, None
            for i, res in enumerate(results):
                if not res.get("ok"):
                    continue
                if best is None or res["fitness"] > best["fitness"]:
                    best_i, best = i, res

            if best is None:
                log.warning("Fold %d: no in-sample run produced enough trades; "
                            "falling back to the baseline parameters", n)
                fold.params = dict(combos[0])
                fold.is_fitness = float("-inf")
            else:
                fold.params = dict(combos[best_i])
                fold.is_fitness = best["fitness"]
                fold.is_return_pct = best["return_pct"]

            # ---- out-of-sample, parameters frozen ------------------------ #
            oos = _run_window((base, fold.params, (oos_s - warmup).isoformat(),
                               oos_e.isoformat(), balance))
            if oos.get("ok"):
                fold.oos_return_pct = oos["return_pct"]
                fold.oos_trades = oos["trades"]
                fold.oos_profit_factor = oos["profit_factor"]
                fold.oos_expectancy_r = oos["expectancy_r"]
                fold.oos_max_dd_pct = oos["max_drawdown_pct"]
                fold.end_balance = oos["final_balance"]
                balance = oos["final_balance"]
                out.trades.extend(oos["trade_objects"])
                for ts, eq, bal in oos["equity_curve"]:
                    equity.append((pd.Timestamp(ts), eq, bal))
            else:
                fold.end_balance = balance

            out.folds.append(fold)
            if checkpoint:
                _checkpoint(out, checkpoint)
            if progress:
                log.info("Fold %d/%d  IS %s→%s %+.2f%%   OOS %s→%s %+.2f%% "
                         "(%d trades)   balance %.0f",
                         n, len(windows), is_s.date(), is_e.date(),
                         fold.is_return_pct, oos_s.date(), oos_e.date(),
                         fold.oos_return_pct, fold.oos_trades, balance)
    finally:
        if executor is not None:
            executor.shutdown(wait=True)

    equity.sort(key=lambda row: row[0])
    out.equity_curve = equity
    out.performance = metrics.analyse(equity, out.trades, out.initial_balance)
    return out


def _checkpoint(result: WalkForwardResult, path: str) -> None:
    """Persist fold-by-fold progress. A 25-minute study should survive a typo."""
    import json
    from pathlib import Path as _Path

    try:
        target = _Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(
            {"folds": [f.to_dict() for f in result.folds],
             "efficiency": round(result.efficiency, 3),
             "consistency": round(result.consistency, 3)},
            indent=2), encoding="utf-8")
    except Exception as exc:  # pragma: no cover
        log.debug("checkpoint failed: %s", exc)


def _median(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2
