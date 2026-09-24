"""Calibration statistics (Phase 2).

Known answers, not self-consistency: every statistic is checked against an
independent computation or against a case where the right answer is known in
advance. The end-to-end test is the one that matters, because it is the one that
would notice if the metrics could not tell a conditionally correct model from a
flat one.
"""

from __future__ import annotations

import numpy as np
import pytest
from scipy import stats

from forecasting.evaluation.calibration import (
    christoffersen,
    crps_grid,
    independence_testable,
    pinball,
    pit,
    pit_bins,
    pit_uniformity,
)
from forecasting.evaluation.tests import kupiec

TAUS = np.array([0.005, 0.025, 0.05, 0.10, 0.25, 0.75, 0.90, 0.95, 0.975, 0.995])


def garch_path(n, seed, w=4e-6, a=0.09, b=0.89, rng=None):
    """A GARCH(1,1) series and its own conditional sd, so the right answer is known."""
    rng = rng or np.random.default_rng(seed)
    var = np.empty(n)
    e = np.empty(n)
    var[0] = w / (1 - a - b)
    for t in range(n):
        e[t] = np.sqrt(var[t]) * rng.standard_normal()
        if t + 1 < n:
            var[t + 1] = w + a * e[t] ** 2 + b * var[t]
    return e, np.sqrt(var)


# ---------------------------------------------------------------- pure functions


def test_conditional_coverage_decomposes_into_kupiec_plus_independence():
    rng = np.random.default_rng(3)
    for _ in range(20):
        hits = (rng.random(400) < 0.2).astype(float)
        c = christoffersen(hits, 0.2)
        assert c.lr_cc - c.lr_ind == pytest.approx(kupiec(hits.sum(), len(hits), 0.2).lr, abs=1e-9)


def test_independence_is_correctly_sized_on_iid_hits():
    rng = np.random.default_rng(5)
    rejects = sum(
        christoffersen((rng.random(500) < 0.2).astype(float), 0.2).p_ind < 0.05 for _ in range(600)
    )
    assert 0.02 <= rejects / 600 <= 0.09  # nominal 0.05


def test_independence_catches_clustering_at_the_same_average_rate():
    """The failure a flat interval makes on a clustered series: the right number of
    misses overall, arriving together. Kupiec cannot see it, and that separation is
    the reason both tests are registered rather than just one.

    Stated over replications, because in any single draw the realised miss rate
    wanders and Kupiec can reject by chance. Asserting on one draw would be asserting
    on the seed.
    """
    rng = np.random.default_rng(7)
    p, pi1 = 0.2, 0.5
    pi0 = p * (1 - pi1) / (1 - p)
    ind_rejects = uc_rejects = clustered = 0
    reps = 200
    for _ in range(reps):
        x = np.zeros(600)
        x[0] = rng.random() < p
        for i in range(1, len(x)):
            x[i] = rng.random() < (pi1 if x[i - 1] else pi0)
        c = christoffersen(x, p)
        ind_rejects += c.p_ind < 0.05
        uc_rejects += kupiec(x.sum(), len(x), p).p_value < 0.05
        clustered += c.clustered
    assert ind_rejects / reps > 0.95, "independence missed clustered misses"
    assert clustered / reps > 0.95
    # Kupiec is not merely blind here, it is over-sized: the marginal rate is 0.2 by
    # construction, but dependent exceedances make the count over-dispersed relative
    # to the binomial it assumes, so it rejects far more often than 5% (measured about
    # 18% at these settings). A Kupiec rejection on clustered misses is therefore not
    # evidence that the average coverage is wrong. Registered in DECISIONS as P2-3.
    assert 0.05 < uc_rejects / reps < 0.40
    assert uc_rejects < ind_rejects / 2


def test_pit_bins_are_uniform_for_a_correct_model_and_not_otherwise():
    rng = np.random.default_rng(11)
    y = rng.standard_normal(4000)
    right = np.tile(stats.norm.ppf(TAUS), (len(y), 1))
    counts, expected = pit_bins(y, right, TAUS)
    assert counts.sum() == len(y)
    assert np.allclose(counts / counts.sum(), expected, atol=0.02)
    assert pit_uniformity(y, right, TAUS)[1] > 0.01
    for scale in (0.5, 1.5):
        assert pit_uniformity(y, right * scale, TAUS)[1] < 1e-6


