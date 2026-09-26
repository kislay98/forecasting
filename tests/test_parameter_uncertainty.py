"""Phase 7 (OP-3): parameter uncertainty propagated through the GARCH recursion.

Known-answer tests, in the order the session prompt lists them:

  1. a zero covariance matrix reproduces the point-estimate model exactly, bit for bit;
  2. a known covariance widens the simulated distribution by a computable amount;
  3. the stationarity and positivity rejection triggers on draws built to violate it,
     at the rate the Normal says it should;
  4. the five existing variance models are untouched.

Plus the one place this module has its own arithmetic that could silently disagree with
arch: the per-draw variance filter, checked against arch's own forecast at parameter
vectors other than the fitted one.
"""

from __future__ import annotations

import inspect
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from scipy.stats import norm

from forecasting.config import (
    DEFAULT_RETURN_MODELS,
    RETURN_VARIANCE,
    load_config,
)
from forecasting.errors import FitError
from forecasting.models import parameter_uncertainty as pu
from forecasting.models import variance
from forecasting.models.base import check_result
from forecasting.models.parameter_uncertainty import (
    GARCHPU,
    GJRGARCHPU,
    admissible,
    draw_parameters,
    filter_span,
    run_paths,
)
from forecasting.models.registry import REGISTRY
from tests.synthetic import garch11_returns

ROOT = Path(__file__).resolve().parents[1]
GJR_NAMES = ["omega", "alpha[1]", "gamma[1]", "beta[1]"]
GARCH_NAMES = ["omega", "alpha[1]", "beta[1]"]
POINT = {"garch": variance.GARCH, "gjr_garch": variance.GJRGARCH}
WITH_PU = {"garch": GARCHPU, "gjr_garch": GJRGARCHPU}


@pytest.fixture(scope="module")
def returns() -> pd.Series:
    """A Nifty-like GARCH(1,1) slice, long enough for a well identified fit."""
    return pd.Series(garch11_returns(n=3000, seed=11))


def _fit(cls, r: pd.Series, seed: int = 5):
    m = cls()
    m.seed = seed
    return m.fit(r)


def _with_cov(cls, cov_fn):
    """A subclass whose draws use a substituted covariance: the test hook."""

    class Substituted(cls):
        def _param_cov(self):
            return cov_fn(super()._param_cov())

    return Substituted


# -- 1. zero covariance is the point model -------------------------------------------


@pytest.mark.parametrize("name", ["garch", "gjr_garch"])
def test_zero_covariance_reproduces_the_point_model_bit_for_bit(returns, name):
    point = _fit(POINT[name], returns)
    zero = _fit(_with_cov(WITH_PU[name], np.zeros_like), returns)
    a = point.simulate(20, 5000, np.random.default_rng(3))
    b = zero.simulate(20, 5000, np.random.default_rng(3))
    assert np.array_equal(a, b)
    assert zero.rejection_rate == 0.0


def test_zero_covariance_draws_are_exactly_the_estimate():
    theta = np.array([0.02, 0.05, 0.10, 0.88])
    d, rate = draw_parameters(theta, np.zeros((4, 4)), GJR_NAMES, 1000, np.random.default_rng(0))
    assert rate == 0.0
    assert (d == theta[None, :]).all()


def test_the_real_covariance_changes_the_paths(returns):
    """The converse of the zero case: without it, the zero test could pass on a model that
    never used its draws at all."""
    point = _fit(variance.GJRGARCH, returns)
    full = _fit(GJRGARCHPU, returns)
    a = point.simulate(20, 5000, np.random.default_rng(3))
    b = full.simulate(20, 5000, np.random.default_rng(3))
    assert not np.array_equal(a, b)


# -- 2. a known covariance widens by a computable amount ------------------------------


