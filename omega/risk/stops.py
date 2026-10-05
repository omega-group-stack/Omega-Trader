"""Stop-loss / take-profit placement and trade management.

Three stop modes are supported:

``atr``        volatility-scaled: ``entry ± atr_stop_mult * ATR``
``fixed_pips`` a constant pip distance
``structure``  behind the last confirmed swing (plus an ATR buffer), which is
               where a trade thesis is actually invalidated — falls back to ATR
               when no clean swing is available.

On top of that the module provides break-even moves, partial take-profits and
a chandelier trailing stop that only ever ratchets in the trade's favour.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from ..config import RiskConfig
from ..core.types import Position, Side, Signal, SymbolSpec


@dataclass(slots=True)
class StopPlan:
    stop_loss: float
    take_profit: Optional[float]
    stop_distance: float
    stop_pips: float
    mode: str
    note: str = ""


def build_stop_plan(
    signal: Signal, spec: SymbolSpec, cfg: RiskConfig, entry_price: float
) -> StopPlan:
    """Decide the initial protective stop and target for a fresh entry."""
    sign = signal.direction.sign
    atr = signal.atr or 0.0
    mode = (cfg.stop_mode or "atr").lower()
    note = ""

    if mode == "fixed_pips":
        distance = spec.pips_to_price(cfg.fixed_stop_pips)
    elif mode == "structure":
        swing = signal.meta.get("swing_low" if sign > 0 else "swing_high") or 0.0
        buffer = 0.35 * atr
        if swing and ((sign > 0 and swing < entry_price) or (sign < 0 and swing > entry_price)):
            distance = abs(entry_price - float(swing)) + buffer
            note = f"structure stop behind swing {float(swing):.5f}"
        else:
            distance = cfg.atr_stop_mult * atr
            note = "no valid swing — fell back to ATR stop"
    else:
        distance = cfg.atr_stop_mult * atr

    if distance <= 0:
        distance = spec.pips_to_price(cfg.fixed_stop_pips)
        note = note or "ATR unavailable — used fixed pip stop"

    # clamp to the configured pip band
    distance = _clamp_distance(distance, spec, cfg)

    stop_loss = spec.normalize_price(entry_price - sign * distance)
    take_profit = _target(entry_price, sign, distance, atr, spec, cfg)

    return StopPlan(
        stop_loss=stop_loss,
        take_profit=take_profit,
        stop_distance=distance,
        stop_pips=spec.price_to_pips(distance),
        mode=mode,
        note=note,
    )


def _target(
    entry: float,
    sign: int,
    distance: float,
    atr: float,
    spec: SymbolSpec,
    cfg: RiskConfig,
) -> Optional[float]:
    target_mode = (cfg.target_mode or "rr").lower()
    if target_mode == "none":
        return None
    if target_mode == "atr" and atr > 0:
        reach = cfg.atr_target_mult * atr
    else:
        reach = cfg.risk_reward * distance
    return spec.normalize_price(entry + sign * reach)


def _clamp_distance(distance: float, spec: SymbolSpec, cfg: RiskConfig) -> float:
    min_d = spec.pips_to_price(cfg.min_stop_pips)
    max_d = spec.pips_to_price(cfg.max_stop_pips)
    return max(min_d, min(distance, max_d)) if max_d > min_d else max(distance, min_d)


# --------------------------------------------------------------------------- #
# In-trade management
# --------------------------------------------------------------------------- #
def manage_position(
    position: Position,
    price: float,
    atr: float,
    spec: SymbolSpec,
    cfg: RiskConfig,
    *,
    highest: Optional[float] = None,
    lowest: Optional[float] = None,
) -> Tuple[Optional[float], str]:
    """Return ``(new_stop_loss, reason)`` — ``None`` when nothing should change.

    The stop can only ever move in the trade's favour (a ratchet), which is what
    keeps a winner from turning into a loser.
    """
    if not cfg.use_trailing_stop and cfg.breakeven_at_r <= 0:
        return None, ""

    sign = position.side.sign
    r = position.r_multiple(price)
    current = position.stop_loss
    proposals: list[tuple[float, str]] = []

    # --- break-even ---------------------------------------------------------
    if (
        cfg.breakeven_at_r > 0
        and not position.breakeven_done
        and r >= cfg.breakeven_at_r
    ):
        offset = spec.pips_to_price(cfg.breakeven_offset_pips)
        proposals.append(
            (position.entry_price + sign * offset, f"break-even at +{r:.2f}R")
        )

    # --- trailing -----------------------------------------------------------
    if cfg.use_trailing_stop and r >= cfg.trail_activate_r and atr > 0:
        trail_mode = (cfg.trail_mode or "chandelier").lower()
        if trail_mode == "breakeven_only":
            pass
        elif trail_mode == "atr":
            proposals.append(
                (price - sign * cfg.trail_atr_mult * atr, f"ATR trail at +{r:.2f}R")
            )
        else:  # chandelier: anchor on the extreme reached since entry
            anchor = (highest if sign > 0 else lowest)
            if anchor is None:
                anchor = price
            proposals.append(
                (anchor - sign * cfg.trail_atr_mult * atr,
                 f"chandelier trail at +{r:.2f}R")
            )

    if not proposals:
        return None, ""

    # pick the tightest favourable proposal, then enforce the ratchet
    best, reason = max(proposals, key=lambda p: p[0] * sign)
    best = spec.normalize_price(best)

    if current is not None and best * sign <= current * sign:
        return None, ""
    if best * sign >= price * sign:  # never place a stop through current price
        return None, ""
    return best, reason


def partial_close_lots(
    position: Position, price: float, spec: SymbolSpec, cfg: RiskConfig
) -> float:
    """Lots to scale out at the configured R multiple (0 = nothing to do)."""
    if cfg.partial_tp_r <= 0 or cfg.partial_tp_fraction <= 0:
        return 0.0
    if position.partials_done >= 1:
        return 0.0
    if position.r_multiple(price) < cfg.partial_tp_r:
        return 0.0

    lots = spec.normalize_lots(position.initial_lots * cfg.partial_tp_fraction)
    remaining = spec.normalize_lots(position.lots - lots)
    if lots <= 0 or remaining <= 0:
        return 0.0
    return lots
