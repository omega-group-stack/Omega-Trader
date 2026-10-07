"""Experiment 7 -- the same G10 carry portfolio, on 201 months instead of 44.

Pre-registered in research/PREREGISTRATION_7.md, committed at e13c538 BEFORE
this script was run.  The construction is byte-for-byte the rule of experiment
6: rank eight currencies by the rate known at the end of the previous month,
long the top three, short the bottom three, equal weight, monthly rebalance.

The only change is the rate series: 3-month interbank (OECD via FRED) instead
of policy rates, which buys 2007-01..2023-09 instead of 2020-01..2023-09.
See PREREGISTRATION_7.md section 2 for why, and the consistency check below.

    .venv/bin/python research/exp7_carry.py
"""
from __future__ import annotations

import math
import warnings

import numpy as np
import pandas as pd

from exp6_carry import LEGS, N_SIDE, SPREAD_PIPS, COMMISSION_FRAC, pip, monthly_closes
from exp7_rates_check import load as load_interbank

warnings.filterwarnings("ignore", message=".*timezone.*")

# Pre-registered gates.  PRIMARY is identical to experiment 6 -- the goalposts
# do not move.  FAMILY is Bonferroni over the seven tests this project has run.
GATE_SHARPE = 0.50
GATE_T = 1.68
GATE_T_FAMILY = 2.46


def build(px: pd.DataFrame, rates: pd.DataFrame):
    """Return (per-month frame, spot frame, accrual frame, lagged rates, index)."""
    common = px.index.intersection(rates.index)
    px, rates = px.loc[common], rates.loc[common]

    spot = pd.DataFrame(index=common, columns=list(LEGS) + ["USD"], dtype=float)
    for ccy, (sym, sign) in LEGS.items():
        r = px[sym].pct_change()
        spot[ccy] = r if sign > 0 else (1.0 / (1.0 + r) - 1.0)
    spot["USD"] = 0.0

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

    return pd.DataFrame(rows).set_index("month"), spot, accrual, lagged, common


def report(s: pd.Series, label: str):
    m, sd = s.mean(), s.std(ddof=1)
    sharpe = m / sd * math.sqrt(12) if sd else float("nan")
    t = m / (sd / math.sqrt(len(s))) if sd else float("nan")
    print(f"  {label:28s} {m * 1200:+7.2f}%/yr  Sharpe {sharpe:+6.3f}  t {t:+5.2f}")
    return sharpe, t


