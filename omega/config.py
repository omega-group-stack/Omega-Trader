"""Typed configuration tree for Omega-Trader.

Every tunable lives here with a sane, conservative default, so a YAML file only
needs to override what you actually care about. Load with::

    cfg = AppConfig.load("config/config.yaml")
"""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, get_type_hints

import yaml

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


# --------------------------------------------------------------------------- #
# Sections
# --------------------------------------------------------------------------- #
@dataclass
class AccountConfig:
    """Account-level settings used by paper/backtest brokers."""

    currency: str = "USD"
    initial_balance: float = 10_000.0
    leverage: int = 30


@dataclass
class MT5Config:
    """MetaTrader 5 terminal connection (live/paper-on-live-data)."""

    login: int = 0
    password: str = ""
    server: str = ""
    terminal_path: str = ""
    timeout_ms: int = 60_000
    portable: bool = False
    magic: int = 920_112
    deviation_points: int = 20  # max slippage accepted on market orders


@dataclass
class SymbolConfig:
    """Per-symbol overrides. Anything left at None is taken from the broker."""

    name: str = "EURUSD"
    enabled: bool = True
    weight: float = 1.0                       # scales risk for this symbol
    digits: Optional[int] = None
    contract_size: Optional[float] = None
    tick_value: Optional[float] = None
    tick_size: Optional[float] = None
    min_lot: Optional[float] = None
    max_lot: Optional[float] = None
    lot_step: Optional[float] = None
    commission_per_lot: Optional[float] = None
    spread_points: Optional[float] = None     # simulated spread for backtest
    max_spread_points: Optional[float] = None  # veto entries above this


@dataclass
class IndicatorConfig:
    """Lookback periods for the indicator battery."""

    ema_fast: int = 21
    ema_slow: int = 55
    ema_baseline: int = 200
    adx_period: int = 14
    adx_trend_min: float = 20.0
    supertrend_period: int = 10
    supertrend_mult: float = 3.0
    psar_step: float = 0.02
    psar_max: float = 0.2
    slope_period: int = 20

    rsi_period: int = 14
    rsi_overbought: float = 70.0
    rsi_oversold: float = 30.0
    stoch_k: int = 14
    stoch_d: int = 3
    stoch_smooth: int = 3
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    cci_period: int = 20
    roc_period: int = 10
    willr_period: int = 14

    atr_period: int = 14
    atr_percentile_window: int = 200
    bb_period: int = 20
    bb_std: float = 2.0
    keltner_period: int = 20
    keltner_mult: float = 1.5
    donchian_period: int = 20
    chop_period: int = 14

    mfi_period: int = 14
    cmf_period: int = 20
    obv_smooth: int = 21
    vol_ma_period: int = 20


@dataclass
class WeightsConfig:
    """Base weights of each voting block (re-weighted per regime)."""

    trend: float = 1.0
    momentum: float = 0.9
    volatility: float = 0.6
    volume: float = 0.5
    structure: float = 0.8   # price action: breakouts, swings, candles
    htf_bias: float = 1.0    # higher-timeframe alignment


@dataclass
class RegimeConfig:
    """Market-regime detection + how it re-weights the ensemble."""

    enabled: bool = True
    adx_trend: float = 23.0
    adx_range: float = 18.0
    chop_range: float = 61.8
    atr_high_pct: float = 0.80   # ATR percentile above which market is "volatile"
    atr_low_pct: float = 0.20    # below which it is "quiet"
    # multipliers applied to WeightsConfig per regime
    trend_multipliers: Dict[str, float] = field(
        default_factory=lambda: {"trend": 1.35, "momentum": 1.0, "volatility": 0.7,
                                 "volume": 0.8, "structure": 1.1, "htf_bias": 1.2}
    )
    range_multipliers: Dict[str, float] = field(
        default_factory=lambda: {"trend": 0.5, "momentum": 1.3, "volatility": 1.2,
                                 "volume": 0.9, "structure": 0.8, "htf_bias": 0.6}
    )
    volatile_multipliers: Dict[str, float] = field(
        default_factory=lambda: {"trend": 1.0, "momentum": 0.8, "volatility": 1.3,
                                 "volume": 1.0, "structure": 0.9, "htf_bias": 1.0}
    )
    quiet_multipliers: Dict[str, float] = field(
        default_factory=lambda: {"trend": 0.8, "momentum": 1.1, "volatility": 0.8,
                                 "volume": 0.8, "structure": 1.0, "htf_bias": 0.9}
    )
    block_trades_in_quiet: bool = False


