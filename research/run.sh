set -e
run(){ name=$1; shift; .venv/bin/python -m omega.cli backtest --set data.source=csv --set data.csv_dir=data -s EURUSD --set risk.max_drawdown_pct=100 --set logging.level=WARNING "$@" > research/$name.txt 2>&1; echo "--- $name"; sed -n '/^═/,$p' research/$name.txt | head -22; }
run m15_full --set data.history_bars=200000 &
run h1_full  --set data.history_bars=100009 --set strategy.timeframe=H1 --set strategy.htf_timeframe=H4 &
wait
