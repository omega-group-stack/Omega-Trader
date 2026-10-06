# Experiment 6 — carry, the last untested FX premium

Written before any experiment-6 result was computed.

## 1. Why carry

Experiment 5 killed the two candidates it tested. Carry is the only major
FX premium left that this project has never looked at, and it has the
strongest academic pedigree of any of them: the forward premium puzzle is
one of the oldest documented anomalies in international finance, and the
"go long high-yield currencies, short low-yield currencies" portfolio has
been studied continuously since the 1980s.

It also has a *reason* to pay, which trend following in FX arguably does
not: it is compensation for crash risk. Carry earns a small positive
return most months and occasionally loses a great deal in a few days when
funding currencies spike. That is a risk premium, not a free lunch, and
it means the strategy can be real and still be a bad idea for a retail
account that cannot survive the crash.

## 2. The data, and its limit

Carry needs interest rates, which this project has never had. Two usable
public sources were found:

* **Monthly central-bank policy rates** for USD, EUR, GBP, JPY, AUD, NZD,
  CAD, CHF — `bek01/forex-trading-bot`, 2020-01 → 2025-12. Validated
  against seven independently known facts (Fed 1.75% in Jan-2020 and
  0.25% after the March-2020 cut, SNB −0.75%, BoJ −0.10%, Fed peak 5.50%
  and ECB deposit 4.00% in Sep-2023): all seven match.
* FX prices: the existing basket, which ends **2023-09**.

The overlap is therefore **45 months, 2020-01 → 2023-09**.

### This test is underpowered, and that is stated before it is run

45 monthly observations. Reaching t = 1.68 needs an annualised Sharpe of
about **0.92**; reaching t = 2.24 needs about **1.23**. Published G10
carry Sharpe ratios are in the 0.5–0.8 range, so **a true effect of the
documented size would probably not reach significance here.** A null
result in this experiment is therefore weak evidence, and will be
reported as weak.

What the window does have going for it is that it is the **most
favourable period available**. The 2022–23 tightening cycle produced the
widest G10 policy-rate dispersion in fifteen years — 5.60% between the
highest and lowest rate in Sep-2023, against 1.00% in Jun-2021. Carry
needs dispersion to have anything to harvest. If the strategy has a
pulse, this is where it shows.

## 3. Construction — textbook, nothing tuned

* **Currencies**: the eight above, accessed through the seven USD majors
  (EURUSD, GBPUSD, AUDUSD, NZDUSD, USDCAD, USDCHF, USDJPY). USD is the
  funding currency and has an excess return of zero by construction.
* **Excess return** of currency `c` in month `t`, in USD terms:

      excess_c,t = spot return of c against USD  +  (r_c − r_USD) / 12

  with the sign of the spot return flipped for USD-base pairs, since
  being long JPY is being short USDJPY.
* **Portfolio**: rank the eight currencies by policy rate known at the
  **end of month t−1**. Long the top three, short the bottom three, equal
  weight 1/3 each. This is the standard high-minus-low carry portfolio.
  The same lagged rate is used for the accrual, so nothing uses a rate
  that was not yet published.
* **Costs**: 1.6 pips round trip plus $7/lot commission, charged on
  turnover at each monthly rebalance — the same retail model as
  experiments 2, 3 and 5.

No lookback to choose, no weighting scheme to tune, no threshold. The
only judgement call is three-long/three-short out of eight, which is the
conventional split.

## 4. Success criteria — fixed now

One hypothesis, so no Bonferroni correction. One-sided, since a negative
carry return is not a tradeable finding.

| outcome | condition |
|---|---|
| **success** | net annualised Sharpe ≥ 0.50 **and** t ≥ 1.68 |
| **failure** | anything else |

**Success here does not mean tradeable.** With 45 months it would mean
"this is the first thing in six experiments that showed a pulse, and it
deserves a proper test on a longer sample" — nothing more. The honest
response to a success would be to find 20 years of rate data, not to
turn the bot on.

## 5. Reported but not decisive

These are descriptive and are **not** success criteria. They are listed
now so that choosing them later cannot be mistaken for a finding:

1. **Decomposition** into the interest accrual and the spot move. The
   forward premium puzzle says the accrual should survive and the spot
   leg should not fully offset it. Seeing which leg does what is the most
   informative part of the experiment regardless of the verdict.
2. **The 2022-01 → 2023-09 sub-period**, the maximum-dispersion window.
   Reported for interpretation only. A positive sub-period inside a
   negative full sample is not a result.
3. **The worst month and the maximum drawdown**, because the whole
   economic story for carry is that it pays a premium for crash risk. A
   good Sharpe with a catastrophic worst month is a reason not to trade
   it, not a reason to trade it.