@dataclass
class SessionConfig:
    """Trading-hour filter (broker/server time, 24h clock)."""

    enabled: bool = True
    timezone: str = "UTC"
    windows: List[List[str]] = field(
        default_factory=lambda: [["07:00", "16:30"], ["13:00", "21:00"]]
    )
    trade_days: List[int] = field(default_factory=lambda: [0, 1, 2, 3, 4])  # Mon..Fri
    avoid_friday_after: str = "19:00"
    close_all_before_weekend: bool = True


@dataclass
class StrategyConfig:
    """The decision engine."""

    name: str = "ensemble"
    timeframe: str = "M15"
    htf_timeframe: str = "H4"
    warmup_bars: int = 300
    entry_threshold: float = 0.28       # |score| needed to open
    exit_threshold: float = 0.10        # score decay that closes a position
    exit_min_r: float = 0.6             # only bank a decayed thesis past +0.6R
    min_confidence: float = 0.45        # [0,1] agreement filter
    min_agreeing_blocks: int = 3        # how many blocks must agree on sign
    require_htf_alignment: bool = True
    # Cross-sectional currency-strength confirmation (see strategy/strength.py).
    # 0 disables it. Requires strength_basket pairs to be available in the feed.
    min_strength_agreement: float = 0.0
    strength_lookback: int = 24        # bars used for the trailing return
    strength_smooth: int = 3           # smoothing applied to the raw scores
    strength_basket: List[str] = field(default_factory=lambda: [
        "EURUSD", "GBPUSD", "AUDUSD", "NZDUSD", "USDCAD", "USDCHF", "USDJPY",
    ])
    allow_longs: bool = True
    allow_shorts: bool = True
    allow_pyramiding: bool = False
    reverse_on_flip: bool = True        # close & flip when score crosses hard
    flip_threshold: float = 0.45
    cooldown_bars_after_exit: int = 2
    indicators: IndicatorConfig = field(default_factory=IndicatorConfig)
    weights: WeightsConfig = field(default_factory=WeightsConfig)
    regime: RegimeConfig = field(default_factory=RegimeConfig)
    sessions: SessionConfig = field(default_factory=SessionConfig)


@dataclass
class RiskConfig:
    """Everything that decides *how much* and *whether* to trade.

    ``risk_per_trade_pct`` is the headline knob: the percentage of account
    equity put at risk between entry and the initial stop-loss.
    """

    # --- position sizing ---------------------------------------------------
    risk_per_trade_pct: float = 1.0
    sizing_mode: str = "percent_risk"   # percent_risk | fixed_lot | equity_fraction
    fixed_lot: float = 0.10
    equity_fraction: float = 0.02
    risk_base: str = "equity"           # equity | balance
    scale_risk_with_confidence: bool = True
    min_risk_multiplier: float = 0.5    # at min_confidence
    max_risk_multiplier: float = 1.25   # at confidence == 1.0

    # --- stops & targets ---------------------------------------------------
    stop_mode: str = "atr"              # atr | fixed_pips | structure
    atr_stop_mult: float = 2.0
    atr_target_mult: float = 3.0
    fixed_stop_pips: float = 25.0
    min_stop_pips: float = 5.0
    max_stop_pips: float = 200.0
    risk_reward: float = 1.8            # used when target mode is 'rr'
    target_mode: str = "rr"             # rr | atr | none
    use_trailing_stop: bool = True
    trail_mode: str = "chandelier"      # chandelier | atr | breakeven_only
    trail_atr_mult: float = 2.5
    trail_activate_r: float = 1.0       # start trailing after +1R
    breakeven_at_r: float = 1.0
    breakeven_offset_pips: float = 1.0
    partial_tp_r: float = 1.5           # take partial profit at +1.5R
    partial_tp_fraction: float = 0.5    # close half
    time_stop_bars: int = 0             # 0 = disabled

    # --- exposure limits ---------------------------------------------------
    max_open_positions: int = 4
    max_positions_per_symbol: int = 1
    max_total_risk_pct: float = 4.0     # sum of open risk
    max_currency_exposure: int = 2      # e.g. max 2 open trades touching USD
    max_lots_per_symbol: float = 5.0
    min_free_margin_pct: float = 35.0   # refuse to trade below this margin level buffer

    # --- loss guards -------------------------------------------------------
    max_daily_loss_pct: float = 3.0
    max_weekly_loss_pct: float = 6.0
    max_drawdown_pct: float = 15.0      # hard kill-switch from equity peak
    max_daily_trades: int = 20
    max_consecutive_losses: int = 4
    cooldown_minutes_after_streak: int = 120
    daily_profit_target_pct: float = 0.0   # 0 = disabled; stop trading when hit
    reduce_risk_after_loss: bool = True
    loss_risk_multiplier: float = 0.6   # risk x0.6 while in a losing streak
    recovery_wins_needed: int = 2


