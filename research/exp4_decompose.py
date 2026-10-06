"""Experiment 4 -- decompose the money-at-risk / outcome relationship.

Pre-registered in research/PREREGISTRATION_4.md. Run after the experiment-3
confirmation panel exists:

    LB=24 AG=0.6 bash research/exp3_confirm.sh
    .venv/bin/python research/exp4_decompose.py

Reads only the unfiltered ("off") arm, which is the default configuration.
"""
from __future__ import annotations

import json
import math
import statistics as st
from pathlib import Path

PAIRS = ["GBPUSD", "AUDUSD", "NZDUSD", "USDCAD", "USDCHF", "USDJPY"]
DIR = Path("research/exp3")


def load(pair: str) -> list[dict]:
    """Trades with the two derived quantities the experiment needs."""
    raw = json.loads((DIR / f"{pair}_off.json").read_text())["trades"]
    out = []
    for t in raw:
        r = t.get("r_multiple")
        pnl = t.get("pnl")
        bal = t.get("balance_after")
        if not r or pnl is None or bal is None:
            continue
        risk = abs(pnl / r)
        equity = bal - pnl          # balance immediately before the close
        if risk <= 0 or equity <= 0:
            continue
        out.append({
            "pair": pair,
            "r": float(r),
            "pnl": float(pnl),
            "risk": risk,
            "equity": equity,
            "f": risk / equity,     # realised risk fraction
            "year": int(t["open_time"][:4]),
        })
    return out


def mean(xs):
    return sum(xs) / len(xs) if xs else float("nan")


def ttest_1samp(xs, mu=0.0):
    """t and two-sided p for a small sample -- used across the 6 pair clusters."""
    n = len(xs)
    if n < 2:
        return float("nan"), float("nan")
    sd = st.stdev(xs)
    if sd == 0:
        return float("inf"), 0.0
    t = (mean(xs) - mu) / (sd / math.sqrt(n))
    # Student-t survival via a continued-fraction incomplete beta.
    df = n - 1
    x = df / (df + t * t)
    p = _betainc(df / 2.0, 0.5, x)
    return t, p


def _betainc(a, b, x):
    """Regularised incomplete beta I_x(a,b); enough precision for a p-value."""
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    lbeta = math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)
    front = math.exp(math.log(x) * a + math.log(1 - x) * b - lbeta) / a
    f, c, d = 1.0, 1.0, 0.0
    for i in range(0, 300):
        m = i // 2
        if i == 0:
            num = 1.0
        elif i % 2 == 0:
            num = (m * (b - m) * x) / ((a + 2 * m - 1) * (a + 2 * m))
        else:
            num = -((a + m) * (a + b + m) * x) / ((a + 2 * m) * (a + 2 * m + 1))
        d = 1.0 + num * d
        d = 1e-30 if abs(d) < 1e-30 else d
        d = 1.0 / d
        c = 1.0 + num / c
        c = 1e-30 if abs(c) < 1e-30 else c
        f *= c * d
        if abs(1.0 - c * d) < 1e-10:
            break
    return front * (f - 1.0)


