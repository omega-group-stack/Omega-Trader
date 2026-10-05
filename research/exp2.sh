wf(){ name=$1; TF=$2; BARS=$3; HTF=$4; SP=$5; SL=$6; CO=$7; IS=$8
  echo "########## $name ##########"
  .venv/bin/python -m omega.cli walkforward -s EURUSD \
    --set data.source=csv --set data.csv_dir=data --set data.history_bars=$BARS \
    --set strategy.timeframe=$TF --set strategy.htf_timeframe=$HTF \
    --set execution.spread_points=$SP --set execution.slippage_points=$SL \
    --set execution.commission_per_lot=$CO --set risk.max_drawdown_pct=100 \
    --param strategy.entry_threshold=0.42,0.56,0.70 \
    --param risk.atr_stop_mult=2.0,3.0 \
    --is-months $IS --oos-months 12 --workers 2 \
    --json research/wf_$name.json 2>&1 | grep -v "cooling down\|KILL SWITCH"
}
wf d1_retail D1 5013   W1 12 2.0 7 60
wf d1_ecn    D1 5013   W1  2 1.5 6 60
wf h4_retail H4 25847  D1 12 2.0 7 36
wf h4_ecn    H4 25847  D1  2 1.5 6 36
wf h1_ecn    H1 100009 H4  2 1.5 6 36
echo "########## ALL DONE ##########"
