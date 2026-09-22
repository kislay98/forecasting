"""Significance tests (forecasting/evaluation/tests.py).

Each test's docstring says where its expected value comes from: worked by hand, or an
independent implementation (statsmodels, scipy, the arch package).
"""

from __future__ import annotations

import math

import numpy as np
import pytest
import statsmodels.api as sm
from scipy import stats
from scipy.stats import chi2_contingency
from statsmodels.stats.multitest import multipletests

from forecasting.evaluation import tests as st

# ---------------------------------------------------------------- DM-HLN


def test_dm_hln_hand_computed():
    """d = [1, 1, 2, 2, 4], h = 2, n = 5, worked by hand:
    mean 2; centred [-1, -1, 0, 0, 2]; g0 = 6/5, g1 = 1/5; var = (6/5 + 2/5) / 5 = 0.32;
    DM = 2 / sqrt(0.32) = 5 / sqrt 2; HLN factor sqrt((5 + 1 - 4 + 2/5) / 5) = sqrt 0.48;
    DM* = 5 sqrt(0.24) = sqrt 6. p two-sided = 2 P(t_4 > sqrt 6)."""
    r = st.dm_hln([1, 1, 2, 2, 4], np.zeros(5), h=2)
    assert r.stat == pytest.approx(math.sqrt(6))
    assert r.p_value == pytest.approx(2 * stats.t.sf(math.sqrt(6), 4))
    assert r.p_better == pytest.approx(stats.t.cdf(math.sqrt(6), 4))
    assert (r.n, r.h, r.lags) == (5, 2, 1)


def test_dm_hln_falls_back_to_h1_when_variance_is_not_positive():
    """d = [1, -1, 2, 0, 3], h = 2 by hand: mean 1, centred [0, -2, 1, -1, 2], g0 = 2,
    g1 = -1, so g0 + 2 g1 = 0. As R's forecast::dm.test, rerun at h = 1:
    DM = 1 / sqrt(2/5), factor sqrt((5 + 1 - 2) / 5), DM* = sqrt 2."""
    r = st.dm_hln([1, -1, 2, 0, 3], np.zeros(5), h=2)
    assert r.stat == pytest.approx(math.sqrt(2))
    assert (r.h, r.lags) == (1, 0)


def test_dm_hln_matches_statsmodels_hac_uniform_kernel():
    """Independent computation: the DM variance is the HAC variance of a mean with a
    uniform kernel and h - 1 lags (statsmodels OLS on a constant, no small-sample
    correction), then scaled by the HLN factor."""
    rng = np.random.default_rng(1)
    n = 40
    for h in (1, 3, 6):
        a, b = np.abs(rng.normal(size=n)) + 0.2, np.abs(rng.normal(size=n))
        d = a - b
        res = sm.OLS(d, np.ones(n)).fit(
            cov_type="HAC",
            cov_kwds={"maxlags": h - 1, "kernel": "uniform", "use_correction": False},
        )
        expected = res.params[0] / res.bse[0] * math.sqrt((n + 1 - 2 * h + h * (h - 1) / n) / n)
        assert st.dm_hln(a, b, h).stat == pytest.approx(expected, rel=1e-10)


def test_dm_hln_is_antisymmetric_and_one_sided_p_points_at_the_better_model():
    rng = np.random.default_rng(2)
    a = np.abs(rng.normal(size=50))
    b = a + 0.3 + 0.1 * rng.normal(size=50)  # b is clearly worse
    ab, ba = st.dm_hln(a, b, 3), st.dm_hln(b, a, 3)
    assert ab.stat == pytest.approx(-ba.stat)
    assert ab.p_value == pytest.approx(ba.p_value)
    assert ab.p_better < 0.01 and ba.p_better > 0.99


def test_dm_hln_identical_losses_give_p_one():
    r = st.dm_hln([1.0, 2.0, 3.0], [1.0, 2.0, 3.0], h=1)
    assert (r.stat, r.p_value) == (0.0, 1.0)


