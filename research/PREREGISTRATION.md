# Pre-registration — experiment 2

Written **before** any of the runs below were executed, and committed in the
same state. The point is that I cannot later claim a result was predicted
when it was not. Experiment 1 (`BACKTEST.md` §5.1) failed exactly because I
changed the search space after seeing the answer.

## Hypothesis

From experiment 1: the gross edge is ≈ +0.06R and the round-trip cost is
≈ 0.05R, so the net edge is ≈ 0. Two independent levers should move the cost
term without touching the signal:

**H1.** Lower trading costs (ECN pricing) add roughly +0.03R per trade.
**H2.** Higher timeframes dilute a fixed pip cost over a larger stop, so the
cost-to-edge ratio falls as H1 → H4 → D1.

Both predict higher out-of-sample profit factor. Neither predicts a *larger*
gross edge — the signal is unchanged.

## Grid — fixed now, not to be widened later

| axis | values |
|---|---|
| timeframe | H1 (htf H4), H4 (htf D1), D1 (htf W1) |
| cost model | `retail` = 12 pts spread, 2.0 pts slip, $7/lot · `ecn` = 2 pts spread, 1.5 pts slip, $6/lot |

6 combinations. Walk-forward parameter grid, identical for all 6, taken
unchanged from experiment 1's second study:

```
strategy.entry_threshold = 0.42, 0.56, 0.70
risk.atr_stop_mult       = 2.0, 3.0
```

Windows: 36-month in-sample / 12-month out-of-sample, rolling 12 months.
D1 uses 60/12 because 36 months of daily bars is too thin to fit on.

## Selection rule — stated before the results exist

Score each of the 6 configurations by

```
rank_score = efficiency × consistency
```

on the **walk-forward** output only (data up to 2022-09). Not by return:
return is the number most easily inflated by one lucky fold.

Tie-break, in order: (1) more out-of-sample trades, (2) lower max drawdown.

## Holdout

The single highest-ranked configuration gets **one** run on
2022-06 → 2023-09, parameters frozen to the modal choice across its folds.
One run. No second look, no re-ranking afterwards.

The holdout has already been consumed once (experiment 1, +0.57%). That
weakens it. It is reported as a sanity check, not as proof.

## Falsification

H1/H2 are wrong if the ECN and higher-timeframe configurations do **not**
show a materially higher out-of-sample profit factor than the H1-retail
baseline (PF 1.20, efficiency +0.20, consistency 67%).

If every configuration still lands near breakeven, the conclusion is that
the ensemble has no edge worth trading, and that is what gets written down.
