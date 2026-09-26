"""Acceptance checks A3 to A9 on synthetic data (spec: Acceptance checks; M7).

Three synthetic processes with known answers (research 9.2) go through the real engine
with every level model, expanding window, T = 150, m = 12, H = 12:

  seasonal AR(1)       only seasonal models beat seasonal naive
  local linear trend   ETS beats naive from h = m / 3 on
  random walk          nothing beats naive (the answer is statistical: 200 series in
                       tests/test_canary.py; here the single run checks A4 and the report)

A1 (reproducibility) is tests/test_engine.py::test_parallel_and_repeat_runs_are_identical
and tests/test_cli.py::test_run_force_reproduces_the_store. A2 (L1, L2, L4, L5) is
tests/test_leakage.py; L3 is tests/test_canary.py. The simulation conformance check is
tests/test_simulation_conformance.py. Everything here is slow (SARIMA at 50 origins).
"""

from __future__ import annotations

import math
import time

import numpy as np
import pytest

from forecasting import pipeline
from forecasting.evaluation.report import build_report
from forecasting.evaluation.scoring import pairwise, score
from tests.acceptance import (
    LEVEL_MODELS_CHEAP,
    LEVEL_MODELS_FULL,
    SyntheticRun,
    backtest_synthetic,
    beats_naive,
)
from tests.synthetic import local_linear_trend, random_walk, seasonal_ar1

M, H = 12, 12
SEASONAL = {"seasonal_naive", "ets", "sarima", "theta", "combination"}
NON_SEASONAL = {"naive", "drift", "ses", "sma"}
BASELINES = {"naive", "seasonal_naive", "drift", "sma"}  # run at warm-up origins too
RUNTIME_BUDGET_S = 600  # A9: a 10-year monthly backtest under 10 minutes, one core

pytestmark = pytest.mark.slow


def timed(y, uid: str) -> tuple[SyntheticRun, float]:
    t0 = time.perf_counter()
    run = backtest_synthetic(y, models=LEVEL_MODELS_FULL, uid=uid)
    return run, time.perf_counter() - t0


@pytest.fixture(scope="module")
def sar():
    return timed(seasonal_ar1(n=150, seed=2), "sar")


@pytest.fixture(scope="module")
def llt():
    return timed(local_linear_trend(n=150, seed=3), "llt")


@pytest.fixture(scope="module")
def rw():
    return timed(random_walk(n=150, seed=1), "rw")


def mase_table(run: SyntheticRun):
    p = score(run.frame, run.manifest, skill_reps=100).point
    return p, p.pivot_table(index="h", columns="model", values="mase")


# ---------------------------------------------------------------- A3 known answers


def test_A3_seasonal_ar_only_seasonal_models_beat_seasonal_naive(sar):
    run, _ = sar
    point, mase = mase_table(run)
    ref = mase["seasonal_naive"]
    for name in ("ets", "sarima", "combination"):
        assert (mase[name] < ref).all(), (name, (mase[name] / ref).round(3).tolist())
    assert mase["theta"].mean() < ref.mean()  # Theta's deseasonalised SES: weaker, still ahead
    for name in NON_SEASONAL:
        # naive, drift and SES coincide with seasonal naive at h = m (SES to 0.02%);
        # they never beat it anywhere else
        assert (mase[name] >= ref * (1 - 1e-3)).all(), (name, (mase[name] / ref).round(3).tolist())
    p1 = [
        pairwise(run.frame, run.manifest, run.uid, run.window, name, "seasonal_naive")
        .set_index("h")
        .loc[1, "dm_p_better"]
        for name in ("ets", "sarima", "combination")
    ]
    assert min(p1) < 0.05  # a seasonal model wins significantly at h = 1
    assert (point.loc[point["model"] == "seasonal_naive", "reference"] == "seasonal_naive").all()


def test_A3_local_linear_trend_ets_beats_naive_from_m_over_3(llt):
    run, _ = llt
    _, mase = mase_table(run)
    hs = range(math.ceil(M / 3), H + 1)
    ratio = (mase.loc[hs, "ets"] / mase.loc[hs, "naive"]).round(3)
    assert (ratio < 1).all(), ratio.tolist()
    variants = run.frame.loc[
        (run.frame["model"] == "ets") & (run.frame["origin_role"] == "test"), "variant"
    ].unique()
    assert all(v.startswith("ETS(A,A") for v in variants), variants  # a trend model
    # the gain grows with the horizon: a trend model against a flat one
    assert ratio.iloc[-1] < ratio.iloc[0]