def test_dm_hln_input_errors():
    with pytest.raises(ValueError, match="NaN"):
        st.dm_hln([1.0, np.nan, 2.0], [1.0, 1.0, 1.0], 1)
    with pytest.raises(ValueError, match="shape"):
        st.dm_hln([1.0, 2.0, 3.0], [1.0, 1.0], 1)
    with pytest.raises(ValueError, match="n > h"):
        st.dm_hln([1.0, 2.0, 3.0], [0.0, 0.0, 0.0], 3)


# ---------------------------------------------------------------- Holm


def test_holm_hand_computed():
    """p = [0.01, 0.04, 0.03, 0.005]. Sorted: 0.005 x 4 = 0.02, 0.01 x 3 = 0.03,
    0.03 x 2 = 0.06, 0.04 x 1 = 0.04 -> running max 0.06. Back in input order:
    [0.03, 0.06, 0.06, 0.02]."""
    np.testing.assert_allclose(st.holm([0.01, 0.04, 0.03, 0.005]), [0.03, 0.06, 0.06, 0.02])


def test_holm_matches_statsmodels():
    """Independent implementation: statsmodels multipletests(method='holm')."""
    p = np.random.default_rng(3).uniform(0, 0.3, 17)
    np.testing.assert_allclose(st.holm(p), multipletests(p, method="holm")[1])


def test_holm_caps_at_one_and_rejects_nan():
    assert st.holm([0.6, 0.7]).max() == 1.0
    with pytest.raises(ValueError, match="NaN"):
        st.holm([0.1, np.nan])


# ---------------------------------------------------------------- Kupiec


def test_kupiec_hand_computed():
    """x = 10 misses in n = 100 at p = 0.05, by hand:
    LR = -2 [90 ln 0.95 + 10 ln 0.05 - 90 ln 0.90 - 10 ln 0.10] = 4.1308 (chi2(1))."""
    lr = -2 * (90 * math.log(0.95) + 10 * math.log(0.05) - 90 * math.log(0.9) - 10 * math.log(0.1))
    r = st.kupiec(10, 100, 0.05)
    assert r.lr == pytest.approx(lr) and r.lr == pytest.approx(4.1308, abs=1e-4)
    assert r.p_value == pytest.approx(stats.chi2.sf(lr, 1))
    assert r.miss_rate == 0.1


def test_kupiec_zero_misses_and_exact_rate():
    """x = 0: the alternative likelihood is 1, so LR = -2 n ln(1 - p). x = n p: LR = 0."""
    assert st.kupiec(0, 50, 0.05).lr == pytest.approx(-2 * 50 * math.log(0.95))
    assert st.kupiec(5, 100, 0.05).lr == pytest.approx(0.0, abs=1e-12)


def test_kupiec_matches_the_binomial_likelihood_ratio():
    """Independent computation: 2 [log Binom(x; n, x/n) - log Binom(x; n, p)] from scipy
    (the binomial coefficient cancels)."""
    for x, n, p in [(3, 40, 0.2), (12, 250, 0.05), (1, 30, 0.05), (29, 30, 0.95)]:
        expected = 2 * (stats.binom.logpmf(x, n, x / n) - stats.binom.logpmf(x, n, p))
        assert st.kupiec(x, n, p).lr == pytest.approx(expected, abs=1e-10)


def test_kupiec_takes_fractional_effective_counts():
    """Effective counts (x / h, n / h) scale the LR by 1 / h."""
    assert st.kupiec(10 / 4, 100 / 4, 0.05).lr == pytest.approx(st.kupiec(10, 100, 0.05).lr / 4)


# ---------------------------------------------------------------- Pesaran-Timmermann


