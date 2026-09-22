from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from forecasting.data.adapters import read_csv
from forecasting.data.validate import validate, volume_thresholds
from forecasting.errors import (
    DataValidationError,
    DuplicateTimestampError,
    IrregularFrequencyError,
    LongGapError,
    MissingColumnError,
    NegativeValueError,
    NonFiniteTargetError,
    NonNumericTargetError,
    NonPositivePriceError,
    TimestampError,
    TooManyMissingError,
    TooManyZerosError,
    TooShortError,
    TradingCalendarError,
)
from tests.conftest import FIXTURES, make_scfg
from tests.synthetic import gbm_prices, random_walk, seasonal_ar1, to_frame

MAPPED = {"date": "date", "value": "value"}

# One bad CSV per rule: (file, labels, expected error). Each must fail on its own rule.
BAD = [
    ("bad_duplicates.csv", {}, DuplicateTimestampError),
    ("bad_irregular_freq.csv", {}, IrregularFrequencyError),
    ("bad_long_gap.csv", {}, LongGapError),
    ("bad_too_short.csv", {}, TooShortError),
    ("bad_zeros.csv", {}, TooManyZerosError),
    ("bad_negatives.csv", {"positive": True}, NegativeValueError),
    ("bad_non_numeric.csv", {}, NonNumericTargetError),
    ("bad_missing_share.csv", {}, TooManyMissingError),
    ("bad_timezone.csv", {}, TimestampError),
    ("bad_weekend.csv", {"freq": "trading_days", "H": 1}, TradingCalendarError),
    ("bad_trading_gap.csv", {"freq": "trading_days", "H": 1}, LongGapError),
    (
        "bad_returns_nonpositive.csv",
        {"freq": "trading_days", "H": 1, "target": "returns"},
        NonPositivePriceError,
    ),
]


def run(df: pd.DataFrame, **labels):
    return validate(df, make_scfg(**labels))


@pytest.mark.parametrize("fname,labels,error", BAD, ids=[b[0] for b in BAD])
def test_bad_fixture_fails_with_its_typed_error(fname, labels, error):
    scfg = make_scfg(columns=MAPPED, **labels)
    df = read_csv(FIXTURES / fname, scfg)
    with pytest.raises(error) as e:
        validate(df, scfg)
    # Exact type, not just a DataValidationError, so a rule cannot shadow another.
    assert type(e.value) is error
    assert e.value.rule in str(e.value)


def test_every_bad_fixture_is_covered():
    on_disk = {p.name for p in FIXTURES.glob("bad_*.csv")}
    assert on_disk == {b[0] for b in BAD}


def test_negatives_allowed_without_positive_flag():
    scfg = make_scfg(columns=MAPPED)
    df = read_csv(FIXTURES / "bad_negatives.csv", scfg)
    # Without positive: true the negatives pass; the file then fails only on volume.
    with pytest.raises(TooShortError):
        validate(df, scfg)


# ------------------------------------------------------------------ good data


def test_good_monthly_passes_and_never_changes_values():
    df = to_frame(seasonal_ar1(n=120, seed=3), "monthly")
    [(series, report)] = run(df, H=12)
    np.testing.assert_array_equal(series.y.to_numpy(), df["y"].to_numpy())
    assert isinstance(series.y.index, pd.PeriodIndex)
    assert series.y.index.freqstr == "M"
    assert report.mode == "full" and report.T == 120 and report.n_missing == 0
    assert series.m == 12 and len(series.data_hash) == 64


def test_input_frame_is_not_mutated():
    df = to_frame(random_walk(n=100), "monthly")
    before = df.copy()
    run(df, H=12)
    pd.testing.assert_frame_equal(df, before)


def test_timestamps_normalised_to_period_start():
    df = to_frame(random_walk(n=100), "monthly")
    df["ds"] = df["ds"] + pd.Timedelta(days=14)  # mid-month dates
    [(series, _)] = run(df, H=12)
    assert str(series.y.index[0]) == "2000-01"


def test_short_gaps_become_nan_not_imputed():
    df = to_frame(random_walk(n=120), "monthly").drop(index=[40, 41, 80])
    [(series, report)] = run(df, H=12)
    assert len(series.y) == 120
    assert series.y.isna().sum() == 3 and report.max_gap == 2
    observed = series.y.dropna().to_numpy()
    np.testing.assert_array_equal(observed, df["y"].to_numpy())


def test_reduced_mode_between_floor_and_minimum():
    t_floor, t_min = volume_thresholds(make_scfg(H=12))
    assert (t_floor, t_min) == (56, 97)  # spec table, monthly H = 12
    df = to_frame(random_walk(n=70), "monthly")
    [(_, report)] = run(df, H=12)
    assert report.mode == "reduced"
    assert any("reduced mode" in w for w in report.warnings)


@pytest.mark.parametrize(
    "labels,expected",
    [
        ({"freq": "monthly", "H": 12}, (56, 97)),
        # Spec table says 36; its own formula max(2m, 12) + H + 20 gives 40. Formula wins.
        ({"freq": "quarterly", "H": 8}, (40, 81)),
        ({"freq": "weekly", "H": 13}, (137, 218)),
        ({"freq": "monthly", "season": "none", "H": 12}, (44, 85)),
    ],
)
def test_volume_thresholds_match_spec_table(labels, expected):
    assert volume_thresholds(make_scfg(**labels)) == expected


def test_quarterly_and_weekly_pass():
    [(q, _)] = run(to_frame(random_walk(n=100), "quarterly"), freq="quarterly", H=8)
    assert q.y.index.freqstr == "Q-DEC"
    [(w, _)] = run(to_frame(random_walk(n=260), "weekly"), freq="weekly", H=13)
    assert w.y.index.freqstr.startswith("W")


