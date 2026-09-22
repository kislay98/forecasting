"""Statistical models: contract, selection behaviour, STL wrapper, combination, engine wiring.

Series are short so the file stays within the unit-test budget; the slow end-to-end and
L1 runs over these models are marked `slow` (CI runs them on every push).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from forecasting.backtest.engine import fold_seed, run_backtest
from forecasting.config import parse_config
from forecasting.data.validate import validate
from forecasting.errors import FitError, NotFittedError
from forecasting.models.base import check_result, z_value
from forecasting.models.combination import EqualWeight, combine
from forecasting.models.registry import build_factories
from forecasting.models.statistical import (
    SES,
    ARAuto,
    ETSAuto,
    SARIMAAuto,
    STLAdjusted,
    Theta,
    kpss_ndiffs,
    seasonal_or_stl,
    seasonal_strength,
)
from tests.synthetic import random_walk, seasonal_ar1, to_frame

LEVELS = (0.8, 0.95)
SEASONAL = pd.Series(seasonal_ar1(n=72, seed=1) + np.linspace(0, 10, 72))
RNG = np.random.default_rng(7)
RETURNS = pd.Series(RNG.normal(0.0005, 0.01, 400))

MODELS = {
    "ses": (lambda: SES(), SEASONAL),
    "ets": (lambda: ETSAuto(12), SEASONAL),
    "sarima": (lambda: SARIMAAuto(12), SEASONAL),
    "theta": (lambda: Theta(12), SEASONAL),
    "ar": (lambda: ARAuto(), RETURNS),
    "stl_ets": (
        lambda: seasonal_or_stl(lambda k: ETSAuto(k), 52),
        pd.Series(seasonal_ar1(n=160, m=52, seed=2)),
    ),
}


@pytest.mark.parametrize("key", list(MODELS))
def test_contract(key):
    make, y = MODELS[key]
    with pytest.raises(NotFittedError):
        make().predict(3)
    a = make().fit(y).predict(12, LEVELS)
    b = make().fit(y).predict(12, LEVELS)
    np.testing.assert_array_equal(a.mean, b.mean)  # deterministic given input
    for lv in LEVELS:
        np.testing.assert_array_equal(a.lower[lv], b.lower[lv])
    check_result(a, 12, LEVELS)
    assert (a.upper[0.95] - a.lower[0.95] >= a.upper[0.8] - a.lower[0.8] - 1e-12).all()
    assert a.info["variant"]
    assert make() is not make()


@pytest.mark.parametrize("key", ["ses", "ets", "sarima", "theta", "ar"])
def test_too_short_is_a_fit_error(key):
    make, _ = MODELS[key]
    with pytest.raises(FitError):
        make().fit(pd.Series([1.0, 2.0]))


def test_ses_interval_matches_formula():
    y = pd.Series(random_walk(n=80, seed=1))
    s = SES().fit(y)
    r = s.predict(4, (0.8,))
    alpha = dict(zip(s._res.param_names, s._res.params, strict=True))["smoothing_level"]
    h = np.arange(1, 5)
    expected = z_value(0.8) * np.sqrt(s._res.mse * (1 + (h - 1) * alpha**2))
    np.testing.assert_allclose(r.upper[0.8] - r.mean, expected, rtol=1e-8)
    assert s.variant == "ETS(A,N,N)"


def test_ets_allowed_variants():
    pos = np.abs(SEASONAL.to_numpy()) + 1
    labels = {(e, t, d, s) for e, t, d, s in ETSAuto(12).allowed(pos)}
    assert ("add", None, False, "mul") not in labels  # (A,*,M) excluded
    assert all(t != "mul" for _, t, _, _ in labels)  # no multiplicative trend
    assert ("mul", "add", True, "mul") in labels
    neg = pos - 200
    assert all(e == "add" and s != "mul" for e, _, _, s in ETSAuto(12).allowed(neg))
    assert all(s is None for *_, s in ETSAuto(12).allowed(pos[:20]))  # needs 2m points
    assert all(s is None for *_, s in ETSAuto(52).allowed(np.ones(200) + np.arange(200)))
    assert len(ETSAuto(12).allowed(pos)) == 2 * 3 * 3 - 3  # 18 minus (A,*,M)


def test_ets_picks_a_seasonal_model_on_seasonal_data():
    m = ETSAuto(12).fit(SEASONAL)
    assert m.variant.endswith((",A)", ",M)"))


def test_ets_multiplicative_intervals_follow_the_seed():
    y = pd.Series(np.exp(np.linspace(3, 4, 72)) * (1 + 0.05 * np.sin(np.arange(72))))
    m = ETSAuto(1, variants=[("mul", "add", True, None)])
    m.seed = 1
    a = m.fit(y).predict(6, LEVELS)
    b = m.predict(6, LEVELS)
    np.testing.assert_array_equal(a.lower[0.8], b.lower[0.8])
    m.seed = 2
    c = m.predict(6, LEVELS)
    assert not np.array_equal(a.lower[0.8], c.lower[0.8])


def test_differencing_tests():
    rng = np.random.default_rng(3)
    noise = rng.normal(0, 1, 200)
    assert kpss_ndiffs(noise) == 0
    assert kpss_ndiffs(np.cumsum(noise)) == 1
    assert kpss_ndiffs(np.cumsum(np.cumsum(noise))) == 2
    assert seasonal_strength(seasonal_ar1(n=120, seed=4), 12) > 0.64
    assert seasonal_strength(noise[:120], 12) < 0.64


def test_sarima_orders_and_drift():
    s = SARIMAAuto(12).fit(SEASONAL)
    assert "(0,1,0)[12]" in s.variant or ",1," in s.variant.split("(")[2]  # D = 1
    rw = pd.Series(np.cumsum(np.random.default_rng(5).normal(0.5, 1, 150)))
    r = SARIMAAuto(1).fit(rw)
    assert r.variant.startswith("SARIMA(") and ",1," in r.variant and r.variant.endswith("+c")
    step = np.diff(r.predict(5).mean)
    assert np.all(step > 0.2)  # drift continues the upward slope


@pytest.mark.slow
def test_sarima_stepwise_matches_grid_on_a_seasonal_series():
    g = SARIMAAuto(12, "grid").fit(SEASONAL)
    s = SARIMAAuto(12, "stepwise").fit(SEASONAL)
    assert g.n_fits == 36 and s.n_fits < 36
    assert s.variant == g.variant


def test_theta_variants():
    assert Theta(12).fit(SEASONAL).variant in ("Theta(multiplicative)", "Theta(additive)")
    assert Theta(1).fit(SEASONAL).variant == "Theta(nonseasonal)"


def test_ar_order_selection():
    rng = np.random.default_rng(6)
    e = rng.normal(0, 0.01, 1000)
    x = np.empty(1000)
    x[0] = 0
    for t in range(1, 1000):
        x[t] = 0.6 * x[t - 1] + e[t]
    assert ARAuto().fit(pd.Series(x)).variant == "AR(1)"
    assert ARAuto().fit(pd.Series(e)).variant == "AR(0)"
    r = ARAuto().fit(pd.Series(x + 0.001)).predict(60)
    assert abs(r.mean[-1] - 0.001) < 0.002  # reverts to the mean


def test_stl_wrapper_continues_the_season():
    y = pd.Series(seasonal_ar1(n=160, m=52, amplitude=10, phi=0.0, sigma=0.1, seed=8))
    m = seasonal_or_stl(lambda k: ETSAuto(k), 52)
    assert isinstance(m, STLAdjusted) and m.name == "ets"
    r = m.fit(y).predict(52)
    assert m.variant.startswith("STL+ETS(")
    truth = 100 + 10 * np.sin(2 * np.pi * np.arange(160, 212) / 52)
    assert np.max(np.abs(r.mean - truth)) < 1.5
    assert isinstance(seasonal_or_stl(lambda k: ETSAuto(k), 12), ETSAuto)


# ---------------------------------------------------------------- combination


def test_combine_rule():
    means = {"ets": np.array([1.0, 2.0]), "sarima": np.array([3.0, 4.0]), "theta": None}
    mean, variant = combine(means)
    np.testing.assert_allclose(mean, [2.0, 3.0])
    assert variant == "mean(ets,sarima); failed: theta"
    with pytest.raises(FitError):
        combine({"ets": None, "sarima": None, "theta": None})


class Broken(ETSAuto):
    def _fit_model(self, v):
        raise FitError("sarima", "planted failure")


@pytest.mark.slow
def test_equal_weight_is_the_mean_of_members():
    members = {"ets": ETSAuto(12), "sarima": SARIMAAuto(12), "theta": Theta(12)}
    eq = EqualWeight(members).fit(SEASONAL).predict(6)
    parts = [
        make().fit(SEASONAL).predict(6).mean
        for make in (lambda: ETSAuto(12), lambda: SARIMAAuto(12), lambda: Theta(12))
    ]
    np.testing.assert_allclose(eq.mean, np.mean(parts, axis=0))
    assert eq.lower == {}
    part = EqualWeight({"ets": ETSAuto(12), "sarima": Broken(12), "theta": Theta(12)})
    assert part.fit(SEASONAL).predict(6).info["variant"] == "mean(ets,theta); failed: sarima"


# ---------------------------------------------------------------- engine wiring


def setup(n: int = 60, **labels):
    raw = {
        "id": "s",
        "source": "csv:x",
        "freq": "monthly",
        "H": 6,
        "window": "expanding",
        "n_dev_origins": 2,
        "n_test_origins": 2,
        **labels,
    }
    cfg = parse_config({"series": [raw]})
    scfg = cfg.series[0]
    df = to_frame(seasonal_ar1(n=n, seed=9) + np.linspace(0, 10, n), "monthly", unique_id="s")
    [(series, _)] = validate(df, scfg)
    return cfg, scfg, series


@pytest.mark.slow
def test_engine_combination_matches_standalone_and_skips_warmup():
    cfg, scfg, series = setup(
        models=["naive", "ets", "sarima", "theta", "combination"], transform="log"
    )
    f = run_backtest(series, scfg, cfg, "rid")[0].frame()
    warm = f[f["origin_role"] == "warmup"]
    assert set(warm["model"]) == {"naive"}  # expensive models skip warm-up origins
    scored = f[f["origin_role"] != "warmup"]
    assert set(scored["model"]) == {"naive", "ets", "sarima", "theta", "combination"}
    assert (scored["status"] == "ok").all(), scored.loc[scored["status"] != "ok", "error"]
    t = int(scored["origin_t"].max())
    comb = scored[(scored["model"] == "combination") & (scored["origin_t"] == t)]
    y = series.y.iloc[: t + 1]
    eq = EqualWeight({"ets": ETSAuto(12), "sarima": SARIMAAuto(12), "theta": Theta(12)})
    for name, mem in eq.members.items():
        mem.seed = fold_seed(cfg.seed, "s", "expanding", t, name)
    standalone = np.exp(eq.fit(np.log(y)).predict(6).mean)
    np.testing.assert_allclose(comb["y_pred"], standalone, rtol=1e-10)
    # every member and the combination were on the same fold transform
    assert set(scored.loc[scored["model"] != "naive", "transform"]) == {"log"}
    assert comb["lo_80"].isna().all()


def test_warmup_models_all_runs_everything_everywhere():
    cfg, scfg, series = setup(models=["naive", "ses"], warmup_models="all")
    f = run_backtest(series, scfg, cfg, "rid")[0].frame()
    warm = f[f["origin_role"] == "warmup"]
    assert set(warm["model"]) == {"naive", "ses"}


@pytest.mark.slow
def test_member_failure_leaves_a_combination_of_the_rest():
    cfg, scfg, series = setup(models=["ets", "sarima", "theta", "combination"])
    factories = dict(build_factories(scfg))
    factories["sarima"] = lambda m: Broken(m)
    f = run_backtest(series, scfg, cfg, "rid", factories=factories)[0].frame()
    scored = f[f["origin_role"] != "warmup"]
    assert (scored.loc[scored["model"] == "sarima", "status"] == "failed").all()
    comb = scored[scored["model"] == "combination"]
    assert (comb["status"] == "ok").all()
    assert (comb["variant"] == "mean(ets,theta); failed: sarima").all()


def test_fold_seed_is_stable_and_distinct():
    a = fold_seed(1, "s", "expanding", 10, "ets")
    assert a == fold_seed(1, "s", "expanding", 10, "ets")
    others = {
        fold_seed(1, "s", "rolling", 10, "ets"),
        fold_seed(1, "s", "expanding", 11, "ets"),
        fold_seed(1, "s", "expanding", 10, "sarima"),
        fold_seed(2, "s", "expanding", 10, "ets"),
    }
    assert a not in others and len(others) == 4


def test_returns_series_runs_ar():
    rng = np.random.default_rng(10)
    prices = 1000 * np.exp(np.cumsum(rng.normal(0.0003, 0.01, 700)))
    raw = {
        "id": "s",
        "source": "csv:x",
        "freq": "trading_days",
        "target": "returns",
        "H": 5,
        "initial_window": 250,
        "n_test_origins": 30,
        "window": "expanding",
    }
    cfg = parse_config({"series": [raw]})
    scfg = cfg.series[0]
    [(series, _)] = validate(to_frame(prices, "trading_days", "2020-01-01", "s"), scfg)
    f = run_backtest(series, scfg, cfg, "rid")[0].frame()
    ar = f[f["model"] == "ar"]
    assert len(ar) > 0 and (ar["status"] == "ok").all()
    assert ar["variant"].str.match(r"AR\(\d\)").all()
    assert set(ar["origin_role"]) == {"dev", "test"}


# ---------------------------------------------------------------- residuals (RQ4)


def test_sarima_residuals_drop_the_diffuse_burn_in():
    """With d + D m differences, SARIMAX's first residuals come from the initialisation
    (y itself, then y minus a partial seasonal difference). They are dropped."""
    s = SARIMAAuto(12).fit(SEASONAL)
    burn = int(s._res.loglikelihood_burn)
    assert burn >= 12  # D = 1 on this series
    r = s.residuals()
    assert len(r) == len(SEASONAL) - burn
    np.testing.assert_allclose(r, np.asarray(s._res.resid)[burn:])
    assert np.abs(r).max() < 10 * np.abs(r).std() + 1e-9  # no initialisation spikes


def test_stl_wrapper_residuals_are_the_inner_models():
    y = pd.Series(seasonal_ar1(n=160, m=52, seed=2))
    m = seasonal_or_stl(lambda k: ETSAuto(k), 52).fit(y)
    np.testing.assert_array_equal(m.residuals(), m.inner.residuals())
    assert len(m.residuals()) == len(y)


def test_engine_records_residual_diagnostics_at_test_origins_only():
    cfg, scfg, series = setup(n=80, models=["naive", "ses", "theta"])
    f = run_backtest(series, scfg, cfg, "rid")[0].frame()
    test = f["origin_role"] == "test"
    assert (f.loc[~test, "n_resid"] == 0).all() and f.loc[~test, "lb_p"].isna().all()
    for name in ("naive", "ses"):
        rows = f[test & (f["model"] == name)]
        assert (rows["n_resid"] > 0).all() and rows["lb_p"].between(0, 1).all()
        assert rows["arch_p"].between(0, 1).all()
    theta = f[test & (f["model"] == "theta")]
    assert (theta["n_resid"] == 0).all() and theta["lb_p"].isna().all()  # no residuals
    # one value per fold, repeated on each h row
    per_fold = f[test].groupby(["model", "origin_t"])["lb_p"].nunique(dropna=False)
    assert (per_fold == 1).all()