def test_known_covariance_on_omega_widens_by_the_normal_quantile():
    """With alpha = beta = 0 the one-day variance is omega itself, and with innovations of
    exactly +/-1 the size of a one-day move is sqrt(omega_i). So if omega ~ N(1, 0.1^2),
    the p-quantile of |r| is sqrt(1 + 0.1 * Phi^-1(p)), against exactly 1 for the point
    model. At p = 0.9 that is 1.0621, a 6.2% widening, computed rather than simulated.
    """
    n = 200_000
    theta = np.array([1.0, 0.0, 0.0])
    cov = np.diag([0.1**2, 0.0, 0.0])
    draws, rate = draw_parameters(theta, cov, GARCH_NAMES, n, np.random.default_rng(1))
    assert rate < 1e-6  # omega < 0 is ten standard deviations away
    omega, alpha, gamma, beta = pu.split_parameters(draws, GARCH_NAMES)
    z = np.where(np.random.default_rng(2).random((n, 1)) < 0.5, -1.0, 1.0)
    r = run_paths(z, omega, omega, alpha, gamma, beta)[:, 0]
    for p in (0.1, 0.5, 0.9, 0.99):
        expected = np.sqrt(1.0 + 0.1 * norm.ppf(p))
        assert np.quantile(np.abs(r), p) == pytest.approx(expected, abs=2e-3)
    # the point model: every |r| is exactly 1
    point = run_paths(z, np.ones(n), np.ones(n), np.zeros(n), np.zeros(n), np.zeros(n))
    assert (np.abs(point[:, 0]) == 1.0).all()


def test_known_covariance_spreads_the_start_variance_by_the_delta_method(returns):
    """Scale the fit's own covariance down by 1e-4 so the map from parameters to
    sigma^2_{t+1|t} is linear over the draws. The spread of the per-draw start variance
    must then be sqrt(J C J'), with the gradient J taken from arch's own forecast by
    finite differences, an implementation independent of this module's filter."""
    from arch import arch_model

    c = 1e-4
    m = _fit(_with_cov(GJRGARCHPU, lambda cov: c * cov), returns)
    draws, names, _ = m.parameter_draws(20_000)
    start = m._start_per_draw(draws, names)

    theta = m._res.params.to_numpy()
    am = arch_model(returns.to_numpy() * variance.SCALE, mean="Zero", vol="GARCH", p=1, o=1, q=1)

    def v1(th):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            f = am.fix(th).forecast(horizon=1, reindex=False)
        return float(f.variance.to_numpy()[-1, 0])

    jac = np.empty(len(theta))
    for i in range(len(theta)):
        step = 1e-5 * max(abs(theta[i]), 1e-3)
        up, dn = theta.copy(), theta.copy()
        up[i] += step
        dn[i] -= step
        jac[i] = (v1(up) - v1(dn)) / (2 * step)
    expected = float(np.sqrt(jac @ (c * m._res.param_cov.to_numpy()) @ jac))
    assert np.std(start) == pytest.approx(expected, rel=0.03)
    assert np.mean(start) == pytest.approx(v1(theta), rel=1e-3)


@pytest.mark.parametrize("name", ["garch", "gjr_garch"])
def test_filter_matches_arch_away_from_the_fit(returns, name):
    """The per-draw start variance is arch's own one-step forecast at that draw, not just
    at the point estimate. Checked at parameter vectors a few standard errors away."""
    from arch import arch_model

    m = _fit(WITH_PU[name], returns)
    theta = m._res.params.to_numpy()
    se = np.sqrt(np.diag(m._res.param_cov.to_numpy()))
    names = [str(x) for x in m._res.params.index]
    rng = np.random.default_rng(4)
    draws = theta[None, :] + rng.uniform(-2, 2, (200, len(theta))) * se[None, :]
    draws = draws[admissible(draws, names)][:5]
    assert len(draws) == 5
    ours = m._start_per_draw(draws, names)
    o = 1 if name == "gjr_garch" else 0
    am = arch_model(returns.to_numpy() * variance.SCALE, mean="Zero", vol="GARCH", p=1, o=o, q=1)
    for d, v in zip(draws, ours, strict=True):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            ref = float(am.fix(d).forecast(horizon=1, reindex=False).variance.to_numpy()[-1, 0])
        assert v == pytest.approx(ref, rel=1e-6)


