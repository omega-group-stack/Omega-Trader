"""Experiment 8 -- stochastic hidden divergence in a correction wave.

Pre-registered in research/PREREGISTRATION_8.md (commit eda5aa0) BEFORE any
signal was computed.  The hypothesis came from the repository owner, from
outside the data -- not from me, not from the sample.

The rule, exactly as frozen there:
  context TF  : trend up (swing structure HH+HL AND close>EMA50 rising);
                inside correction wave #1 or #2 of that trend.
  trigger TF  : same trend definition, up;
                freshly confirmed hidden bullish divergence on the last two
                swing lows (price higher low, Stoch(12,3,3) %K lower low,
                <=100 bars apart);
                smoothed %K between 0 and 30.
  entry       : open of the next trigger bar.
  stop        : second divergence low - 0.1*ATR(14).   target: 2R.
  max hold    : 500 trigger bars.  Sells are the exact mirror.

Two scales: A = M15 context + M1 trigger (as specified, 6.5 months).
            B = H4  context + M15 trigger (16:1 analogue, 8 years).

    PYTHONPATH=research .venv/bin/python research/exp8_divergence.py
"""
from __future__ import annotations

import math
import os
import warnings
from bisect import bisect_right

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

warnings.filterwarnings("ignore", message=".*timezone.*")

K_FRACTAL = 3            # fractal half-width (window 7)
EMA_LEN = 50
STOCH_N, STOCH_K, STOCH_D = 12, 3, 3
BAND_BUY = (0.0, 30.0)   # on smoothed %K at the decision bar
BAND_SELL = (70.0, 100.0)
MAX_SWING_GAP = 100      # trigger bars between the two divergence swings
CORRECTIONS = {1, 2}
STOP_BUF_ATR = 0.1
RR = 2.0
MAX_HOLD = 500
SPREAD_PIPS = 1.2
COMMISSION_FRAC = 7e-5
GATE_R, GATE_T, GATE_T_FAM = 0.05, 1.68, 2.54

PAIRS = ("EURUSD", "GBPUSD", "AUDUSD", "NZDUSD", "USDCAD", "USDCHF", "USDJPY",
         "EURGBP", "EURJPY", "EURCHF", "EURAUD", "EURCAD", "EURNZD",
         "GBPJPY", "GBPCHF", "GBPAUD", "GBPCAD")

_CACHE: dict[tuple[str, str], pd.DataFrame] = {}
_ARR: dict[tuple[str, str], dict] = {}
_SIG_CACHE: dict[tuple[str, str, str], list] = {}


def arrays_for(sym: str, tf: str) -> dict:
    if (sym, tf) not in _ARR:
        df = load(sym, tf)
        _ARR[(sym, tf)] = {x: df[x].to_numpy()
                           for x in ("open", "high", "low", "close")}
    return _ARR[(sym, tf)]


def pip(sym: str) -> float:
    return 0.01 if sym.endswith("JPY") else 0.0001


def load(sym: str, tf: str) -> pd.DataFrame:
    key = (sym, tf)
    if key not in _CACHE:
        df = pd.read_csv(f"data/{sym}_{tf}.csv")
        c = next(x for x in df.columns
                 if "time" in x.lower() or "date" in x.lower())
        df[c] = pd.to_datetime(df[c], utc=True, format="mixed")
        df = df.set_index(c).sort_index()
        _CACHE[key] = df[["open", "high", "low", "close"]].astype(float)
    return _CACHE[key]


def smoothed_k(h: np.ndarray, l: np.ndarray, c: np.ndarray) -> np.ndarray:
    ll = pd.Series(l).rolling(STOCH_N, min_periods=STOCH_N).min()
    hh = pd.Series(h).rolling(STOCH_N, min_periods=STOCH_N).max()
    raw = 100.0 * (pd.Series(c) - ll) / (hh - ll)
    return raw.rolling(STOCH_K, min_periods=STOCH_K).mean().to_numpy()


def atr14(h: np.ndarray, l: np.ndarray, c: np.ndarray) -> np.ndarray:
    pc = np.concatenate(([c[0]], c[:-1]))
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    return (pd.Series(tr).ewm(alpha=1 / 14, adjust=False, min_periods=14)
            .mean().to_numpy())


