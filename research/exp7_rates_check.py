"""Validate the transcribed FRED 3-month interbank panel before it is used.

The data in data/rates/g10_3m_interbank.csv was transcribed by hand from FRED
(the sandbox cannot reach fred.stlouisfed.org), so it must be checked against
independently-known facts before any backtest is run on it.  If this script
does not print ALL CHECKS PASSED, the panel must not be used.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

PATH = Path("data/rates/g10_3m_interbank.csv")
CCYS = ["USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF"]


def load(path: Path = PATH) -> pd.DataFrame:
    start = None
    n = None
    series: dict[str, list[float | None]] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, rhs = line.partition("=")
        if key == "START":
            start = rhs
        elif key == "N":
            n = int(rhs)
        else:
            series[key] = [float(v) if v.strip() else None for v in rhs.split(",")]
    assert start and n, "missing START/N header"
    idx = pd.period_range(start=start, periods=n, freq="M")
    for ccy, vals in series.items():
        assert len(vals) == n, f"{ccy}: {len(vals)} values, expected {n}"
    return pd.DataFrame(series, index=idx)[CCYS]


FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    status = "ok  " if ok else "FAIL"
    print(f"  [{status}] {label}{(' -- ' + detail) if detail else ''}")
    if not ok:
        FAILURES.append(label)


def near(got, want, tol) -> bool:
    return got is not None and pd.notna(got) and abs(float(got) - want) <= tol


def main() -> int:
    df = load()
    print(f"panel: {df.shape[0]} months x {df.shape[1]} currencies, "
          f"{df.index[0]} .. {df.index[-1]}\n")

    print("structure")
    check("203 months", len(df) == 203)
    check("8 currencies", list(df.columns) == CCYS)
    check("index is contiguous monthly",
          (df.index == pd.period_range("2006-12", periods=203, freq="M")).all())

    nan_counts = df.isna().sum()
    check("only one gap in the whole panel", int(nan_counts.sum()) == 1,
          f"gaps per ccy: {dict(nan_counts[nan_counts > 0])}")
    check("the gap is USD 2020-04",
          bool(df.loc[pd.Period("2020-04", "M"), "USD"] != df.loc[pd.Period("2020-04", "M"), "USD"]))

    print("\nplausible ranges (3m interbank, 2007-2023)")
    for ccy in CCYS:
        s = df[ccy].dropna()
        check(f"{ccy} within [-1.5, 10]", bool(s.min() >= -1.5 and s.max() <= 10.0),
              f"min {s.min():+.3f}  max {s.max():+.3f}")

    print("\nknown levels")
    # USD: Fed funds was 5.25% through 2006-2007; USD Libor 3m sat just above it.
    check("USD 2006-12 ~ 5.32", near(df.loc["2006-12", "USD"], 5.32, 0.05))
    # ZIRP: 2010-2015 the whole stretch is pinned near zero.
    zirp = df.loc["2010-01":"2015-06", "USD"]
    check("USD 2010-01..2015-06 all < 0.60", bool((zirp < 0.60).all()),
          f"max {zirp.max():.3f}")
    # 2023 hiking cycle peak.
    check("USD 2023-09 ~ 5.49", near(df.loc["2023-09", "USD"], 5.49, 0.05))
    # Lehman spike: USD 3m interbank jumped on the credit spread in Oct 2008.
    check("USD 2008-10 > 2008-09 (Lehman credit spread)",
          float(df.loc["2008-10", "USD"]) > float(df.loc["2008-09", "USD"]),
          f"{float(df.loc['2008-09','USD']):.2f} -> {float(df.loc['2008-10','USD']):.2f}")

    print("\nnegative-rate regimes")
    eur_neg = df.loc["2015-06":"2022-06", "EUR"]
    check("EUR negative throughout 2015-06..2022-06", bool((eur_neg < 0).all()),
          f"max {eur_neg.max():+.4f}")
    chf_neg = df.loc["2015-02":"2022-06", "CHF"]
    check("CHF negative throughout 2015-02..2022-06", bool((chf_neg < 0).all()),
          f"max {chf_neg.max():+.4f}")
    check("CHF 2015-2021 near the -0.75 SNB policy rate",
          near(df.loc["2016-01":"2021-12", "CHF"].mean(), -0.78, 0.12),
          f"mean {df.loc['2016-01':'2021-12', 'CHF'].mean():+.4f}")
    check("CHF Jan-2015 floor removal: big drop Dec-14 -> Jan-15",
          float(df.loc["2015-01", "CHF"]) - float(df.loc["2014-12", "CHF"]) < -0.35,
          f"{float(df.loc['2014-12','CHF']):+.4f} -> {float(df.loc['2015-01','CHF']):+.4f}")
    jpy_neg = df.loc["2020-05":"2022-05", "JPY"]
    check("JPY mostly negative 2020-05..2022-05", bool((jpy_neg < 0).mean() > 0.9),
          f"share negative {float((jpy_neg < 0).mean()):.2f}")
    check("JPY never above 1.0 anywhere in sample", bool(df["JPY"].max() < 1.0),
          f"max {df['JPY'].max():+.3f}")

    print("\nhigh-yielder peaks (the carry long leg)")
    check("AUD 2008-03 ~ 7.90", near(df.loc["2008-03", "AUD"], 7.90, 0.10))
    check("NZD 2007-12 ~ 8.90", near(df.loc["2007-12", "NZD"], 8.90, 0.10))
    check("NZD is the highest yielder in 2007", 
          df.loc["2007-01":"2007-12"].mean().idxmax() == "NZD",
          f"ranking {df.loc['2007-01':'2007-12'].mean().round(2).sort_values(ascending=False).to_dict()}")
    check("JPY is the lowest yielder in 2007",
          df.loc["2007-01":"2007-12"].mean().idxmin() == "JPY")

    print("\n2022-2023 hiking cycle (every currency rises)")
    for ccy in CCYS:
        lo = float(df.loc["2021-12", ccy])
        hi = float(df.loc["2023-09", ccy])
        ok = hi > lo
        check(f"{ccy} rose 2021-12 -> 2023-09", ok, f"{lo:+.2f} -> {hi:+.2f}")
    check("JPY rose the least of all 8",
          (df.loc["2023-09"] - df.loc["2021-12"]).idxmin() == "JPY")

    print("\noverlap with the committed policy-rate file (2020-01..2023-09)")
    pol_path = Path("data/rates/policy_rates.csv")
    if pol_path.exists():
        pol = pd.read_csv(pol_path)
        datecol = pol.columns[0]
        pol[datecol] = pd.PeriodIndex(pd.to_datetime(pol[datecol]), freq="M")
        pol = pol.set_index(datecol)
        common = [c for c in CCYS if c in pol.columns]
        both = df.loc["2020-01":"2023-09", common]
        polw = pol.loc["2020-01":"2023-09", common]
        for ccy in common:
            a = both[ccy].astype(float).ffill()
            b = polw[ccy].astype(float)
            mad = (a - b).abs().mean()
            # A constant policy rate (BoJ held -0.10 for the whole window) has
            # zero variance, so correlation is undefined.  Fall back to level
            # agreement in that case.
            if b.std() < 1e-9:
                check(f"{ccy} tracks its policy rate (flat: mean|diff| < 0.50pp)",
                      bool(mad < 0.50),
                      f"policy rate constant at {b.iloc[0]:+.2f}  mean|diff| {mad:.3f}pp")
            else:
                corr = a.corr(b)
                check(f"{ccy} tracks its policy rate (corr > 0.90)", bool(corr > 0.90),
                      f"corr {corr:+.3f}  mean|diff| {mad:.3f}pp")
    else:
        print("  (policy_rates.csv not found -- skipped)")

    print()
    if FAILURES:
        print(f"{len(FAILURES)} CHECK(S) FAILED:")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
