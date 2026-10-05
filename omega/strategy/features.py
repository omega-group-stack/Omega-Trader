"""Feature engineering: turn raw OHLCV into every indicator the ensemble votes on.

Everything is computed **vectorised, once per data refresh**, so a 50k-bar
backtest and a live bar use exactly the same code path — which means a backtest
result is a faithful simulation of what live would have done.

Look-ahead safety
-----------------
* Indicators only use data up to and including the current bar.
* Higher-timeframe features are stamped at the HTF bar's *close* time and then
  forward-filled onto the lower timeframe, so an H4 bar never leaks into the
  M15 bars that formed it.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .. import indicators as ind
from ..config import IndicatorConfig
from ..data.base import resample
from ..data.timeframes import delta as tf_delta


def build_features(df: pd.DataFrame, cfg: IndicatorConfig) -> pd.DataFrame:
    """Compute the full indicator panel for a lower-timeframe OHLCV frame."""
    o, h, l, c, v = df["open"], df["high"], df["low"], df["close"], df["volume"]
    f = pd.DataFrame(index=df.index)

    # --- price & volatility backbone --------------------------------------
    f["close"] = c
    f["open"] = o
    f["high"] = h
    f["low"] = l
    f["volume"] = v
    f["spread"] = df["spread"]
    f["atr"] = ind.atr(h, l, c, cfg.atr_period)
    f["atr_pct"] = ind.rolling_percentile(f["atr"], cfg.atr_percentile_window)
    f["natr"] = ind.natr(h, l, c, cfg.atr_period)
    atr_safe = f["atr"].replace(0.0, np.nan)

    # --- trend -------------------------------------------------------------
    f["ema_fast"] = ind.ema(c, cfg.ema_fast)
    f["ema_slow"] = ind.ema(c, cfg.ema_slow)
    f["ema_base"] = ind.ema(c, cfg.ema_baseline)
    f["ema_spread_atr"] = (f["ema_fast"] - f["ema_slow"]) / atr_safe
    f["price_vs_base_atr"] = (c - f["ema_base"]) / atr_safe

    adx_df = ind.adx(h, l, c, cfg.adx_period)
    f["adx"] = adx_df["adx"]
    f["plus_di"] = adx_df["plus_di"]
    f["minus_di"] = adx_df["minus_di"]
    f["di_delta"] = (adx_df["plus_di"] - adx_df["minus_di"]) / 50.0

    st = ind.supertrend(h, l, c, cfg.supertrend_period, cfg.supertrend_mult)
    f["supertrend"] = st["supertrend"]
    f["supertrend_dir"] = st["direction"]

    f["psar"] = ind.parabolic_sar(h, l, cfg.psar_step, cfg.psar_max)
    f["psar_dir"] = np.sign(c - f["psar"])
    f["slope"] = ind.linreg_slope(c, cfg.slope_period)
    f["slope_z"] = _zscore(f["slope"], 100)

    # --- momentum ----------------------------------------------------------
    f["rsi"] = ind.rsi(c, cfg.rsi_period)
    stoch = ind.stochastic(h, l, c, cfg.stoch_k, cfg.stoch_d, cfg.stoch_smooth)
    f["stoch_k"] = stoch["k"]
    f["stoch_d"] = stoch["d"]
    macd_df = ind.macd(c, cfg.macd_fast, cfg.macd_slow, cfg.macd_signal)
    f["macd"] = macd_df["macd"]
    f["macd_signal"] = macd_df["signal"]
    f["macd_hist_atr"] = macd_df["hist"] / atr_safe
    f["cci"] = ind.cci(h, l, c, cfg.cci_period)
    f["roc"] = ind.roc(c, cfg.roc_period)
    f["roc_z"] = _zscore(f["roc"], 100)
    f["willr"] = ind.williams_r(h, l, c, cfg.willr_period)
    f["ao"] = ind.awesome_oscillator(h, l)
    f["divergence"] = ind.momentum_divergence(c, f["rsi"], 20)

    # --- volatility / envelopes --------------------------------------------
    bb = ind.bollinger(c, cfg.bb_period, cfg.bb_std)
    f["bb_pct_b"] = bb["pct_b"]
    f["bb_bandwidth"] = bb["bandwidth"]
    f["bb_bw_pct"] = ind.rolling_percentile(bb["bandwidth"], cfg.atr_percentile_window)
    kc = ind.keltner(h, l, c, cfg.keltner_period, cfg.keltner_mult)
    f["kc_upper"] = kc["upper"]
    f["kc_lower"] = kc["lower"]
    f["kc_break"] = np.where(
        c > kc["upper"], (c - kc["upper"]) / atr_safe,
        np.where(c < kc["lower"], (c - kc["lower"]) / atr_safe, 0.0),
    )
    dc = ind.donchian(h, l, cfg.donchian_period)
    f["donchian_upper"] = dc["upper"]
    f["donchian_lower"] = dc["lower"]
    f["squeeze"] = ind.squeeze(h, l, c, cfg.bb_period).astype(float)
    f["chop"] = ind.choppiness(h, l, c, cfg.chop_period)

    # --- volume / flow ------------------------------------------------------
    f["mfi"] = ind.money_flow_index(h, l, c, v, cfg.mfi_period)
    f["cmf"] = ind.chaikin_money_flow(h, l, c, v, cfg.cmf_period)
    obv = ind.obv(c, v)
    f["obv_slope"] = _zscore(obv.diff(cfg.obv_smooth), 100)
    f["rel_volume"] = ind.relative_volume(v, cfg.vol_ma_period)
    f["force_z"] = _zscore(ind.force_index(c, v, 13), 100)

    # --- structure / price action -------------------------------------------
    swings = ind.last_swing_levels(h, l, 3, 3)
    f["swing_high"] = swings["swing_high"]
    f["swing_low"] = swings["swing_low"]
    f["structure_bias"] = ind.structure_bias(h, l)
    f["breakout"] = ind.breakout_strength(h, l, c, cfg.donchian_period)
    f["pullback"] = ind.pullback_depth(h, l, c, cfg.donchian_period)
    f["candle"] = ind.candle_signal(o, h, l, c)

    return f


def build_htf_features(
    ltf_df: pd.DataFrame,
    htf_df: pd.DataFrame | None,
    htf_timeframe: str,
    cfg: IndicatorConfig,
) -> pd.DataFrame:
    """Higher-timeframe bias features aligned onto the lower-timeframe index.

    If ``htf_df`` is None the HTF series is derived by resampling ``ltf_df``.
    The result is indexed exactly like ``ltf_df`` and contains no look-ahead:
    each HTF value is only visible from the moment that HTF bar closed.
    """
    source = htf_df if htf_df is not None and len(htf_df) else resample(ltf_df, htf_timeframe)
    if source is None or len(source) < 10:
        return pd.DataFrame(
            {"htf_trend": 0.0, "htf_rsi": 50.0, "htf_st_dir": 0.0, "htf_slope": 0.0},
            index=ltf_df.index,
        )

    h, l, c = source["high"], source["low"], source["close"]
    atr_h = ind.atr(h, l, c, cfg.atr_period).replace(0.0, np.nan)
    ema_f = ind.ema(c, cfg.ema_fast)
    ema_s = ind.ema(c, cfg.ema_slow)
    st = ind.supertrend(h, l, c, cfg.supertrend_period, cfg.supertrend_mult)

    htf = pd.DataFrame(
        {
            "htf_trend": np.tanh((ema_f - ema_s) / atr_h),
            "htf_rsi": ind.rsi(c, cfg.rsi_period),
            "htf_st_dir": st["direction"],
            "htf_slope": np.tanh(_zscore(ind.linreg_slope(c, cfg.slope_period), 100) / 2),
        },
        index=source.index,
    )

    # Stamp each HTF row at the moment its bar *closed*, then forward-fill.
    htf.index = htf.index + tf_delta(htf_timeframe)
    aligned = htf.reindex(htf.index.union(ltf_df.index)).ffill().reindex(ltf_df.index)
    return aligned.fillna({"htf_trend": 0.0, "htf_rsi": 50.0,
                           "htf_st_dir": 0.0, "htf_slope": 0.0})


def _zscore(series: pd.Series, window: int) -> pd.Series:
    mean = series.rolling(window, min_periods=max(5, window // 5)).mean()
    std = series.rolling(window, min_periods=max(5, window // 5)).std(ddof=0)
    return ((series - mean) / std.replace(0.0, np.nan)).clip(-5, 5)