def test_pesaran_timmermann_hand_computed():
    """8 rows, 4 up in each, 6 hits. P = 0.75, py = px = 0.5, P* = 0.5,
    V(P) = 0.25 / 8, V(P*) = 4 (0.5)^4 / 64 = 0.25 / 64,
    S = 0.25 / sqrt(0.25/8 - 0.25/64) = 1.51186; p = 1 - Phi(S)."""
    y = np.array([1, -1, 1, 1, -1, -1, 1, -1.0])
    f = np.array([1, -1, -1, 1, -1, 1, 1, -1.0])
    r = st.pesaran_timmermann(y, f)
    s = 0.25 / math.sqrt(0.25 / 8 - 0.25 / 64)
    assert (r.hit_rate, r.expected) == (0.75, 0.5)
    assert r.stat == pytest.approx(s) and r.stat == pytest.approx(1.51186, abs=1e-5)
    assert r.p_value == pytest.approx(stats.norm.sf(s))


def test_pesaran_timmermann_is_close_to_the_chi2_independence_test():
    """Independent computation: PT (1992) note S^2 is asymptotically the Pearson chi2
    test of independence on the 2 x 2 table of directions (scipy chi2_contingency)."""
    rng = np.random.default_rng(4)
    n = 5000
    x = rng.normal(size=n)
    y = 0.2 * x + rng.normal(size=n)
    tab = [
        [np.sum((y > 0) & (x > 0)), np.sum((y > 0) & (x <= 0))],
        [np.sum((y <= 0) & (x > 0)), np.sum((y <= 0) & (x <= 0))],
    ]
    chi2 = chi2_contingency(np.array(tab), correction=False)[0]
    assert st.pesaran_timmermann(y, x).stat ** 2 == pytest.approx(chi2, rel=1e-3)


def test_pesaran_timmermann_is_undefined_for_a_constant_direction():
    """The zero-return forecast is never 'up': PT has no variance to test against."""
    r = st.pesaran_timmermann([0.1, -0.2, 0.3], np.zeros(3))
    assert math.isnan(r.stat) and math.isnan(r.p_value)
    assert r.hit_rate == pytest.approx(1 / 3)


# ---------------------------------------------------------------- bootstrap and MCS


def test_moving_block_indices_shape_and_blocks():
    idx = st.moving_block_indices(10, 3, 200, np.random.default_rng(0))
    assert idx.shape == (200, 10)
    assert idx.min() >= 0 and idx.max() <= 9
    # within each block of 3 the indices are consecutive
    for j in (0, 3, 6):
        assert (np.diff(idx[:, j : j + 3], axis=1) == 1).all()
    assert (st.moving_block_indices(4, 10, 5, np.random.default_rng(0)) == np.arange(4)).all()


def test_moving_block_indices_match_arch():
    """Independent implementation: arch's MovingBlockBootstrap draws the same block
    starts from the same Generator."""
    from arch.bootstrap import MovingBlockBootstrap

    bs = MovingBlockBootstrap(4, np.arange(23), seed=np.random.default_rng(11))
    theirs = np.array([bs.update_indices() for _ in range(50)])
    ours = st.moving_block_indices(23, 4, 50, np.random.default_rng(11))
    np.testing.assert_array_equal(ours, theirs)


def test_mcs_matches_arch_with_the_same_bootstrap_indices():
    """Independent implementation: arch.bootstrap.MCS(method='max', bootstrap='mbb').
    Fed arch's own bootstrap indices, the MCS p-values agree exactly."""
    from arch.bootstrap import MCS

    rng = np.random.default_rng(5)
    for shift in ([0, 0.05, 0.1, 0.4, 0.6], [0, 0, 0.02, 0.03], [0, 0.3]):
        L = np.abs(rng.normal(size=(60, len(shift)))) + np.array(shift)
        ref = MCS(L, size=0.1, reps=300, block_size=3, method="max", bootstrap="mbb", seed=7)
        ref.compute()
        idx = np.array(ref._bootstrap_indices)
        ours = st.model_confidence_set(L, alpha=0.1, indices=idx)
        np.testing.assert_allclose(ours.p_values, ref.pvalues.sort_index().to_numpy().ravel())
        assert set(np.flatnonzero(ours.included)) == set(ref.included)


