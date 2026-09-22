"""Diagnostics (forecasting/evaluation/diagnostics.py): RQ4 to RQ6.

Each docstring says where the expected value comes from: worked by hand, or an
independent implementation (statsmodels).
"""

from __future__ import annotations

import math
import warnings

import numpy as np
import pandas as pd
import pytest
from scipy import stats
from statsmodels.stats.diagnostic import acorr_ljungbox, het_arch

from forecasting.evaluation import diagnostics as dg

# ---------------------------------------------------------------- residual tests


def test_ljung_box_hand_computed():
    """r = [1, -1, 1, -1, 1, -1] (mean 0, sum of squares 6): rho_1 = -5/6, rho_2 = 4/6.
    Q(2) = 6 * 8 * ((25/36) / 5 + (16/36) / 4) = 48 * (5/36 + 4/36) = 12."""
    r = np.array([1.0, -1, 1, -1, 1, -1])
    assert dg.ljung_box(r, 2) == pytest.approx(stats.chi2.sf(12.0, 2))


@pytest.mark.parametrize("lag", [1, 10, 24])
def test_ljung_box_matches_statsmodels(lag):
    """Independent implementation: statsmodels acorr_ljungbox (model_df = 0)."""
    r = np.random.default_rng(lag).standard_t(5, size=300)
    expected = acorr_ljungbox(r, lags=[lag], return_df=True)["lb_pvalue"].iloc[0]
    assert dg.ljung_box(r, lag) == pytest.approx(expected, rel=1e-10)


@pytest.mark.parametrize("lags", [4, 12])
def test_arch_lm_matches_statsmodels(lags):
    """Independent implementation: statsmodels het_arch (LM p-value)."""
    rng = np.random.default_rng(lags)
    r = rng.normal(size=400) * np.repeat([1.0, 3.0, 1.0, 2.0], 100)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        expected = het_arch(r, nlags=lags)[1]
    assert dg.arch_lm(r, lags) == pytest.approx(expected, rel=1e-8)


def test_residual_lags_follow_the_spec():
    """Spec: Ljung-Box at m and 2m (10 if m = 1); ARCH-LM with max(m, 4) lags."""
    assert dg.residual_lags(1) == ((10,), 4)
    assert dg.residual_lags(4) == ((4, 8), 4)
    assert dg.residual_lags(12) == ((12, 24), 12)
    assert dg.residual_lags(52) == ((52, 104), 52)


def test_residual_tests_detect_what_they_should():
    rng = np.random.default_rng(0)
    n = 600
    white = rng.normal(size=n)
    ar = np.empty(n)
    ar[0] = 0.0
    for t in range(1, n):  # AR(1), phi = 0.6: autocorrelated
        ar[t] = 0.6 * ar[t - 1] + white[t]
    sigma = np.empty(n)
    garch = np.empty(n)
    sigma[0], garch[0] = 1.0, 0.0
    for t in range(1, n):  # ARCH(1): volatility clusters, no autocorrelation in levels
        sigma[t] = math.sqrt(0.2 + 0.7 * garch[t - 1] ** 2)
        garch[t] = sigma[t] * white[t]
    w, a, g = (dg.residual_tests(x, 1) for x in (white, ar, garch))
    assert w["lb_p"] > 0.05 and w["arch_p"] > 0.05
    assert a["lb_p"] < 1e-6
    assert g["arch_p"] < 1e-6
    assert w["n_resid"] == n and math.isnan(w["lb_p_2m"])  # m = 1: one Ljung-Box lag


def test_residual_tests_give_nan_when_a_test_cannot_run():
    """A test with L lags needs 2L + 1 residuals; constant residuals have no variance."""
    rng = np.random.default_rng(1)
    short = dg.residual_tests(rng.normal(size=24), 12)  # lag 12 needs 25
    assert short["n_resid"] == 24
    assert math.isnan(short["lb_p"]) and math.isnan(short["lb_p_2m"])
    assert math.isnan(short["arch_p"])
    mid = dg.residual_tests(rng.normal(size=30), 12)  # enough for lag 12, not for 24
    assert not math.isnan(mid["lb_p"]) and math.isnan(mid["lb_p_2m"])
    assert not math.isnan(mid["arch_p"])
    const = dg.residual_tests(np.ones(100), 1)
    assert const["n_resid"] == 100 and math.isnan(const["lb_p"]) and math.isnan(const["arch_p"])
    assert dg.residual_tests(np.array([]), 1)["n_resid"] == 0


def test_residual_tests_drop_non_finite_values():
    r = np.random.default_rng(2).normal(size=100)
    with_nan = np.concatenate([[np.nan, np.inf], r])
    assert dg.residual_tests(with_nan, 1) == dg.residual_tests(r, 1)


# ---------------------------------------------------------------- summaries