def test_A3_random_walk_single_run_is_consistent_with_no_predictability(rw):
    """The statistical answer is the canary; one path can trend for 40 points. Here:
    SES and ETS(., N, N) cannot beat naive at h = 1 by more than sampling noise, and no
    model beats naive at h = 1 (n / h = 30) after Holm."""
    run, _ = rw
    for name in ("ses", "ets"):
        rel = pairwise(run.frame, run.manifest, run.uid, run.window, name, "naive").iloc[0]
        assert abs(rel["rel_mae"] - 1) < 0.1, (name, rel["rel_mae"])
    table = beats_naive(run)
    assert not table.loc[table["h"] == 1, "hit"].any(), table[table["h"] == 1]
    assert (table.loc[table["h"] == 1, "rel_mae"] > 0.9).all()


# ---------------------------------------------------------------- A4 coverage of models


@pytest.mark.parametrize("which", ["sar", "llt", "rw"])
def test_A4_every_cell_has_a_row_or_a_typed_failure(which, request):
    run, _ = request.getfixturevalue(which)
    f = run.frame
    plan = run.manifest["plans"][run.uid]
    n_origins, n_scored = plan["n_origins"], plan["n_dev"] + plan["n_test"]
    models = set(f["model"])
    for name in models:
        rows = f[f["model"] == name]
        expected = (n_origins if name in BASELINES or name.startswith("sma_") else n_scored) * H
        assert len(rows) == expected, (name, len(rows), expected)
        assert not rows.duplicated(["origin_t", "h"]).any()
    assert set(f["status"]) <= {"ok", "failed", "skipped"}
    failed = f[f["status"] == "failed"]
    assert len(failed) / len(f) < 0.01, failed["error"].value_counts()
    assert (failed["error"] != "").all()  # a typed failure carries its message
    assert f.loc[f["status"] == "ok", "y_pred"].notna().all()


# ---------------------------------------------------------------- A5 to A9


@pytest.mark.parametrize("which", ["sar", "llt", "rw"])
def test_A5_to_A8_report_answers_every_question(which, request):
    run, _ = request.getfixturevalue(which)
    rep = build_report(run.frame, run.manifest, skill_reps=200, mcs_reps=200)
    v = {x.rq: x for x in rep.series[0].verdicts}
    assert v["RQ1"].answer in {"yes", "partly", "no"}  # A5
    assert v["RQ2"].answer.startswith("h* = ")  # A6
    assert v["RQ3"].answer in {"calibrated", "under-covers", "over-covers", "miscalibrated"}  # A7
    assert v["RQ4"].answer.startswith("autocorrelation ")  # A8
    assert v["RQ5"].answer == "not answerable"  # one window here; both windows: test_report
    assert v["RQ6"].answer in {"log", "none", "boxcox", "mixed"}
    assert rep.series[0].exit.answer in {"proceed", "proceed, restricted", "stop modelling"}
    assert chr(0x2014) not in rep.markdown


def test_A9_runtime_on_one_core(sar, llt, rw):
    """Every model at 50 scored origins of a 12.5-year monthly series."""
    for run, seconds in (sar, llt, rw):
        assert seconds < RUNTIME_BUDGET_S, (run.uid, seconds)


def test_known_answer_processes_are_what_they_claim():
    """The DGPs themselves: a random walk has unit-root increments with no drift, the
    seasonal AR has period m, the local linear trend has a drifting slope."""
    rw_ = random_walk(n=2000, seed=1)
    d = np.diff(rw_)
    assert abs(d.mean()) < 3 * d.std() / np.sqrt(len(d))
    sar_ = seasonal_ar1(n=1200, seed=2) - 100
    acf_m = np.corrcoef(sar_[M:], sar_[:-M])[0, 1]
    assert acf_m > 0.9
    llt_ = local_linear_trend(n=150, seed=3)
    assert np.polyfit(np.arange(150), llt_, 1)[0] > 0


def test_A4_failed_share_is_measured_not_trusted():
    """A4's second half, the 1% cap, on the run rather than in the run summary's prose.

    Phase 5b forced this: 1.29% of its rows were typed failures, all rolling-window GARCH
    fits, with none on the expanding window its decision came from. Nothing in the code
    noticed, because until now A4 was only asserted on synthetic runs where no model
    fails. The status is computed for every run and recorded in the manifest.
    """
    run = backtest_synthetic(random_walk(n=150, seed=1), models=LEVEL_MODELS_CHEAP, uid="a4")
    a4 = run.manifest["a4"]
    assert a4["limit"] == 0.01
    assert a4["passed"] is True
    assert a4["failed_share"] == 0.0
    assert set(a4["failed_share_by_window"]) == {run.window}

    # A frame with 2% typed failures must be reported as over the limit, per window.
    frame = run.frame.copy()
    n = len(frame)
    frame.loc[frame.index[: int(0.02 * n)], "status"] = "failed"
    bad = pipeline.a4_status(frame)
    assert bad["passed"] is False
    assert bad["failed_share"] >= 0.02