def find_swings(h: np.ndarray, l: np.ndarray, k: int = K_FRACTAL):
    """Fractal swings.  Returns (hi, lo); each event (bar, price, confirm_bar).

    An event only becomes *known* at bar+k -- that is the no-lookahead rule
    everything downstream relies on.
    """
    n = len(h)
    w = 2 * k + 1
    if n < w:
        return [], []
    wh = sliding_window_view(h, w)
    wl = sliding_window_view(l, w)
    hi_idx = np.nonzero(wh[:, k] == wh.max(axis=1))[0] + k
    lo_idx = np.nonzero(wl[:, k] == wl.min(axis=1))[0] + k
    hi = [(int(i), float(h[i]), int(i + k)) for i in hi_idx]
    lo = [(int(i), float(l[i]), int(i + k)) for i in lo_idx]
    return hi, lo


def regime(df: pd.DataFrame) -> dict:
    """Per-bar trend/correction state for one series (PREREGISTRATION_8 s2.3-2.4)."""
    h = df["high"].to_numpy()
    l = df["low"].to_numpy()
    c = df["close"].to_numpy()
    n = len(c)
    E = pd.Series(c).ewm(span=EMA_LEN, adjust=False).mean().to_numpy()
    hi_ev, lo_ev = find_swings(h, l)

    by_conf: dict[int, list] = {}
    for bar, price, conf in hi_ev:
        by_conf.setdefault(conf, []).append(("H", bar, price))
    for bar, price, conf in lo_ev:
        by_conf.setdefault(conf, []).append(("L", bar, price))

    trend_up = np.zeros(n, bool)
    trend_dn = np.zeros(n, bool)
    corr_up = np.zeros(n, int)      # confirmed swing highs since trend start
    corr_dn = np.zeros(n, int)      # confirmed swing lows since trend start
    in_corr_up = np.zeros(n, bool)
    in_corr_dn = np.zeros(n, bool)

    hi_bars: list[int] = []         # confirmed-by-now, ascending (order = bar)
    hi_prices: list[float] = []
    lo_bars: list[int] = []
    lo_prices: list[float] = []
    prev_up = prev_dn = False
    cur_start = -1
    for j in range(n):
        for kind, bar, price in by_conf.get(j, ()):  # absorb newly confirmed
            if kind == "H":
                hi_bars.append(bar)
                hi_prices.append(price)
            else:
                lo_bars.append(bar)
                lo_prices.append(price)

        ema_up = j >= 1 and c[j] > E[j] > E[j - 1]
        ema_dn = j >= 1 and c[j] < E[j] < E[j - 1]
        nh, nl = len(hi_bars), len(lo_bars)
        struct_up = (nh >= 2 and nl >= 2
                     and hi_prices[-1] > hi_prices[-2]
                     and lo_prices[-1] > lo_prices[-2])
        struct_dn = (nh >= 2 and nl >= 2
                     and hi_prices[-1] < hi_prices[-2]
                     and lo_prices[-1] < lo_prices[-2])
        up = struct_up and ema_up
        dn = struct_dn and ema_dn
        if up and not prev_up:
            cur_start = j
        trend_up[j] = up
        trend_dn[j] = dn
        if up:
            corr_up[j] = nh - bisect_right(hi_bars, cur_start)
            in_corr_up[j] = (c[j] < hi_prices[-1] and c[j] > lo_prices[-1])
        if dn:
            corr_dn[j] = nl - bisect_right(lo_bars, cur_start)
            in_corr_dn[j] = (c[j] > lo_prices[-1] and c[j] < hi_prices[-1])
        prev_up, prev_dn = up, dn

    return {"h": h, "l": l, "c": c, "E": E, "hi_ev": hi_ev, "lo_ev": lo_ev,
            "trend_up": trend_up, "trend_dn": trend_dn,
            "corr_up": corr_up, "corr_dn": corr_dn,
            "in_corr_up": in_corr_up, "in_corr_dn": in_corr_dn}


