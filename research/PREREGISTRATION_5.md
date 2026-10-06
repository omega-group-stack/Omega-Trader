# Experiment 5 — picking a different strategy, not tuning this one

Written before any experiment-5 result was computed.

## 1. Why the strategy is being replaced rather than repaired

Four experiments on the ensemble produced the same answer. The decisive
numbers, now that the counting bug of experiment 4 is fixed:

| measurement | value |
|---|---|
| per-position expectancy, 6 unseen pairs, 2,549 positions | **+0.0162R** (t = 0.91) |
| total P&L ÷ total risked | **−0.0032R** |
| `corr(\|entry_score\|, R)` | **+0.024** |

That last line is the one that ends the discussion. The ensemble's own
conviction carries **no information about the outcome**. Re-weighting six
indicator blocks that are individually uninformative cannot produce an
informative combination, so there is nothing to repair. Tuning further
would only fit noise, and the holdout and six confirmation pairs are
already spent.

So the question is not "how do I fix this strategy" but "is there any FX
strategy with evidence strong enough to risk money on". That is a
different question with a much shorter list of candidates.

## 2. Why this candidate

Three properties are required before a strategy is worth real money, and
they rule out almost everything:

1. **Out-of-sample evidence produced by people who were not me**, across
   decades and markets I did not select.
2. **An economic reason to exist** — a risk premium or a behavioural
   friction — rather than a pattern found by searching.
3. **Costs that are small relative to the signal.** Our intraday work died
   here: a 1.6 pip spread plus commission on a 25–35 pip stop is ≈ 0.05R
   per trade, and no measured edge ever exceeded it.

**Time-series momentum** (also "trend following", "managed futures") is
the one FX strategy that satisfies all three. It is the most widely
replicated anomaly across asset classes, it is documented for currencies
specifically, and it has standing explanations — slow diffusion of
macroeconomic information, central banks that lean against moves rather
than jumping to the new level, and investor underreaction. Crucially it
rebalances **monthly**, so the same 1.6 pips is spread over a position
held for months and moves of hundreds of pips: cost per unit of signal
falls by roughly two orders of magnitude.

Our entire research programme has been M15 and H1. The horizon at which
the documented FX premium actually lives has never been tested here.

**This is not a free bet.** FX trend following is widely reported to have
weakened after 2008, and our data starts in 2007 — almost entirely the
weak era. A negative result is a real possibility and will be reported
as one.

## 3. The specification — fixed now, nothing tuned

Every number below is the canonical value from the literature. There is
no grid, no optimiser, and no walk-forward, because there is nothing to
select: the point of using a documented anomaly is that its parameters
come from outside this project.

* **Universe**: all 17 pairs of the basket with ≥ 10 years of history,
  resampled from H1 to daily (UTC day boundary).
* **Signal**, at each month end `t`, for each pair: `sign(P_t / P_{t−252} − 1)`,
  the 12-month trailing return. Long if positive, short if negative, never
  flat.
* **Position sizing**: inverse volatility. Each pair is scaled so its
  ex-ante annualised volatility contribution is equal, using a 60-day
  realised volatility computed from data up to and including `t`. The
  portfolio is then scaled to a **10% annualised target**.
* **Rebalance**: monthly, on the last trading day, executed at the **next
  day's open**. Signal and volatility use only data up to the signal date.
* **Costs**: 1.6 pips round trip plus $7/lot commission, charged on
  turnover — the same retail model used in experiments 2 and 3.
* **Sample**: everything available, 2007 → 2023-09.

## 4. Success criteria — fixed now

Primary metric: annualised Sharpe of **net** monthly portfolio returns,
and the t-statistic of the mean monthly return.

Two hypotheses are being tested in this experiment (see §5), so each is
judged at **p < 0.025** (Bonferroni), i.e. **|t| ≥ 2.24**.

| outcome | condition |
|---|---|
| **success** | net Sharpe ≥ 0.40 **and** t ≥ 2.24 **and** mean return positive in *both* halves of the sample |
| **weak** | net Sharpe ≥ 0.40 and t ≥ 2.24 but one half is negative |
| **failure** | anything else |

Only **success** justifies trading it. "Weak" means the result depends on
which years you happened to get, which is the same thing as not having an
edge you can rely on.

A result that is positive but fails these bars will be reported as a
failure, not as "promising".

## 5. Secondary hypothesis — the existing strategy at daily frequency

The ensemble has only ever been run on M15 and H1. At D1 the stops are
150–250 pips, so the same retail costs fall from ≈ 0.05R to ≈ 0.01R per
trade. This is cheap to run with machinery that already exists, so it is
tested as a secondary hypothesis under the same Bonferroni threshold:
per-position expectancy on the 17-pair basket, **t ≥ 2.24** required.

This is the last test the ensemble gets. If a fivefold reduction in cost
does not produce a measurable edge, the strategy is finished.

## 6. What will not be done

* No parameter search over lookbacks, volatility windows or rebalance
  frequencies. The canonical values are used exactly once.
* No reporting of a sub-period that worked if the full sample did not.
* No claim of profitability from a backtest alone, whatever it shows.
  A success here means "worth paper-trading next", not "turn it on".