def folds_frame() -> pd.DataFrame:
    """Two models, 4 test origins each; hand-set p-values."""
    return pd.DataFrame(
        {
            "unique_id": "s",
            "window": "expanding",
            "model": ["a"] * 4 + ["b"] * 4 + ["c"] * 2,
            "origin_t": [1, 2, 3, 4] * 2 + [1, 2],
            "n_resid": [50] * 8 + [0, 0],
            "lb_p": [0.01, 0.2, 0.03, np.nan, 0.5, 0.6, 0.7, 0.8, np.nan, np.nan],
            "lb_p_2m": [np.nan] * 10,
            "arch_p": [0.001, 0.002, 0.3, 0.4, 0.9, 0.01, 0.9, 0.9, np.nan, np.nan],
            "transform": ["log", "log", "boxcox(0.300)", "boxcox(-0.100)"] * 2 + ["none"] * 2,
        }
    )


def test_residual_summary_shares_hand_computed():
    """a: Ljung-Box rejects at 2 of 3 non-null origins, ARCH at 2 of 4. b: 0 of 4 and
    1 of 4. c has no residuals (n_resid 0) and is left out."""
    s = dg.residual_summary(folds_frame()).set_index("model")
    assert list(s.index) == ["a", "b"]
    assert s.loc["a", "n_origins"] == 4 and s.loc["a", "n_lb"] == 3
    assert s.loc["a", "share_lb"] == pytest.approx(2 / 3)
    assert s.loc["a", "share_arch"] == 0.5
    assert s.loc["b", "share_lb"] == 0.0 and s.loc["b", "share_arch"] == 0.25
    assert math.isnan(s.loc["a", "share_lb_2m"])


def test_transform_counts_count_each_fold_once():
    """Models a and b share each fold's transform (M4-5), so each origin counts once:
    log 2, boxcox 2 with lambdas -0.1 and 0.3; model c's folds are distinct origins
    already counted."""
    t = dg.transform_counts(folds_frame().query("model != 'c'")).set_index("transform")
    assert t["n_folds"].to_dict() == {"boxcox": 2, "log": 2}
    assert t.loc["boxcox", "lambda_min"] == pytest.approx(-0.1)
    assert t.loc["boxcox", "lambda_max"] == pytest.approx(0.3)
    assert t["share"].sum() == pytest.approx(1.0)
    assert dg.transform_family("none (fallback: y <= 0)") == "none (fallback)"


def gap_rows(n: int = 40, shift: float = 0.0) -> pd.DataFrame:
    """One model at h = 1 in both windows; the rolling window's errors are larger by shift."""
    rng = np.random.default_rng(3)
    y = rng.normal(size=n)
    e = np.abs(rng.normal(size=n))
    base = {"unique_id": "s", "model": "m", "h": 1, "origin_t": np.arange(n), "y_true": y}
    return pd.concat(
        [
            pd.DataFrame({**base, "window": "expanding", "y_pred": y - e}),
            pd.DataFrame({**base, "window": "rolling", "y_pred": y - e - shift}),
        ]
    )


def test_window_gap_ratio_and_dm_direction():
    """With rolling errors e + 0.5 vs e, the ratio is mean(e + 0.5) / mean(e) and the
    expanding window is significantly better; with identical errors the gap is nil."""
    rows = gap_rows(shift=0.5)
    g = dg.window_gap(rows, [1]).iloc[0]
    e = (
        rows.query("window == 'expanding'")["y_true"]
        - rows.query("window == 'expanding'")["y_pred"]
    ).abs()
    assert g["ratio"] == pytest.approx((e + 0.5).mean() / e.mean())
    assert g["p_expanding_better_holm"] < 0.001 and g["p_rolling_better_holm"] > 0.99
    same = dg.window_gap(gap_rows(shift=0.0), [1]).iloc[0]
    assert same["ratio"] == 1.0 and same["dm_p"] == 1.0


def test_window_gap_needs_both_windows():
    rows = gap_rows().query("window == 'expanding'")
    assert dg.window_gap(rows, [1]).empty


def test_subperiods_split_origins_into_consecutive_blocks():
    """10 origins in 4 blocks: numpy array_split gives sizes 3, 3, 2, 2. The model's
    error is half the reference's in block 1 only."""
    t = np.arange(10)
    y = np.zeros(10)
    ref = pd.DataFrame({"model": "r", "h": 1, "origin_t": t, "y_true": y, "y_pred": np.ones(10)})
    mp = np.ones(10)
    mp[:3] = 0.5
    mod = pd.DataFrame({"model": "m", "h": 1, "origin_t": t, "y_true": y, "y_pred": mp})
    s = dg.subperiods(pd.concat([ref, mod]), "r", [1])
    assert s["n"].tolist() == [3, 3, 2, 2]
    assert s["rel_mae"].tolist() == [0.5, 1.0, 1.0, 1.0]
    assert s[["first_origin", "last_origin"]].values.tolist() == [[0, 2], [3, 5], [6, 7], [8, 9]]
