"""Market-regime classification.

A signal that is brilliant in a trend is a losing machine in a range. Omega
therefore classifies every bar into a regime and re-weights the ensemble
accordingly — trend-followers get louder in trends, oscillators get louder in
ranges, and position size shrinks when volatility is extreme.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import RegimeConfig, WeightsConfig
from ..core.types import Regime


def classify(features: pd.DataFrame, cfg: RegimeConfig) -> pd.Series:
    """Vectorised regime label per bar."""
    if not cfg.enabled:
        return pd.Series(Regime.RANGE.value, index=features.index)

    adx = features["adx"].fillna(0.0)
    chop = features["chop"].fillna(50.0)
    atr_pct = features["atr_pct"].fillna(0.5)
    direction = np.sign(
        features["ema_spread_atr"].fillna(0.0) + features["di_delta"].fillna(0.0)
    )

    trending = (adx >= cfg.adx_trend) & (chop < cfg.chop_range)
    ranging = (adx <= cfg.adx_range) | (chop >= cfg.chop_range)

    labels = np.full(len(features), Regime.RANGE.value, dtype=object)
    labels[np.asarray(ranging)] = Regime.RANGE.value
    labels[np.asarray(trending & (direction > 0))] = Regime.TREND_UP.value
    labels[np.asarray(trending & (direction <= 0))] = Regime.TREND_DOWN.value

    # Volatility overlays win over the trend/range label when extreme.
    labels[np.asarray((atr_pct >= cfg.atr_high_pct) & ~trending)] = Regime.VOLATILE.value
    labels[np.asarray(atr_pct <= cfg.atr_low_pct)] = Regime.QUIET.value

    return pd.Series(labels, index=features.index, dtype=object)


def weights_for(regime: Regime, base: WeightsConfig, cfg: RegimeConfig) -> dict[str, float]:
    """Apply the regime multipliers to the configured base weights."""
    weights = {
        "trend": base.trend,
        "momentum": base.momentum,
        "volatility": base.volatility,
        "volume": base.volume,
        "structure": base.structure,
        "htf_bias": base.htf_bias,
    }
    if not cfg.enabled:
        return weights

    multipliers = {
        Regime.TREND_UP: cfg.trend_multipliers,
        Regime.TREND_DOWN: cfg.trend_multipliers,
        Regime.RANGE: cfg.range_multipliers,
        Regime.VOLATILE: cfg.volatile_multipliers,
        Regime.QUIET: cfg.quiet_multipliers,
    }.get(regime, {})

    return {k: w * float(multipliers.get(k, 1.0)) for k, w in weights.items()}


def risk_multiplier(regime: Regime, atr_percentile: float) -> float:
    """Shrink size when volatility is extreme or the market is dead."""
    mult = 1.0
    if regime is Regime.VOLATILE:
        mult *= 0.7
    elif regime is Regime.QUIET:
        mult *= 0.85
    if atr_percentile >= 0.95:
        mult *= 0.75
    elif atr_percentile <= 0.05:
        mult *= 0.9
    return float(np.clip(mult, 0.3, 1.0))