@dataclass
class ExecutionConfig:
    """Order routing / simulation realism."""

    mode: str = "paper"                 # backtest | paper | live
    slippage_points: float = 2.0
    spread_points: float = 12.0         # default simulated spread (5-digit points)
    commission_per_lot: float = 7.0     # round-turn in account currency
    swap_enabled: bool = True
    fill_on: str = "next_open"          # next_open | close  (backtest only)
    stop_first_on_ambiguous_bar: bool = True  # conservative intrabar assumption
    poll_seconds: float = 5.0           # live/paper loop cadence
    confirm_live: bool = False          # must be true to send real orders


def _coerce_like(raw: Any, like: Any) -> Any:
    """Coerce ``raw`` to the type of ``like``. Non-strings pass through."""
    if isinstance(like, bool):
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in ("1", "true", "yes", "on")
    if isinstance(like, int) and not isinstance(like, bool):
        return int(float(raw))
    if isinstance(like, float):
        return float(raw)
    if isinstance(like, list):
        if isinstance(raw, list):
            return raw
        return [p.strip() for p in str(raw).split(",") if p.strip()]
    return raw


@dataclass
class DataConfig:
    source: str = "synthetic"           # mt5 | csv | synthetic
    csv_dir: str = "data"
    cache_dir: str = "runtime/cache"
    history_bars: int = 5_000
    start: str = ""                     # ISO date for backtests
    end: str = ""
    synthetic_seed: int = 7
    synthetic_bars: int = 6_000
    synthetic_stream: bool = False      # reveal bars over wall-clock time
    synthetic_speed: float = 120.0      # simulated seconds per real second


@dataclass
class DashboardConfig:
    enabled: bool = True
    host: str = "0.0.0.0"
    port: int = 8080
    refresh_ms: int = 2_000
    title: str = "Omega-Trader"
    # The dashboard exposes /api/control, which can flatten your book. If you
    # bind it to anything other than localhost, set a token. Empty = no auth.
    auth_token: str = ""


@dataclass
class TelegramConfig:
    """Telegram bot credentials and behaviour.

    Leave the token out of the YAML file and use the environment instead::

        bot_token: "${TELEGRAM_BOT_TOKEN}"
    """

    bot_token: str = ""          # from @BotFather
    chat_id: str = ""            # your user id or a group id (negative)
    allow_commands: bool = True  # long-poll for /status, /halt, ...
    allow_flatten: bool = True   # let /flatten close positions remotely
    poll_timeout_seconds: int = 50   # Telegram long-poll window
    timeout_seconds: float = 20.0
    max_retries: int = 4
    min_interval_seconds: float = 0.4   # throttle floor between sends
    queue_size: int = 500


@dataclass
class NotificationConfig:
    """Which events get pushed, and where."""

    enabled: bool = False
    provider: str = "telegram"      # telegram | none
    on_entry: bool = True
    on_exit: bool = True
    on_halt: bool = True
    on_error: bool = True
    on_lifecycle: bool = True       # loop started / stopped
    on_daily_summary: bool = True
    daily_summary_hour: int = 21    # UTC hour to send the roll-up
    heartbeat_minutes: int = 0      # 0 = off; otherwise a silent status ping
    telegram: TelegramConfig = field(default_factory=TelegramConfig)


@dataclass
class LoggingConfig:
    level: str = "INFO"
    dir: str = "runtime/logs"
    to_file: bool = True
    json_lines: bool = False


@dataclass
class StorageConfig:
    enabled: bool = True
    path: str = "runtime/omega.sqlite"


