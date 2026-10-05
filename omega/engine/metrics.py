"""Performance analytics.

Turns an equity curve plus a trade list into the numbers that actually decide
whether a strategy is worth risking money on — risk-adjusted returns, drawdown
behaviour, expectancy and trade quality.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from ..core.types import Trade

SECONDS_PER_YEAR = 365.25 * 24 * 3600


@dataclass
class Performance:
    """Full performance report."""

    # headline
    start: Optional[str] = None
    end: Optional[str] = None
    days: float = 0.0
    initial_balance: float = 0.0
    final_balance: float = 0.0
    final_equity: float = 0.0
    net_profit: float = 0.0
    return_pct: float = 0.0
    cagr_pct: float = 0.0

    # risk
    max_drawdown_pct: float = 0.0
    max_drawdown_money: float = 0.0
    max_drawdown_duration_days: float = 0.0
    ulcer_index: float = 0.0
    sharpe: float = 0.0
    sortino: float = 0.0
    calmar: float = 0.0
    recovery_factor: float = 0.0
    volatility_pct: float = 0.0

    # trades
    trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate_pct: float = 0.0
    profit_factor: float = 0.0
    expectancy: float = 0.0
    expectancy_r: float = 0.0
    payoff_ratio: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    largest_win: float = 0.0
    largest_loss: float = 0.0
    max_consecutive_wins: int = 0
    max_consecutive_losses: int = 0
    avg_bars_held: float = 0.0
    avg_r: float = 0.0
    sqn: float = 0.0
    total_commission: float = 0.0
    total_swap: float = 0.0

    # breakdowns
    by_reason: Dict[str, int] = field(default_factory=dict)
    by_regime: Dict[str, Dict[str, float]] = field(default_factory=dict)
    by_side: Dict[str, Dict[str, float]] = field(default_factory=dict)
    monthly_returns_pct: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)

    def summary(self) -> str:
        """Compact, console-friendly report."""
        lines = [
            f"Period          : {self.start} -> {self.end}  ({self.days:.0f} days)",
            f"Balance         : {self.initial_balance:,.2f} -> {self.final_balance:,.2f}",
            f"Net profit      : {self.net_profit:,.2f}  ({self.return_pct:+.2f}%)",
            f"CAGR            : {self.cagr_pct:+.2f}%",
            f"Max drawdown    : {self.max_drawdown_pct:.2f}%  "
            f"({self.max_drawdown_money:,.2f}, {self.max_drawdown_duration_days:.0f}d)",
            f"Sharpe / Sortino: {self.sharpe:.2f} / {self.sortino:.2f}",
            f"Calmar / Recov. : {self.calmar:.2f} / {self.recovery_factor:.2f}",
            "",
            f"Trades          : {self.trades}  "
            f"(W {self.wins} / L {self.losses}, {self.win_rate_pct:.1f}% win rate)",
            f"Profit factor   : {self.profit_factor:.2f}",
            f"Expectancy      : {self.expectancy:,.2f} per trade  ({self.expectancy_r:+.3f}R)",
            f"Payoff ratio    : {self.payoff_ratio:.2f}  "
            f"(avg win {self.avg_win:,.2f} / avg loss {self.avg_loss:,.2f})",
            f"Best / worst    : {self.largest_win:,.2f} / {self.largest_loss:,.2f}",
            f"Streaks         : {self.max_consecutive_wins}W / {self.max_consecutive_losses}L",
            f"SQN             : {self.sqn:.2f}",
            f"Costs           : commission {self.total_commission:,.2f}, "
            f"swap {self.total_swap:,.2f}",
        ]
        if self.by_reason:
            reasons = ", ".join(f"{k}={v}" for k, v in sorted(self.by_reason.items()))
            lines.append(f"Exit reasons    : {reasons}")
        return "\n".join(lines)


def analyse(
    equity_curve: Sequence[Tuple[datetime, float, float]],
    trades: Iterable[Trade],
    initial_balance: float,
) -> Performance:
    """Compute the full report from an equity curve and closed trades."""
    trades = list(trades)
    perf = Performance(initial_balance=round(initial_balance, 2))

    curve = _curve_frame(equity_curve)
    if not curve.empty:
        equity = curve["equity"]
        perf.start = str(curve.index[0])
        perf.end = str(curve.index[-1])
        span = (curve.index[-1] - curve.index[0]).total_seconds()
        perf.days = span / 86_400.0
        perf.final_equity = round(float(equity.iloc[-1]), 2)
        perf.final_balance = round(float(curve["balance"].iloc[-1]), 2)
        perf.net_profit = round(perf.final_equity - initial_balance, 2)
        perf.return_pct = _pct(perf.net_profit, initial_balance)

        years = max(span / SECONDS_PER_YEAR, 1e-9)
        if initial_balance > 0 and perf.final_equity > 0 and years > 0.01:
            perf.cagr_pct = ((perf.final_equity / initial_balance) ** (1 / years) - 1) * 100

        dd = _drawdown(equity)
        perf.max_drawdown_pct = round(dd["max_pct"], 3)
        perf.max_drawdown_money = round(dd["max_money"], 2)
        perf.max_drawdown_duration_days = round(dd["max_duration_days"], 2)
        perf.ulcer_index = round(dd["ulcer"], 3)

        # Risk ratios are computed on *daily* returns. Annualising 15-minute
        # returns produces absurd numbers because equity is flat on the many
        # bars with no open position, which collapses the denominator.
        daily = equity.resample("1D").last().dropna()
        returns = daily.pct_change().replace([np.inf, -np.inf], np.nan).dropna()
        periods_per_year = 252.0
        perf.sharpe = round(_sharpe(returns, periods_per_year), 3)
        perf.sortino = round(_sortino(returns, periods_per_year), 3)
        perf.volatility_pct = round(
            float(returns.std(ddof=0) * math.sqrt(periods_per_year) * 100), 3
        )
        if perf.max_drawdown_pct > 0:
            perf.calmar = round(perf.cagr_pct / perf.max_drawdown_pct, 3)
            perf.recovery_factor = round(
                perf.net_profit / max(perf.max_drawdown_money, 1e-9), 3
            )
        perf.monthly_returns_pct = _monthly_returns(equity)

    perf.cagr_pct = round(perf.cagr_pct, 3)
    perf.return_pct = round(perf.return_pct, 3)

    if not trades:
        return perf

    pnls = np.array([t.pnl for t in trades], dtype=float)
    wins = pnls[pnls > 0]
    losses = pnls[pnls <= 0]

    perf.trades = len(trades)
    perf.wins = int(len(wins))
    perf.losses = int(len(losses))
    perf.win_rate_pct = round(len(wins) / len(pnls) * 100, 2)
    gross_profit = float(wins.sum())
    gross_loss = float(-losses.sum())
    perf.profit_factor = round(
        gross_profit / gross_loss if gross_loss > 0 else (math.inf if gross_profit else 0.0), 3
    )
    perf.expectancy = round(float(pnls.mean()), 3)
    perf.avg_win = round(float(wins.mean()) if len(wins) else 0.0, 3)
    perf.avg_loss = round(float(losses.mean()) if len(losses) else 0.0, 3)
    perf.payoff_ratio = round(
        abs(perf.avg_win / perf.avg_loss) if perf.avg_loss else 0.0, 3
    )
    perf.largest_win = round(float(pnls.max()), 2)
    perf.largest_loss = round(float(pnls.min()), 2)
    perf.max_consecutive_wins, perf.max_consecutive_losses = _streaks(pnls)
    perf.avg_bars_held = round(
        float(np.mean([t.bars_held for t in trades])) if trades else 0.0, 1
    )
    r_values = np.array([t.r_multiple for t in trades], dtype=float)
    perf.avg_r = round(float(r_values.mean()), 3)
    perf.expectancy_r = perf.avg_r
    if len(pnls) > 1 and pnls.std(ddof=1) > 0:
        perf.sqn = round(float(math.sqrt(len(pnls)) * pnls.mean() / pnls.std(ddof=1)), 3)
    perf.total_commission = round(float(sum(t.commission for t in trades)), 2)
    perf.total_swap = round(float(sum(t.swap for t in trades)), 2)

    perf.by_reason = _count_by(trades, lambda t: t.reason.value)
    perf.by_regime = _stats_by(trades, lambda t: t.regime or "UNKNOWN")
    perf.by_side = _stats_by(trades, lambda t: t.side.value)
    return perf


# --------------------------------------------------------------------------- #
# Internals
# --------------------------------------------------------------------------- #
def _curve_frame(curve: Sequence[Tuple[datetime, float, float]]) -> pd.DataFrame:
    if not curve:
        return pd.DataFrame(columns=["equity", "balance"])
    df = pd.DataFrame(curve, columns=["time", "equity", "balance"])
    df["time"] = pd.to_datetime(df["time"], utc=True)
    return df.set_index("time").sort_index()


def _drawdown(equity: pd.Series) -> Dict[str, float]:
    peak = equity.cummax()
    dd_money = peak - equity
    dd_pct = (dd_money / peak.replace(0.0, np.nan)).fillna(0.0) * 100.0

    # longest stretch spent below a previous peak
    underwater = equity < peak
    max_duration = 0.0
    start: Optional[pd.Timestamp] = None
    for ts, under in underwater.items():
        if under and start is None:
            start = ts
        elif not under and start is not None:
            max_duration = max(max_duration, (ts - start).total_seconds() / 86_400)
            start = None
    if start is not None:
        max_duration = max(
            max_duration, (equity.index[-1] - start).total_seconds() / 86_400
        )

    return {
        "max_pct": float(dd_pct.max()) if len(dd_pct) else 0.0,
        "max_money": float(dd_money.max()) if len(dd_money) else 0.0,
        "max_duration_days": max_duration,
        "ulcer": float(np.sqrt((dd_pct**2).mean())) if len(dd_pct) else 0.0,
    }


def _periods_per_year(index: pd.DatetimeIndex) -> float:
    if len(index) < 3:
        return 252.0
    deltas = np.diff(index.view("int64")) / 1e9
    median = float(np.median(deltas[deltas > 0])) if np.any(deltas > 0) else 86_400.0
    # FX trades ~120 hours a week, not 168
    return max(1.0, SECONDS_PER_YEAR * (120 / 168) / median)


def _sharpe(returns: pd.Series, periods_per_year: float) -> float:
    if len(returns) < 2:
        return 0.0
    std = float(returns.std(ddof=0))
    if std <= 0:
        return 0.0
    return float(returns.mean() / std * math.sqrt(periods_per_year))


def _sortino(returns: pd.Series, periods_per_year: float) -> float:
    if len(returns) < 2:
        return 0.0
    downside = returns[returns < 0]
    dd = float(downside.std(ddof=0)) if len(downside) > 1 else 0.0
    if dd <= 0:
        return 0.0
    return float(returns.mean() / dd * math.sqrt(periods_per_year))


def _monthly_returns(equity: pd.Series) -> Dict[str, float]:
    if equity.empty:
        return {}
    monthly = equity.resample("ME").last().dropna()
    if monthly.empty:
        return {}
    first = equity.iloc[0]
    prev = pd.concat([pd.Series([first], index=[monthly.index[0]]), monthly]).shift(1)
    out = {}
    previous = first
    for ts, value in monthly.items():
        out[ts.strftime("%Y-%m")] = round(_pct(value - previous, previous), 2)
        previous = value
    return out


def _streaks(pnls: np.ndarray) -> Tuple[int, int]:
    best_w = best_l = cur_w = cur_l = 0
    for p in pnls:
        if p > 0:
            cur_w, cur_l = cur_w + 1, 0
            best_w = max(best_w, cur_w)
        else:
            cur_l, cur_w = cur_l + 1, 0
            best_l = max(best_l, cur_l)
    return best_w, best_l


def _count_by(trades: List[Trade], key) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for t in trades:
        out[key(t)] = out.get(key(t), 0) + 1
    return dict(sorted(out.items()))


def _stats_by(trades: List[Trade], key) -> Dict[str, Dict[str, float]]:
    buckets: Dict[str, List[Trade]] = {}
    for t in trades:
        buckets.setdefault(key(t), []).append(t)
    out: Dict[str, Dict[str, float]] = {}
    for name, group in sorted(buckets.items()):
        pnls = np.array([t.pnl for t in group])
        wins = pnls[pnls > 0]
        gross_loss = float(-pnls[pnls <= 0].sum())
        out[name] = {
            "trades": len(group),
            "net_pnl": round(float(pnls.sum()), 2),
            "win_rate_pct": round(len(wins) / len(pnls) * 100, 1),
            "profit_factor": round(
                float(wins.sum()) / gross_loss if gross_loss > 0 else 0.0, 2
            ),
            "avg_r": round(float(np.mean([t.r_multiple for t in group])), 3),
        }
    return out


def _pct(amount: float, base: float) -> float:
    return (amount / base * 100.0) if base else 0.0
