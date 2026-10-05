"""The Omega ensemble strategy.

Six independent "voting blocks" each score the market in ``[-1, +1]``:

======================================================================
 block       reads                                   typical weight
======================================================================
 trend       EMA stack, ADX/DI, Supertrend, PSAR,     1.00
             linear-regression slope
 momentum    RSI, Stochastic, MACD, CCI, ROC,         0.90
             Williams %R, RSI divergence
 volatility  Bollinger %B, Keltner breakout,          0.60
             squeeze, ATR percentile
 volume      MFI, Chaikin Money Flow, OBV slope,      0.50
             Force Index, relative volume
 structure   swing structure, Donchian breakout,      0.80
             pullback depth, candlestick intent
 htf_bias    the same read on a higher timeframe      1.00
======================================================================

The block scores are combined with **regime-adjusted weights**: trend blocks
get louder in trends, oscillators get louder (and *contrarian*) in ranges. The
weighted result is the ensemble score; a trade only fires when the score clears
``entry_threshold`` **and** enough blocks agree **and** confidence clears
``min_confidence`` **and** no veto (session, spread, HTF conflict) applies.
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from ..config import StrategyConfig
from ..core.types import ComponentScore, Regime, Signal, SignalDirection, SymbolSpec
from . import regime as regime_mod
from . import sessions
from .features import build_features, build_htf_features

BLOCKS = ("trend", "momentum", "volatility", "volume", "structure", "htf_bias")


class EnsembleStrategy:
    """Stateless-per-bar, vectorised multi-indicator decision engine."""

    name = "ensemble"

    def __init__(self, cfg: StrategyConfig, spec: SymbolSpec) -> None:
        self.cfg = cfg
        self.spec = spec
        self.features: pd.DataFrame = pd.DataFrame()
        self.scores: pd.DataFrame = pd.DataFrame()
        self.regimes: pd.Series = pd.Series(dtype=object)
        self.weights: pd.DataFrame = pd.DataFrame()
        self.session_ok: pd.Series = pd.Series(dtype=bool)

    # ------------------------------------------------------------------ #
    # Preparation
    # ------------------------------------------------------------------ #
    def prepare(
        self, df: pd.DataFrame, htf_df: Optional[pd.DataFrame] = None
    ) -> "EnsembleStrategy":
        """Compute indicators, block scores and regimes for the whole frame."""
        ic = self.cfg.indicators
        f = build_features(df, ic)
        htf = build_htf_features(df, htf_df, self.cfg.htf_timeframe, ic)
        f = f.join(htf)

        self.regimes = regime_mod.classify(f, self.cfg.regime)
        is_range = self.regimes.isin([Regime.RANGE.value, Regime.QUIET.value])

        scores = pd.DataFrame(index=f.index)
        scores["trend"] = _trend_score(f, ic)
        scores["momentum"] = _momentum_score(f, ic, is_range)
        scores["volatility"] = _volatility_score(f, is_range)
        scores["volume"] = _volume_score(f)
        scores["structure"] = _structure_score(f, is_range)
        scores["htf_bias"] = _htf_score(f)
        scores = scores.clip(-1.0, 1.0).fillna(0.0)

        self.features = f
        self.scores = scores
        self.weights = self._weight_frame()
        self.session_ok = sessions.mask(
            pd.DatetimeIndex(f.index), self.cfg.sessions
        )
        self._combine()
        return self

    def _weight_frame(self) -> pd.DataFrame:
        """Per-bar weights derived from the regime label (cached per regime)."""
        cache: Dict[str, Dict[str, float]] = {}
        rows = []
        for label in self.regimes:
            if label not in cache:
                cache[label] = regime_mod.weights_for(
                    Regime(label), self.cfg.weights, self.cfg.regime
                )
            rows.append(cache[label])
        return pd.DataFrame(rows, index=self.regimes.index, columns=list(BLOCKS))

    def _combine(self) -> None:
        """Weighted ensemble score, agreement count and confidence."""
        w = self.weights
        s = self.scores
        total_w = w.sum(axis=1).replace(0.0, np.nan)
        raw = (s * w).sum(axis=1) / total_w

        sign = np.sign(raw)
        meaningful = s.abs() > 0.08
        agree = (np.sign(s).eq(sign, axis=0)) & meaningful
        agree_w = (w.where(agree, 0.0)).sum(axis=1) / total_w

        strength = (raw.abs() / max(self.cfg.entry_threshold, 1e-9)).clip(0.0, 1.0)
        confidence = (0.6 * agree_w + 0.4 * strength).clip(0.0, 1.0)

        self.scores = s.assign(
            ensemble=raw.fillna(0.0),
            agreement=agree_w.fillna(0.0),
            n_agree=agree.sum(axis=1),
            confidence=confidence.fillna(0.0),
        )

    # ------------------------------------------------------------------ #
    # Signal extraction
    # ------------------------------------------------------------------ #
    def signal_at(self, i: int, symbol: Optional[str] = None) -> Signal:
        """Build the :class:`Signal` for positional bar ``i``."""
        cfg = self.cfg
        f = self.features
        s = self.scores
        if i < 0:
            i += len(f)
        ts = f.index[i]
        row = f.iloc[i]
        srow = s.iloc[i]
        wrow = self.weights.iloc[i]
        reg = Regime(self.regimes.iloc[i])

        raw = float(srow["ensemble"])
        confidence = float(srow["confidence"])
        n_agree = int(srow["n_agree"])

        components = [
            ComponentScore(
                name=block,
                score=float(srow[block]),
                weight=float(wrow[block]),
                detail=_detail(block, row),
            )
            for block in BLOCKS
        ]

        reasons: List[str] = []
        vetoes: List[str] = []

        warm = i < cfg.warmup_bars
        if warm:
            vetoes.append(f"warm-up: bar {i} < {cfg.warmup_bars}")

        if not bool(self.session_ok.iloc[i]):
            allowed, why = sessions.is_open(ts.to_pydatetime(), cfg.sessions)
            vetoes.append(why or "session: closed")

        max_spread = self._max_spread_points()
        if max_spread and float(row.get("spread", 0.0)) > max_spread:
            vetoes.append(
                f"spread {row['spread']:.0f}pts > max {max_spread:.0f}pts"
            )

        if cfg.regime.block_trades_in_quiet and reg is Regime.QUIET:
            vetoes.append("regime: market too quiet")

        direction = SignalDirection.FLAT
        if abs(raw) >= cfg.entry_threshold:
            direction = SignalDirection.LONG if raw > 0 else SignalDirection.SHORT
            reasons.append(f"score {raw:+.3f} clears ±{cfg.entry_threshold:.2f}")
        else:
            reasons.append(f"score {raw:+.3f} below ±{cfg.entry_threshold:.2f}")

        if direction is not SignalDirection.FLAT:
            if confidence < cfg.min_confidence:
                vetoes.append(
                    f"confidence {confidence:.2f} < {cfg.min_confidence:.2f}"
                )
            if n_agree < cfg.min_agreeing_blocks:
                vetoes.append(
                    f"only {n_agree} blocks agree (need {cfg.min_agreeing_blocks})"
                )
            if direction is SignalDirection.LONG and not cfg.allow_longs:
                vetoes.append("longs disabled")
            if direction is SignalDirection.SHORT and not cfg.allow_shorts:
                vetoes.append("shorts disabled")
            if cfg.require_htf_alignment:
                htf = float(srow["htf_bias"])
                if htf * direction.sign < -0.12:
                    vetoes.append(
                        f"higher timeframe ({cfg.htf_timeframe}) opposes: {htf:+.2f}"
                    )
                else:
                    reasons.append(f"{cfg.htf_timeframe} bias {htf:+.2f} aligned")
            reasons.append(f"regime {reg.value}, {n_agree}/6 blocks agree")

        atr = float(row.get("atr", 0.0) or 0.0)
        price = float(row["close"])

        return Signal(
            time=ts.to_pydatetime(),
            symbol=symbol or self.spec.symbol,
            direction=direction,
            score=raw,
            confidence=confidence,
            regime=reg,
            components=components,
            atr=atr,
            price=price,
            reasons=reasons,
            vetoes=vetoes,
            meta={
                "swing_high": _f(row.get("swing_high")),
                "swing_low": _f(row.get("swing_low")),
                "atr_percentile": _f(row.get("atr_pct"), 0.5),
                "adx": _f(row.get("adx")),
                "rsi": _f(row.get("rsi"), 50.0),
                "spread_points": _f(row.get("spread")),
                "risk_multiplier": regime_mod.risk_multiplier(
                    reg, _f(row.get("atr_pct"), 0.5)
                ),
                "bar_index": i,
            },
        )

    def latest(self, symbol: Optional[str] = None) -> Signal:
        return self.signal_at(len(self.features) - 1, symbol)

    def exit_pressure(self, i: int, side_sign: int) -> float:
        """How strongly the ensemble now argues *against* an open position.

        Returns a value in ``[0, 1]``: 0 = thesis intact, 1 = fully reversed.
        """
        raw = float(self.scores["ensemble"].iloc[i])
        aligned = raw * side_sign
        if aligned >= self.cfg.exit_threshold:
            return 0.0
        return float(np.clip((self.cfg.exit_threshold - aligned) / 1.0, 0.0, 1.0))

    # -- helpers ------------------------------------------------------------
    def _max_spread_points(self) -> float:
        return float(getattr(self, "max_spread_points", 0.0) or 0.0)


# --------------------------------------------------------------------------- #
# Block scorers (all vectorised, all returning [-1, +1])
# --------------------------------------------------------------------------- #
def _trend_score(f: pd.DataFrame, ic) -> pd.Series:
    ema_stack = np.tanh(f["ema_spread_atr"].fillna(0.0))
    baseline = np.tanh(f["price_vs_base_atr"].fillna(0.0) / 2.0)
    supert = f["supertrend_dir"].fillna(0.0)
    adx_gate = (f["adx"].fillna(0.0) / 40.0).clip(0.0, 1.0)
    di = (f["di_delta"].fillna(0.0) * 2.0).clip(-1.0, 1.0) * adx_gate
    psar = f["psar_dir"].fillna(0.0) * 0.8
    slope = np.tanh(f["slope_z"].fillna(0.0) / 2.0)

    weights = np.array([1.0, 0.8, 1.0, 0.9, 0.6, 0.8])
    stack = np.vstack([ema_stack, baseline, supert, di, psar, slope])
    return pd.Series(np.average(stack, axis=0, weights=weights), index=f.index)


def _momentum_score(f: pd.DataFrame, ic, is_range: pd.Series) -> pd.Series:
    """Pro-trend in trends, mean-reverting in ranges — the same oscillators."""
    rsi = f["rsi"].fillna(50.0)
    stoch = f["stoch_k"].fillna(50.0)
    cci = f["cci"].fillna(0.0)
    willr = f["willr"].fillna(-50.0)

    # --- continuation read -------------------------------------------------
    pro = np.average(
        np.vstack([
            ((rsi - 50.0) / 25.0).clip(-1, 1),
            np.tanh(f["macd_hist_atr"].fillna(0.0) * 2.0),
            ((stoch - 50.0) / 35.0).clip(-1, 1),
            (cci / 150.0).clip(-1, 1),
            np.tanh(f["roc_z"].fillna(0.0) / 2.0),
            ((willr + 50.0) / 35.0).clip(-1, 1),
        ]),
        axis=0,
        weights=[1.0, 1.1, 0.8, 0.7, 0.7, 0.6],
    )

    # --- fade-the-extreme read ---------------------------------------------
    rev = np.average(
        np.vstack([
            (-(rsi - 50.0) / (ic.rsi_overbought - 50.0)).clip(-1, 1),
            (-(stoch - 50.0) / 30.0).clip(-1, 1),
            (-cci / 150.0).clip(-1, 1),
            (-(willr + 50.0) / 35.0).clip(-1, 1),
        ]),
        axis=0,
        weights=[1.2, 1.0, 0.8, 0.7],
    )

    blended = np.where(is_range.to_numpy(), rev, pro)
    divergence = f["divergence"].fillna(0.0).to_numpy() * 0.35
    return pd.Series(np.clip(blended + divergence, -1, 1), index=f.index)


def _volatility_score(f: pd.DataFrame, is_range: pd.Series) -> pd.Series:
    pct_b = f["bb_pct_b"].fillna(0.5)
    band_pos = ((pct_b - 0.5) * 2.0).clip(-1.5, 1.5)
    kc_break = np.tanh(f["kc_break"].fillna(0.0) * 1.5)

    breakout_read = np.clip(0.55 * band_pos + 0.75 * kc_break, -1, 1)
    fade_read = np.clip(-band_pos, -1, 1)

    score = np.where(is_range.to_numpy(), fade_read, breakout_read)

    # A squeeze carries no direction — damp the block while it lasts.
    squeeze = f["squeeze"].fillna(0.0).to_numpy()
    score = score * np.where(squeeze > 0, 0.4, 1.0)
    return pd.Series(np.clip(score, -1, 1), index=f.index)


def _volume_score(f: pd.DataFrame) -> pd.Series:
    mfi = ((f["mfi"].fillna(50.0) - 50.0) / 30.0).clip(-1, 1)
    cmf = (f["cmf"].fillna(0.0) * 5.0).clip(-1, 1)
    obv = np.tanh(f["obv_slope"].fillna(0.0) / 2.0)
    force = np.tanh(f["force_z"].fillna(0.0) / 2.0)

    core = np.average(
        np.vstack([mfi, cmf, obv, force]), axis=0, weights=[1.0, 0.9, 1.0, 0.7]
    )
    # Participation scales conviction: thin volume => quieter vote.
    participation = f["rel_volume"].fillna(1.0).clip(0.4, 1.6) / 1.2
    return pd.Series(np.clip(core * participation, -1, 1), index=f.index)


def _structure_score(f: pd.DataFrame, is_range: pd.Series) -> pd.Series:
    bias = f["structure_bias"].fillna(0.0)
    breakout = (f["breakout"].fillna(0.0) / 1.5).clip(-1, 1)
    candle = f["candle"].fillna(0.0)

    # Buying dips in an uptrend / selling rallies in a downtrend.
    pullback = f["pullback"].fillna(0.5)
    dip_bonus = np.where(
        (bias > 0) & (pullback < 0.4), 0.45,
        np.where((bias < 0) & (pullback > 0.6), -0.45, 0.0),
    )
    # In ranges, the edges of the range are the opportunity.
    edge_fade = np.where(pullback > 0.85, -0.5, np.where(pullback < 0.15, 0.5, 0.0))
    context = np.where(is_range.to_numpy(), edge_fade, dip_bonus)

    core = np.average(
        np.vstack([bias, breakout, candle]), axis=0, weights=[1.0, 1.0, 0.7]
    )
    return pd.Series(np.clip(core + context, -1, 1), index=f.index)


def _htf_score(f: pd.DataFrame) -> pd.Series:
    trend = f["htf_trend"].fillna(0.0)
    st = f["htf_st_dir"].fillna(0.0)
    rsi = ((f["htf_rsi"].fillna(50.0) - 50.0) / 25.0).clip(-1, 1)
    slope = f["htf_slope"].fillna(0.0)
    return pd.Series(
        np.clip(
            np.average(
                np.vstack([trend, st, rsi, slope]), axis=0, weights=[1.1, 1.0, 0.6, 0.8]
            ),
            -1,
            1,
        ),
        index=f.index,
    )


# --------------------------------------------------------------------------- #
# Human-readable detail strings for the dashboard
# --------------------------------------------------------------------------- #
def _detail(block: str, row: pd.Series) -> str:
    try:
        if block == "trend":
            return (
                f"EMA {row['ema_fast']:.5f}/{row['ema_slow']:.5f} · "
                f"ADX {row['adx']:.1f} · ST {'up' if row['supertrend_dir'] > 0 else 'down'}"
            )
        if block == "momentum":
            return (
                f"RSI {row['rsi']:.1f} · Stoch {row['stoch_k']:.1f} · "
                f"MACD hist {row['macd_hist_atr']:+.2f} ATR"
            )
        if block == "volatility":
            return (
                f"%B {row['bb_pct_b']:.2f} · BW pct {row['bb_bw_pct']:.2f} · "
                f"{'squeeze' if row['squeeze'] > 0 else 'expanded'}"
            )
        if block == "volume":
            return (
                f"MFI {row['mfi']:.1f} · CMF {row['cmf']:+.3f} · "
                f"rel vol {row['rel_volume']:.2f}x"
            )
        if block == "structure":
            return (
                f"bias {row['structure_bias']:+.0f} · breakout {row['breakout']:+.2f} · "
                f"range pos {row['pullback']:.2f}"
            )
        if block == "htf_bias":
            return (
                f"HTF trend {row['htf_trend']:+.2f} · HTF RSI {row['htf_rsi']:.1f} · "
                f"ST {'up' if row['htf_st_dir'] > 0 else 'down'}"
            )
    except (KeyError, TypeError, ValueError):
        pass
    return ""


def _f(value: object, default: float = 0.0) -> float:
    try:
        out = float(value)  # type: ignore[arg-type]
        return default if np.isnan(out) else out
    except (TypeError, ValueError):
        return default
