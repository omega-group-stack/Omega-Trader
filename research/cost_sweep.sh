run(){ name=$1; shift; .venv/bin/python -m omega.cli backtest -s EURUSD \
  --set data.source=csv --set data.csv_dir=data --set logging.level=ERROR \
  --set risk.max_drawdown_pct=100 --set strategy.entry_threshold=0.70 \
  "$@" 2>&1 | sed -n '/^Trades\|^Profit factor\|^Expectancy\|^Net profit\|^Costs\|^Payoff/p' \
  | sed "s/^/  /" ; }
for tf in "H1 100009 H4" "H4 25847 D1" "D1 5013 W1"; do
  set -- $tf; TF=$1; BARS=$2; HTF=$3
  for cm in "retail 12 2.0 7" "ecn 2 1.5 6"; do
    set -- $cm; CM=$1; SP=$2; SL=$3; CO=$4
    echo "=== $TF / $CM (spread ${SP}pts, slip ${SL}, comm \$${CO}) ==="
    run x --set data.history_bars=$BARS --set strategy.timeframe=$TF \
        --set strategy.htf_timeframe=$HTF --set execution.spread_points=$SP \
        --set execution.slippage_points=$SL --set execution.commission_per_lot=$CO
  done
done