def test_filter_span_bounds_the_point_fits_influence():
    beta = np.array([0.5, 0.9])
    k = filter_span(beta, 10_000, weight=1e-8)
    assert 0.9**k < 1e-8 <= 0.9 ** (k - 1)
    assert filter_span(np.array([0.9999999]), 500) == 500  # never longer than the slice
    assert filter_span(np.array([1.0]), 500) == 500


# -- 3. rejection ---------------------------------------------------------------------


def test_admissible_rejects_each_constraint_on_a_draw_built_to_break_it():
    rows = {
        "fine": [0.02, 0.05, 0.10, 0.88],
        "persistence exactly 1": [0.02, 0.05, 0.10, 0.90],
        "persistence above 1": [0.02, 0.10, 0.10, 0.90],
        "omega zero": [0.0, 0.05, 0.10, 0.80],
        "alpha negative": [0.02, -0.01, 0.10, 0.80],
        "alpha + gamma negative": [0.02, 0.05, -0.06, 0.80],
        "beta negative": [0.02, 0.05, 0.10, -0.01],
        "not finite": [np.nan, 0.05, 0.10, 0.80],
    }
    ok = admissible(np.array(list(rows.values())), GJR_NAMES)
    assert dict(zip(rows, ok.tolist(), strict=True)) == {k: k == "fine" for k in rows}
    # gamma may be negative on its own, as in arch, provided alpha + gamma is not
    assert admissible(np.array([[0.02, 0.05, -0.03, 0.90]]), GJR_NAMES).all()


def test_rejection_rate_is_the_normal_tail_beyond_the_stationarity_boundary():
    """theta has persistence 0.99 and only beta is uncertain, with sd 0.01, so a draw is
    non-stationary exactly when beta >= 0.95: probability 1 - Phi(1) = 0.1587."""
    theta = np.array([0.05, 0.05, 0.0, 0.94])
    cov = np.diag([0.0, 0.0, 0.0, 0.01**2])
    draws, rate = draw_parameters(theta, cov, GJR_NAMES, 100_000, np.random.default_rng(7))
    assert rate == pytest.approx(1 - norm.cdf(1.0), abs=0.004)
    assert admissible(draws, GJR_NAMES).all()
    assert draws[:, 3].max() < 0.95


def test_a_fit_on_the_boundary_becomes_a_typed_failure_not_a_hang():
    theta = np.array([0.05, 0.05, 0.0, 0.96])  # persistence 1.01: every draw is refused
    cov = np.diag([0.0, 1e-8, 0.0, 1e-8])
    with pytest.raises(FitError, match="rejection rate"):
        draw_parameters(theta, cov, GJR_NAMES, 1000, np.random.default_rng(0), max_rounds=5)


def test_the_rejection_rate_reaches_the_store_through_the_variant(returns):
    m = _fit(GJRGARCHPU, returns)
    m.simulate(20, 2000, np.random.default_rng(0))
    assert 0.0 <= m.rejection_rate < 1.0
    assert m.variant.endswith(f"draws rejected {m.rejection_rate:.4f}")
    assert m.variant.startswith("GJR-GARCH(1,1) persistence")


def test_parameter_draws_come_from_their_own_seed(returns):
    """Same seed, same draws, whatever the path generator; a different seed, different
    draws. The path stream and the parameter stream do not share state."""
    a = _fit(GJRGARCHPU, returns, seed=5)
    b = _fit(GJRGARCHPU, returns, seed=5)
    c = _fit(GJRGARCHPU, returns, seed=6)
    da, _, _ = a.parameter_draws(500)
    a.simulate(5, 100, np.random.default_rng(99))
    assert np.array_equal(da, a.parameter_draws(500)[0])
    assert np.array_equal(da, b.parameter_draws(500)[0])
    assert not np.array_equal(da, c.parameter_draws(500)[0])


