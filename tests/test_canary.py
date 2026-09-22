"""L3 random-walk canary (spec: Deliberate leakage tests).

200 simulated random walks (T = 150, m = 12), each through a full backtest. On a random
walk nothing can beat naive, so any model that "beats naive with DM-HLN p < 0.05 at a
decision horizon" is a false positive. Holm over every (model, decision horizon) of a
series controls the family-wise rate at 5%, so under the null the share of series with a
hit is at most 5%; a share significantly above that signals leakage.

Two tiers:
  slow    200 series, the cheap level models (baselines, SES, Theta): about 2 minutes,
          runs in CI with the other slow tests.
  canary  200 series, every level model (ETS, SARIMA, Theta, combination): hours on one
          core, so it is excluded by default (pyproject addopts) and run nightly or by
          hand with `uv run pytest -m canary`.
"""

from __future__ import annotations

import os

import numpy as np
import pandas as pd
import pytest
from joblib import Parallel, delayed
from scipy.stats import binomtest, ttest_1samp

from forecasting.evaluation.scoring import score
from tests.acceptance import LEVEL_MODELS_CHEAP, LEVEL_MODELS_FULL, backtest_synthetic, beats_naive
from tests.synthetic import random_walk

N_SERIES = 200
T = 150
ALPHA = 0.05
MCS_ALPHA = 0.10
N_JOBS = max(1, min(4, os.cpu_count() or 1))


def one_series(seed: int, models) -> dict:
    """One random walk: the L3 hit flag, and A3's MCS statistic (the share of horizons
    at which naive is left out of the Model Confidence Set)."""
    run = backtest_synthetic(random_walk(n=T, seed=seed), models=models, uid=f"rw{seed}")
    table = beats_naive(run, ALPHA)
    point = score(run.frame, run.manifest, skill_reps=50).point
    naive = point[point["model"] == "naive"].sort_values("h")
    return {
        "seed": seed,
        "hit": bool(table["hit"].any()),
        "n_tests": len(table),
        "failed": int((run.frame["status"] == "failed").sum()),
        "naive_excluded": float((~naive["mcs_in"].astype(bool)).mean()),
        "naive_excluded_h1": bool(not naive["mcs_in"].iloc[0]),
    }


def canary(models, n_series: int = N_SERIES, first_seed: int = 1000) -> pd.DataFrame:
    rows = Parallel(n_jobs=N_JOBS, backend="loky" if N_JOBS > 1 else "sequential")(
        delayed(one_series)(seed, models) for seed in range(first_seed, first_seed + n_series)
    )
    return pd.DataFrame(rows)


def check(result: pd.DataFrame) -> None:
    """L3: the share of series with a hit is not significantly above alpha. A3 (random
    walk): naive leaves the MCS at no more than the MCS level (10%) of horizons on
    average (one-sided t-test over series), and at h = 1 in no more than 10% of series."""
    hits = int(result["hit"].sum())
    n = len(result)
    p = binomtest(hits, n, ALPHA, alternative="greater").pvalue
    excl = result["naive_excluded"].to_numpy()
    t_p = ttest_1samp(excl, MCS_ALPHA, alternative="greater").pvalue
    h1 = int(result["naive_excluded_h1"].sum())
    p_h1 = binomtest(h1, n, MCS_ALPHA, alternative="greater").pvalue
    print(
        f"L3: {hits} of {n} series ({hits / n:.1%}) beat naive at Holm-adjusted p < {ALPHA}; "
        f"binomial p (share > {ALPHA}) = {p:.3f}. Naive out of the MCS at {excl.mean():.1%} of "
        f"horizons (t-test p vs {MCS_ALPHA} = {t_p:.3f}), at h = 1 in {h1} series "
        f"(p = {p_h1:.3f}); failed rows {int(result['failed'].sum())}"
    )
    assert result["n_tests"].gt(0).all()
    assert p >= ALPHA, f"{hits} of {n} random walks were 'beaten': leakage or a broken test"
    assert t_p >= ALPHA, f"naive left the MCS at {excl.mean():.1%} of horizons"
    assert p_h1 >= ALPHA, f"naive left the MCS at h = 1 in {h1} of {n} series"


@pytest.mark.slow
def test_L3_random_walk_canary_cheap_models():
    check(canary(LEVEL_MODELS_CHEAP))


@pytest.mark.canary
@pytest.mark.slow  # also slow: a command-line -m replaces the addopts -m 'not canary'
def test_L3_random_walk_canary_every_model():
    check(canary(LEVEL_MODELS_FULL))


def test_beats_naive_hits_when_a_model_is_planted_to_win():
    """The statistic can see a win: a copy of naive shifted toward the truth is a hit."""
    run = backtest_synthetic(random_walk(n=T, seed=5), models=("naive", "drift"), uid="rw")
    f = run.frame
    cheat = f[f["model"] == "drift"].copy()
    cheat["model"] = "peek"
    cheat["y_pred"] = cheat["y_true"] + 0.1 * (cheat["y_pred"] - cheat["y_true"])
    run.frame = pd.concat([f, cheat], ignore_index=True)
    table = beats_naive(run, ALPHA)
    peek = table[table["model"] == "peek"].set_index("h")["hit"]
    assert peek[1] and peek.any()  # at h = 12, n / h = 2.5 leaves DM-HLN little power
    assert not table.loc[table["model"] == "drift", "hit"].any()
    assert np.isfinite(table["p_holm"]).all()
