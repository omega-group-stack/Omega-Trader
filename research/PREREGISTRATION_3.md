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
normalised per pair in the time dimension (see the amendment below).

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


---

## Amendment 1 — normalisation (recorded before any result was produced)

As originally written above, "standardised across the cross-section" meant
z-scoring the finished per-currency scores at each timestamp. While writing
the unit tests — and **before running a single backtest cell** — that
definition turned out to be wrong for the stated purpose.

Per-timestamp z-scoring rescales every timestamp to unit dispersion. The
consequence: "EUR rose against all seven counterparts" and "EUR rose against
one counterpart while nothing else moved" receive the **same** score. The
breadth of a move is precisely the information this experiment claims to add
over the single-pair signal, and that normalisation divides it out. The test
`test_an_idiosyncratic_move_does_not_produce_a_strong_score` failed, which is
how it was caught.

**Amended definition.** Each pair's trailing log return is divided by that
pair's own trailing volatility (rolling, causal, 500 bars, min 20, clipped to
±5 so one dislocated pair cannot veto the whole book). The scaled returns are
then averaged per currency, and the cross-section is centred to sum to zero.
Centring is kept — it is a relative statement and does not destroy breadth —
but the division by cross-sectional spread is removed. Regime comparability,
the original reason for standardising, is preserved by the per-pair volatility
scaling, verified by
`test_the_same_shape_of_move_scores_the_same_in_calm_and_volatile_regimes`.

Units change from "cross-sectional standard deviations" to "average number of
typical moves", both of order ±1. **The pre-registered grid
`min_strength_agreement ∈ {0.0, 0.3, 0.6}` is left exactly as it was**, since
no result has been observed and the scale is comparable.

A second expectation also proved false and is recorded here so it is not
quietly dropped: enabling a veto-only filter does **not** reliably reduce the
backtest trade count. Vetoing an early entry changes the equity path, which
changes when cooldowns and the drawdown kill switch fire; a filtered run that
dodges an early halt can take more trades than the unfiltered one (measured:
90 versus 72 on synthetic data). The guarantee holds at the signal level only
— filtered entry signals are a strict subset of unfiltered ones — and that is
what the test asserts. **Trade count is therefore not usable as a sanity
check on whether the filter is active**; per-trade R is the metric of record,
as already specified.


---

## Amendment 2 — the basket was mis-specified (recorded after one look at
## development data, before any confirmation data was touched)

The first development grid was run with the basket set to the seven USD
majors. It produced an almost perfectly inert filter: 345 baseline trades
versus 340 / 341 / 333 filtered, and expectancy differences in the fourth
decimal (+0.2120R baseline, +0.2130R best cell). Before treating that as
evidence about H3, the mechanism was checked, and it is a setup error rather
than a result:

> In a basket of the seven USD majors, **every non-USD currency appears in
> exactly one pair**. USD appears in seven. So `strength(EUR)` is nothing but
> the signed return of EURUSD itself, and
> `spread(EURUSD) = z(EURUSD) + mean(z over the seven majors)` — the pair's
> own return on both sides. The filter was re-asking the ensemble's own
> momentum question, which is why it changed almost nothing. With no EUR
> cross in the basket it is structurally incapable of distinguishing
> "EUR strong against everything" from "EURUSD drifted", which is the entire
> hypothesis.

This is the same failure mode the unit test
`test_an_idiosyncratic_move_does_not_produce_a_strong_score` was written to
catch; that test passes only because its fixture includes EURGBP and EURJPY,
and the production basket had no crosses at all.

**Amended basket:** the seven majors plus ten crosses (EURGBP, EURJPY,
EURCHF, EURAUD, EURCAD, EURNZD, GBPJPY, GBPCHF, GBPAUD, GBPCAD), giving
coverage USD 7, EUR 7, GBP 6, AUD 3, CHF 3, CAD 3, JPY 3, NZD 2. An
eighteenth pair, AUDCHF, was downloaded and discarded: every row was zero,
stamped 1970-01-01. `build_strength` now rejects empty, constant or
non-positive series instead of propagating `log(0) = -inf` through the
cross-section, which would have vetoed every trade on every pair silently.

**Cost to the experiment, stated plainly.** The EURUSD development set has
now been looked at once under the old basket. The grid, the selection rule,
the hypothesis and the success thresholds are unchanged, and the development
grid is simply re-run with the corrected basket. **No confirmation pair has
been run at all**, so the six-pair out-of-sample panel — the part that
carries the statistical weight — remains untouched. The honest reading is
that the development phase has cost one degree of freedom, and the
confirmation test is still clean.

