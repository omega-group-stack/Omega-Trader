"""Cross-sectional currency strength.

A single pair cannot tell you *why* it moved. EURUSD rising means one of
three things, and the candle looks identical in all three:

1. EUR is strong against everything  — macro flow, tends to persist
2. USD is weak against everything    — macro flow, tends to persist
3. the EURUSD rate drifted and neither currency moved against anyone else
   — pair-level noise

Give the strategy the whole cross-section and the three become separable.
For each currency, average the trailing log return of every pair containing
it, signed positive where it is the base and negative where it is the quote.
A currency that is rising against all seven of its counterparts scores high;
one that is only rising against a single counterpart scores near zero.

This is the currency-momentum factor from Menkhoff, Sarno, Schmeling &
Schrimpf (2012), used here as a *confirmation filter* rather than as a
standalone strategy: it does not generate entries, it vetoes the ones the
cross-section disagrees with.

Look-ahead safety: every value at time ``t`` is built from returns over bars
ending at ``t``, and alignment onto a pair's own index uses forward-fill
only, which can repeat a past value but never borrow a future one.
"""

from __future__ import annotations

import logging
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

#: The eight currencies spanned by the seven USD majors.
MAJORS: Tuple[str, ...] = ("USD", "EUR", "GBP", "JPY", "CHF", "AUD", "NZD", "CAD")


def split_pair(symbol: str) -> Optional[Tuple[str, str]]:
    """``"EURUSD"`` -> ``("EUR", "USD")``. ``None`` if it is not a 6-letter pair.

    Tolerates broker suffixes such as ``EURUSD.pro``, ``EURUSDm`` or
    ``EURUSD_raw``, which are extremely common and would otherwise silently
    disable the whole feature on a live account.
    """
    if not symbol:
        return None
    cleaned = "".join(ch for ch in symbol.upper() if ch.isalpha())
    if len(cleaned) < 6:
        return None
    base, quote = cleaned[:3], cleaned[3:6]
    if base in MAJORS and quote in MAJORS and base != quote:
        return base, quote
    return None


def build_strength(
    closes: Dict[str, pd.Series],
    lookback: int = 24,
    smooth: int = 3,
    vol_window: int = 500,
) -> pd.DataFrame:
    """Per-currency strength over time.

    ``closes`` maps symbol -> close series. Any set of pairs works; the more
    pairs a currency appears in, the better conditioned its score. Returns a
    frame indexed by the union of all timestamps, one column per currency.

    Each pair's trailing return is divided by *that pair's own* trailing
    volatility before averaging. This is the part that makes the number mean
    something. The obvious alternative — z-scoring the finished scores across
    the cross-section at each timestamp — was tried first and is wrong: it
    rescales every timestamp to the same dispersion, so "EUR rose against all
    seven counterparts" and "EUR rose against one counterpart and nothing
    else moved" come out with the *same* score. That erases exactly the
    breadth information the filter exists to measure. Normalising per pair in
    the time dimension keeps breadth (averaging three confirming pairs beats
    one confirming and two flat) while still making calm and volatile regimes
    comparable, which is what a single fixed threshold needs.

    Units are therefore "average number of typical moves", roughly ±1.
    """
    if not closes:
        return pd.DataFrame()

    usable: Dict[Tuple[str, str], pd.Series] = {}
    for symbol, series in closes.items():
        pair = split_pair(symbol)
        if pair is None:
            log.debug("strength: skipping unrecognised symbol %r", symbol)
            continue
        clean = pd.Series(series).astype(float)
        clean = clean[~clean.index.duplicated(keep="last")].sort_index()

        # Reject degenerate series rather than let them poison the basket.
        # A file of zeros gives log(0) = -inf, which propagates through the
        # mean into every currency and silently vetoes the entire book. This
        # is not hypothetical: one pair downloaded for experiment 3 arrived
        # as all-zero rows stamped 1970-01-01.
        positive = clean[clean > 0]
        if len(positive) < 2 or positive.nunique() < 2:
            log.warning("strength: ignoring %s — series is empty, constant or "
                        "non-positive", symbol)
            continue
        if len(positive) < len(clean):
            log.warning("strength: dropping %d non-positive rows from %s",
                        len(clean) - len(positive), symbol)
        usable[pair] = positive

    if len(usable) < 2:
        log.warning(
            "Currency strength needs at least 2 recognisable pairs, got %d — "
            "the filter will be inert.", len(usable)
        )
        return pd.DataFrame()

    index = pd.DatetimeIndex(sorted(set().union(*[s.index for s in usable.values()])))

    # Trailing log return per pair, scaled by that pair's own volatility.
    # Computed on each pair's own index first, then aligned, so a pair with
    # missing bars does not smear its neighbours.
    min_periods = max(20, lookback)
    returns: Dict[Tuple[str, str], pd.Series] = {}
    for pair, series in usable.items():
        logret = np.log(series).diff(lookback)
        # Causal scale: the rolling window ends at the current bar, so no
        # future volatility leaks into the present score.
        scale = logret.rolling(vol_window, min_periods=min_periods).std()
        scaled = logret / scale.replace(0.0, np.nan)
        # One dislocated pair (a peg break, a flash crash) must not be able to
        # swamp the average and veto every trade in the book for days.
        scaled = scaled.clip(-5.0, 5.0)
        returns[pair] = scaled.reindex(index).ffill()

    currencies = sorted({c for pair in usable for c in pair})
    totals = pd.DataFrame(0.0, index=index, columns=currencies)
    counts = pd.DataFrame(0.0, index=index, columns=currencies)

    for (base, quote), logret in returns.items():
        present = logret.notna()
        totals.loc[:, base] = totals[base].add(logret.fillna(0.0), fill_value=0.0)
        totals.loc[:, quote] = totals[quote].sub(logret.fillna(0.0), fill_value=0.0)
        counts.loc[present, base] += 1.0
        counts.loc[present, quote] += 1.0

    strength = totals / counts.replace(0.0, np.nan)

    if smooth and smooth > 1:
        strength = strength.rolling(smooth, min_periods=1).mean()

    # Centre the cross-section so the scores sum to zero: strength is a purely
    # relative statement, and a basket-wide USD move should not drag every
    # currency's score in the same direction. Centring is safe here — unlike
    # dividing by the cross-sectional spread, subtracting the mean does not
    # destroy the breadth signal.
    centred = strength.sub(strength.mean(axis=1), axis=0)

    return centred.fillna(0.0)


