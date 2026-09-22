from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from forecasting.errors import NotFittedError, TransformError
from forecasting.transforms import (
    BoxCox,
    Identity,
    Interpolator,
    Log,
    LogReturn,
    OutlierFlagger,
    select_transform,
    transform_label,
    variance_grows_with_level,
)
from tests.synthetic import gbm_prices, random_walk


def s(values) -> pd.Series:
    return pd.Series(np.asarray(values, dtype=float))


@pytest.mark.parametrize("tr", [Identity(), Log(), BoxCox()])
def test_round_trip_to_1e9(tr):
    y = s(gbm_prices(n=300, seed=1) / 100)
    tr.fit(y)
    back = tr.inverse(tr.transform(y).to_numpy())
    np.testing.assert_allclose(back, y.to_numpy(), rtol=1e-9, atol=1e-9)


@pytest.mark.parametrize("tr", [Log(), BoxCox()])
def test_nonpositive_slice_raises(tr):
    with pytest.raises(TransformError):
        tr.fit(s([1.0, 0.0, 2.0, 3.0]))


def test_use_before_fit_raises():
    for tr in (Identity(), Log(), BoxCox(), LogReturn(), Interpolator()):
        with pytest.raises(NotFittedError):
            tr.transform(s([1.0, 2.0]))


def test_boxcox_lambda_comes_from_fit_slice_only():
    y = s(gbm_prices(n=400, seed=3))
    a = BoxCox().fit(y.iloc[:200])
    b = BoxCox().fit(y)
    assert a.lmbda != b.lmbda
    assert transform_label(a).startswith("boxcox(")


def test_auto_picks_log_when_spread_grows_with_level():
    rng = np.random.default_rng(0)
    level = np.linspace(10, 1000, 240)
    multiplicative = s(level * np.exp(rng.normal(0, 0.05, 240)))
    additive = s(level + rng.normal(0, 5, 240))
    assert variance_grows_with_level(multiplicative, 12)
    assert not variance_grows_with_level(additive, 12)
    assert select_transform(multiplicative, "auto", 12).name == "log"
    assert select_transform(additive, "auto", 12).name == "none"


def test_auto_is_none_with_nonpositive_values():
    assert select_transform(s(random_walk(n=100) - 200), "auto", 12).name == "none"


def test_log_return_round_trip_and_price_path():
    p = s(gbm_prices(n=50, seed=4))
    lr = LogReturn().fit(p)
    r = lr.transform(p)
    assert len(r) == 49
    np.testing.assert_allclose(r.to_numpy(), np.log(p.to_numpy()[1:] / p.to_numpy()[:-1]))
    # inverse maps a return path to a price path starting from the last training price
    path = lr.inverse(np.array([0.01, -0.02, 0.0]))
    expected = p.iloc[-1] * np.exp(np.cumsum([0.01, -0.02, 0.0]))
    np.testing.assert_allclose(path, expected, rtol=1e-12)
    np.testing.assert_allclose(lr.inverse(np.zeros(3)), np.full(3, p.iloc[-1]))


def test_log_return_rejects_bad_prices():
    with pytest.raises(TransformError):
        LogReturn().fit(s([1.0, -1.0, 2.0]))
    with pytest.raises(TransformError):
        LogReturn().fit(s([1.0]))


def test_interpolator_fills_inside_only():
    y = s([np.nan, 1.0, np.nan, 3.0, 4.0])
    itp = Interpolator().fit(y)
    out = itp.transform(y)
    assert out.tolist() == [1.0, 2.0, 3.0, 4.0]  # leading NaN dropped, interior filled
    assert itp.n_filled == 1  # the leading NaN is dropped, not filled


def test_interpolator_refuses_missing_last_value():
    with pytest.raises(TransformError):
        Interpolator().fit(s([1.0, 2.0, np.nan]))


def test_outlier_flagger_flags_spike_without_changing_values():
    y = s(np.random.default_rng(1).normal(100, 1, 120))
    y.iloc[80] = 150
    f = OutlierFlagger(m=12).fit(y)
    assert f.n_flags >= 1 and f.flags[80]
    pd.testing.assert_series_equal(f.transform(y), y)


def test_outlier_flag_at_t_uses_only_the_past():
    y = s(np.random.default_rng(2).normal(0, 1, 100))
    before = OutlierFlagger(m=1).fit(y.iloc[:60]).flags
    after = OutlierFlagger(m=1).fit(y).flags[:60]
    np.testing.assert_array_equal(before, after)
