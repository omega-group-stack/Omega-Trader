"""Experiment 6 -- the G10 carry portfolio, 2020-01 to 2023-09.

Pre-registered in research/PREREGISTRATION_6.md. Textbook construction:
rank eight currencies by the policy rate known at the end of the previous
month, go long the top three and short the bottom three, equal weight.

    .venv/bin/python research/exp6_carry.py
"""
from __future__ import annotations

import glob
import warnings
import math
import os

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", message=".*timezone.*")

# currency -> (pair, +1 if the pair is CCY/USD, -1 if it is USD/CCY)
LEGS = {
    "EUR": ("EURUSD", +1), "GBP": ("GBPUSD", +1),
    "AUD": ("AUDUSD", +1), "NZD": ("NZDUSD", +1),
    "CAD": ("USDCAD", -1), "CHF": ("USDCHF", -1), "JPY": ("USDJPY", -1),
}
N_SIDE = 3              # long the top three, short the bottom three
SPREAD_PIPS = 1.6       # round trip
COMMISSION_FRAC = 7e-5  # round trip


def pip(sym: str) -> float:
    return 0.01 if sym.endswith("JPY") else 0.0001


def monthly_closes() -> pd.DataFrame:
    out = {}
    for path in sorted(glob.glob("data/*_H1.csv")):
        sym = os.path.basename(path).split("_")[0]
        if sym not in {v[0] for v in LEGS.values()}:
            continue
        df = pd.read_csv(path)
        tcol = next(c for c in df.columns
                    if "time" in c.lower() or "date" in c.lower())
        df[tcol] = pd.to_datetime(df[tcol], utc=True, format="mixed")
        out[sym] = df.set_index(tcol)["close"].resample("ME").last()
    px = pd.DataFrame(out).dropna()
    px.index = px.index.to_period("M")
    return px