def test_single_day_prediction_honours_the_forecast_contract(returns):
    m = _fit(GJRGARCHPU, returns)
    levels = (0.8, 0.95)
    res = m.predict(5, levels)
    check_result(res, 5, levels)
    assert (res.upper[0.95] > res.upper[0.8]).all()


# -- 4. the existing models are untouched ---------------------------------------------


def test_the_five_existing_models_are_the_classes_they_were():
    expected = {
        "zero_return_fhs": variance.ConstantSigma,
        "ewma": variance.EWMA,
        "garch_normal": variance.GARCHNormal,
        "garch": variance.GARCH,
        "gjr_garch": variance.GJRGARCH,
    }
    for name, cls in expected.items():
        m = REGISTRY[name](1)
        assert type(m) is cls
        assert not isinstance(m, pu._ParameterUncertainty)
        assert inspect.getmodule(type(m)) is variance
    assert RETURN_VARIANCE[:5] == tuple(expected)
    assert type(REGISTRY["garch_pu"](1)) is GARCHPU
    assert type(REGISTRY["gjr_garch_pu"](1)) is GJRGARCHPU


def test_parameter_uncertainty_is_opt_in():
    """Adding a name to RETURN_VARIANCE also adds it to the default model list of a
    cumulative config with no `models:` key. No committed config relies on that default,
    and this keeps it so: a config that did would silently change model list and run_id."""
    assert "gjr_garch_pu" not in DEFAULT_RETURN_MODELS
    for path in sorted((ROOT / "configs").glob("phase*/phase*.yaml")):
        cfg = load_config(path)
        for s in cfg.series:
            if s.target == "cumulative_returns" and not path.parent.name.startswith("phase7"):
                assert not {"garch_pu", "gjr_garch_pu"} & set(s.models), path
    for path in sorted((ROOT / "configs").glob("**/*.yaml")):
        text = path.read_text()
        if "cumulative_returns" in text:
            assert "\n    models:" in text, f"{path} relies on the default model list"


# -- through the engine ---------------------------------------------------------------


@pytest.mark.slow
def test_through_the_engine_the_point_rows_are_unchanged_and_the_twin_reports():
    """A short cumulative backtest with both models on the A12 DGP. The point model's rows
    must not depend on whether its parameter-uncertainty twin ran beside it, and the twin
    must be a real, ok, reported model."""
    from tests.acceptance import backtest_synthetic
    from tests.synthetic import garch11_prices

    kw = dict(
        uid="pu",
        freq="trading_days",
        season="none",
        H=20,
        decision_horizons=(1, 5, 20),
        window="expanding",
        seed=20260926,
        n_paths=2000,
        levels=(0.8, 0.95),
        target="cumulative_returns",
        origin_step=20,
        n_dev_origins=5,
        n_test_origins=10,
    )
    prices = garch11_prices(n=1500, seed=3)
    alone = backtest_synthetic(prices, models=("gjr_garch",), **kw).frame
    both = backtest_synthetic(prices, models=("gjr_garch", "gjr_garch_pu"), **kw).frame
    cols = ["origin_t", "h", "y_pred", "lo_80", "hi_80", "lo_95", "hi_95", "es_95"]
    a = alone[alone.model == "gjr_garch"].sort_values(["origin_t", "h"])[cols]
    b = both[both.model == "gjr_garch"].sort_values(["origin_t", "h"])[cols]
    pd.testing.assert_frame_equal(a.reset_index(drop=True), b.reset_index(drop=True))
    twin = both[both.model == "gjr_garch_pu"]
    assert (twin.status == "ok").all()
    assert twin.variant.str.contains("draws rejected").all()