def compute_signals(sym: str, ctx_tf: str, trg_tf: str,
                    tf_minutes: dict[str, int],
                    cut_close: pd.Timestamp | None = None) -> list[dict]:
    """All entry signals for one pair, in bar order.

    ``cut_close`` truncates history to bars fully closed by that instant --
    used only by the no-lookahead audit.
    """
    ctx = load(sym, ctx_tf)
    trg = load(sym, trg_tf)
    if cut_close is None and (sym, ctx_tf, trg_tf) in _SIG_CACHE:
        return _SIG_CACHE[(sym, ctx_tf, trg_tf)]
    tmin, cmin = tf_minutes[trg_tf], tf_minutes[ctx_tf]
    if cut_close is not None:
        ctx = ctx[ctx.index + pd.Timedelta(minutes=cmin) <= cut_close]
        trg = trg[trg.index + pd.Timedelta(minutes=tmin) <= cut_close]

    ctxr = regime(ctx)
    trgr = regime(trg)
    Ks = smoothed_k(trgr["h"], trgr["l"], trgr["c"])
    A = atr14(trgr["h"], trgr["l"], trgr["c"])
    o = trg["open"].to_numpy()
    tindex = trg.index

    # instant each context bar becomes known (open-time stamps -> close time)
    ctx_known = (ctx.index + pd.Timedelta(minutes=cmin)).tz_localize(None)
    ctx_known_vals = ctx_known.to_numpy()

    signals: list[dict] = []
    hi_ev, lo_ev = trgr["hi_ev"], trgr["lo_ev"]

    def ctx_state(t: int):
        close_t = tindex[t] + pd.Timedelta(minutes=tmin)
        j = int(np.searchsorted(ctx_known_vals,
                                close_t.tz_localize(None).to_numpy(),
                                side="right") - 1)
        if j < 0:
            return None
        return j

    def check(t: int, direction: int):
        if direction > 0:
            if not trgr["trend_up"][t]:
                return False
            if not (BAND_BUY[0] <= Ks[t] <= BAND_BUY[1]):
                return False
        else:
            if not trgr["trend_dn"][t]:
                return False
            if not (BAND_SELL[0] <= Ks[t] <= BAND_SELL[1]):
                return False
        j = ctx_state(t)
        if j is None:
            return False
        if direction > 0:
            return bool(ctxr["trend_up"][j] and ctxr["in_corr_up"][j]
                        and ctxr["corr_up"][j] in CORRECTIONS)
        return bool(ctxr["trend_dn"][j] and ctxr["in_corr_dn"][j]
                    and ctxr["corr_dn"][j] in CORRECTIONS)

    # bullish: freshly confirmed swing low, price HL / oscillator LL
    for k2 in range(1, len(lo_ev)):
        b2, p2, conf2 = lo_ev[k2]
        b1, p1, _ = lo_ev[k2 - 1]
        t = conf2
        if not (p2 > p1 and Ks[b2] < Ks[b1] and b2 - b1 <= MAX_SWING_GAP):
            continue
        if math.isnan(Ks[t]) or math.isnan(A[t]):
            continue
        if not check(t, +1):
            continue
        stop = p2 - STOP_BUF_ATR * A[t]
        signals.append({"sym": sym, "t": t, "dir": +1, "stop": stop,
                        "ts": tindex[t], "K": float(Ks[t])})

    # bearish: freshly confirmed swing high, price LH / oscillator HH
    for k2 in range(1, len(hi_ev)):
        b2, p2, conf2 = hi_ev[k2]
        b1, p1, _ = hi_ev[k2 - 1]
        t = conf2
        if not (p2 < p1 and Ks[b2] > Ks[b1] and b2 - b1 <= MAX_SWING_GAP):
            continue
        if math.isnan(Ks[t]) or math.isnan(A[t]):
            continue
        if not check(t, -1):
            continue
        stop = p2 + STOP_BUF_ATR * A[t]
        signals.append({"sym": sym, "t": t, "dir": -1, "stop": stop,
                        "ts": tindex[t], "K": float(Ks[t])})

    signals.sort(key=lambda s: s["t"])
    if cut_close is None:
        _SIG_CACHE[(sym, ctx_tf, trg_tf)] = signals
    return signals


def sim_trade(o, h, l, c, entry_i: int, direction: int, stop: float,
              rr: float = RR, max_hold: int = MAX_HOLD):
    """Pessimistic fill: if a bar touches both levels, the stop wins."""
    if entry_i >= len(o) or math.isnan(o[entry_i]):
        return None
    entry = float(o[entry_i])
    r_dist = (entry - stop) if direction > 0 else (stop - entry)
    if r_dist <= 0:
        return None
    tp = entry + rr * r_dist if direction > 0 else entry - rr * r_dist
    end = min(entry_i + max_hold, len(c)) - 1
    for j in range(entry_i, end + 1):
        if direction > 0 and l[j] <= stop:
            return entry, (stop - entry) / r_dist, "STOP", j
        if direction < 0 and h[j] >= stop:
            return entry, (entry - stop) / r_dist, "STOP", j
        if direction > 0 and h[j] >= tp:
            return entry, (tp - entry) / r_dist, "TP", j
        if direction < 0 and l[j] <= tp:
            return entry, (entry - tp) / r_dist, "TP", j
    return entry, (c[end] - entry) / r_dist if direction > 0 \
        else (entry - c[end]) / r_dist, "TIME", end


