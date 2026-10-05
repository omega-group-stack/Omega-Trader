"""Trading-session filter.

FX liquidity is wildly uneven across the day. Spreads blow out at the Asian
roll-over, and Friday evening / Sunday open are where gap risk lives. This
module answers one question: *is this bar inside a window we want to trade?*
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from typing import List, Tuple
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from ..config import SessionConfig


def _parse(hhmm: str) -> time:
    hh, _, mm = hhmm.partition(":")
    return time(int(hh), int(mm or 0))


def windows_of(cfg: SessionConfig) -> List[Tuple[time, time]]:
    return [(_parse(a), _parse(b)) for a, b in cfg.windows if a and b]


def is_open(ts: datetime, cfg: SessionConfig) -> tuple[bool, str]:
    """``(allowed, reason_if_blocked)`` for a single timestamp."""
    if not cfg.enabled:
        return True, ""

    local = _to_zone(ts, cfg.timezone)

    if local.weekday() not in set(cfg.trade_days):
        return False, f"session: {local:%a} is not a trading day"

    if cfg.avoid_friday_after and local.weekday() == 4:
        if local.time() >= _parse(cfg.avoid_friday_after):
            return False, "session: Friday close-out window"

    wins = windows_of(cfg)
    if not wins:
        return True, ""

    t = local.time()
    for start, end in wins:
        if start <= end:
            if start <= t <= end:
                return True, ""
        else:  # window wraps past midnight
            if t >= start or t <= end:
                return True, ""
    return False, f"session: {t:%H:%M} outside trading windows"


def mask(index: pd.DatetimeIndex, cfg: SessionConfig) -> pd.Series:
    """Vectorised version of :func:`is_open` for a whole index."""
    if not cfg.enabled:
        return pd.Series(True, index=index)

    idx = index.tz_convert(ZoneInfo(cfg.timezone)) if index.tz is not None else index
    minutes = idx.hour * 60 + idx.minute
    allowed = np.zeros(len(idx), dtype=bool)

    for start, end in windows_of(cfg):
        s = start.hour * 60 + start.minute
        e = end.hour * 60 + end.minute
        allowed |= (minutes >= s) & (minutes <= e) if s <= e else \
            ((minutes >= s) | (minutes <= e))
    if not windows_of(cfg):
        allowed[:] = True

    allowed &= np.isin(idx.dayofweek, list(cfg.trade_days))

    if cfg.avoid_friday_after:
        cut = _parse(cfg.avoid_friday_after)
        cut_m = cut.hour * 60 + cut.minute
        allowed &= ~((idx.dayofweek == 4) & (minutes >= cut_m))

    return pd.Series(allowed, index=index)


def should_flatten_for_weekend(ts: datetime, cfg: SessionConfig) -> bool:
    """True inside the final hour before the FX weekend close."""
    if not (cfg.enabled and cfg.close_all_before_weekend):
        return False
    local = _to_zone(ts, cfg.timezone)
    if local.weekday() != 4:
        return False
    cut = _parse(cfg.avoid_friday_after or "20:00")
    cut_dt = local.replace(hour=cut.hour, minute=cut.minute, second=0, microsecond=0)
    return local >= cut_dt


def _to_zone(ts: datetime, tz_name: str) -> datetime:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    try:
        return ts.astimezone(ZoneInfo(tz_name or "UTC"))
    except Exception:
        return ts.astimezone(timezone.utc)
