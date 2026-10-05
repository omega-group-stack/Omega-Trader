#!/usr/bin/env bash
# Experiment 3, confirmation phase.
#
# The single winning configuration from the EURUSD development grid, frozen,
# run on the six majors that were used for NO decision -- not to pick the
# feature, not the grid, not the winner. Each pair is run twice, with and
# without the filter, so the comparison is paired and the cost model is
# identical. Full history (2007 -> 2023-09) for maximum trade count.
#
# Usage: LB=24 AG=0.3 bash research/exp3_confirm.sh
cd "$(dirname "$0")/.."
LB=${LB:?set LB (strength_lookback)}
AG=${AG:?set AG (min_strength_agreement)}
mkdir -p research/exp3
run(){ sym=$1; ag=$2; tag=$3
  .venv/bin/python -m omega.cli backtest -s $sym \
    --set data.source=csv --set data.csv_dir=data --set data.history_bars=100009 \
    --set strategy.timeframe=H1 --set strategy.htf_timeframe=H4 \
    --set execution.spread_points=2 --set execution.slippage_points=1.5 \
    --set execution.commission_per_lot=6 --set risk.max_drawdown_pct=100 \
    --set strategy.entry_threshold=0.70 --set risk.atr_stop_mult=2.0 \
    --set strategy.min_strength_agreement=$ag \
    --set strategy.strength_lookback=$LB \
    --report research/exp3/${sym}_${tag}.json >/dev/null 2>&1
  echo "  $sym $tag done"
}
for sym in GBPUSD AUDUSD NZDUSD USDCAD USDCHF USDJPY; do
  echo "########## $sym ##########"
  run $sym 0.0 off
  run $sym $AG  on
done
echo "########## CONFIRMATION DONE ##########"
