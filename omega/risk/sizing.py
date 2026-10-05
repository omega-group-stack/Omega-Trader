"""Position sizing.

The headline rule: **risk a fixed percentage of equity between entry and the
initial stop-loss.** Lot size is therefore derived from the stop distance, never
guessed — a wide stop gets a small position, a tight stop a larger one, and the
money at risk stays constant.

    risk_money   = equity * risk_pct / 100
    loss_per_lot = stop_distance / tick_size * tick_value
    lots         = risk_money / loss_per_lot      (floored to the lot step)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict

from ..config import RiskConfig
from ..core.types import SymbolSpec


@dataclass(slots=True)
class SizingResult:
    lots: float
    risk_money: float
    risk_pct: float
    stop_distance: float
    stop_pips: float
    loss_per_lot: float
    margin_required: float
    notes: list[str] = field(default_factory=list)
    detail: Dict[str, float] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.lots > 0


def position_size(
    spec: SymbolSpec,
    cfg: RiskConfig,
    equity: float,
    stop_distance: float,
    entry_price: float,
    *,
    confidence: float = 1.0,
    risk_multiplier: float = 1.0,
    symbol_weight: float = 1.0,
    free_margin: float | None = None,
) -> SizingResult:
    """Compute the lot size for one trade.

    ``risk_multiplier`` folds in regime scaling, losing-streak de-risking and
    any other global throttle; ``confidence`` is the ensemble's conviction and
    optionally scales risk between ``min_risk_multiplier`` and
    ``max_risk_multiplier``.
    """
    notes: list[str] = []
    stop_distance = abs(float(stop_distance))
    stop_pips = spec.price_to_pips(stop_distance)

    if stop_distance <= 0:
        return SizingResult(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
                            ["stop distance is zero — cannot size"])

    # --- effective risk percentage -----------------------------------------
    risk_pct = float(cfg.risk_per_trade_pct)
    if cfg.scale_risk_with_confidence:
        lo, hi = cfg.min_risk_multiplier, cfg.max_risk_multiplier
        span = max(1e-9, 1.0 - 0.0)
        conf_mult = lo + (hi - lo) * max(0.0, min(1.0, confidence)) / span
        risk_pct *= conf_mult
        notes.append(f"confidence {confidence:.2f} -> risk x{conf_mult:.2f}")

    if risk_multiplier != 1.0:
        risk_pct *= risk_multiplier
        notes.append(f"risk throttle x{risk_multiplier:.2f}")
    if symbol_weight != 1.0:
        risk_pct *= symbol_weight
        notes.append(f"symbol weight x{symbol_weight:.2f}")

    risk_money = max(0.0, equity) * risk_pct / 100.0
    loss_per_lot = spec.money_per_lot(stop_distance)
    if loss_per_lot <= 0:
        return SizingResult(0.0, risk_money, risk_pct, stop_distance, stop_pips, 0.0,
                            0.0, ["broker spec gives zero loss-per-lot"])

    # --- raw size by mode ----------------------------------------------------
    mode = (cfg.sizing_mode or "percent_risk").lower()
    if mode == "fixed_lot":
        raw_lots = cfg.fixed_lot
        notes.append(f"fixed lot {cfg.fixed_lot}")
    elif mode == "equity_fraction":
        notional = equity * cfg.equity_fraction
        raw_lots = notional / max(spec.notional_per_lot(entry_price), 1e-9)
        notes.append(f"equity fraction {cfg.equity_fraction:.2%}")
    else:
        raw_lots = risk_money / loss_per_lot

    # --- commission drag: make the *total* loss equal the risk budget ---------
    if mode == "percent_risk" and spec.commission_per_lot > 0:
        denom = loss_per_lot + spec.commission_per_lot
        raw_lots = risk_money / denom

    lots = spec.normalize_lots(raw_lots)
    if lots <= 0:
        notes.append(
            f"size {raw_lots:.4f} below broker minimum {spec.min_lot} — "
            f"increase risk % or reduce stop distance"
        )

    # --- margin guard ---------------------------------------------------------
    notional_per_lot = spec.notional_per_lot(entry_price)
    margin_per_lot = notional_per_lot * spec.margin_rate
    margin_required = lots * margin_per_lot
    if free_margin is not None and margin_required > free_margin > 0:
        affordable = spec.normalize_lots(free_margin / max(margin_per_lot, 1e-9))
        notes.append(
            f"margin-capped {lots:.2f} -> {affordable:.2f} lots "
            f"(need {margin_required:.0f}, free {free_margin:.0f})"
        )
        lots = affordable
        margin_required = lots * margin_per_lot

    if cfg.max_lots_per_symbol and lots > cfg.max_lots_per_symbol:
        notes.append(f"capped at max_lots_per_symbol={cfg.max_lots_per_symbol}")
        lots = spec.normalize_lots(cfg.max_lots_per_symbol)

    actual_risk = lots * (loss_per_lot + spec.commission_per_lot)
    return SizingResult(
        lots=lots,
        risk_money=actual_risk,
        risk_pct=(actual_risk / equity * 100.0) if equity > 0 else 0.0,
        stop_distance=stop_distance,
        stop_pips=stop_pips,
        loss_per_lot=loss_per_lot,
        margin_required=margin_required,
        notes=notes,
        detail={
            "budget_risk_money": risk_money,
            "budget_risk_pct": risk_pct,
            "raw_lots": raw_lots,
        },
    )
