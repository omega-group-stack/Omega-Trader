# Experiment 4 — where the account's money went

Written before any experiment-4 result was computed. Same rules as
experiments 2 and 3: the decision thresholds below are fixed now, and any
departure gets an amendment appended with the date and the reason.

## 1. The observation

On the 2,717 trades of the experiment-3 confirmation panel:

| statistic | value |
|---|---|
| mean R per trade (unweighted) | **+0.1110** |
| total P&L ÷ total money risked (risk-weighted) | **−0.0097** |
| clustered t, unweighted | +6.31 (p ≈ 0.0015) |
| clustered t, risk-weighted | −0.39 (n.s.) |

Quintiles of trades sorted by money at risk:

| quintile | mean R | win rate | P&L |
|---|---|---|---|
| Q1 (smallest) | +0.7367 | 74% | +$13,790 |
| Q3 | +0.0053 | — | — |
| Q5 (largest) | −0.1941 | 41% | −$13,252 |

The per-trade average is positive and significant. What the account earned
is zero. The difference is entirely in *how much money was on each trade*.

An account does not compound mean R. It compounds Σ P&L. So the
risk-weighted number is the real one, and the unweighted number is the
description of a strategy nobody traded.

## 2. The question

**Is the negative size/outcome relationship a property of the sizing rule,
or an artifact of equity growth?**

These demand opposite responses, and telling them apart is the whole
experiment.

* **H4-A — equity/time confound.** Money at risk is `equity × risk_pct`,
  so it tracks the equity curve. If the strategy's edge decayed across the
  2007→2023 sample, late trades are mechanically both *larger* and *worse*,
  with no defect in the sizing rule at all. Under H4-A the correct
  conclusion is "the edge decays", and flat-risk sizing would have
  "fixed" it only because the good years happened to come first — which is
  hindsight, not a rule.

* **H4-B — within-period sizing defect.** Something varies size *at a given
  moment* and systematically selects worse trades. Under default config
  three channels can do this, spanning a 2.5× range between them:
  confidence scaling (`scale_risk_with_confidence=True`, multiplier
  0.5–1.25), losing-streak de-risking, and regime throttling. Under H4-B
  the defect is removable and the fix is real.

## 3. Measurements

For every trade, money at risk is recovered as `|pnl / r_multiple|`
(the definition `metrics.py` already uses), and equity at entry as
`balance_after − pnl`.

Define the **realised risk fraction**

    f_i = money_at_risk_i / equity_at_entry_i

This is the quantity the sizing rule is supposed to hold constant. All
variation in `f` is within-period by construction: equity has been divided
out.

Three decompositions, all on the same 2,717 trades:

1. **Dispersion split.** `sd(log money_at_risk)` vs `sd(log f)`. The ratio
   says how much of the size variation is equity drift and how much is the
   sizing rule.
2. **Quintiles by `f`** (the within-period signal), against quintiles by
   raw money at risk.
3. **Quintiles by year-demeaned money at risk** — raw size minus the median
   size of its calendar year, which removes the slow equity trend while
   keeping fast variation.

## 4. Decision rules — fixed in advance

**H4-A is declared the principal cause** if *either*:
  * `sd(log f) < 0.5 × sd(log money_at_risk)`, **or**
  * the Q1−Q5 mean-R spread on year-demeaned size is **< 1/3** of the
    spread on raw size (raw spread is +0.93R, so the threshold is +0.31R).

**H4-B is declared present** for a given channel if quintiles by that
channel's multiplier are monotone in mean R with a Q1−Q5 spread
**≥ 0.20R** and clustered-by-pair **p < 0.05**.

Both can be true at once; the decomposition reports each.

## 5. What follows from each outcome

* **H4-A only.** No sizing change is justified, and none will be claimed.
  The finding is reported as *the edge decays over the sample*, and the
  follow-up question becomes whether anything is left in recent years —
  measured, not assumed. Switching to flat lots will **not** be presented
  as an improvement, because the backtest gain would come entirely from
  the good years preceding the bad ones.
* **H4-B only, or both.** The offending channel is disabled, and the change
  is confirmed on pairs that have never been used for any decision in this
  project (EURUSD and the six majors GBPUSD, AUDUSD, NZDUSD, USDCAD,
  USDCHF, USDJPY are all spent). Success = risk-weighted expectancy
  improves by ≥ +0.05R with p < 0.05 on the unspent panel.

