from __future__ import annotations

import numpy as np
import pytest

from tests.synthetic import (
    garch11_prices,
    garch11_returns,
    gbm_prices,
    local_linear_trend,
    random_walk,
    seasonal_ar1,
    to_frame,
    trend_reversal_variance_jump,
)


def test_same_seed_same_series_different_seed_different_series():
    for gen in (random_walk, local_linear_trend, seasonal_ar1, gbm_prices, garch11_prices):
        np.testing.assert_array_equal(gen(seed=7), gen(seed=7))
        assert not np.array_equal(gen(seed=7), gen(seed=8))
    a, ba = trend_reversal_variance_jump(seed=7)
    b, bb = trend_reversal_variance_jump(seed=7)
    np.testing.assert_array_equal(a, b)
    assert ba == bb


def test_random_walk_increments_are_white_noise():
    d = np.diff(random_walk(n=5000, seed=1))
    assert abs(d.std() - 1.0) < 0.05
    assert abs(np.corrcoef(d[:-1], d[1:])[0, 1]) < 0.05


def test_local_linear_trend_trends():
    y = local_linear_trend(n=240, slope0=0.3, slope_sigma=0.0, seed=1)
    slope = np.polyfit(np.arange(240), y, 1)[0]
    assert 0.2 < slope < 0.4


def test_seasonal_ar1_has_seasonal_autocorrelation():
    y = seasonal_ar1(n=600, m=12, seed=1)
    x = y - y.mean()
    acf12 = np.dot(x[:-12], x[12:]) / np.dot(x, x)
    acf6 = np.dot(x[:-6], x[6:]) / np.dot(x, x)
    assert acf12 > 0.8 and acf6 < -0.8


def test_trend_reversal_flips_slope_and_jumps_variance():
    y, b = trend_reversal_variance_jump(n=2000, seed=1)
    t = np.arange(2000)
    before = np.polyfit(t[:b], y[:b], 1)
    after = np.polyfit(t[b:], y[b:], 1)
    assert before[0] > 0.4 and after[0] < -0.4
    r_before = y[:b] - np.polyval(before, t[:b])
    r_after = y[b:] - np.polyval(after, t[b:])
    assert 2.0 < r_after.std() / r_before.std() < 3.0


def test_to_frame_is_canonical():
    df = to_frame(random_walk(n=10), "monthly")
    assert list(df.columns) == ["unique_id", "ds", "y"]
    assert df["ds"].iloc[1].month == 2
    tf = to_frame(gbm_prices(n=10), "trading_days", "2024-01-06")  # a Saturday
    assert (tf["ds"].dt.dayofweek < 5).all()
    assert (gbm_prices(n=100) > 0).all()


def test_garch11_is_what_the_control_claims_it_is():
    """The A12 control rests on this DGP, so check it against its own parameters.

    Not a coverage test: these are the properties the control's known answer depends on.
    If the unconditional variance drifted from omega / (1 - alpha - beta), or the
    clustering went away, the control would still pass and would be testing nothing.
    """
    omega, alpha, beta = 1.6e-6, 0.09, 0.90
    r = garch11_returns(n=40_000, omega=omega, alpha=alpha, beta=beta, seed=11)

    target_sd = np.sqrt(omega / (1 - alpha - beta))
    assert abs(r.std() / target_sd - 1) < 0.10, (r.std(), target_sd)
    assert abs(r.mean()) < 0.1 * r.std()

    # Clustering: squared returns are autocorrelated, plain returns are not.
    def ac1(x):
        x = x - x.mean()
        return float(np.corrcoef(x[:-1], x[1:])[0, 1])

    assert ac1(r**2) > 0.10, ac1(r**2)
    assert abs(ac1(r)) < 0.03, ac1(r)

    # Conditional normality plus clustering leaves unconditional excess kurtosis.
    z4 = float(np.mean(((r - r.mean()) / r.std()) ** 4))
    assert z4 > 3.5, z4

    # The burn-in is what makes the slice independent of the variance it started at.
    assert not np.allclose(
        garch11_returns(n=200, seed=11, burn_in=0), garch11_returns(n=200, seed=11)[:200]
    )


def test_garch11_rejects_a_non_stationary_parameterisation():
    with pytest.raises(ValueError, match="stationary"):
        garch11_returns(n=100, alpha=0.2, beta=0.85)
