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

---

# Results — filled in after the runs, nothing above was edited

## Walk-forward ranking (data to 2022-09, selection rule applied as written)

| rank_score | config | efficiency | consistency | OOS trades | maxDD | OOS return |
|---:|---|---:|---:|---:|---:|---:|
| **0.1372** | **h1_ecn** | 0.18 | 0.75 | 748 | 10.26% | +62.20% |
| 0.1361 | h1_retail | 0.20 | 0.67 | 598 | 7.53% | +48.11% |
| 0.0310 | h4_retail | 0.06 | 0.50 | 432 | 10.17% | +1.10% |
| −0.0070 | h4_ecn | −0.01 | 0.50 | 453 | 9.91% | −0.88% |
| −0.0963 | d1_ecn | −0.22 | 0.44 | 96 | 5.09% | +0.07% |
| −0.0990 | d1_retail | −0.22 | 0.44 | 96 | 5.16% | −0.31% |

Winner `h1_ecn` by 0.0011 — a dead heat with `h1_retail`. The tie-break
(more out-of-sample trades: 748 vs 598) points the same way, so the choice is
not sensitive to the rounding. Modal parameters across its 12 folds, 6 of 12:
`entry_threshold=0.70`, `atr_stop_mult=2.0`.

## Holdout — one run, as promised

2022-06-17 → 2023-09-11, parameters frozen, ECN costs:

```
Balance      : 10,000.00 → 10,116.76   (+1.17%)
Trades       : 35   (W 19 / L 16, 54.3%)
Profit factor: 1.08
Max drawdown : 3.27%
Expectancy   : +0.138R
```

The same holdout under retail costs (experiment 1) gave +0.57% / PF 1.04.

## Verdict on the hypotheses

**H1 — supported.** ECN pricing adds **+0.042R** per trade on the full sample
(+0.201R → +0.243R at H1) and lifts the holdout from PF 1.04 to PF 1.08. The
predicted magnitude was +0.03R; measured +0.042R. The mechanism is confirmed.

**H2 — falsified, and the falsification is the interesting part.** The cost
*saving* does shrink with timeframe exactly as predicted (+0.042R at H1,
+0.033R at H4, +0.004R at D1 — a fixed pip cost spread over a bigger stop).
But the *edge* collapses faster than the cost does, because trade count
collapses: 495 trades at H1, 120 at H4, 27 at D1 over the same sixteen years.
H4 and D1 land at rank_score ≈ 0 and below. **H1 is the best timeframe for
this strategy, not the worst.** The pre-registered prediction was wrong.

## The number that actually settles it

A single-sample t-test on the 35 holdout trades:

| costs | n | mean | sd | t | p | trades needed for p<0.05 |
|---|---:|---:|---:|---:|---:|---:|
| retail | 35 | +0.114R | 1.077R | +0.63 | 0.53 | ~341 |
| ECN | 35 | +0.138R | 1.095R | +0.75 | 0.45 | ~240 |

**p = 0.45.** The holdout result is statistically indistinguishable from zero.
At roughly 50 trades a year, separating this edge from noise would take about
**five years of live trading**.

So the correct conclusion is not "ECN makes it profitable". It is:

> Lower costs reliably move the result in the right direction, and the best
> configuration found is a small positive number that cannot be distinguished
> from zero. There is no edge here worth risking money on.

That is the falsification criterion written above, triggering as written:
"If every configuration still lands near breakeven, the conclusion is that the
ensemble has no edge worth trading, and that is what gets written down."
