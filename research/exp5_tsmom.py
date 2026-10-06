"""Experiment 5 -- time-series momentum on the FX basket, daily, monthly rebalanced.

Pre-registered in research/PREREGISTRATION_5.md. Canonical parameters only:
12-month signal, 60-day volatility estimate, monthly rebalance. No grid, no
optimiser, no walk-forward -- the whole point of testing a documented anomaly
is that its parameters come from outside this project.

    .venv/bin/python research/exp5_tsmom.py
"""
from __future__ import annotations

import glob
import math
import os
import statistics as st

import numpy as np
import pandas as pd

LOOKBACK_D = 252       # 12 months
VOL_WINDOW = 60        # days
TARGET_VOL = 0.10      # annualised, portfolio level
SPREAD_PIPS = 1.6      # round trip, retail ECN
COMMISSION_FRAC = 7e-5 # $7 per 100k lot, round trip
EXEC_LAG_DAYS = 2      # signal at close t -> earliest P&L from t+2


def pip_size(symbol: str) -> float:
    return 0.01 if symbol.endswith("JPY") else 0.0001


def daily_closes() -> pd.DataFrame:
    """Resample every H1 file to a daily close panel, UTC day boundaries."""
    series = {}
    for path in sorted(glob.glob("data/*_H1.csv")):
        sym = os.path.basename(path).split("_")[0]
        df = pd.read_csv(path)
        tcol = next(c for c in df.columns
                    if "time" in c.lower() or "date" in c.lower())
        df[tcol] = pd.to_datetime(df[tcol], utc=True, format="mixed")
        s = df.set_index(tcol)["close"].resample("1D").last().dropna()
        if len(s) > 3000:
            series[sym] = s
    panel = pd.DataFrame(series).sort_index()
    # A day on which a pair did not print is a market holiday, not a gap in
    # the position: carry the last price rather than dropping the row.
    return panel.ffill()


def build(panel: pd.DataFrame):
    """Return (net daily portfolio returns, gross, turnover) -- no look-ahead."""
    rets = panel.pct_change()
    # Ex-ante volatility: only data up to and including the signal date.
    vol = rets.rolling(VOL_WINDOW).std() * math.sqrt(252)
    signal = np.sign(panel / panel.shift(LOOKBACK_D) - 1.0)

    # Inverse-volatility weights, equal risk per pair.
    n = panel.shape[1]
    raw_w = signal * (TARGET_VOL / vol) / n
    raw_w = raw_w.replace([np.inf, -np.inf], np.nan)

    # Hold the month-end weight until the next month end.
    month_end = panel.index.to_series().groupby(
        [panel.index.year, panel.index.month]).transform("max") == panel.index
    held = raw_w.where(month_end).ffill()

    # Execution lag: a weight decided at close t cannot earn before t+2.
    held = held.shift(EXEC_LAG_DAYS)
    valid = held.notna().all(axis=1)
    held, rets_v = held[valid], rets[valid]

    gross = (held * rets_v).sum(axis=1)

    # Costs on turnover, charged the day the trade happens. One-way cost is
    # half the quoted round trip.
    one_way = pd.Series(
        {s: (SPREAD_PIPS * pip_size(s) / panel[s].mean() + COMMISSION_FRAC) / 2.0
         for s in panel.columns}
    )
    turnover = held.diff().abs().fillna(0.0)
    cost = (turnover * one_way).sum(axis=1)
    return gross - cost, gross, turnover.sum(axis=1), held


def stats(daily: pd.Series, label: str):
    monthly = (1 + daily).resample("ME").prod() - 1
    monthly = monthly[monthly != 0]
    n = len(monthly)
    m, s = monthly.mean(), monthly.std(ddof=1)
    sharpe = (m / s) * math.sqrt(12) if s > 0 else float("nan")
    t = m / (s / math.sqrt(n)) if s > 0 else float("nan")
    curve = (1 + daily).cumprod()
    dd = (curve / curve.cummax() - 1).min()
    ann = curve.iloc[-1] ** (252 / len(daily)) - 1
    print(f"\n--- {label} ---")
    print(f"  months            : {n}  ({monthly.index[0]:%Y-%m} -> "
          f"{monthly.index[-1]:%Y-%m})")
    print(f"  annualised return : {ann * 100:+.2f}%")
    print(f"  annualised vol    : {daily.std() * math.sqrt(252) * 100:.2f}%")
    print(f"  Sharpe            : {sharpe:+.3f}")
    print(f"  t-stat of mean    : {t:+.2f}")
    print(f"  max drawdown      : {dd * 100:.2f}%")
    return monthly, sharpe, t


def main() -> int:
    panel = daily_closes()
    print(f"{panel.shape[1]} pairs, {panel.shape[0]} daily bars "
          f"{panel.index[0]:%Y-%m-%d} -> {panel.index[-1]:%Y-%m-%d}")
    print(f"pairs: {', '.join(panel.columns)}")

    net, gross, turnover, held = build(panel)

    print(f"\nmean monthly turnover (sum |dw|): {turnover.sum() / (len(net) / 21):.4f}")
    lev = held.abs().sum(axis=1).mean()
    print(f"mean gross exposure: {lev:.2f}x notional")

    stats(gross, "GROSS (no costs)")
    monthly, sharpe, t = stats(net, "NET of retail costs")

    # Pre-registered split-half.
    half = len(monthly) // 2
    h1, h2 = monthly.iloc[:half], monthly.iloc[half:]
    print("\n--- split halves (net) ---")
    print(f"  {h1.index[0]:%Y-%m}..{h1.index[-1]:%Y-%m}: "
          f"mean {h1.mean() * 100:+.3f}%/mo  Sharpe "
          f"{h1.mean() / h1.std() * math.sqrt(12):+.3f}")
    print(f"  {h2.index[0]:%Y-%m}..{h2.index[-1]:%Y-%m}: "
          f"mean {h2.mean() * 100:+.3f}%/mo  Sharpe "
          f"{h2.mean() / h2.std() * math.sqrt(12):+.3f}")

    print("\n=== pre-registered verdict (Bonferroni: |t| >= 2.24) ===")
    ok_sharpe, ok_t = sharpe >= 0.40, t >= 2.24
    ok_halves = h1.mean() > 0 and h2.mean() > 0
    print(f"  net Sharpe >= 0.40 : {sharpe:+.3f}  -> {ok_sharpe}")
    print(f"  t >= 2.24          : {t:+.2f}  -> {ok_t}")
    print(f"  both halves > 0    : {ok_halves}")
    if ok_sharpe and ok_t and ok_halves:
        print("\n  SUCCESS -- worth paper-trading next.")
    elif ok_sharpe and ok_t:
        print("\n  WEAK -- depends on which years you got. Not tradeable.")
    else:
        print("\n  FAILURE by the pre-registered rule.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
