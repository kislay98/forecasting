"""Regenerate the bad-data fixtures and example data. Deterministic; run from repo root:

    uv run python tests/fixtures/make_fixtures.py

Each bad_*.csv breaks exactly one rule; tests/test_validate.py maps file -> error.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tests.synthetic import gbm_prices, seasonal_ar1, to_frame  # noqa: E402

FIX = ROOT / "tests" / "fixtures"
EX = ROOT / "examples" / "data"


def months(n: int, start: str = "2020-01-01") -> list[str]:
    return [d.strftime("%Y-%m-%d") for d in pd.date_range(start, periods=n, freq="MS")]


def write(name: str, dates: list[str], values: list[str]) -> None:
    pd.DataFrame({"date": dates, "value": values}).to_csv(FIX / name, index=False)


def main() -> None:
    FIX.mkdir(parents=True, exist_ok=True)
    base = [str(100 + i) for i in range(40)]

    d = months(12)
    write("bad_duplicates.csv", [*d[:3], d[2], *d[3:]], [*base[:3], "103", *base[3:12]])

    q = [x.strftime("%Y-%m-%d") for x in pd.date_range("2020-01-01", periods=12, freq="QS")]
    write("bad_irregular_freq.csv", q, base[:12])

    d = months(24)
    gap = [x for i, x in enumerate(d) if i not in (10, 11, 12, 13)]
    write("bad_long_gap.csv", gap, base[: len(gap)])

    write("bad_too_short.csv", months(30), [str(100 + i) for i in range(30)])

    vals = base[:12]
    for i in (1, 3, 5, 7, 9):
        vals[i] = "0"
    write("bad_zeros.csv", months(12), vals)

    vals = base[:12]
    vals[4], vals[8] = "-3", "-1.5"
    write("bad_negatives.csv", months(12), vals)

    vals = base[:12]
    vals[6] = "abc"
    write("bad_non_numeric.csv", months(12), vals)

    vals = [str(100 + i) for i in range(24)]
    vals[5], vals[15] = "", ""
    write("bad_missing_share.csv", months(24), vals)

    # trading_days: 2024-01-01 is a Monday.
    wd = [x.strftime("%Y-%m-%d") for x in pd.bdate_range("2024-01-01", periods=10)]
    write("bad_weekend.csv", [*wd[:5], "2024-01-06", *wd[5:]], [str(100 + i) for i in range(11)])

    wd = [x.strftime("%Y-%m-%d") for x in pd.bdate_range("2024-01-01", periods=60)]
    kept = [x for i, x in enumerate(wd) if not 20 <= i < 27]
    write("bad_trading_gap.csv", kept, [str(100 + i) for i in range(len(kept))])

    vals = [str(100 + i) for i in range(10)]
    vals[4] = "0"
    write("bad_returns_nonpositive.csv", wd[:10], vals)

    tz = [f"{x}T00:00:00+05:30" for x in months(12)]
    write("bad_timezone.csv", tz, base[:12])

    # Examples for the CLI walkthrough.
    EX.mkdir(parents=True, exist_ok=True)
    elec = to_frame(seasonal_ar1(n=240, seed=1), "monthly", "2005-01-01", "electricity_like")
    elec.rename(columns={"ds": "observation_date", "y": "value"})[
        ["observation_date", "value"]
    ].to_csv(EX / "electricity_like.csv", index=False, float_format="%.4f")

    idx = to_frame(gbm_prices(n=2100, seed=2), "trading_days", "2015-01-01", "index_like")
    holidays = idx.index[::23]  # about 11 closures a year, flagged not imputed
    idx = idx.drop(index=holidays)
    idx["Date"] = idx["ds"].dt.strftime("%d-%b-%Y")
    idx.rename(columns={"y": "Close"})[["Date", "Close"]].to_csv(
        EX / "index_like.csv", index=False, float_format="%.2f"
    )


if __name__ == "__main__":
    main()