## 6. What this experiment cannot do

It cannot create an edge. It can only establish whether an edge that
already shows up per-trade was being thrown away by the money allocation.
If H4-A holds, the answer to "can we trade this with real money" stays
**no**, and no amount of sizing work changes it.

---

## Amendment 1 — both pre-registered hypotheses were wrong (2026-10-06)

Added after the decomposition ran, before any remedy was tested.

**H4-A is rejected.** Equity drift explains almost none of it:
`sd(log f) / sd(log money_at_risk) = 0.931`, and the Q1−Q5 spread on
year-detrended size is +0.8659R against +0.8432R raw. Both pre-registered
H4-A rules fail. 93% of the size variation is within-period.

**H4-B fired, but attributing it exposed the real cause, which was not on
the list.** The within-period spread is +0.8043R, p = 0.0001, positive on
all six pairs. Chasing which channel produced it:

* confidence scaling is innocent — `corr(risk fraction, |entry_score|) =
  −0.037`, and `corr(|entry_score|, R) = +0.024`. This confirms the earlier
  rejection of model conviction as an explanation.
* the realised risk fraction is **bimodal** at ≈0.73% and ≈1.20% of equity.
  0.732 = 1.22 × 0.6, which is `loss_risk_multiplier`, so the modes are
  "normal" versus "de-risked after two consecutive losses".
* but `PARTIAL_TP` records appear **only in quintiles 1 and 2** — 122 and 46
  of them, 168 in total, exactly the number of duplicated tickets — and
  they average +1.55R.

## Amendment 2 — H4-C, the measurement artifact (2026-10-06)

**This was not pre-registered.** It was found while attributing H4-B, and
is reported as a post-hoc discovery. It is not offered as a hypothesis
test: it is a defect in the measuring instrument, demonstrable from the
code and from arithmetic, and the statistics below merely size it.

A position that takes a partial profit writes **two** trade records — the
partial at ≈+1.5R and the remainder at whatever it finally makes — each
carrying roughly half the money at risk. A position that runs straight into
its stop writes **one** record at −1R. Winners are therefore counted twice
and losers once, and both halves of a winner land in the small-size
quintiles because their lots were halved.

This single mechanism produces both headline observations:

| measured over | mean R | clustered t |
|---|---|---|
| raw records (n = 2717) | **+0.1110** | **+6.31** |
| records excluding partial exits (n = 2549) | +0.0161 | +0.90 |
| positions, partials merged back (n = 2549) | **+0.0162** | **+0.91** |

and the size/outcome relationship:

| sorted by realised risk fraction | Q1−Q5 spread | clustered t |
|---|---|---|
| per record | **+0.8043R** | **+10.65** |
| per position | +0.1189R | +1.93 |

6.6% of positions took a partial, and they supplied **86% of the apparent
per-trade edge**. The bias is remarkably stable across pairs — +0.0889R to
+0.1035R, mean **+0.0948R** — which gives a correction rule: subtract
≈0.095R from every per-record R figure produced by this project before
2026-10-06.

### Consequences

1. **There is no sizing defect to fix.** The negative size/outcome
   relationship is not a property of the sizing rule; it is the arithmetic
   of halving a position that is winning. No remedy is justified and none
   is claimed, so the unspent confirmation panel stays unspent.
2. **The one statistic that suggested an edge was an artifact.** +0.1110R
   at t = +6.31 looked decisive. The honest figure is +0.0162R at t = 0.91,
   which agrees with the risk-weighted −0.0032R. All three measurements now
   say the same thing: nothing.
3. **Experiment 2's holdout result is overstated too.** +0.138R becomes
   ≈ +0.043R on n = 35. It was already insignificant (p = 0.45); it is now
   also small.
4. Detecting +0.0168R per position against sd = 1.089 would need **16,137
   trades** — about 608 pair-years. The edge, if it exists at all, is not
   measurable and not tradeable.

### Fixed in code

`metrics.analyse` now groups records into positions before computing
`expectancy_r`, reports `positions` and `partial_exits`, and keeps the old
per-record figure as `expectancy_r_per_record` so the size of the bias is
visible rather than hidden. Three tests in `tests/test_metrics.py` pin the
behaviour. Grouping only engages when partial exits are present.