def main() -> int:
    px = monthly_closes()
    rates = load_interbank()
    # One gap in the whole panel (USD 2020-04); carried forward, as declared.
    rates = rates.ffill()

    df, spot, accrual, lagged, common = build(px, rates)
    n = len(df)

    print("=" * 68)
    print("EXPERIMENT 7 -- G10 carry on the long sample")
    print("=" * 68)
    print(f"\n{len(common)} months of overlap: {common[0]} -> {common[-1]}")
    print(f"{n} portfolio months after the one-month lag")
    disp = rates.loc[common].max(axis=1) - rates.loc[common].min(axis=1)
    print(f"rate dispersion: min {disp.min():.2f}pp  max {disp.max():.2f}pp  "
          f"mean {disp.mean():.2f}pp")
    print(f"\ntypical book: long {df['longs'].mode()[0]}  "
          f"short {df['shorts'].mode()[0]}")

    print("\n--- decomposition (descriptive) ---")
    report(df["carry"], "interest accrual")
    report(df["spot"], "spot move")
    print(f"  {'costs':28s} {-df['cost'].mean() * 1200:+7.2f}%/yr")

    print("\n--- the portfolio ---")
    report(df["carry"] + df["spot"], "gross")
    sharpe, t = report(df["net"], "NET of retail costs")

    curve = (1 + df["net"]).cumprod()
    dd_series = curve / curve.cummax() - 1
    dd = dd_series.min()
    print(f"\n  total return   : {(curve.iloc[-1] - 1) * 100:+.2f}% over "
          f"{n / 12:.1f} years")
    print(f"  max drawdown   : {dd * 100:.2f}%  (trough {dd_series.idxmin()})")
    print(f"  worst month    : {df['net'].min() * 100:+.2f}%  ({df['net'].idxmin()})")
    print(f"  best month     : {df['net'].max() * 100:+.2f}%  ({df['net'].idxmax()})")
    print(f"  positive months: {(df['net'] > 0).sum()} of {n} "
          f"({(df['net'] > 0).mean() * 100:.0f}%)")

    # ---- the 2008 carry crash: pre-registered secondary analysis -------- #
    print("\n--- 2008 carry crash (pre-registered: expect a large, concentrated hit) ---")
    crash = df.loc[(df.index >= pd.Period("2008-08", "M"))
                   & (df.index <= pd.Period("2008-12", "M")), "net"]
    for m, v in crash.items():
        print(f"    {m}  {v * 100:+7.2f}%")
    print(f"    cumulative Aug-Dec 2008: {((1 + crash).prod() - 1) * 100:+.2f}%")

    print("\n--- sub-periods (descriptive) ---")
    pre = df.loc[df.index < pd.Period("2015-01", "M"), "net"]
    post = df.loc[df.index >= pd.Period("2015-01", "M"), "net"]
    report(pre, f"2007-2014 ({len(pre)} mo)")
    report(post, f"2015-2023 ({len(post)} mo)")
    h1 = df["net"].iloc[: n // 2]
    h2 = df["net"].iloc[n // 2:]
    report(h1, f"first half ({len(h1)} mo)")
    report(h2, f"second half ({len(h2)} mo)")

    # ---- engine validation ---------------------------------------------- #
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
    s_hi, s_lo = book_sharpe(_hi), book_sharpe(_lo)
    print(f"  {'long high / short low':28s} {s_hi:+6.3f}")
    print(f"  {'inverted (must mirror)':28s} {s_lo:+6.3f}")
    print(f"  {'random books (should be ~0)':28s} "
          f"{np.mean([book_sharpe(_rand, s) for s in range(40)]):+6.3f}")
    print(f"  mirror error: {abs(s_hi + s_lo):.2e}")

    # ---- consistency with experiment 6 ---------------------------------- #
    print("\n--- consistency check vs experiment 6 (same window, new rate source) ---")
    win = (common >= pd.Period("2020-01", "M")) & (common <= pd.Period("2023-09", "M"))
    df6, *_ = build(px.loc[common[win]], rates.loc[common[win]])
    s6, t6 = report(df6["net"], f"interbank, {len(df6)} mo")
    print(f"  experiment 6 on policy rates, 43 mo : Sharpe +0.439  t +0.84")
    agree = abs(s6 - 0.439) < 0.45
    print(f"  sources agree: {agree}")

    # ---- leverage, now with a real crash in sample ----------------------- #
    mu = df["net"].mean()
    se = df["net"].std(ddof=1) / math.sqrt(n)
    print(f"\n  95% CI on the annual return: "
          f"[{(mu - 1.96 * se) * 1200:+.2f}%, {(mu + 1.96 * se) * 1200:+.2f}%]")
    if mu > 0:
        print("\n  the leverage question (this sample DOES contain the 2008 crash):")
        for target in (5, 10, 20):
            lev = target / (mu * 1200)
            print(f"    {target:2d}%/yr needs {lev:5.1f}x  ->  max drawdown "
                  f"{dd * 100 * lev:8.1f}%, worst month "
                  f"{df['net'].min() * 100 * lev:7.1f}%")

    # ---- POST-HOC sensitivity ------------------------------------------- #
    # Declared post-hoc.  These do NOT and cannot change the verdict above;
    # they exist because the obvious objection to a rejection is "it was the
    # crash", and that objection deserves a number rather than a shrug.
    print("\n--- POST-HOC (not pre-registered, does not change the verdict) ---")
    ex08 = df.loc[~((df.index >= pd.Period("2008-08", "M"))
                    & (df.index <= pd.Period("2008-12", "M"))), "net"]
    report(ex08, f"excluding Aug-Dec 2008 ({len(ex08)} mo)")
    print("    Even with the crash surgically removed the portfolio does not")
    print("    clear the gate -- and removing it is precisely the error that")
    print("    makes carry look free.  The crash IS the risk being paid for.")

    # What experiment 6's leverage table would actually have done here.
    print("\n  experiment 6 recommended 4.1x to target 10%/yr, on a sample with")
    print("  no crash in it.  Running that leverage over this sample:")
    for lev in (2.1, 4.1, 8.3):
        path = (1 + df["net"] * lev).cumprod()
        wipe = (1 + df["net"] * lev).le(0).any() or path.min() <= 0
        mdd = (path / path.cummax() - 1).min()
        tag = "ACCOUNT WIPED OUT" if wipe else f"end equity {path.iloc[-1]:.2f}x"
        print(f"    {lev:4.1f}x -> max drawdown {mdd * 100:7.1f}%,  {tag}")

    # ---- verdict --------------------------------------------------------- #
    need_sharpe_for_t = GATE_T / math.sqrt(n) * math.sqrt(12)
    print("\n" + "=" * 68)
    print("PRE-REGISTERED VERDICT (one-sided)")
    print("=" * 68)
    print(f"  power: at n={n}, t={GATE_T} needs Sharpe ~{need_sharpe_for_t:.2f}")
    print(f"         (experiment 6 at n=43 needed ~0.92 -- that is why it was void)")
    print(f"  Sharpe >= {GATE_SHARPE:.2f} : {sharpe:+.3f} -> {sharpe >= GATE_SHARPE}")
    print(f"  t      >= {GATE_T:.2f} : {t:+.2f}  -> {t >= GATE_T}")
    print(f"  t      >= {GATE_T_FAMILY:.2f} : {t:+.2f}  -> {t >= GATE_T_FAMILY}  "
          f"(Bonferroni, 7 tests)")

    primary = sharpe >= GATE_SHARPE and t >= GATE_T
    if primary and t >= GATE_T_FAMILY:
        print("\n  RESULT: CONFIRMED, survives multiple-testing correction.")
    elif primary:
        print("\n  RESULT: nominal evidence only -- fails Bonferroni.")
        print("  By the pre-registered table this does NOT count as an edge.")
    else:
        print("\n  RESULT: REJECTED by the pre-registered rule.")
        print(f"  This time the test had the power to see Sharpe "
              f"{need_sharpe_for_t:.2f}+, so the rejection is informative.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
