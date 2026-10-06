"""Synthetic FX market generator.

Lets you exercise the full stack — strategy, risk, execution, dashboard —
without a broker connection. The series is deliberately *realistic* rather than
random: it has trending and ranging regimes, volatility clustering, intraday
session seasonality, weekend gaps and occasional news spikes.

It is for development and smoke-testing only. Never judge a strategy's edge on
synthetic data.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np
import pandas as pd

from ..core.types import SymbolSpec
from .base import DataFeed, sanitize
from .timeframes import minutes as tf_minutes

# symbol -> (starting price, typical *daily* close-to-close sigma, digits)
# Calibrated to real majors so ATR-based stops land in a realistic pip range.
_PROFILES = {
    "EURUSD": (1.0850, 0.00550, 5),
    "GBPUSD": (1.2700, 0.00750, 5),
    "USDJPY": (150.50, 0.85000, 3),
    "AUDUSD": (0.6600, 0.00450, 5),
    "USDCHF": (0.8800, 0.00450, 5),
    "USDCAD": (1.3600, 0.00500, 5),
    "NZDUSD": (0.6100, 0.00420, 5),
    "XAUUSD": (2_350.0, 22.0000, 2),
}


class SyntheticFeed(DataFeed):
    """Deterministic (seeded) pseudo-market data generator."""

    name = "synthetic"

    #: how many bars beyond "now" are pre-generated for streaming mode
    FUTURE_BARS = 3_000

    def __init__(
        self,
        seed: int = 7,
        bars: int = 6_000,
        stream: bool = False,
        speed: float = 120.0,
        anchor: str | None = None,
    ) -> None:
        self.seed = seed
        self.bars = bars
        self.stream = stream
        self.speed = max(1.0, float(speed))
        # Without an anchor the series ends at "now", so two feeds built a
        # second apart produce different timestamps. The prices are seeded
        # and identical either way, but the session filter reads hour-of-day
        # off the index, so a run that straddles an hour boundary can take a
        # different set of trades. That made test_backtest_is_reproducible
        # flaky. Set data.end to pin it.
        self.anchor = anchor or None
        self._cache: dict[tuple[str, str, int], pd.DataFrame] = {}
        self._origin = time.monotonic()

    # -- DataFeed -----------------------------------------------------------
    def candles(
        self,
        symbol: str,
        timeframe: str,
        count: int = 1_000,
        end: Optional[datetime] = None,
    ) -> pd.DataFrame:
        total = max(count, self.bars) + (self.FUTURE_BARS if self.stream else 0)
        key = (symbol.upper(), timeframe.upper(), total)
        if key not in self._cache:
            self._cache[key] = self._generate(symbol, timeframe, total, end)
        df = self._cache[key]

        if self.stream:
            df = df.iloc[: self._visible(timeframe, len(df))]
        if end is not None:
            df = df[df.index <= pd.Timestamp(end, tz="UTC")]
        return df.tail(count).copy()

    def _visible(self, timeframe: str, length: int) -> int:
        """How much of the pre-generated series has 'happened' by now.

        In streaming mode the clock runs ``speed`` times faster than real time,
        so a 15-minute chart produces a fresh bar every few seconds — enough to
        watch the bot actually trade in the dashboard.
        """
        bar_seconds = tf_minutes(timeframe) * 60.0
        elapsed = (time.monotonic() - self._origin) * self.speed
        revealed = max(0, int(elapsed // bar_seconds))
        base = length - self.FUTURE_BARS
        return int(min(length, base + revealed))

    def symbol_spec(self, symbol: str) -> SymbolSpec:
        return default_spec(symbol)

    # -- generation ---------------------------------------------------------
    def _generate(
        self, symbol: str, timeframe: str, bars: int, end: Optional[datetime]
    ) -> pd.DataFrame:
        sym = symbol.upper()
        base_price, daily_sigma, digits = _PROFILES.get(sym, (1.1000, 0.00550, 5))
        step = tf_minutes(timeframe)
        rng = np.random.default_rng(abs(hash((sym, self.seed))) % (2**32))

        end_ts = pd.Timestamp(end or self.anchor or datetime.now(timezone.utc))
        end_ts = (
            end_ts.tz_convert("UTC") if end_ts.tzinfo else end_ts.tz_localize("UTC")
        ).floor(f"{step}min")
        if self.stream:
            # leave head-room so the stream has future bars to reveal
            end_ts = end_ts + pd.Timedelta(minutes=step * self.FUTURE_BARS)

        # Build a calendar index that skips weekends (FX closes Fri 22:00 UTC)
        index = _fx_index(end_ts, bars, step)

        n = len(index)
        bar_vol = daily_sigma / np.sqrt(1_440 / step)  # per-bar sigma

        # --- regime process: alternate trend / range blocks -----------------
        regimes = np.zeros(n)
        drift = np.zeros(n)
        i = 0
        while i < n:
            block = int(rng.integers(120, 600))
            kind = rng.choice([1, -1, 0, 0], p=[0.28, 0.28, 0.22, 0.22])
            strength = rng.uniform(0.04, 0.16) * bar_vol
            regimes[i:i + block] = kind
            drift[i:i + block] = kind * strength
            i += block

        # --- volatility clustering (GARCH-ish) ------------------------------
        vol = np.zeros(n)
        vol[0] = bar_vol
        shocks = rng.standard_normal(n)
        for t in range(1, n):
            vol[t] = np.sqrt(
                0.02 * bar_vol**2 + 0.07 * (vol[t - 1] * shocks[t - 1]) ** 2
                + 0.91 * vol[t - 1] ** 2
            )

        # --- intraday session seasonality -----------------------------------
        hours = index.hour + index.minute / 60.0
        london = np.exp(-0.5 * ((hours - 10.0) / 2.6) ** 2)
        newyork = np.exp(-0.5 * ((hours - 15.0) / 2.6) ** 2)
        tokyo = 0.55 * np.exp(-0.5 * ((hours - 2.0) / 2.4) ** 2)
        season = 0.35 + 1.25 * (london + newyork + tokyo) / 2.2
        vol = vol * season

        # --- occasional news spikes -----------------------------------------
        spike_idx = rng.choice(n, size=max(1, n // 450), replace=False)
        spike = np.zeros(n)
        spike[spike_idx] = rng.standard_normal(len(spike_idx)) * bar_vol * 7.0

        # --- mean reversion inside ranges ------------------------------------
        returns = np.zeros(n)
        price = np.zeros(n)
        price[0] = base_price
        anchor = base_price
        for t in range(1, n):
            mr = 0.0
            if regimes[t] == 0:
                mr = -0.015 * (price[t - 1] - anchor)
            else:
                anchor = 0.995 * anchor + 0.005 * price[t - 1]
            returns[t] = drift[t] + mr + vol[t] * rng.standard_normal() + spike[t]
            price[t] = max(price[t - 1] + returns[t], base_price * 0.25)

        # --- OHLC from the close path ----------------------------------------
        close = price
        open_ = np.concatenate([[base_price], close[:-1]])
        wick = np.abs(rng.standard_normal(n)) * vol * 0.9
        high = np.maximum(open_, close) + wick
        low = np.minimum(open_, close) - np.abs(rng.standard_normal(n)) * vol * 0.9

        volume = (
            400 * season
            + 900 * np.abs(returns) / np.maximum(bar_vol, 1e-12)
            + rng.integers(20, 160, size=n)
        ).round()

        # Realistic retail/ECN spread for a major: ~0.2 pips in the London/NY
        # overlap, widening into the illiquid Asian roll-over.
        spread_points = np.clip(
            (1.0 / np.maximum(season, 0.25)) * rng.uniform(1.2, 3.0, size=n), 1, 30
        ).round()

        df = pd.DataFrame(
            {
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
                "spread": spread_points,
            },
            index=index,
        ).round({"open": digits, "high": digits, "low": digits, "close": digits})
        df.index.name = "time"
        return sanitize(df)


def _fx_index(end_ts: pd.Timestamp, bars: int, step_minutes: int) -> pd.DatetimeIndex:
    """Timestamps going back from ``end_ts``, skipping the FX weekend."""
    # Over-generate then filter, so we still end up with ``bars`` rows.
    span = int(bars * 1.6) + 100
    raw = pd.date_range(
        end=end_ts, periods=span, freq=f"{step_minutes}min", tz="UTC"
    )
    dow = raw.dayofweek
    hour = raw.hour
    open_mask = ~(
        (dow == 5)                      # Saturday
        | ((dow == 6) & (hour < 22))    # Sunday before 22:00 UTC
        | ((dow == 4) & (hour >= 22))   # Friday after 22:00 UTC
    )
    filtered = raw[open_mask]
    return filtered[-bars:]


def default_spec(symbol: str) -> SymbolSpec:
    """Sensible contract spec for a pair when no broker is connected."""
    sym = symbol.upper()
    _, _, digits = _PROFILES.get(sym, (1.1, 0.0055, 5))
    point = 10 ** (-digits)

    if sym.startswith("XAU"):
        return SymbolSpec(
            symbol=sym, digits=2, point=0.01, tick_size=0.01, tick_value=1.0,
            contract_size=100.0, min_lot=0.01, max_lot=50.0, lot_step=0.01,
            margin_rate=0.05, commission_per_lot=7.0, base="XAU", quote="USD",
        )

    quote = sym[3:6] if len(sym) >= 6 else "USD"
    # tick_value for 1 standard lot, quoted in the *quote* currency, then
    # roughly converted to USD for non-USD quotes.
    tick_value = 100_000.0 * point
    if quote == "JPY":
        tick_value = 100_000.0 * point / 150.0
    elif quote not in ("USD",):
        tick_value = 100_000.0 * point  # close enough for majors vs USD

    return SymbolSpec(
        symbol=sym,
        digits=digits,
        point=point,
        tick_size=point,
        tick_value=round(tick_value, 6),
        contract_size=100_000.0,
        min_lot=0.01,
        max_lot=100.0,
        lot_step=0.01,
        margin_rate=0.0333,
        commission_per_lot=7.0,
    )