---

## Amendment 3 — the development grid selects the filter OFF

Development grid, corrected 17-pair basket, EURUSD, walk-forward to 2021-09,
ranked by the pre-registered `rank_score = efficiency × consistency`:

| cell | eff | cons | rank | trades | expectancy | OOS return | maxDD | PF | win% |
|---|---|---|---|---|---|---|---|---|---|
| **lb24_ag0.0 / lb72_ag0.0 (baseline)** | 0.28 | 0.90 | **0.2511** | 345 | +0.2120R | +42.01% | 8.41% | 1.28 | 55.4 |
| lb24_ag0.6 | 0.25 | 1.00 | 0.2490 | 326 | +0.2260R | +46.83% | 7.74% | 1.31 | 56.4 |
| lb72_ag0.6 | 0.27 | 0.90 | 0.2457 | 332 | +0.2190R | +40.34% | 10.26% | 1.27 | 55.7 |
| lb24_ag0.3 | 0.24 | 1.00 | 0.2400 | 341 | +0.2170R | +43.58% | 8.38% | 1.29 | 55.4 |
| lb72_ag0.3 | 0.25 | 0.90 | 0.2268 | 339 | +0.1950R | +33.75% | 10.27% | 1.23 | 54.6 |

Two things to record before going further.

**1. The selection rule picks the filter off.** `min_strength_agreement = 0`
ranks first. That is already evidence against H3, and it is written down here
rather than quietly discarded. The two baseline cells are byte-identical
across both lookbacks, which is the intended determinism check on the wiring.

**2. The spread between cells is noise.** 0.2511 / 0.2490 / 0.2457 over ten
folds and ~340 trades is not a ranking anybody should believe. The filtered
cells do look mildly better on the metrics the rule does *not* use —
lb24_ag0.6 has the best expectancy (+0.2260R vs +0.2120R), the only 100%
fold consistency together with lb24_ag0.3, and the lowest drawdown — while
ranking second on the rule itself.

**Deviation, stated plainly.** Taken literally, "run the single winning
configuration on the six other majors" now means running a no-op. That
would answer nothing. The confirmation panel is therefore run with
**lb24_ag0.6** as the filtered arm against the unfiltered baseline. This is
an extra degree of freedom — best-of-four chosen after seeing development
results — and it is bounded and disclosed: four candidates, and lb24_ag0.6 is
simultaneously the best on expectancy, consistency and drawdown, so no metric
was shopped for.

It also does not weaken the test, because the decision thresholds are
unchanged and the panel carries roughly six times the development sample.
The honest prior going in: development already failed to prefer the filter,
and the improvement on the metric of record is +0.014R against a +0.05R
success bar. **H3 is expected to fail.** The panel is run anyway, because the
point of pre-registration is to publish the answer you get, not the one you
hoped for.

---

# Results

Run once, as specified. No re-runs, no widened grid.

## H3 — the pre-registered test

Frozen configuration `strength_lookback=24, min_strength_agreement=0.6`
against the unfiltered baseline, on the six majors used for no decision.
Welch t-test on per-trade R, filtered minus unfiltered:

| pair | n off | R off | n on | R on | delta | t | p |
|---|---|---|---|---|---|---|---|
| GBPUSD | 515 | +0.1097 | 495 | +0.1233 | +0.0136 | +0.19 | 0.850 |
| AUDUSD | 400 | +0.1683 | 395 | +0.1530 | −0.0153 | −0.19 | 0.847 |
| NZDUSD | 368 | +0.0564 | 363 | +0.0614 | +0.0050 | +0.06 | 0.951 |
| USDCAD | 479 | +0.0661 | 457 | +0.0788 | +0.0126 | +0.17 | 0.861 |
| USDCHF | 487 | +0.1386 | 463 | +0.1740 | +0.0354 | +0.49 | 0.624 |
| USDJPY | 482 | +0.1233 | 447 | +0.1210 | −0.0023 | −0.03 | 0.975 |
| **POOLED** | **2731** | **+0.1110** | **2620** | **+0.1200** | **+0.0090** | **+0.29** | **0.769** |

Positive on 4 of 6 pairs. Improvement +0.009R against a +0.05R bar, p = 0.77.

**H3 is FALSIFIED.** Cross-sectional currency strength does not improve this
ensemble. The feature stays in the codebase behind `min_strength_agreement`,
default `0.0` (off). No further variants will be tried.

The sample-size problem that killed experiment 2 is solved — 5,351 trades
instead of 35 — and the answer it returns is a clean no.

