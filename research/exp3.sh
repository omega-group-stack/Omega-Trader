#!/usr/bin/env bash
# Experiment 3, development phase: the frozen 6-cell grid on EURUSD.
#
# Everything except the two strength axes is pinned to the experiment-2
# winner (H1, ECN costs, entry_threshold 0.70, atr_stop_mult 2.0). The
# single-value --param keeps the walk-forward machinery in play without
# letting it optimise anything.
#
# Note: the two agreement=0.0 cells are the filter switched off and must
# produce byte-identical results regardless of lookback. That duplicate is
# deliberate -- it is a free determinism check on the wiring.
cd "$(dirname "$0")/.."
wf(){ lb=$1; ag=$2; name="lb${lb}_ag${ag/./}"
  echo "########## lookback=$lb agreement=$ag ##########"
  .venv/bin/python -m omega.cli walkforward -s EURUSD \
    --set data.source=csv --set data.csv_dir=data --set data.history_bars=100009 \
    --set data.end=2021-09-01 \
    --set strategy.timeframe=H1 --set strategy.htf_timeframe=H4 \
    --set execution.spread_points=2 --set execution.slippage_points=1.5 \
    --set execution.commission_per_lot=6 --set risk.max_drawdown_pct=100 \
    --set risk.atr_stop_mult=2.0 \
    --set strategy.min_strength_agreement=$ag \
    --set strategy.strength_lookback=$lb \
    --param strategy.entry_threshold=0.70 \
    --is-months 36 --oos-months 12 --workers 2 \
    --json research/wf3_$name.json 2>&1 | grep -v "cooling down\|KILL SWITCH"
}
for lb in 24 72; do for ag in 0.0 0.3 0.6; do wf $lb $ag; done; done
echo "########## ALL DONE ##########"
