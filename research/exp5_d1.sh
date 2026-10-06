#!/usr/bin/env bash
# Experiment 5, secondary hypothesis: the existing ensemble at daily frequency.
# Same frozen configuration as the experiment-3 confirmation panel; only the
# timeframe changes. H4 is the htf for H1, so D1 gets W1.
cd "$(dirname "$0")/.."
mkdir -p research/exp5
for sym in EURUSD GBPUSD AUDUSD NZDUSD USDCAD USDCHF USDJPY EURGBP EURJPY \
           EURCHF EURAUD EURCAD EURNZD GBPJPY GBPCHF GBPAUD GBPCAD; do
  .venv/bin/python -m omega.cli backtest -s $sym \
    --set data.source=csv --set data.csv_dir=data --set data.history_bars=5200 \
    --set strategy.timeframe=D1 --set strategy.htf_timeframe=W1 \
    --set execution.spread_points=2 --set execution.slippage_points=1.5 \
    --set execution.commission_per_lot=6 --set risk.max_drawdown_pct=100 \
    --set strategy.entry_threshold=0.70 --set risk.atr_stop_mult=2.0 \
    --set strategy.warmup_bars=300 \
    --report research/exp5/${sym}_d1.json >/dev/null 2>&1 \
    && echo "  $sym done" || echo "  $sym FAILED"
done
echo "D1 PANEL DONE"