def test_mcs_drops_a_clearly_worse_model_and_keeps_the_best():
    rng = np.random.default_rng(6)
    L = np.abs(rng.normal(size=(80, 3))) + np.array([0.0, 0.02, 1.0])
    r = st.model_confidence_set(L, reps=500, block=1, rng=np.random.default_rng(1))
    assert r.included[0] and not r.included[2]
    assert r.eliminated[0] == 2 and r.p_values.max() == 1.0


def test_mcs_is_reproducible_from_the_seed_and_needs_one():
    L = np.abs(np.random.default_rng(7).normal(size=(30, 3)))
    a = st.model_confidence_set(L, rng=st.child_rng(1, "mcs", "s", 1))
    b = st.model_confidence_set(L, rng=st.child_rng(1, "mcs", "s", 1))
    np.testing.assert_array_equal(a.p_values, b.p_values)
    with pytest.raises(ValueError, match="rng"):
        st.model_confidence_set(L)
    with pytest.raises(ValueError, match="NaN"):
        st.model_confidence_set(np.where(L > 1, np.nan, L), rng=np.random.default_rng(0))


def test_child_rng_depends_on_seed_and_labels_only():
    draw = lambda *a: st.child_rng(*a).integers(0, 2**31, 3).tolist()  # noqa: E731
    assert draw(1, "skill", "s", 2) == draw(1, "skill", "s", 2)
    assert draw(1, "skill", "s", 2) != draw(2, "skill", "s", 2)
    assert draw(1, "skill", "s", 2) != draw(1, "skill", "s", 3)


# ---------------------------------------------------------------- skill and h*


def test_skill_point_value_hand_computed():
    """|e_model| = [1, 1, 2], |e_ref| = [2, 2, 2]: SS = 1 - (4/3) / 2 = 1/3."""
    r = st.skill_bootstrap(
        [1.0, 1.0, 2.0], [2.0, 2.0, 2.0], h=1, rng=np.random.default_rng(0), reps=200
    )
    assert r.skill == pytest.approx(1 / 3)
    assert r.lo <= r.skill <= r.hi
    assert (r.n, r.block) == (3, 1)


def test_skill_ci_brackets_a_clear_win_and_a_null():
    rng = np.random.default_rng(8)
    ref = np.abs(rng.normal(size=200))
    good = st.skill_bootstrap(0.5 * ref, ref, 4, np.random.default_rng(1))
    assert good.lo > 0 and good.skill == pytest.approx(0.5)
    other = np.abs(rng.normal(size=200))
    null = st.skill_bootstrap(other, ref, 4, np.random.default_rng(1))
    assert null.lo < 0 < null.hi
    assert st.skill_bootstrap(other, ref, 500, np.random.default_rng(1), reps=10).block == 200


@pytest.mark.parametrize(
    "lower, expected",
    [
        ([0.2, 0.1, 0.05, 0.01], 4),  # skill at every h
        ([0.2, 0.1, -0.01, 0.3], 2),  # first failure at h = 3
        ([0.0, 0.1, 0.2, 0.3], 0),  # a bound of exactly 0 is a failure
        ([0.2, np.nan, 0.2, 0.2], 1),  # no data at h = 2 counts as no skill
    ],
)
def test_h_star(lower, expected):
    """Spec: h* = last h before the first h whose lower CI bound <= 0."""
    assert st.h_star([1, 2, 3, 4], lower) == expected


def test_mcs_keeps_identical_models_that_tie_at_the_end():
    """Naive and seasonal naive forecast the same at h = m. When the only models left
    are tied, they all stay in the set (p-value 1) rather than leaving it empty."""
    rng = np.random.default_rng(9)
    base = np.abs(rng.normal(size=40))
    L = np.column_stack([base, base, base + 1.0])
    r = st.model_confidence_set(L, reps=200, rng=np.random.default_rng(0))
    assert r.included.tolist() == [True, True, False]
    assert r.p_values[:2].tolist() == [1.0, 1.0]
