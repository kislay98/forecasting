from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from forecasting.config import LEVEL_MODELS, RETURN_MODELS
from forecasting.errors import FitError, NotFittedError
from forecasting.models.base import check_result, z_value
from forecasting.models.baselines import (
    SMA,
    Drift,
    LastReturn,
    MeanReturn,
    Naive,
    SeasonalNaive,
    ZeroReturn,
)
from forecasting.models.registry import REGISTRY, build_factories
from tests.conftest import make_scfg
from tests.synthetic import random_walk

Y = pd.Series([3.0, 5.0, 4.0, 6.0, 8.0, 7.0, 9.0, 11.0])
LEVELS = (0.8, 0.95)


def test_registry_matches_config_names():
    names = (set(LEVEL_MODELS) | set(RETURN_MODELS)) - {"sma", "combination"}
    assert set(REGISTRY) == names


def test_naive_formula():
    r = Naive().fit(Y).predict(3, LEVELS)
    np.testing.assert_array_equal(r.mean, [11.0, 11.0, 11.0])
    e = np.diff(Y.to_numpy())
    sigma = np.sqrt(np.sum(e**2) / len(e))
    np.testing.assert_allclose(r.upper[0.95] - r.mean, z_value(0.95) * sigma * np.sqrt([1, 2, 3]))


def test_seasonal_naive_formula():
    y = pd.Series(np.arange(1.0, 9.0))  # 1..8, m = 4
    r = SeasonalNaive(4).fit(y).predict(6, LEVELS)
    # y_{T+h-m(k+1)}: h=1..4 -> 5,6,7,8; h=5,6 -> 5,6 again
    np.testing.assert_array_equal(r.mean, [5, 6, 7, 8, 5, 6])
    sd = (r.upper[0.8] - r.mean) / z_value(0.8)
    e = y.to_numpy()[4:] - y.to_numpy()[:4]
    sigma = np.sqrt(np.mean(e**2))
    np.testing.assert_allclose(sd, sigma * np.sqrt([1, 1, 1, 1, 2, 2]))


def test_drift_formula():
    r = Drift().fit(Y).predict(2, LEVELS)
    T = len(Y)
    slope = (11.0 - 3.0) / (T - 1)
    np.testing.assert_allclose(r.mean, [11 + slope, 11 + 2 * slope])
    e = np.diff(Y.to_numpy()) - slope
    sigma = np.sqrt(np.sum(e**2) / (len(e) - 1))
    h = np.array([1, 2])
    np.testing.assert_allclose(
        (r.upper[0.95] - r.mean) / z_value(0.95), sigma * np.sqrt(h * (1 + h / (T - 1)))
    )


def test_drift_is_exact_on_a_line():
    y = pd.Series(np.arange(10.0))
    r = Drift().fit(y).predict(3, LEVELS)
    np.testing.assert_allclose(r.mean, [10, 11, 12])


def test_sma_formula_and_no_interval():
    r = SMA(3).fit(Y).predict(4, LEVELS)
    np.testing.assert_allclose(r.mean, np.full(4, (7 + 9 + 11) / 3))
    assert r.lower == {} and r.upper == {}
    res = SMA(3).fit(Y).residuals()
    np.testing.assert_allclose(res[0], 6.0 - (3 + 5 + 4) / 3)
    assert len(res) == len(Y) - 3


def test_return_baselines():
    r = pd.Series([0.01, -0.02, 0.03, 0.0, 0.01])
    z = ZeroReturn().fit(r).predict(2, LEVELS)
    np.testing.assert_array_equal(z.mean, [0, 0])
    np.testing.assert_allclose(
        z.upper[0.8], z_value(0.8) * np.sqrt(np.mean(r.to_numpy() ** 2)) * np.ones(2)
    )
    mr = MeanReturn().fit(r).predict(2, LEVELS)
    np.testing.assert_allclose(mr.mean, np.full(2, r.mean()))
    sd = r.std(ddof=1) * np.sqrt(1 + 1 / len(r))
    np.testing.assert_allclose((mr.upper[0.95] - mr.mean) / z_value(0.95), [sd, sd])
    lr = LastReturn().fit(r).predict(2, LEVELS)
    np.testing.assert_allclose(lr.mean, [0.01, 0.01])


ALL = [
    lambda: Naive(),
    lambda: SeasonalNaive(12),
    lambda: Drift(),
    lambda: SMA(6),
    lambda: ZeroReturn(),
    lambda: MeanReturn(),
    lambda: LastReturn(),
]


@pytest.mark.parametrize("make", ALL)
def test_contract(make):
    y = pd.Series(random_walk(n=60, seed=5))
    with pytest.raises(NotFittedError):
        make().predict(3)
    with pytest.raises(NotFittedError):
        make().residuals()
    a = make().fit(y).predict(12, LEVELS)
    b = make().fit(y).predict(12, LEVELS)
    np.testing.assert_array_equal(a.mean, b.mean)  # deterministic
    check_result(a, 12, LEVELS)  # finite, ordered bounds
    for lv in a.lower:
        assert (a.lower[lv] <= a.mean).all() and (a.mean <= a.upper[lv]).all()
    if a.lower:
        assert (a.upper[0.95] - a.lower[0.95] >= a.upper[0.8] - a.lower[0.8]).all()
    assert make() is not make()


@pytest.mark.parametrize("make", ALL)
def test_too_short_and_nan_raise_fit_error(make):
    with pytest.raises(FitError):
        make().fit(pd.Series([1.0]))
    with pytest.raises(FitError):
        make().fit(pd.Series([1.0, np.nan] * 20))


def test_factories_are_fresh_and_expand_sma():
    scfg = make_scfg(H=12)
    f = build_factories(scfg)
    stat = {"ses", "ets", "sarima", "theta"}  # combination is derived, not a factory
    assert (
        set(f) == {"naive", "seasonal_naive", "drift", "sma_3", "sma_6", "sma_12", "sma_24"} | stat
    )
    assert f["naive"](12) is not f["naive"](12)
    rf = build_factories(make_scfg(freq="trading_days", target="returns", H=20))
    assert set(rf) == {"ar"} | {
        "zero_return",
        "mean_return",
        "last_return",
        "sma_5",
        "sma_20",
        "sma_60",
        "sma_250",
    }
