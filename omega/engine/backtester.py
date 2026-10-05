"""Event-driven backtester.

Bar loop (the ordering is what keeps it honest):

1. the new bar arrives — **pending orders from the previous bar fill at its
   open**, which is the only price a decision made on the previous close could
   realistically have been executed at
2. the bar plays out — stops and targets are checked against its high/low
3. the bar closes — the strategy sees it and may queue an order for the next bar

No indicator ever reads a value it could not have known, and no fill happens at
a price the bot could not have reached.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd

from ..config import AppConfig
from ..core.types import Candle, CloseReason, SymbolSpec, Trade
from ..data import DataFeed, build_feed
from ..data.base import resample
from ..execution.simulated import SimulatedBroker
from ..risk import RiskManager
from ..strategy import EnsembleStrategy
from .core import BarOutcome, TradingCore
from .metrics import Performance, analyse

log = logging.getLogger(__name__)


@dataclass
class BacktestResult:
    performance: Performance
    trades: List[Trade] = field(default_factory=list)
    equity_curve: List[tuple] = field(default_factory=list)
    signals: pd.DataFrame = field(default_factory=pd.DataFrame)
    candles: Dict[str, pd.DataFrame] = field(default_factory=dict)
    config: Optional[AppConfig] = None
    log: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "performance": self.performance.to_dict(),
            "trades": [t.to_dict() for t in self.trades],
            "equity_curve": [
                {"time": t.isoformat(), "equity": round(e, 2), "balance": round(b, 2)}
                for t, e, b in self.equity_curve
            ],
        }


class Backtester:
    """Runs the full stack over historical data."""

    def __init__(self, cfg: AppConfig, feed: Optional[DataFeed] = None) -> None:
        self.cfg = cfg
        self.feed = feed or build_feed(cfg)

    def run(self, progress: bool = False) -> BacktestResult:
        cfg = self.cfg
        symbols = [s.name.upper() for s in cfg.active_symbols]
        if not symbols:
            raise ValueError("No enabled symbols to backtest")

        # --- load data & prepare strategies --------------------------------
        data: Dict[str, pd.DataFrame] = {}
        specs: Dict[str, SymbolSpec] = {}
        strategies: Dict[str, EnsembleStrategy] = {}

        strength = self._strength_frame(symbols)

        for sym in symbols:
            df = self._load(sym)
            if len(df) <= cfg.strategy.warmup_bars + 10:
                raise ValueError(
                    f"{sym}: only {len(df)} bars — need more than "
                    f"warmup_bars={cfg.strategy.warmup_bars}"
                )
            spec = self._spec_for(sym)
            htf = resample(df, cfg.strategy.htf_timeframe)
            strategy = EnsembleStrategy(cfg.strategy, spec).prepare(
                df, htf, strength=strength, symbol=sym
            )
            strategy.max_spread_points = self._max_spread(sym)  # type: ignore[attr-defined]

            data[sym] = df
            specs[sym] = spec
            # Dashboard-only prose; pure overhead over hundreds of thousands
            # of bars. Re-enabled implicitly for live/paper (class default).
            strategy.detailed = False
            strategies[sym] = strategy

        broker = SimulatedBroker(cfg.execution, cfg.account, specs)
        risk = RiskManager(cfg.risk, cfg.account.initial_balance)
        core = TradingCore(cfg, broker, risk, strategies)

        # --- unified chronological timeline ---------------------------------
        timeline = sorted(set().union(*[set(df.index) for df in data.values()]))
        positions_of: Dict[str, int] = {s: i for i, s in enumerate(symbols)}
        index_map = {sym: {ts: i for i, ts in enumerate(df.index)} for sym, df in data.items()}

        journal: List[str] = []
        warmup = cfg.strategy.warmup_bars
        next_open = cfg.execution.fill_on == "next_open"

        total = len(timeline)
        for n, ts in enumerate(timeline):
            for sym in symbols:
                i = index_map[sym].get(ts)
                if i is None or i <= warmup:
                    continue
                row = data[sym].iloc[i]
                bar = Candle(
                    time=ts.to_pydatetime(),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]),
                    spread=float(row["spread"]),
                )

                if next_open:
                    # Decide on the *previous* closed bar, execute at this bar's
                    # open — the only price such a decision could have reached.
                    open_tick = Candle(
                        time=bar.time, open=bar.open, high=bar.open, low=bar.open,
                        close=bar.open, volume=0.0, spread=bar.spread,
                    )
                    broker.set_bar(sym, open_tick)
                    outcome = core.on_bar(sym, i - 1, open_tick)
                    self._journal(journal, outcome)
                    # then let the bar play out against stops and targets
                    self._settle(broker.process_bar(sym, bar), risk, journal)
                else:
                    self._settle(broker.process_bar(sym, bar), risk, journal)
                    outcome = core.on_bar(sym, i, bar)
                    self._journal(journal, outcome)

            if progress and total > 100 and n % max(1, total // 20) == 0:
                acct = broker.account()
                log.info("  %5.1f%%  %s  equity %.2f",
                         n / total * 100, ts, acct.equity)

        # --- flatten whatever is still open ----------------------------------
        for result in broker.close_all(CloseReason.END_OF_DATA):
            if result.ok and isinstance(result.raw, Trade):
                risk.on_trade_closed(result.raw)
        broker._record_equity()

        performance = analyse(
            broker.equity_curve, broker.closed_trades(), cfg.account.initial_balance
        )
        signals = pd.concat(
            {sym: st.scores for sym, st in strategies.items()}, names=["symbol"]
        ) if strategies else pd.DataFrame()

        return BacktestResult(
            performance=performance,
            trades=broker.closed_trades(),
            equity_curve=broker.equity_curve,
            signals=signals,
            candles=data,
            config=cfg,
            log=journal,
        )

    # ------------------------------------------------------------------ #
    @staticmethod
    def _settle(closed: List[Trade], risk: RiskManager, journal: List[str]) -> None:
        """Report broker-side exits (stop / target hits) to the risk manager.

        Without this the daily-loss, streak and cool-down guards would never
        see the trades that the broker closed on its own.
        """
        for trade in closed:
            risk.on_trade_closed(trade)
            journal.append(
                f"{trade.close_time:%Y-%m-%d %H:%M} | {trade.symbol} | "
                f"closed #{trade.ticket} {trade.side.value} {trade.lots:.2f} "
                f"@ {trade.exit_price:.5f} ({trade.reason.value}) "
                f"pnl {trade.pnl:+.2f} [{trade.r_multiple:+.2f}R]"
            )

    @staticmethod
    def _journal(journal: List[str], outcome: BarOutcome) -> None:
        for action in outcome.actions:
            journal.append(f"{outcome.time:%Y-%m-%d %H:%M} | {outcome.symbol} | {action}")

    def _strength_frame(self, symbols) -> Optional[pd.DataFrame]:
        """Build the cross-sectional strength basket, or None when disabled.

        Deliberately loads the basket through the same feed and the same date
        window as the traded symbols, so a backtest cannot accidentally see
        cross-sectional data from outside its own period.
        """
        cfg = self.cfg
        if cfg.strategy.min_strength_agreement <= 0:
            return None

        basket = list(dict.fromkeys(
            [s.upper() for s in cfg.strategy.strength_basket] +
            [s.upper() for s in symbols]
        ))
        closes = {}
        for sym in basket:
            try:
                frame = self._load(sym)
            except Exception as exc:
                log.warning("strength basket: %s unavailable (%s)", sym, exc)
                continue
            if frame is not None and not frame.empty:
                closes[sym] = frame["close"]

        from ..strategy.strength import build_strength

        frame = build_strength(closes, cfg.strategy.strength_lookback,
                               cfg.strategy.strength_smooth)
        if frame.empty:
            log.warning("Currency-strength filter is enabled but the basket is "
                        "empty — the filter will have no effect.")
            return None
        log.info("Currency strength: %d pairs -> %d currencies",
                 len(closes), len(frame.columns))
        return frame

    def _load(self, symbol: str) -> pd.DataFrame:
        cfg = self.cfg
        df = self.feed.candles(symbol, cfg.strategy.timeframe, cfg.data.history_bars)
        if cfg.data.start:
            df = df[df.index >= pd.Timestamp(cfg.data.start, tz="UTC")]
        if cfg.data.end:
            df = df[df.index <= pd.Timestamp(cfg.data.end, tz="UTC")]
        return df

    def _spec_for(self, symbol: str) -> SymbolSpec:
        spec = self.feed.symbol_spec(symbol)
        if spec is None:
            from ..data.synthetic import default_spec

            spec = default_spec(symbol)
        sc = self.cfg.symbol(symbol)
        for attr in ("digits", "contract_size", "tick_value", "tick_size",
                     "min_lot", "max_lot", "lot_step", "commission_per_lot"):
            value = getattr(sc, attr, None)
            if value is not None:
                setattr(spec, attr, value)
        if sc.commission_per_lot is None:
            spec.commission_per_lot = self.cfg.execution.commission_per_lot
        return spec

    def _max_spread(self, symbol: str) -> float:
        sc = self.cfg.symbol(symbol)
        return float(sc.max_spread_points or 0.0)