def test_duplicates_aggregated_only_when_configured():
    df = to_frame(random_walk(n=100), "monthly")
    dup = pd.concat([df, df.iloc[[10]]], ignore_index=True)
    with pytest.raises(DuplicateTimestampError):
        run(dup, H=12)
    [(series, report)] = run(dup, H=12, duplicates="sum")
    assert series.y.iloc[10] == pytest.approx(2 * df["y"].iloc[10])
    assert report.n_aggregated == 2
    [(series, _)] = run(dup, H=12, duplicates="mean")
    assert series.y.iloc[10] == pytest.approx(df["y"].iloc[10])


def test_finer_than_declared_is_irregular():
    df = to_frame(random_walk(n=100), "weekly")
    with pytest.raises(IrregularFrequencyError, match="finer"):
        run(df, freq="monthly", H=12)


def test_missing_required_column():
    df = to_frame(random_walk(n=100), "monthly").drop(columns=["y"])
    with pytest.raises(MissingColumnError):
        run(df, H=12)


def test_infinite_values_rejected():
    df = to_frame(random_walk(n=100), "monthly")
    df.loc[5, "y"] = np.inf
    with pytest.raises(NonFiniteTargetError):
        run(df, H=12)


def test_string_nan_is_not_silently_missing():
    df = to_frame(random_walk(n=100), "monthly")
    df["y"] = df["y"].astype(str)
    df.loc[5, "y"] = "nan"
    with pytest.raises(NonNumericTargetError):
        run(df, H=12)


def test_unparseable_dates():
    df = to_frame(random_walk(n=100), "monthly")
    df["ds"] = df["ds"].astype(str)
    df.loc[3, "ds"] = "not a date"
    with pytest.raises(TimestampError):
        run(df, H=12)


def test_extra_columns_ignored_with_warning():
    df = to_frame(random_walk(n=100), "monthly")
    df["volume"] = 1.0
    [(_, report)] = run(df, H=12)
    assert report.ignored_columns == ("volume",)
    assert any("ignored columns" in w for w in report.warnings)


def test_start_drops_rows_before_a_long_gap():
    df = to_frame(random_walk(n=140), "monthly").drop(index=range(10, 15))
    with pytest.raises(LongGapError, match="start"):
        run(df, H=12)
    [(series, report)] = run(df, H=12, start="2001-04-01")
    assert str(series.y.index[0]) == "2001-04"
    assert report.n_dropped_before_start == 10


def test_panel_returns_one_result_per_series():
    a = to_frame(random_walk(n=100, seed=1), "monthly", unique_id="a")
    b = to_frame(random_walk(n=100, seed=2), "monthly", unique_id="b")
    results = run(pd.concat([b, a]), H=12)
    assert [s.unique_id for s, _ in results] == ["a", "b"]


def test_data_hash_changes_with_data():
    df = to_frame(random_walk(n=100), "monthly")
    [(s1, _)] = run(df, H=12)
    df2 = df.copy()
    df2.loc[50, "y"] += 1e-9
    [(s2, _)] = run(df2, H=12)
    assert s1.data_hash != s2.data_hash


# ------------------------------------------------------------------ trading days


def trading_frame(n: int = 2000, drop_every: int = 23, seed: int = 0) -> pd.DataFrame:
    df = to_frame(gbm_prices(n=n, seed=seed), "trading_days", "2015-01-01")
    return df.drop(index=df.index[::drop_every]).reset_index(drop=True)


def test_trading_days_flags_closures_without_imputing():
    df = trading_frame()
    [(series, report)] = run(df, freq="trading_days", H=20, target="returns")
    assert isinstance(series.y.index, pd.DatetimeIndex)
    assert len(series.y) == len(df)  # closures are not inserted
    assert series.y.isna().sum() == 0
    # Row 0 is dropped too, but it sits before the first date, so it is not a closure.
    assert len(report.flagged_weekdays) == len(range(23, 2000, 23))
    assert report.mode == "full"
    np.testing.assert_array_equal(series.y.to_numpy(), df["y"].to_numpy())


def test_trading_days_volume_uses_trading_design():
    t_floor, t_min = volume_thresholds(make_scfg(freq="trading_days", H=20))
    assert (t_floor, t_min) == (370, 1865)
    df = trading_frame(n=600)
    [(_, report)] = run(df, freq="trading_days", H=20, target="returns")
    assert report.mode == "reduced"
    with pytest.raises(TooShortError):
        run(trading_frame(n=300), freq="trading_days", H=20)


def test_trading_days_rejects_intraday_times():
    df = trading_frame()
    df["ds"] = df["ds"] + pd.Timedelta(hours=15, minutes=30)
    with pytest.raises(TimestampError, match="timezone"):
        run(df, freq="trading_days", H=20)


def test_trading_days_weekly_data_is_irregular():
    df = to_frame(gbm_prices(n=500), "weekly")
    df["ds"] = df["ds"] + pd.Timedelta(days=2)  # Wednesdays
    with pytest.raises(IrregularFrequencyError, match="coarser"):
        run(df, freq="trading_days", H=5)


def test_trading_days_nan_rows_count_as_missing():
    df = trading_frame()
    df.loc[100:102, "y"] = np.nan  # three trading days without a value
    with pytest.raises(LongGapError):
        run(df, freq="trading_days", H=20)


def test_all_errors_are_data_validation_errors():
    for _, _, err in BAD:
        assert issubclass(err, DataValidationError)


def test_fixture_dir_exists(fixtures_dir: Path):
    assert (fixtures_dir / "make_fixtures.py").exists()