def main() -> int:
    px = monthly_closes()
    rates = pd.read_csv("data/rates/policy_rates.csv", index_col=0)
    rates.index = pd.PeriodIndex(rates.index, freq="M")

    common = px.index.intersection(rates.index)
    px, rates = px.loc[common], rates.loc[common]
    print(f"{len(common)} months: {common[0]} -> {common[-1]}")
    print(f"policy-rate dispersion: min {(rates.max(axis=1) - rates.min(axis=1)).min():.2f}%"
          f"  max {(rates.max(axis=1) - rates.min(axis=1)).max():.2f}%")

    # Spot excess return of each currency against USD.
    spot = pd.DataFrame(index=common, columns=list(LEGS) + ["USD"], dtype=float)
    for ccy, (sym, sign) in LEGS.items():
        r = px[sym].pct_change()
        spot[ccy] = r if sign > 0 else (1.0 / (1.0 + r) - 1.0)
    spot["USD"] = 0.0

    # Rate known at the end of the previous month drives both the ranking
    # and the accrual, so nothing uses a rate that was not yet published.
    lagged = rates.shift(1)
    accrual = lagged.sub(lagged["USD"], axis=0) / 100.0 / 12.0

    rows = []
    prev_w = pd.Series(0.0, index=spot.columns)
    for m in common[1:]:
        r_prev = lagged.loc[m]
        if r_prev.isna().any():
            continue
        order = r_prev.sort_values(ascending=False)
        longs, shorts = list(order.index[:N_SIDE]), list(order.index[-N_SIDE:])

        w = pd.Series(0.0, index=spot.columns)
        w[longs] = 1.0 / N_SIDE
        w[shorts] = -1.0 / N_SIDE

        carry_leg = float((w * accrual.loc[m].reindex(w.index).fillna(0.0)).sum())
        spot_leg = float((w * spot.loc[m].reindex(w.index).fillna(0.0)).sum())

        cost = 0.0
        for ccy, (sym, _) in LEGS.items():
            dw = abs(w[ccy] - prev_w[ccy])
            if dw:
                one_way = (SPREAD_PIPS * pip(sym) / px[sym].mean()
                           + COMMISSION_FRAC) / 2.0
                cost += dw * one_way
        prev_w = w
        rows.append({"month": m, "carry": carry_leg, "spot": spot_leg,
                     "cost": cost, "net": carry_leg + spot_leg - cost,
                     "longs": "/".join(longs), "shorts": "/".join(shorts)})

    df = pd.DataFrame(rows).set_index("month")
    n = len(df)
    print(f"\ntypical book: long {df['longs'].mode()[0]}  short {df['shorts'].mode()[0]}")

    def report(s: pd.Series, label: str):
        m, sd = s.mean(), s.std(ddof=1)
        sharpe = m / sd * math.sqrt(12) if sd else float("nan")
        t = m / (sd / math.sqrt(len(s))) if sd else float("nan")
        print(f"  {label:26s} {m * 1200:+7.2f}%/yr  Sharpe {sharpe:+6.3f}  t {t:+5.2f}")
        return sharpe, t

    print("\n--- decomposition (descriptive) ---")
    report(df["carry"], "interest accrual")
    report(df["spot"], "spot move")
    print(f"  {'costs':26s} {-df['cost'].mean() * 1200:+7.2f}%/yr")
    print("\n--- the portfolio ---")
    report(df["carry"] + df["spot"], "gross")
    sharpe, t = report(df["net"], "NET of retail costs")

    curve = (1 + df["net"]).cumprod()
    dd = (curve / curve.cummax() - 1).min()
    print(f"\n  max drawdown   : {dd * 100:.2f}%")
    print(f"  worst month    : {df['net'].min() * 100:+.2f}%  ({df['net'].idxmin()})")
    print(f"  best month     : {df['net'].max() * 100:+.2f}%  ({df['net'].idxmax()})")
    print(f"  positive months: {(df['net'] > 0).sum()} of {n}")

    sub = df.loc[df.index >= pd.Period("2022-01", "M"), "net"]
    print(f"\n--- 2022-01 onward, max dispersion (descriptive, {len(sub)} months) ---")
    report(sub, "net")

    # ---- is the instrument working? ------------------------------------ #
    # A null result from a broken engine is worthless (see experiment 5).
    def book_sharpe(pick, seed=None):
        rng = np.random.default_rng(seed)
        out = []
        for m in common[1:]:
            rp = lagged.loc[m]
            if rp.isna().any():
                continue
            w = pd.Series(0.0, index=spot.columns)
            lg, sh = pick(rp, rng)
            w[lg], w[sh] = 1.0 / N_SIDE, -1.0 / N_SIDE
            out.append(float((w * accrual.loc[m].reindex(w.index).fillna(0)).sum()
                             + (w * spot.loc[m].reindex(w.index).fillna(0)).sum()))
        ser = pd.Series(out)
        return ser.mean() / ser.std() * math.sqrt(12)

    def _hi(r, _):
        o = r.sort_values(ascending=False)
        return list(o.index[:N_SIDE]), list(o.index[-N_SIDE:])

    def _lo(r, _):
        lg, sh = _hi(r, None)
        return sh, lg

    def _rand(r, g):
        p = g.permutation(list(r.index))
        return list(p[:N_SIDE]), list(p[N_SIDE:2 * N_SIDE])

    print("\n--- engine validation ---")
    print(f"  {'long high / short low':26s} {book_sharpe(_hi):+6.3f}")
    print(f"  {'inverted (must mirror)':26s} {book_sharpe(_lo):+6.3f}")
    print(f"  {'random books (should be ~0)':26s} "
          f"{np.mean([book_sharpe(_rand, s) for s in range(40)]):+6.3f}")

    # ---- what it would take to earn a real return ----------------------- #
    mu = df["net"].mean()
    se = df["net"].std(ddof=1) / math.sqrt(n)
    print(f"\n  95% CI on the annual return: "
          f"[{(mu - 1.96 * se) * 1200:+.2f}%, {(mu + 1.96 * se) * 1200:+.2f}%]")
    print("\n  the leverage question (this sample contains no carry crash,")
    print("  so every drawdown below is optimistic):")
    for target in (5, 10, 20):
        lev = target / (mu * 1200)
        print(f"    {target:2d}%/yr needs {lev:4.1f}x  ->  max drawdown "
              f"{dd * 100 * lev:7.1f}%, worst month "
              f"{df['net'].min() * 100 * lev:6.1f}%")

    print("\n=== pre-registered verdict (one-sided) ===")
    print(f"  Sharpe >= 0.50 : {sharpe:+.3f} -> {sharpe >= 0.50}")
    print(f"  t >= 1.68      : {t:+.2f} -> {t >= 1.68}")
    if sharpe >= 0.50 and t >= 1.68:
        print("\n  SUCCESS -- the first pulse in six experiments.")
        print("  This means 'find 20 years of rate data', NOT 'turn the bot on'.")
    else:
        print("\n  FAILURE by the pre-registered rule.")
        print(f"  (Underpowered by design: {n} months needs Sharpe ~0.92 for t=1.68.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
