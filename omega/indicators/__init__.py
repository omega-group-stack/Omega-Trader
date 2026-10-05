"""Indicator battery used by the Omega ensemble.

All functions take/return pandas objects and are vectorised, so the same code
powers both backtests (whole series at once) and live trading (rolling window).
"""

from .momentum import (
    awesome_oscillator,
    cci,
    macd,
    momentum_divergence,
    roc,
    rsi,
    stoch_rsi,
    stochastic,
    williams_r,
)
from .structure import (
    breakout_strength,
    candle_signal,
    gap_signal,
    last_swing_levels,
    pullback_depth,
    structure_bias,
    swing_high,
    swing_low,
)
from .trend import (
    adx,
    dema,
    ema,
    hma,
    ichimoku,
    linreg_slope,
    parabolic_sar,
    rma,
    sma,
    supertrend,
    true_range,
    wma,
)
from .volatility import (
    atr,
    bollinger,
    choppiness,
    donchian,
    keltner,
    natr,
    realized_volatility,
    rolling_percentile,
    squeeze,
)
from .volume import (
    chaikin_money_flow,
    force_index,
    money_flow_index,
    obv,
    relative_volume,
    volume_oscillator,
    vwap,
)

__all__ = [
    # trend
    "sma", "ema", "wma", "hma", "dema", "rma", "true_range", "adx", "supertrend",
    "parabolic_sar", "linreg_slope", "ichimoku",
    # momentum
    "rsi", "stoch_rsi", "stochastic", "macd", "cci", "roc", "williams_r",
    "awesome_oscillator", "momentum_divergence",
    # volatility
    "atr", "natr", "bollinger", "keltner", "donchian", "squeeze", "choppiness",
    "rolling_percentile", "realized_volatility",
    # volume
    "obv", "money_flow_index", "chaikin_money_flow", "volume_oscillator",
    "force_index", "vwap", "relative_volume",
    # structure
    "swing_high", "swing_low", "last_swing_levels", "structure_bias",
    "breakout_strength", "pullback_depth", "candle_signal", "gap_signal",
]
