"""Metrics (forecasting/evaluation/metrics.py) against hand-computed values on tiny arrays.

Every expected value below is worked out by hand in the test or its docstring; none
is produced by the function under test.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from scipy import stats

from forecasting.evaluation import metrics as mt

Y = np.array([1.0, 2.0, 3.0, 4.0])
F = np.array([2.0, 2.0, 1.0, 5.0])  # e = y - f = [-1, 0, 2, -1]


def test_point_metrics_hand_computed():
    """e = [-1, 0, 2, -1]: MAE 4/4, RMSE sqrt(6/4), bias 0/4, WAPE 4/10."""
    assert mt.mae(Y, F) == 1.0
    assert mt.rmse(Y, F) == pytest.approx(math.sqrt(1.5))
    assert mt.bias(Y, F) == 0.0
    assert mt.wape(Y, F) == pytest.approx(0.4)


def test_bias_sign_is_actual_minus_forecast():
    """Forecasting 1 below every actual gives bias +1 (e = y - y_hat, research 5.7)."""
    assert mt.bias(Y, Y - 1) == 1.0


def test_mape_and_smape_hand_computed():
    """MAPE = 100 mean(1/1, 0/2, 2/3, 1/4) = 100 * 23/48.
    sMAPE = 100 mean(2/3, 0/4, 4/4, 2/9) = 100 * (6 + 0 + 9 + 2) / 36."""
    assert mt.mape(Y, F) == pytest.approx(100 * 23 / 48)
    assert mt.smape(Y, F) == pytest.approx(100 * 17 / 36)


def test_smape_zero_over_zero_is_zero():
    assert mt.smape([0.0, 2.0], [0.0, 2.0]) == 0.0


def test_mape_refuses_non_positive_actuals():
    with pytest.raises(ValueError, match="actual > 0"):
        mt.mape([1.0, 0.0], [1.0, 1.0])
    with pytest.raises(ValueError, match="actual > 0"):
        mt.mape([1.0, -0.01], [1.0, 1.0])


def test_mase_uses_each_rows_own_scale():
    """|e| = [1, 2, 3], scale = [1, 4, 1]: mean(1, 0.5, 3) = 1.5. A pooled scale
    (MAE / mean scale = 2 / 2 = 1) would give a different answer."""
    y, f = np.array([1.0, 2.0, 3.0]), np.zeros(3)
    assert mt.mase(y, f, [1.0, 4.0, 1.0]) == pytest.approx(1.5)
    with pytest.raises(ValueError, match="scale"):
        mt.mase(y, f, [1.0, 0.0, 1.0])


def test_relative_mae_and_rmse():
    """Model e = [-1, 0, 2, -1] (MAE 1, RMSE sqrt 1.5); zero forecast e = y (MAE 2.5,
    RMSE sqrt 7.5)."""
    zero = np.zeros(4)
    assert mt.relative_mae(Y, F, zero) == pytest.approx(1 / 2.5)
    assert mt.relative_rmse(Y, F, zero) == pytest.approx(math.sqrt(1.5 / 7.5))
    with pytest.raises(ValueError, match="reference"):
        mt.relative_mae(Y, F, Y)


def test_coverage_and_winkler_hand_computed():
    """y = [0, 5, -3] in [-1, 1] at 80%: a = 0.2, 2 / a = 10.
    Scores: 2; 2 + 10 * 4 = 42; 2 + 10 * 2 = 22; mean 22. One of three inside."""
    y, lo, hi = np.array([0.0, 5.0, -3.0]), -np.ones(3), np.ones(3)
    assert mt.coverage(y, lo, hi) == pytest.approx(1 / 3)
    assert mt.winkler(y, lo, hi, 0.8) == pytest.approx(22.0)
    assert mt.mean_width(lo, hi) == 2.0


def test_coverage_counts_the_bounds_as_inside():
    assert mt.coverage([1.0, -1.0], [-1.0, -1.0], [1.0, 1.0]) == 1.0


def test_coverage_band_is_the_central_binomial_range():
    """For k = floor(n_eff) trials, lo * k is the smallest count with cdf >= 2.5% and
    hi * k the smallest with cdf >= 97.5% (checked with scipy's cdf, not ppf)."""
    for level, n_eff in [(0.8, 30.0), (0.95, 12.5), (0.8, 250.0)]:
        k = math.floor(n_eff)
        lo, hi = mt.coverage_band(level, n_eff)
        a, b = round(lo * k), round(hi * k)
        assert stats.binom.cdf(a, k, level) >= 0.025 > stats.binom.cdf(a - 1, k, level)
        assert stats.binom.cdf(b, k, level) >= 0.975 > stats.binom.cdf(b - 1, k, level)


def test_coverage_band_widens_as_the_effective_sample_shrinks():
    lo30, hi30 = mt.coverage_band(0.8, 30)
    lo3, hi3 = mt.coverage_band(0.8, 30 / 10)
    assert lo3 < lo30 and hi3 >= hi30
    assert mt.n_effective(30, 10) == 3.0


def test_directional_accuracy_treats_zero_as_not_up():
    """Directions (value > 0): y up, down, up, flat; f up, up, down, flat -> 2 of 4.
    A zero forecast is never 'up', so it scores the share of non-positive actuals."""
    y = np.array([0.5, -0.2, 0.1, 0.0])
    f = np.array([0.3, 0.1, -0.4, 0.0])
    assert mt.directional_accuracy(y, f) == 0.5
    assert mt.directional_accuracy(y, np.zeros(4)) == 0.5


def test_r2_oos_hand_computed():
    """y = [1, -1, 2], model [0.5, -0.5, 1] (SSE 0.25 + 0.25 + 1 = 1.5), benchmark 0
    (SSE 6): R^2 = 1 - 1.5 / 6 = 0.75. The benchmark against itself is 0."""
    y = np.array([1.0, -1.0, 2.0])
    assert mt.r2_oos(y, [0.5, -0.5, 1.0], np.zeros(3)) == pytest.approx(0.75)
    assert mt.r2_oos(y, np.zeros(3), np.zeros(3)) == 0.0


@pytest.mark.parametrize(
    "fn, args",
    [
        (mt.mae, ([1.0, np.nan], [1.0, 1.0])),
        (mt.rmse, ([1.0, 2.0], [1.0, np.inf])),
        (mt.mase, ([1.0], [1.0], [np.nan])),
        (mt.coverage, ([1.0], [np.nan], [2.0])),
        (mt.winkler, ([1.0], [0.0], [np.nan], 0.8)),
        (mt.directional_accuracy, ([np.nan], [1.0])),
        (mt.r2_oos, ([1.0], [1.0], [np.nan])),
    ],
)
def test_nan_is_a_value_error(fn, args):
    with pytest.raises(ValueError, match="NaN"):
        fn(*args)


@pytest.mark.parametrize(
    "fn, args",
    [
        (mt.mae, ([1.0, 2.0], [1.0])),
        (mt.mase, ([1.0, 2.0], [1.0, 2.0], [1.0])),
        (mt.coverage, ([1.0], [0.0, 0.0], [2.0])),
        (mt.relative_mae, ([1.0, 2.0], [1.0, 2.0], [[1.0, 2.0]])),
    ],
)
def test_shape_mismatch_is_a_value_error(fn, args):
    with pytest.raises(ValueError, match="shape"):
        fn(*args)


def test_empty_input_is_a_value_error():
    with pytest.raises(ValueError, match="empty"):
        mt.mae([], [])


@pytest.mark.parametrize(
    "m, H, expected",
    [
        (
            12,
            24,
            {"very_short": [1], "short": [2, 3], "medium": range(4, 13), "long": range(13, 25)},
        ),
        (1, 20, {"very_short": [1], "short": [2, 3], "long": range(4, 21)}),
        (4, 8, {"very_short": [1], "medium": [2, 3, 4], "long": [5, 6, 7, 8]}),
        (
            52,
            104,
            {
                "very_short": [1],
                "short": range(2, 14),
                "medium": range(14, 53),
                "long": range(53, 105),
            },
        ),
        (12, 6, {"very_short": [1], "short": [2, 3], "medium": [4, 5, 6]}),
        (1, 2, {"very_short": [1], "short": [2]}),
    ],
)
def test_horizon_buckets(m, H, expected):
    """Spec: very short {1}; short {2 .. ceil(m/4)}; medium {ceil(m/4)+1 .. m}; long
    {m+1 .. H}; m = 1 gives {1}, {2..3}, {4..H}; empty buckets omitted."""
    got = mt.horizon_buckets(m, H)
    assert {k: list(v) for k, v in got.items()} == {k: list(v) for k, v in expected.items()}
    assert list(got) == [k for k in mt.BUCKET_NAMES if k in expected]  # ordered short to long


def test_test_horizons():
    assert mt.test_horizons(12, 24, (1, 12, 24)) == (1, 12, 24)
    assert mt.test_horizons(12, 24) == (1, 3, 12, 24)  # last h of each bucket
    assert mt.test_horizons(1, 20) == (1, 3, 20)
