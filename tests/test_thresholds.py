"""Threshold-exceedance probabilities from the quantile grid (OP-4), checked against
answers known in advance.

The grid is the stored one: ten quantiles from levels (0.5, 0.8, 0.9, 0.95, 0.99). On a
Normal grid the probit interpolation is exact by construction, which is why the Normal
case is a known-answer test for the code rather than for the method. The Student-t case
is the one that says how far the method is from the truth when the distribution is not
Normal, with tolerances set from a measurement and stated here.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from scipy import stats

from forecasting.backtest.store import columns_for
from forecasting.evaluation.thresholds import (
    CROSSED,
    NO_GRID,
    OK,
    OUTSIDE,
    cdf_from_grid,
    grid_columns,
    grid_probabilities,
    loss_to_log_return,
    summarise,
    threshold_probabilities,
)

LEVELS = (0.5, 0.8, 0.9, 0.95, 0.99)
PROBS = grid_probabilities(LEVELS)

# Measured over 20,001 points spanning the grid (see the module docstring):
#   Normal, probit: 2e-16.     Normal, linear: 0.0110 max, 0.0034 in the tails.
#   Student t(4), probit: 0.0046 max, 0.0014 where F < 0.05.
NORMAL_PROBIT_TOL = 1e-9
NORMAL_LINEAR_TOL = 0.012
T4_PROBIT_TOL = 0.005
T4_PROBIT_TAIL_TOL = 0.0015


def _frame(q: np.ndarray, y=None, role: str = "test") -> pd.DataFrame:
    """A store-shaped frame with the given quantile rows, one row per origin."""
    q = np.atleast_2d(q)
    n = len(q)
    names = [c for c, _ in columns_for(LEVELS)]
    d = {c: [np.nan] * n for c in names}
    d.update(
        run_id=["r"] * n,
        unique_id=["s"] * n,
        model=["m"] * n,
        window=["expanding"] * n,
        origin_t=list(range(n)),
        origin_period=[f"2020-01-{i % 28 + 1:02d}" for i in range(n)],
        origin_role=[role] * n,
        h=[1] * n,
        target_period=[""] * n,
        y_true=list(np.zeros(n) if y is None else y),
        y_true_missing=[False] * n,
        y_pred=[0.0] * n,
        status=["ok"] * n,
    )
    frame = pd.DataFrame(d)[names]
    for j, c in enumerate(grid_columns(LEVELS)):
        frame[c] = q[:, j]
    return frame


def test_grid_is_the_stored_one():
    assert np.allclose(PROBS, [0.005, 0.025, 0.05, 0.1, 0.25, 0.75, 0.9, 0.95, 0.975, 0.995])
    assert grid_columns(LEVELS) == [
        "lo_99", "lo_95", "lo_90", "lo_80", "lo_50",
        "hi_50", "hi_80", "hi_90", "hi_95", "hi_99",
    ]  # fmt: skip
    assert set(grid_columns(LEVELS)) <= {c for c, _ in columns_for(LEVELS)}


def test_normal_grid_matches_the_analytic_probability():
    """Known answer: on a Normal quantile grid, P(loss > L) is Phi((log(1 - L) - mu) / s)."""
    mu, sd = 0.001, 0.045  # about a month of an equity index
    q = stats.norm.ppf(PROBS, mu, sd)
    losses = np.linspace(-0.12, 0.10, 45)
    out = threshold_probabilities(_frame(q), LEVELS, losses)
    assert (out["state"] == OK).all()
    truth = stats.norm.cdf(np.log1p(-losses), mu, sd)
    assert np.allclose(out["p_exceed"].to_numpy(), truth, atol=NORMAL_PROBIT_TOL)
    lin = threshold_probabilities(_frame(q), LEVELS, losses, method="linear")
    assert np.abs(lin["p_exceed"].to_numpy() - truth).max() < NORMAL_LINEAR_TOL


def test_probit_beats_linear_on_a_fat_tailed_grid():
    """Student t(4): not exact, within a measured tolerance, and closer than linear."""
    dist = stats.t(4)
    q = dist.ppf(PROBS)
    x = np.linspace(q[0], q[-1], 4001)
    grid = np.tile(q, (len(x), 1))
    F, state = cdf_from_grid(PROBS, grid, x)
    Fl, _ = cdf_from_grid(PROBS, grid, x, method="linear")
    assert (state == OK).all()
    truth = dist.cdf(x)
    err, err_lin = np.abs(F - truth), np.abs(Fl - truth)
    assert err.max() < T4_PROBIT_TOL
    assert err[truth < 0.05].max() < T4_PROBIT_TAIL_TOL
    assert err.mean() < err_lin.mean()


def test_stored_quantiles_are_reproduced_exactly():
    q = stats.t(5).ppf(PROBS) * 0.02
    for method in ("probit", "linear"):
        F, state = cdf_from_grid(PROBS, np.tile(q, (len(q), 1)), q, method=method)
        assert (state == OK).all()
        assert np.allclose(F, PROBS, atol=1e-12)


def test_beyond_the_outermost_quantile_is_refused_not_extrapolated():
    q = stats.norm.ppf(PROBS, 0.0, 0.01)  # a day: the 0.5% quantile is a 2.5% loss
    out = threshold_probabilities(_frame(q), LEVELS, [0.10, -0.10, 0.02])
    by = out.set_index("loss_threshold")
    for L in (0.10, -0.10):
        assert by.loc[L, "state"] == OUTSIDE
        assert np.isnan(by.loc[L, "p_beyond"]) and np.isnan(by.loc[L, "p_exceed"])
        # What the grid does support is a bound on the rare side, never a point.
        assert (by.loc[L, "p_lo"], by.loc[L, "p_hi"]) == (0.0, 0.005)
    assert by.loc[0.02, "state"] == OK
    assert by.loc[0.02, "p_lo"] == by.loc[0.02, "p_hi"] == by.loc[0.02, "p_beyond"]


def test_the_certain_side_of_the_grid_is_bracketed_too():
    """A threshold inside everything the grid covers, from the other side: the loss is
    near certain to be beyond it, and the bracket says [0.995, 1] rather than 1."""
    q = stats.norm.ppf(PROBS, -0.30, 0.01)  # a certain 26% loss
    out = threshold_probabilities(_frame(q), LEVELS, [0.05])
    r = out.iloc[0]
    assert r["state"] == OUTSIDE and (r["p_lo"], r["p_hi"]) == (0.995, 1.0)


def test_the_outermost_quantile_itself_is_answered():
    q = stats.norm.ppf(PROBS, 0.0, 0.03)
    F, state = cdf_from_grid(PROBS, q[None, :], q[0])  # exactly the 0.5% quantile
    assert state[0] == OK and F[0] == pytest.approx(0.005, abs=1e-12)


def test_monotone_in_the_threshold_on_random_grids():
    """For any valid grid the answer never increases as the loss threshold grows, and
    p_beyond on the gain side never increases as the gain grows."""
    rng = np.random.default_rng(3)
    for _ in range(200):
        q = np.sort(rng.standard_t(3, size=len(PROBS))) * rng.uniform(0.005, 0.08)
        if rng.random() < 0.1:
            q[5] = q[4]  # ties happen; allow them
        x = np.sort(rng.uniform(q[0], q[-1], 300))
        for method in ("probit", "linear"):
            F, state = cdf_from_grid(PROBS, np.tile(q, (len(x), 1)), x, method=method)
            assert (state == OK).all()
            assert np.all(np.diff(F) >= -1e-15)
            assert F.min() >= PROBS[0] - 1e-15 and F.max() <= PROBS[-1] + 1e-15
    q = stats.norm.ppf(PROBS, 0.0, 0.05)
    losses = np.linspace(0.0, 0.11, 60)
    p = threshold_probabilities(_frame(q), LEVELS, losses)["p_beyond"].to_numpy()
    assert np.all(np.diff(p) <= 0)
    gains = -np.linspace(0.0, 0.11, 60)
    p = threshold_probabilities(_frame(q), LEVELS, gains)["p_beyond"].to_numpy()
    assert np.all(np.diff(p) <= 0)


def test_gain_threshold_is_the_complement_on_the_other_side():
    mu, sd = 0.0, 0.04
    q = stats.norm.ppf(PROBS, mu, sd)
    out = threshold_probabilities(_frame(q), LEVELS, [-0.05]).iloc[0]
    # A 5% gain: loss below -0.05, a log return above log(1.05).
    truth = 1 - stats.norm.cdf(np.log(1.05), mu, sd)
    assert out["p_beyond"] == pytest.approx(truth, abs=NORMAL_PROBIT_TOL)
    assert out["p_exceed"] == pytest.approx(1 - truth, abs=NORMAL_PROBIT_TOL)


def test_crossed_grid_is_refused_unless_rearranged():
    q = stats.norm.ppf(PROBS, 0.0, 0.03)
    crossed = q.copy()
    crossed[1], crossed[2] = q[2], q[1]  # the 2.5% and 5% quantiles swapped
    x = np.log1p(-0.04)
    F, state = cdf_from_grid(PROBS, crossed[None, :], x)
    assert state[0] == CROSSED and np.isnan(F[0])
    Fr, sr = cdf_from_grid(PROBS, crossed[None, :], x, rearrange=True)
    Fs, _ = cdf_from_grid(PROBS, np.sort(crossed)[None, :], x)
    assert sr[0] == OK and Fr[0] == Fs[0]


def test_missing_grid_is_its_own_state():
    q = stats.norm.ppf(PROBS, 0.0, 0.03)
    q[3] = np.nan
    F, state = cdf_from_grid(PROBS, q[None, :], 0.0)
    assert state[0] == NO_GRID and np.isnan(F[0])


def test_loss_of_the_whole_position_is_not_a_threshold():
    with pytest.raises(ValueError):
        loss_to_log_return(1.0)
    with pytest.raises(ValueError):
        cdf_from_grid(PROBS, np.zeros((1, 3)), 0.0)


def test_summary_of_a_calibrated_model_matches_what_happened():
    """Truth drawn from the forecast distribution itself: the answered mean probability
    and the realised frequency on the same origins must agree within sampling error,
    and the refused rows are counted rather than dropped."""
    rng = np.random.default_rng(12)
    n = 4000
    sd = rng.uniform(0.01, 0.05, n)  # volatility varies by origin, as it does
    q = stats.norm.ppf(PROBS)[None, :] * sd[:, None]
    y = rng.standard_normal(n) * sd
    out = threshold_probabilities(_frame(q, y), LEVELS, [0.05, -0.05])
    s = summarise(out).set_index("loss_threshold")
    for L in (0.05, -0.05):
        r = s.loc[L]
        assert r["n"] == n and r["n_answered"] + r["n_outside"] + r["n_crossed"] == n
        assert r["n_outside"] > 0, "small-sd origins cannot answer a 5% threshold"
        se = np.sqrt(r["p_mean"] * (1 - r["p_mean"]) / r["n_answered"])
        assert abs(r["p_mean"] - r["realised"]) < 4 * se
        # Refused on the rare side means P < 0.005; a handful of hits at most.
        assert r["outside_hits"] <= max(5, 0.005 * r["n_outside"] * 4)


def test_summary_reads_only_the_requested_role():
    q = stats.norm.ppf(PROBS, 0.0, 0.04)
    frame = pd.concat([_frame(q, [0.0], role="dev"), _frame(q, [0.0], role="test")])
    s = summarise(threshold_probabilities(frame, LEVELS, [0.05]), role="test")
    assert s["n"].tolist() == [1]


def test_report_section_prints_bounds_counts_and_the_caveat():
    """The risk report's table: refusals print as a bound, never as a number, and the
    interpolation caveat is on the page."""
    from forecasting.evaluation.phase3_report import DEFAULT_THRESHOLDS, threshold_section

    rng = np.random.default_rng(5)
    n = 60
    sd = np.where(np.arange(n) % 2 == 0, 0.005, 0.04)  # calm and volatile origins
    q = stats.norm.ppf(PROBS)[None, :] * sd[:, None]
    frame = _frame(q, rng.standard_normal(n) * sd)
    text = "\n".join(threshold_section(frame, LEVELS, DEFAULT_THRESHOLDS, "m", "expanding", [1]))
    assert "## Probability of a move beyond a threshold" in text
    assert "not simulated frequencies" in text
    assert "< 0.005" in text  # the latest origin is calm: a 10% loss is off its grid
    assert "| 1 | loss 10% |" in text and "| 1 | gain 5% |" in text
    rows = {line.split(" | ")[1]: line for line in text.splitlines() if line.startswith("| 1 |")}
    assert "30 of 60" in rows["loss 5%"]  # only the volatile half can answer a 5% loss
    assert "0 of 60" in rows["loss 10%"] and "not tested" in rows["loss 10%"]
    empty = threshold_section(frame, LEVELS, DEFAULT_THRESHOLDS, "absent", "expanding", [1])
    assert "no table" in "\n".join(empty)
