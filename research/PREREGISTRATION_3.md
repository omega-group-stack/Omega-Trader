# Pre-registration — experiment 3: cross-sectional currency strength

Written and committed **before** the feature was implemented or any run
executed. Experiment 1 was ruined by choosing the search space after seeing
the data; experiment 2 fixed that but died on sample size (n=35, p=0.45).
This design attacks the sample-size problem directly.

## The problem with experiments 1 and 2

The best configuration found reached +1.17% over 15 months on a holdout of
**35 trades**, with a per-trade standard deviation of 1.095R. That is
p = 0.45 — indistinguishable from zero. No amount of extra tuning on EURUSD
fixes this: the pair simply does not produce enough independent trades.

## The idea being tested

A move in EURUSD has three possible causes:

1. **EUR-driven** — EUR is rising against everything.
2. **USD-driven** — USD is falling against everything.
3. **Idiosyncratic** — the EURUSD rate moved but neither currency moved
   against the rest of the world.

Only (1) and (2) reflect macro flow that has any reason to persist. Case (3)
is pair-level noise, and a single-pair indicator cannot tell the three apart —
it sees the same candle in all three cases.

With all seven USD majors we can separate them. For each of the eight
currencies {USD, EUR, GBP, JPY, CHF, AUD, NZD, CAD}, define a strength score
as the average trailing log return of every pair containing it, signed
positive when it is the base currency and negative when it is the quote. The
cross-sectional signal for a pair is then `strength(base) - strength(quote)`,
standardised across the cross-section.

This is the currency-momentum factor documented by Menkhoff, Sarno,
Schmeling & Schrimpf (2012), applied as a confirmation filter rather than as
a standalone strategy.

## Hypothesis

**H3.** Requiring cross-sectional agreement before taking a trade raises
out-of-sample expectancy by at least **+0.05R** per trade relative to the
experiment-2 winner (H1 + ECN, entry_threshold 0.70, atr_stop_mult 2.0,
which scored +0.177R walk-forward and +0.138R on holdout).

**Mechanism prediction.** Trade count falls (idiosyncratic moves are
rejected), win rate rises modestly, and the improvement is *larger* on pairs
with more idiosyncratic flow.

## Grid — fixed now

| axis | values |
|---|---|
| `strength_lookback` | 24, 72 bars (1 day, 3 days at H1) |
| `min_strength_agreement` | 0.0 (filter off = baseline), 0.3, 0.6 |

6 combinations. Everything else frozen at the experiment-2 winner. Costs: ECN
(2 pts spread, 1.5 pts slippage, $6/lot). Walk-forward windows 36/12 as before.

## Development data

EURUSD only, walk-forward to **2021-09**. Shortened by one year versus
experiment 2 so the holdout is two years rather than one.

## Selection rule — stated before results exist

Same as experiment 2: `rank_score = efficiency × consistency` on walk-forward
output. Tie-break: more out-of-sample trades, then lower max drawdown.

## The confirmation test — the point of this experiment

The single winning configuration, parameters frozen, is then run on the
**six other majors**: GBPUSD, AUDUSD, NZDUSD, USDCAD, USDCHF, USDJPY.

None of those pairs is used for any decision: not to pick the feature, not to
pick the grid, not to pick the winner. They are a true out-of-sample panel,
and they should yield on the order of 1,500-4,000 trades rather than 35.

For each pair, the *same* frozen rule is run both with and without the filter,
so the comparison is paired and the cost model is identical.

## Statistical test — specified in advance

Pooled across the six confirmation pairs, a Welch t-test on per-trade R
between filtered and unfiltered.

- **Success:** mean R improves by ≥ +0.05R with p < 0.05, and the sign of the
  improvement is positive on at least 4 of the 6 pairs.
- **Weak result:** improvement positive and p < 0.05 but smaller than +0.05R.
- **Failure:** p ≥ 0.05, or improvement negative.

A result that is positive on EURUSD and absent on the other six is a failure,
and will be reported as the feature not generalising.

## What gets written down either way

If H3 fails, the conclusion is that cross-sectional confirmation does not
rescue an indicator ensemble, the feature stays in the codebase behind a
default-off flag, and BACKTEST.md records the negative result. No re-running
with a widened grid.