def run_scale(name: str, ctx_tf: str, trg_tf: str,
              tf_minutes: dict[str, int]) -> pd.DataFrame:
    print(f"\n{'=' * 68}\nSCALE {name}: context {ctx_tf}, trigger {trg_tf}\n{'=' * 68}")
    trades, n_sig = [], 0
    skipped_overlap = skipped_invalid = 0
    for sym in PAIRS:
        df = load(sym, trg_tf)
        o = df["open"].to_numpy()
        h = df["high"].to_numpy()
        l = df["low"].to_numpy()
        c = df["close"].to_numpy()
        sigs = compute_signals(sym, ctx_tf, trg_tf, tf_minutes)
        n_sig += len(sigs)
        busy_until = -1
        for s in sigs:
            if s["t"] <= busy_until:
                skipped_overlap += 1
                continue
            res = sim_trade(o, h, l, c, s["t"] + 1, s["dir"], s["stop"])
            if res is None:
                skipped_invalid += 1
                continue
            entry, gross, reason, j_exit = res
            r_dist = abs(entry - s["stop"])
            cost_price = SPREAD_PIPS * pip(sym) + COMMISSION_FRAC * entry
            trades.append({
                "sym": sym, "dir": s["dir"], "ts": s["ts"],
                "stop_pips": r_dist / pip(sym),
                "gross": gross, "net": gross - cost_price / r_dist,
                "reason": reason, "hold": j_exit - s["t"],
            })
            busy_until = j_exit
    tr = pd.DataFrame(trades)
    print(f"signals: {n_sig}   taken: {len(tr)}   "
          f"skipped-overlap: {skipped_overlap}   invalid-stop: {skipped_invalid}")
    if tr.empty:
        print("NO TRADES -- test is void for this scale.")
        return tr
    return tr


def stats(tr: pd.DataFrame, label: str) -> tuple[float, float]:
    if len(tr) == 0:
        return float("nan"), float("nan")
    m, sd = tr["net"].mean(), tr["net"].std(ddof=1)
    t = m / (sd / math.sqrt(len(tr))) if sd > 0 else float("nan")
    g = tr["gross"].mean() if "gross" in tr.columns else float("nan")
    gs = f"gross {g:+.3f}R  " if not math.isnan(g) else ""
    print(f"  {label:34s} n={len(tr):4d}  {gs}net {m:+.3f}R  t {t:+5.2f}")
    return m, t