def test_pit_is_the_position_of_the_actual_in_its_own_distribution():
    q = np.array([[-2.0, -1.0, 0.0, 1.0, 2.0]])
    taus = np.array([0.1, 0.3, 0.5, 0.7, 0.9])
    assert pit([0.0], q, taus)[0] == pytest.approx(0.5)
    assert pit([-1.0], q, taus)[0] == pytest.approx(0.3)
    assert pit([0.5], q, taus)[0] == pytest.approx(0.6)  # linear between grid points


def test_pit_rejects_a_non_monotone_quantile_function():
    with pytest.raises(ValueError, match="monotone"):
        pit([0.0], np.array([[1.0, -1.0]]), np.array([0.25, 0.75]))


def test_pinball_matches_its_definition_and_is_minimised_at_the_true_quantile():
    rng = np.random.default_rng(13)
    y, q, tau = rng.standard_normal(500), rng.standard_normal(500), 0.3
    d = y - q
    assert pinball(y, q, tau) == pytest.approx(np.mean(np.where(d >= 0, tau * d, (tau - 1) * d)))
    z = rng.standard_normal(200_000)
    best = stats.norm.ppf(tau)
    assert pinball(z, np.full_like(z, best), tau) < pinball(z, np.full_like(z, best + 0.1), tau)
    assert pinball(z, np.full_like(z, best), tau) < pinball(z, np.full_like(z, best - 0.1), tau)


def test_crps_grid_converges_to_the_closed_form_normal_crps():
    rng = np.random.default_rng(17)
    y = rng.standard_normal(4000)
    dense = np.linspace(0.001, 0.999, 999)
    got = crps_grid(y, np.tile(stats.norm.ppf(dense), (len(y), 1)), dense)
    closed = float(
        np.mean(y * (2 * stats.norm.cdf(y) - 1) + 2 * stats.norm.pdf(y) - 1 / np.sqrt(np.pi))
    )
    assert got == pytest.approx(closed, rel=0.01)


def test_independence_only_runs_where_origins_do_not_overlap():
    assert independence_testable(1, 5)
    assert independence_testable(5, 5)
    assert not independence_testable(20, 5)


# ---------------------------------------------------------------- known answer


@pytest.mark.slow
def test_conditional_variance_is_calibrated_and_a_flat_interval_is_not():
    """The point of the whole exercise, on data whose truth is known.

    A GARCH series is generated, then two one-step-ahead 80% intervals are formed at
    every point: one from the true conditional sd, one from the unconditional sd. Both
    should cover about 80% overall. Only the flat one should miss in clusters.
    """
    e, sd = garch_path(6000, seed=23)
    z = stats.norm.ppf(0.9)
    burn = 500
    y = e[burn:]

    cond_hits = (np.abs(y) > z * sd[burn:]).astype(float)
    flat_sd = float(np.sqrt(np.mean(e[:burn] ** 2)))
    flat_hits = (np.abs(y) > z * flat_sd).astype(float)

    cond = christoffersen(cond_hits, 0.2)
    flat = christoffersen(flat_hits, 0.2)

    # Both are roughly right on average.
    assert kupiec(cond_hits.sum(), len(cond_hits), 0.2).p_value > 0.01
    # The conditional one is not clustered; the flat one is, and badly.
    assert cond.p_ind > 0.01, f"true conditional sd looked clustered: {cond}"
    assert flat.p_ind < 1e-6, f"flat interval did not look clustered: {flat}"
    assert flat.clustered


@pytest.mark.slow
def test_garch_recovers_its_own_parameters_and_beats_a_flat_interval_on_pinball():
    """Fitted, not oracle: the model has to estimate what the previous test was given."""
    from forecasting.models.variance import GARCH, ConstantSigma

    e, sd = garch_path(5000, seed=29)
    train, test = e[:4000], e[4000:]
    import pandas as pd

    y = pd.Series(train, index=pd.RangeIndex(len(train)))

    g = GARCH().fit(y)
    assert g.persistence == pytest.approx(0.98, abs=0.03)

    flat = ConstantSigma().fit(y)
    # One-step ahead, refit-free: compare the first-step quantiles on the held-out point.
    gq = g.predict(1, (0.8,))
    fq = flat.predict(1, (0.8,))
    assert gq.lower[0.8][0] < 0 < gq.upper[0.8][0]
    # The GARCH sd reacts to the state it was left in; the flat one cannot.
    g_sd = (gq.upper[0.8][0] - gq.lower[0.8][0]) / 2
    f_sd = (fq.upper[0.8][0] - fq.lower[0.8][0]) / 2
    assert abs(g_sd - sd[len(train)]) < abs(f_sd - sd[len(train)])
    assert len(test) > 0