## The finding that matters more

Setting the filter aside, the baseline itself was run on six pairs that
never informed any tuning decision. Two different ways of summarising the
same 2,717 trades:

| pair | n | mean R (unweighted) | risk-weighted R | PF | return | maxDD |
|---|---|---|---|---|---|---|
| GBPUSD | 514 | +0.1099 | +0.0146 | 1.03 | +5.9% | 18.4% |
| AUDUSD | 400 | +0.1683 | +0.0772 | 1.17 | +28.8% | 8.8% |
| NZDUSD | 368 | +0.0564 | −0.0780 | 0.85 | −20.5% | 21.9% |
| USDCAD | 479 | +0.0661 | −0.0814 | 0.84 | −26.9% | 31.6% |
| USDCHF | 487 | +0.1386 | +0.0207 | 1.04 | +9.9% | 18.2% |
| USDJPY | 469 | +0.1267 | −0.0115 | 0.98 | −4.5% | 21.5% |
| **mean** | | **+0.1110** | **−0.0097** | | | |

Unweighted, clustered by pair: t = +6.31 on 5 dof, p ≈ 0.0015 — apparently a
real edge. Risk-weighted: **−0.0097R, t = −0.39. Nothing.**

`expectancy_r` is an unweighted mean of per-trade R. It assumes every trade
risked the same amount. Under percent-of-equity sizing it never does, and
here losing trades were **9% to 26% larger than winning ones on all six
pairs**. Quintiles by money-at-risk, computed within each pair and then
pooled:

| quintile | n | mean R | risk-weighted | total P&L | win rate |
|---|---|---|---|---|---|
| Q1 smallest | 540 | +0.7367 | +0.6000 | +$13,790 | 73.9% |
| Q2 | 540 | +0.1586 | +0.1565 | +$5,497 | 53.1% |
| Q3 | 540 | +0.0053 | +0.0016 | +$65 | 49.3% |
| Q4 | 540 | −0.1390 | −0.1268 | −$6,821 | 41.3% |
| Q5 largest | 557 | −0.1941 | −0.1853 | −$13,252 | 40.9% |

Perfectly monotone: the system wins on its smallest bets and loses on its
biggest, and the two cancel.

Three candidate explanations were tested and **rejected**:

- *Volatility.* Bucketing by stop width (2×ATR as a share of price) gives
  +0.124 / +0.108 / +0.169 / +0.069 / +0.089 — no pattern.
- *Model conviction.* Bucketing by `|entry_score|` gives +0.056 / +0.089 /
  +0.140 / +0.085 / +0.185 — weakly positive if anything, not inverted.
- *De-risking after losses.* Trade outcomes are positively autocorrelated
  (+0.186R after a win, +0.031R after a loss, n = 2,711), so cutting size
  after a losing streak helps rather than hurts.

What remains is the equity path: size is proportional to equity, outcomes
cluster, and the account is therefore largest just as a winning cluster
breaks. Whatever the mechanism, the operational conclusion does not depend
on it — **R-multiples are not spendable.**

`Performance.expectancy_r_weighted` (= Σ P&L / Σ money risked) is now
computed and printed alongside `expectancy_r` everywhere. When the two
disagree, the weighted one is right.

## Bugs found while running this experiment

1. **Majors-only basket** (Amendment 2) — methodological; the cross-section
   could not measure breadth at all.
2. **Notional for USD-base pairs.** `margin = lots × contract_size × price ×
   margin_rate` is only correct when the quote currency is the account
   currency. One lot of USDJPY is already 100,000 USD, so multiplying by the
   JPY price overstated margin 114×, the margin guard capped the position,
   and USDJPY traded at **0.05% risk instead of 1%** — lots pinned at 0.02
   regardless of stop distance — across all 482 trades of the first panel
   run. Fixed via `SymbolSpec.notional_per_lot`, and the panel was re-run.
3. **All-zero price file.** AUDCHF downloaded as zeros stamped 1970-01-01.
   `log(0) = -inf` would have propagated through the cross-section and
   vetoed every trade on every pair, silently. `build_strength` now rejects
   empty, constant and non-positive series.

## Standing conclusion after three experiments

There is still no demonstrated edge. Experiment 2 ended at p = 0.45 on 35
trades; experiment 3 bought the sample size and the answer came back
risk-weighted −0.01R on 2,717 out-of-sample trades. The honest summary is
that this ensemble, at these costs, does not make money, and the previously
quoted R-expectancies overstated it by roughly 0.12R per trade.
