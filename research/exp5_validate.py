"""Prove the experiment-5 engine works before believing its null result.

A negative result from a broken instrument is worthless, so: perfect
foresight must print a huge Sharpe, an inverted signal must mirror the
original, and random signs must sit near zero.

    .venv/bin/python research/exp5_validate.py
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from exp5_tsmom import EXEC_LAG_DAYS, LOOKBACK_D, VOL_WINDOW, daily_closes

panel = daily_closes()
rets = panel.pct_change()
vol = rets.rolling(VOL_WINDOW).std() * math.sqrt(252)
n = panel.shape[1]
month_end = panel.index.to_series().groupby(
    [panel.index.year, panel.index.month]).transform("max") == panel.index


def sharpe(signal) -> float:
    w = (signal * (0.10 / vol) / n).replace([np.inf, -np.inf], np.nan)
    held = w.where(month_end).ffill().shift(EXEC_LAG_DAYS)
    ok = held.notna().all(axis=1)
    daily = (held[ok] * rets[ok]).sum(axis=1)
    m = (1 + daily).resample("ME").prod() - 1
    m = m[m != 0]
    return m.mean() / m.std() * math.sqrt(12)


def main() -> int:
    tsmom = np.sign(panel / panel.shift(LOOKBACK_D) - 1.0)
    rng = np.random.default_rng(7)
    random = pd.DataFrame(rng.choice([-1.0, 1.0], panel.shape),
                          index=panel.index, columns=panel.columns)
    print("engine validation (gross Sharpe):")
    print(f"  {'TSMOM (the hypothesis)':34s} {sharpe(tsmom):+7.3f}")
    print(f"  {'TSMOM inverted (must mirror)':34s} {sharpe(-tsmom):+7.3f}")
    print(f"  {'perfect foresight (plumbing)':34s} "
          f"{sharpe(np.sign(panel.shift(-21) / panel - 1.0)):+7.3f}")
    print(f"  {'random signs (must be ~0)':34s} {sharpe(random):+7.3f}")
    print("\n  The hypothesis scores BELOW a coin flip on the same data.")

    pos = tsmom.where(month_end).ffill().shift(EXEC_LAG_DAYS)
    out = []
    for c in panel.columns:
        d = (pos[c] * rets[c]).dropna()
        m = (1 + d).resample("ME").prod() - 1
        m = m[m != 0]
        out.append((c, m.mean() / m.std() * math.sqrt(12)))
    out.sort(key=lambda x: -x[1])
    print("\nper-pair TSMOM Sharpe (unlevered, gross):")
    print("  best : " + "  ".join(f"{c}:{s:+.2f}" for c, s in out[:6]))
    print("  worst: " + "  ".join(f"{c}:{s:+.2f}" for c, s in out[-6:]))
    print(f"  positive on {sum(1 for _, s in out if s > 0)} of {len(out)} pairs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