def main() -> int:
    tf_minutes = {"M1": 1, "M15": 15, "H4": 240}
    print("EXPERIMENT 8 -- hidden divergence in the correction wave")
    print("pre-registered in PREREGISTRATION_8.md (commit eda5aa0)")

    scales = {}
    scales["A"] = run_scale("A", "M15", "M1", tf_minutes)
    scales["B"] = run_scale("B", "H4", "M15", tf_minutes)

    for name, tr in scales.items():
        if tr.empty:
            continue
        print(f"\n--- scale {name}: composition ---")
        print(f"  stops: {(tr['reason'] == 'STOP').mean() * 100:.0f}%  "
              f"targets: {(tr['reason'] == 'TP').mean() * 100:.0f}%  "
              f"time-exits: {(tr['reason'] == 'TIME').mean() * 100:.0f}%")
        print(f"  buys {int((tr['dir'] > 0).sum())} / sells "
              f"{int((tr['dir'] < 0).sum())}   median hold "
              f"{int(tr['hold'].median())} bars")
        print(f"  stop distance: median {tr['stop_pips'].median():.1f} pips, "
              f"10% {tr['stop_pips'].quantile(0.1):.1f}, "
              f"90% {tr['stop_pips'].quantile(0.9):.1f}")
        cost_r = tr["gross"] - tr["net"]
        print(f"  cost per trade: median {cost_r.median():.3f}R  "
              f"90% {cost_r.quantile(0.9):.3f}R")
        print("  per-pair net R:")
        for sym, g in tr.groupby("sym"):
            print(f"    {sym:7s} n={len(g):3d}  net {g['net'].mean():+.3f}R")

    # ---- controls ------------------------------------------------------- #
    print(f"\n{'=' * 68}\nCONTROLS (validation is mandatory before any number)\n{'=' * 68}")
    for name, ctx_tf, trg_tf in (("A", "M15", "M1"), ("B", "H4", "M15")):
        tr = scales[name]
        if tr.empty:
            print(f"scale {name}: no trades, controls void")
            continue
        print(f"\n--- scale {name} ---")
        all_sigs = [(sym, s) for sym in PAIRS
                    for s in compute_signals(sym, ctx_tf, trg_tf, tf_minutes)]

        def trade_control(sym: str, s: dict, d: int):
            """Trade moment ``s`` in direction ``d`` at the same risk distance."""
            a = arrays_for(sym, trg_tf)
            o, h, l, c = a["open"], a["high"], a["low"], a["close"]
            i = s["t"] + 1
            if i >= len(o):
                return None
            entry = float(o[i])
            r = abs(entry - s["stop"])
            if r <= 0:
                return None
            stop = entry - r if d > 0 else entry + r
            res = sim_trade(o, h, l, c, i, d, stop)
            if res is None:
                return None
            _, gross, _, _ = res
            cost = SPREAD_PIPS * pip(sym) + COMMISSION_FRAC * entry
            return gross - cost / r

        # 1. inverted: every signal traded the other way
        inv = [v for sym, s in all_sigs
               if (v := trade_control(sym, s, -s["dir"])) is not None]
        stats(pd.DataFrame({"net": inv}), "inverted (should mirror)")

        # 2. random direction at the same moments
        means = []
        for seed in range(20):
            rng = np.random.default_rng(seed)
            outs = [v for sym, s in all_sigs
                    if (v := trade_control(sym, s,
                                           int(rng.integers(0, 2)) * 2 - 1))
                    is not None]
            if outs:
                means.append(float(np.mean(outs)))
        if means:
            print(f"  random direction, 20 seeds: mean {np.mean(means):+.3f}R "
                  f"(range {min(means):+.3f} .. {max(means):+.3f})")

        # 3. oracle: picks the better direction at the same moments
        orc = []
        for sym, s in all_sigs:
            lo_hi = [v for d in (+1, -1)
                     if (v := trade_control(sym, s, d)) is not None]
            if lo_hi:
                orc.append(max(lo_hi))
        stats(pd.DataFrame({"net": orc}), "oracle (must be large +)")

    # ---- no-lookahead audit --------------------------------------------- #
    print(f"\n{'=' * 68}\nNO-LOOKAHEAD AUDIT (recompute on truncated history)\n{'=' * 68}")
    rng = np.random.default_rng(7)
    for name, ctx_tf, trg_tf in (("A", "M15", "M1"), ("B", "H4", "M15")):
        tr = scales[name]
        if tr.empty:
            continue
        tmin = tf_minutes[trg_tf]
        picks = rng.choice(len(tr), size=min(6, len(tr)), replace=False)
        ok = bad = 0
        for i in picks:
            row = tr.iloc[int(i)]
            sym = row["sym"]
            full = load(sym, trg_tf)
            close_t = row["ts"] + pd.Timedelta(minutes=tmin)
            sigs = compute_signals(sym, ctx_tf, trg_tf, tf_minutes,
                                   cut_close=close_t)
            hit = any(s["sym"] == sym and s["ts"] == row["ts"]
                      and s["dir"] == row["dir"] for s in sigs)
            ok += hit
            bad += not hit
        print(f"  scale {name}: {ok} reproduced, {bad} missing "
              f"-> {'PASS' if bad == 0 else 'FAIL -- LOOKAHEAD'}")

    # ---- verdict --------------------------------------------------------- #
    print(f"\n{'=' * 68}\nPRE-REGISTERED VERDICT\n{'=' * 68}")
    for name in ("A", "B"):
        tr = scales[name]
        if tr.empty:
            print(f"  scale {name}: VOID (no trades)")
            continue
        m, t = stats(tr, f"scale {name} NET")
        gm = tr["gross"].mean()
        gt = gm / (tr["gross"].std(ddof=1) / math.sqrt(len(tr)))
        print(f"    gross (before costs): {gm:+.3f}R  t {gt:+.2f}")
        gate = m >= GATE_R and t >= GATE_T
        print(f"    net >= +0.05R: {m:+.3f} -> {m >= GATE_R}   "
              f"t >= 1.68: {t:+.2f} -> {t >= GATE_T}   "
              f"t >= 2.54 (Bonferroni): {t >= GATE_T_FAM}")
        if gate and t >= GATE_T_FAM:
            print("    => CONFIRMED, survives multiple-testing correction")
        elif gate:
            print("    => nominal only, fails Bonferroni -- no edge claimed")
        else:
            print("    => REJECTED by the pre-registered rule")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