def quintiles(trades, key):
    """Pooled quintiles sorted by ``key``; returns per-quintile stats."""
    ordered = sorted(trades, key=lambda t: t[key])
    n = len(ordered)
    out = []
    for q in range(5):
        chunk = ordered[q * n // 5:(q + 1) * n // 5]
        out.append({
            "n": len(chunk),
            "mean_r": mean([t["r"] for t in chunk]),
            "win": 100.0 * sum(1 for t in chunk if t["pnl"] > 0) / len(chunk),
            "pnl": sum(t["pnl"] for t in chunk),
            "lo": chunk[0][key],
            "hi": chunk[-1][key],
        })
    return out


def show(title, qs):
    print(f"\n{title}")
    print(f"  {'q':>2s} {'n':>5s} {'mean R':>9s} {'win%':>6s} "
          f"{'P&L':>11s} {'range':>24s}")
    for i, q in enumerate(qs, 1):
        print(f"  Q{i} {q['n']:5d} {q['mean_r']:+9.4f} {q['win']:6.1f} "
              f"{q['pnl']:+11.0f}   [{q['lo']:.5g}, {q['hi']:.5g}]")
    spread = qs[0]["mean_r"] - qs[-1]["mean_r"]
    print(f"  Q1 - Q5 spread: {spread:+.4f}R")
    return spread


def main() -> int:
    per_pair = {p: load(p) for p in PAIRS}
    missing = [p for p, v in per_pair.items() if not v]
    if missing:
        print(f"missing results for {missing} -- run research/exp3_confirm.sh")
        return 1

    trades = [t for p in PAIRS for t in per_pair[p]]
    print(f"{len(trades)} trades across {len(PAIRS)} pairs")

    tot_pnl = sum(t["pnl"] for t in trades)
    tot_risk = sum(t["risk"] for t in trades)
    print(f"  unweighted mean R      : {mean([t['r'] for t in trades]):+.4f}")
    print(f"  risk-weighted (P&L/risk): {tot_pnl / tot_risk:+.4f}")

    # ---- 1. dispersion split ------------------------------------------- #
    sd_risk = st.stdev([math.log(t["risk"]) for t in trades])
    sd_f = st.stdev([math.log(t["f"]) for t in trades])
    print("\n=== 1. where does size variation come from? ===")
    print(f"  sd(log money_at_risk)      = {sd_risk:.4f}")
    print(f"  sd(log realised_risk_frac) = {sd_f:.4f}")
    print(f"  ratio sd(log f)/sd(log risk) = {sd_f / sd_risk:.3f}"
          f"   [H4-A if < 0.500]")

    # ---- 2. quintiles --------------------------------------------------- #
    print("\n=== 2. quintiles ===")
    raw_spread = show("by raw money at risk (the original finding)",
                      quintiles(trades, "risk"))
    f_spread = show("by realised risk fraction (equity divided out)",
                    quintiles(trades, "f"))

    # ---- 3. year-demeaned size ------------------------------------------ #
    by_year: dict[int, list[float]] = {}
    for t in trades:
        by_year.setdefault(t["year"], []).append(t["risk"])
    med = {y: st.median(v) for y, v in by_year.items()}
    for t in trades:
        t["risk_detrended"] = t["risk"] / med[t["year"]]
    det_spread = show("by year-detrended money at risk",
                      quintiles(trades, "risk_detrended"))

    # ---- 4. clustered significance of the detrended spread -------------- #
    per_pair_spread = []
    for p in PAIRS:
        qs = quintiles(per_pair[p], "risk_detrended")
        per_pair_spread.append(qs[0]["mean_r"] - qs[-1]["mean_r"])
    t_stat, p_val = ttest_1samp(per_pair_spread)
    print("\n=== 3. detrended spread, per pair (clustered) ===")
    for p, s in zip(PAIRS, per_pair_spread):
        print(f"  {p}: {s:+.4f}R")
    print(f"  mean {mean(per_pair_spread):+.4f}R  t={t_stat:+.2f}  p={p_val:.4f}")

    # ---- 5. the pre-registered verdict ---------------------------------- #
    print("\n=== 4. pre-registered decision ===")
    a_disp = sd_f / sd_risk < 0.5
    a_spread = det_spread < raw_spread / 3.0
    print(f"  dispersion rule  : sd ratio {sd_f / sd_risk:.3f} < 0.5 "
          f"-> {'H4-A' if a_disp else 'not H4-A'}")
    print(f"  spread rule      : detrended {det_spread:+.4f}R < "
          f"{raw_spread / 3.0:+.4f}R -> {'H4-A' if a_spread else 'not H4-A'}")
    b = abs(f_spread) >= 0.20 and p_val < 0.05
    print(f"  H4-B (within-period): |f spread| {abs(f_spread):.4f} >= 0.20 "
          f"and p {p_val:.4f} < 0.05 -> {'H4-B present' if b else 'no H4-B'}")
    print()
    if a_disp or a_spread:
        print("  VERDICT: the raw size/outcome relationship is principally an")
        print("           equity/time confound. Sizing is not the defect.")
    if b:
        print("  VERDICT: a within-period sizing channel is also harmful.")
    if not (a_disp or a_spread or b):
        print("  VERDICT: neither pre-registered rule fires. Inconclusive.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
