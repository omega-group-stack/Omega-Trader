"""Pooled Welch t-test on per-trade R, filtered vs unfiltered.

Specified in research/PREREGISTRATION_3.md before any result existed:
  success = mean R improves by >= +0.05R, p < 0.05, positive on >= 4 of 6 pairs
  weak    = improvement positive and p < 0.05 but < +0.05R
  failure = p >= 0.05, or improvement negative
"""
import json
import math
import sys
from pathlib import Path

PAIRS = ["GBPUSD", "AUDUSD", "NZDUSD", "USDCAD", "USDCHF", "USDJPY"]
DIR = Path("research/exp3")


def rs(path):
    data = json.loads(path.read_text())
    return [float(t["r_multiple"]) for t in data["trades"]
            if t.get("r_multiple") is not None]


def mean(xs):
    return sum(xs) / len(xs) if xs else float("nan")


def var(xs):
    if len(xs) < 2:
        return float("nan")
    m = mean(xs)
    return sum((x - m) ** 2 for x in xs) / (len(xs) - 1)


def welch(a, b):
    """Returns (t, dof, p) for mean(a) - mean(b)."""
    na, nb = len(a), len(b)
    if na < 2 or nb < 2:
        return float("nan"), float("nan"), float("nan")
    va, vb = var(a) / na, var(b) / nb
    t = (mean(a) - mean(b)) / math.sqrt(va + vb)
    dof = (va + vb) ** 2 / (va ** 2 / (na - 1) + vb ** 2 / (nb - 1))
    try:
        from statistics import NormalDist
        # two-sided, normal approximation is fine at these sample sizes
        p = 2 * (1 - NormalDist().cdf(abs(t)))
    except Exception:
        p = float("nan")
    return t, dof, p


def main():
    pooled_on, pooled_off = [], []
    better = 0
    print(f"{'pair':8s} {'n off':>6s} {'R off':>8s} {'n on':>6s} "
          f"{'R on':>8s} {'delta':>8s} {'t':>7s} {'p':>7s}")
    print("-" * 64)
    for pair in PAIRS:
        off_path, on_path = DIR / f"{pair}_off.json", DIR / f"{pair}_on.json"
        if not off_path.exists() or not on_path.exists():
            print(f"{pair:8s} MISSING")
            continue
        off, on = rs(off_path), rs(on_path)
        pooled_off += off
        pooled_on += on
        t, _, p = welch(on, off)
        delta = mean(on) - mean(off)
        if delta > 0:
            better += 1
        print(f"{pair:8s} {len(off):6d} {mean(off):+8.4f} {len(on):6d} "
              f"{mean(on):+8.4f} {delta:+8.4f} {t:+7.2f} {p:7.3f}")

    print("-" * 64)
    t, dof, p = welch(pooled_on, pooled_off)
    delta = mean(pooled_on) - mean(pooled_off)
    print(f"{'POOLED':8s} {len(pooled_off):6d} {mean(pooled_off):+8.4f} "
          f"{len(pooled_on):6d} {mean(pooled_on):+8.4f} {delta:+8.4f} "
          f"{t:+7.2f} {p:7.3f}")
    print(f"\npositive on {better}/6 pairs,  dof {dof:.0f}")

    if p < 0.05 and delta >= 0.05 and better >= 4:
        verdict = "SUCCESS"
    elif p < 0.05 and delta > 0:
        verdict = "WEAK"
    else:
        verdict = "FAILURE"
    print(f"PRE-REGISTERED VERDICT: {verdict}")


if __name__ == "__main__":
    sys.exit(main())
