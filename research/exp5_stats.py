"""Experiment 5, secondary hypothesis: the ensemble at daily frequency.

Per-POSITION expectancy (partial exits merged back, per experiment 4),
clustered across the 17 pairs of the basket.

Pre-registered bar: |t| >= 2.24 (Bonferroni for the two hypotheses of
experiment 5). See research/PREREGISTRATION_5.md.
"""
from __future__ import annotations

import collections
import json
import math
import statistics as st
from pathlib import Path

PAIRS = ["EURUSD", "GBPUSD", "AUDUSD", "NZDUSD", "USDCAD", "USDCHF", "USDJPY",
         "EURGBP", "EURJPY", "EURCHF", "EURAUD", "EURCAD", "EURNZD",
         "GBPJPY", "GBPCHF", "GBPAUD", "GBPCAD"]
DIR = Path("research/exp5")


def positions(pair: str):
    """One row per position: total P&L over total money risked."""
    path = DIR / f"{pair}_d1.json"
    if not path.exists():
        return []
    recs = [t for t in json.loads(path.read_text())["trades"] if t.get("r_multiple")]
    agg = collections.defaultdict(lambda: [0.0, 0.0])
    for t in recs:
        agg[t["ticket"]][0] += t["pnl"]
        agg[t["ticket"]][1] += abs(t["pnl"] / t["r_multiple"])
    return [(pnl, risk) for pnl, risk in agg.values() if risk > 0]


def main() -> int:
    rows, pooled = [], []
    print(f"{'pair':8s} {'positions':>10s} {'mean R':>9s} {'P&L/risk':>10s}")
    print("-" * 42)
    for p in PAIRS:
        pos = positions(p)
        if not pos:
            print(f"{p:8s} {'MISSING':>10s}")
            continue
        rs = [pnl / risk for pnl, risk in pos]
        wtd = sum(x[0] for x in pos) / sum(x[1] for x in pos)
        rows.append((p, st.mean(rs), wtd, len(pos)))
        pooled.extend(rs)
        print(f"{p:8s} {len(pos):10d} {st.mean(rs):+9.4f} {wtd:+10.4f}")

    if len(rows) < 3:
        print("\nnot enough results -- run research/exp5_d1.sh")
        return 1

    per_pair = [r[1] for r in rows]
    n = len(per_pair)
    m, sd = st.mean(per_pair), st.stdev(per_pair)
    t = m / (sd / math.sqrt(n))
    print("-" * 42)
    print(f"{'POOLED':8s} {len(pooled):10d} {st.mean(pooled):+9.4f}")
    print(f"\nclustered across {n} pairs: mean {m:+.4f}R  sd {sd:.4f}  t = {t:+.2f}")
    print(f"positive on {sum(1 for x in per_pair if x > 0)} of {n} pairs")

    print("\n=== pre-registered verdict (|t| >= 2.24) ===")
    if t >= 2.24:
        print(f"  t = {t:+.2f} -> SUCCESS")
    else:
        print(f"  t = {t:+.2f} -> FAILURE. The ensemble is finished.")

    need = (1.96 * st.stdev(pooled) / abs(st.mean(pooled))) ** 2
    print(f"\n  trades needed to call {st.mean(pooled):+.4f}R different from "
          f"zero: {need:,.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