# --------------------------------------------------------------------------- #
# Root
# --------------------------------------------------------------------------- #
@dataclass
class AppConfig:
    account: AccountConfig = field(default_factory=AccountConfig)
    mt5: MT5Config = field(default_factory=MT5Config)
    symbols: List[SymbolConfig] = field(
        default_factory=lambda: [SymbolConfig(name="EURUSD")]
    )
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    data: DataConfig = field(default_factory=DataConfig)
    dashboard: DashboardConfig = field(default_factory=DashboardConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    notifications: NotificationConfig = field(default_factory=NotificationConfig)

    # -- helpers ------------------------------------------------------------
    @property
    def active_symbols(self) -> List[SymbolConfig]:
        return [s for s in self.symbols if s.enabled]

    def symbol(self, name: str) -> SymbolConfig:
        for s in self.symbols:
            if s.name.upper() == name.upper():
                return s
        return SymbolConfig(name=name)

    # -- (de)serialisation ---------------------------------------------------
    @classmethod
    def load(cls, path: str | os.PathLike[str] | None = None) -> "AppConfig":
        if path is None:
            return cls()
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"Config file not found: {p}")
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        return cls.from_dict(_expand_env(raw))

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AppConfig":
        return _build(cls, data or {})

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def set(self, dotted: str, value: Any) -> "AppConfig":
        """Set a nested key by dotted path, coercing to the existing type.

        ``cfg.set("risk.risk_per_trade_pct", "0.5")`` works as well as passing
        a real float: strings are coerced to match whatever type currently
        lives at that key, so CLI input and programmatic input behave the
        same. Raises ``KeyError`` for an unknown path rather than silently
        creating a key that nothing reads.
        """
        target: Any = self
        parts = dotted.split(".")
        for part in parts[:-1]:
            target = getattr(target, part, None)
            if target is None or is_dataclass(target) is False:
                raise KeyError(f"unknown config section in {dotted!r}")
        leaf = parts[-1]
        if not hasattr(target, leaf):
            raise KeyError(f"unknown config key {dotted!r}")
        current = getattr(target, leaf)
        setattr(target, leaf, _coerce_like(value, current))
        return self

    def dump(self, path: str | os.PathLike[str]) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(yaml.safe_dump(self.to_dict(), sort_keys=False, allow_unicode=True),
                     encoding="utf-8")

    def validate(self) -> List[str]:
        """Return a list of human-readable problems (empty == healthy)."""
        problems: List[str] = []
        r = self.risk
        if not 0 < r.risk_per_trade_pct <= 100:
            problems.append("risk.risk_per_trade_pct must be in (0, 100]")
        if r.risk_per_trade_pct > 5:
            problems.append(
                f"risk.risk_per_trade_pct={r.risk_per_trade_pct}% is very aggressive "
                "(professional range is 0.25%–2%)"
            )
        if r.max_total_risk_pct < r.risk_per_trade_pct:
            problems.append("risk.max_total_risk_pct must be >= risk_per_trade_pct")
        if r.max_daily_loss_pct <= 0 or r.max_drawdown_pct <= 0:
            problems.append("risk loss guards must be positive")
        if r.atr_stop_mult <= 0:
            problems.append("risk.atr_stop_mult must be > 0")
        if r.partial_tp_fraction and not 0 < r.partial_tp_fraction < 1:
            problems.append("risk.partial_tp_fraction must be in (0, 1)")
        s = self.strategy
        if not 0 < s.entry_threshold <= 1:
            problems.append("strategy.entry_threshold must be in (0, 1]")
        if s.exit_threshold >= s.entry_threshold:
            problems.append("strategy.exit_threshold should be < entry_threshold")
        if not self.active_symbols:
            problems.append("no enabled symbols configured")
        if self.execution.mode == "live" and not self.execution.confirm_live:
            problems.append(
                "execution.mode=live requires execution.confirm_live=true (safety interlock)"
            )
        return problems


# --------------------------------------------------------------------------- #
# Internals
# --------------------------------------------------------------------------- #
def _expand_env(obj: Any) -> Any:
    """Expand ``${VAR}`` / ``${VAR:-default}`` inside string values."""
    if isinstance(obj, dict):
        return {k: _expand_env(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand_env(v) for v in obj]
    if isinstance(obj, str):
        def sub(m: re.Match[str]) -> str:
            return os.environ.get(m.group(1), m.group(2) or "")
        return _ENV_PATTERN.sub(sub, obj)
    return obj


def _build(cls: type, data: Any) -> Any:
    """Recursively instantiate nested dataclasses from plain dicts.

    ``from __future__ import annotations`` turns field types into strings, so we
    resolve them with ``get_type_hints`` before deciding how to coerce a value.
    """
    if not is_dataclass(cls):
        return data
    if not isinstance(data, dict):
        return cls()  # type: ignore[call-arg]

    kwargs: Dict[str, Any] = {}
    known = {f.name: f for f in fields(cls)}
    try:
        hints = get_type_hints(cls, globalns=globals())
    except Exception:  # pragma: no cover - defensive
        hints = {}
    for key, value in data.items():
        f = known.get(key)
        if f is None:
            continue  # silently ignore unknown keys — forward compatible
        ftype = hints.get(key, f.type)
        if is_dataclass(ftype) and isinstance(value, dict):
            kwargs[key] = _build(ftype, value)  # type: ignore[arg-type]
        elif key == "symbols" and isinstance(value, list):
            kwargs[key] = [
                _build(SymbolConfig, v) if isinstance(v, dict) else SymbolConfig(name=str(v))
                for v in value
            ]
        else:
            kwargs[key] = value
    return cls(**kwargs)  # type: ignore[call-arg]
