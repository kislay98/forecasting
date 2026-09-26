"""VaR and expected shortfall (Phase 3), checked against answers known in advance."""

from __future__ import annotations

import numpy as np
import pytest
from scipy import stats

from forecasting.evaluation.risk import es_backtest, simple_loss


def test_simple_loss_matches_its_definition():
    assert simple_loss(0.0) == pytest.approx(0.0)
    assert simple_loss(-0.10) == pytest.approx(1 - np.exp(-0.10))
    # The gap between a log return and the loss it implies is not cosmetic in the tail.
    assert simple_loss(-0.10) == pytest.approx(0.0952, abs=1e-4)
    assert simple_loss(-0.50) == pytest.approx(0.3935, abs=1e-4)


def _normal_var_es(p_tail: float):
    """True VaR and ES of a standard normal at a lower-tail probability."""
    var = stats.norm.ppf(p_tail)
    es = -stats.norm.pdf(var) / p_tail
    return var, es


def test_es_backtest_covers_zero_when_the_tail_mean_is_right():
    """The null is a correct ES, so a correct model must not be flagged."""
    p_tail = 0.05
    var, es = _normal_var_es(p_tail)
    rng = np.random.default_rng(4)
    flagged = 0
    reps = 200
    for i in range(reps):
        y = rng.standard_normal(4000)
        r = es_backtest(y, np.full_like(y, var), np.full_like(y, es), reps=800, seed=i)
        assert r.n_breaches > 100
        flagged += not r.covers_zero
    assert flagged / reps < 0.12  # nominal 0.05 for a bootstrap interval this size


def test_es_backtest_catches_an_optimistic_tail():
    """ES understated by 25%: the interval must exclude zero on the low side, meaning
    realised breaches were worse than predicted."""
    p_tail = 0.05
    var, es = _normal_var_es(p_tail)
    rng = np.random.default_rng(9)
    y = rng.standard_normal(6000)
    r = es_backtest(y, np.full_like(y, var), np.full_like(y, es * 0.75), seed=1)
    assert not r.covers_zero
    assert r.ci_high < 0, "an optimistic ES should show a negative bias"
    assert r.bias < 0


def test_es_backtest_catches_an_over_cautious_tail():
    p_tail = 0.05
    var, es = _normal_var_es(p_tail)
    rng = np.random.default_rng(11)
    y = rng.standard_normal(6000)
    r = es_backtest(y, np.full_like(y, var), np.full_like(y, es * 1.35), seed=2)
    assert not r.covers_zero
    assert r.ci_low > 0


def test_es_backtest_declines_on_too_few_breaches():
    rng = np.random.default_rng(3)
    y = rng.standard_normal(50)
    r = es_backtest(y, np.full_like(y, -5.0), np.full_like(y, -6.0), seed=0)
    assert r.n_breaches < 10
    assert np.isnan(r.bias)
    assert r.covers_zero  # declining to judge is not the same as failing


def test_es_is_always_worse_than_var():
    """A sanity property the store's es_ column must satisfy: the mean beyond a
    quantile is below the quantile."""
    for p in (0.005, 0.025, 0.05, 0.10, 0.25):
        var, es = _normal_var_es(p)
        assert es < var
