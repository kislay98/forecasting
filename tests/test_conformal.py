"""Split conformal calibration (Phase 4), against known answers.

The two things that matter: it restores coverage on a model that is deliberately the
wrong width, and it never uses an outcome that was not observable yet.
"""

from __future__ import annotations

from itertools import pairwise

import numpy as np
import pandas as pd
import pytest
from scipy.stats import norm

from forecasting.backtest.store import level_tag
from forecasting.evaluation.conformal import (
    MIN_CALIBRATION,
    conformalise,
    correction,
    eligible_mask,
    enforce_monotone,
)


def test_eligible_mask_excludes_origins_whose_outcome_is_not_out_yet():
    """An origin at t' produces its h-step truth at t' + h, so it can inform a forecast
    made at t only once t' + h <= t. The boundary is inclusive: an outcome landing
    exactly at t is observable at t."""
    origins = np.array([0, 5, 10, 15, 20])
    # At h = 1, everything strictly before t is usable; t's own outcome is not.
    assert list(eligible_mask(origins, 20, 1)) == [True, True, True, True, False]
    # At h = 20 only the origin whose window has just closed qualifies.
    assert list(eligible_mask(origins, 20, 20)) == [True, False, False, False, False]
    # Using t' < t instead of t' + h <= t would wrongly admit three more origins here,
    # each carrying up to 20 periods of the future.
    assert eligible_mask(origins, 20, 20).sum() == 1


def test_correction_declines_on_a_short_calibration_set():
    assert np.isnan(correction(np.zeros(MIN_CALIBRATION - 1), 0.8))
    assert np.isfinite(correction(np.zeros(MIN_CALIBRATION), 0.8))


def test_correction_is_the_quantile_of_the_scores():
    rng = np.random.default_rng(1)
    s = rng.standard_normal(2000)
    q = correction(s, 0.8)
    # Within sampling error of the 80th percentile.
    assert q == pytest.approx(np.quantile(s, 0.8), abs=0.06)