def pair_spread(strength: pd.DataFrame, symbol: str) -> pd.Series:
    """``strength(base) - strength(quote)`` for one pair.

    Positive means the cross-section favours the base currency, i.e. it
    agrees with a long position. Returns an all-zero series (the neutral
    value) when the pair or its currencies are unavailable, so a missing feed
    degrades to "no opinion" rather than to a crash or a silent veto.
    """
    pair = split_pair(symbol)
    if strength.empty or pair is None:
        return pd.Series(dtype=float)
    base, quote = pair
    if base not in strength.columns or quote not in strength.columns:
        return pd.Series(0.0, index=strength.index)
    return strength[base] - strength[quote]


def align(series: pd.Series, index: pd.DatetimeIndex) -> pd.Series:
    """Put a strength series onto a pair's own bar index.

    Forward-fill only: repeating the last known cross-section is honest,
    interpolating toward a future value is not.
    """
    if series.empty:
        return pd.Series(0.0, index=index)
    return series.reindex(index.union(series.index)).ffill().reindex(index).fillna(0.0)


def load_closes(
    feed,
    symbols: Sequence[str],
    timeframe: str,
    count: int,
) -> Dict[str, pd.Series]:
    """Pull close series for the strength basket, skipping what is missing.

    A missing pair is a warning, not an error: the basket still works with
    four pairs, just less precisely, and refusing to start because one CSV is
    absent would be a poor trade-off on a live account.
    """
    closes: Dict[str, pd.Series] = {}
    for symbol in symbols:
        try:
            frame = feed.candles(symbol, timeframe, count)
        except Exception as exc:
            log.warning("strength basket: %s unavailable (%s)", symbol, exc)
            continue
        if frame is None or frame.empty:
            log.warning("strength basket: %s returned no bars", symbol)
            continue
        closes[symbol.upper()] = frame["close"]
    return closes


def describe(strength: pd.DataFrame, at: Optional[pd.Timestamp] = None) -> str:
    """Human-readable ranking, for the dashboard and the Telegram /signal view."""
    if strength.empty:
        return "no cross-sectional data"
    row = strength.iloc[-1] if at is None else strength.loc[at]
    ordered = row.sort_values(ascending=False)
    return "  ".join(f"{c} {v:+.2f}" for c, v in ordered.items())