def _toy(n=400, step=20, h=20, width=1.0, seed=0):
    """A frame whose intervals are a known multiple of the right width."""
    rng = np.random.default_rng(seed)
    y = rng.standard_normal(n)
    half = 1.2815515655446004 * width  # the 80% half-width of a standard normal
    origins = np.arange(n) * step
    return pd.DataFrame(
        {
            "unique_id": "s",
            "model": "m",
            "window": "expanding",
            "origin_t": origins,
            "origin_role": ["dev"] * (n // 2) + ["test"] * (n - n // 2),
            "h": h,
            "y_true": y,
            "y_true_missing": False,
            "y_pred": 0.0,
            "status": "ok",
            "lo_80": -half,
            "hi_80": half,
        }
    )


def _coverage(df):
    t = df[(df.origin_role == "test") & (df.conformal_n > 0)]
    return float(((t.y_true >= t.lo_80) & (t.y_true <= t.hi_80)).mean()), len(t)


@pytest.mark.parametrize("width", [1.8, 0.6])
def test_conformal_restores_coverage_on_a_wrong_width_model(width):
    """Intervals 80% too wide, and 40% too narrow, both land near nominal."""
    raw = _toy(width=width)
    before = float(((raw.y_true >= raw.lo_80) & (raw.y_true <= raw.hi_80)).mean())
    out = conformalise(raw, (0.8,), window=100, model_window="expanding")
    after, n = _coverage(out)
    assert n > 150
    assert abs(after - 0.80) < 0.05, f"conformal left coverage at {after}"
    assert abs(after - 0.80) < abs(before - 0.80), "it should be closer than it started"


def test_conformal_is_a_no_op_on_an_already_calibrated_model():
    raw = _toy(width=1.0)
    out = conformalise(raw, (0.8,), window=100, model_window="expanding")
    after, _ = _coverage(out)
    assert abs(after - 0.80) < 0.05
    # The correction should be small, not merely the coverage right by luck.
    q = out[out.conformal_n > 0]["conformal_q"]
    assert q.abs().median() < 0.25


def test_conformal_never_uses_an_outcome_from_the_future():
    """Poison every outcome after a cut and the corrections before it must not move.

    The L1 test, applied to the calibration layer rather than to a model.
    """
    raw = _toy(width=1.8, seed=4)
    cut = raw["origin_t"].quantile(0.6)
    poisoned = raw.copy()
    later = poisoned["origin_t"] > cut
    poisoned.loc[later, "y_true"] = 1e6

    a = conformalise(raw, (0.8,), window=100, model_window="expanding")
    b = conformalise(poisoned, (0.8,), window=100, model_window="expanding")
    keep = a["origin_t"] <= cut
    for col in ("lo_80", "hi_80", "conformal_q", "conformal_n"):
        assert np.allclose(
            a.loc[keep, col].to_numpy(dtype=float),
            b.loc[keep, col].to_numpy(dtype=float),
            equal_nan=True,
        ), f"{col} moved when only the future changed"


def test_conformal_marks_rows_it_could_not_calibrate():
    raw = _toy(n=40, width=1.8)
    out = conformalise(raw, (0.8,), window=100, model_window="expanding")
    untouched = out[out.conformal_n == 0]
    assert len(untouched) > 0
    assert (untouched["lo_80"] == raw["lo_80"].iloc[0]).all()


def test_corrected_grid_is_ordered_and_only_outer_levels_move():
    """OP-10: per-level corrections can cross, and the repair fixes it from the inside out.

    Found by the threshold work reading the whole grid, not by any gate: the gates read
    only the 80% and 95% bands, and on Phase 4's store 19.3% of corrected rows crossed
    somewhere, most at the 95% to 99% pair.
    """
    levels = (0.5, 0.8, 0.95)
    # A grid that crosses: the 95% band sits inside the 80% band.
    frame = pd.DataFrame(
        {
            "lo_50": [-1.0], "hi_50": [1.0],
            "lo_80": [-2.0], "hi_80": [2.0],
            "lo_95": [-1.5], "hi_95": [1.5],
        }
    )  # fmt: skip
    fixed = enforce_monotone(frame, levels)
    assert fixed["lo_95"].iloc[0] == -2.0  # widened out to the 80% bound
    assert fixed["hi_95"].iloc[0] == 2.0
    assert fixed["lo_80"].iloc[0] == -2.0  # inner levels untouched
    assert fixed["hi_80"].iloc[0] == 2.0
    assert fixed["lo_50"].iloc[0] == -1.0
    assert fixed["hi_50"].iloc[0] == 1.0

    # An already ordered grid is returned unchanged.
    ok = pd.DataFrame(
        {
            "lo_50": [-1.0], "hi_50": [1.0],
            "lo_80": [-2.0], "hi_80": [2.0],
            "lo_95": [-3.0], "hi_95": [3.0],
        }
    )  # fmt: skip
    pd.testing.assert_frame_equal(enforce_monotone(ok, levels), ok)

    # `only` confines the repair to the rows named.
    two = pd.concat([frame, frame], ignore_index=True)
    part = enforce_monotone(two, levels, only=pd.Series([True, False]))
    assert part["hi_95"].iloc[0] == 2.0
    assert part["hi_95"].iloc[1] == 1.5


def test_conformalise_never_returns_a_crossed_grid():
    """The property the report and the threshold reader depend on, end to end.

    The intervals start deliberately too wide, which is the regime that makes the
    corrections negative and lets the outer levels overtake the inner ones.
    """
    levels = (0.5, 0.8, 0.9, 0.95, 0.99)
    frame = _toy(n=200, width=1.6)
    for lv in levels:
        tag = level_tag(lv)
        half = float(norm.ppf(0.5 + lv / 2)) * 1.6
        frame[f"lo_{tag}"] = -half
        frame[f"hi_{tag}"] = half
    out = conformalise(frame, levels, window=60, model_window="expanding")
    tags = [level_tag(lv) for lv in levels]
    for a, b in pairwise(tags):
        assert (out[f"lo_{b}"] <= out[f"lo_{a}"] + 1e-12).all(), f"lo crosses {a} to {b}"
        assert (out[f"hi_{b}"] >= out[f"hi_{a}"] - 1e-12).all(), f"hi crosses {a} to {b}"
