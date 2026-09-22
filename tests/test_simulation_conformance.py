"""Simulation conformance (spec: Monte Carlo decision), the one Monte Carlo piece in Phase 1.

Proves the statsmodels path primitive Phase 2 will build on: ETS and SARIMA `simulate()`
with Gaussian errors, N = 10,000 paths, on three synthetic series. Simulated P10 and P90
at each h must match the analytic 80% bounds within 3 Monte Carlo standard errors, and
the path mean must match the point forecast within 3 SE.

Standard errors, with sigma_h the analytic forecast SD at h (from the 80% bounds):
  mean: sigma_h / sqrt(N)
  quantile at p: sqrt(p (1 - p) / N) / phi(z_p) x sigma_h   (the asymptotic SE of a
  sample quantile of a Normal sample)

ETS is restricted to additive-error variants, whose analytic intervals are exact; for
multiplicative errors statsmodels itself simulates the intervals (1,000 paths), so the
comparison would be simulation against a noisier simulation. Writing this test found
that statsmodels' default SARIMAX initialisation breaks down with d = 2 (M7-2).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from scipy.stats import norm

from forecasting.models.statistical import ETSAuto, SARIMAAuto
from tests.synthetic import local_linear_trend, random_walk, seasonal_ar1

N = 10_000
H = 12
LEVEL = 0.8
Z = norm.ppf(0.5 + LEVEL / 2)  # 1.2816
ADDITIVE = [
    ("add", trend, damped, season)
    for trend, damped in ((None, False), ("add", False), ("add", True))
    for season in (None, "add")
]
SERIES = {
    "random_walk": random_walk(n=150, seed=1),
    "seasonal_ar1": seasonal_ar1(n=150, seed=2),
    "local_linear_trend": local_linear_trend(n=150, seed=3),
}


def simulate_paths(model, h: int, n: int, seed: int) -> np.ndarray:
    """(h, n) Gaussian paths from the fitted statsmodels result, anchored at the end
    of the sample. The rng argument is the seed pitfall the spec warns about."""
    sims = model._res.simulate(h, anchor="end", repetitions=n, rng=np.random.default_rng(seed))
    return np.asarray(sims, dtype=float).reshape(h, n)


def z_scores(model, paths: np.ndarray) -> dict[str, np.ndarray]:
    """Deviation of the path mean, P10 and P90 from the analytic values, in SE units."""
    r = model.predict(H, (LEVEL,))
    lo, hi = r.lower[LEVEL], r.upper[LEVEL]
    sigma = (hi - lo) / (2 * Z)
    se_q = np.sqrt(0.1 * 0.9 / N) / norm.pdf(Z) * sigma
    return {
        "mean": (paths.mean(axis=1) - r.mean) / (sigma / np.sqrt(N)),
        "p10": (np.quantile(paths, 0.1, axis=1) - lo) / se_q,
        "p90": (np.quantile(paths, 0.9, axis=1) - hi) / se_q,
    }


@pytest.mark.slow
@pytest.mark.parametrize("series", list(SERIES))
@pytest.mark.parametrize("kind", ["ets", "sarima"])
def test_simulated_paths_match_analytic_forecasts(series, kind):
    y = pd.Series(SERIES[series])
    model = ETSAuto(12, variants=ADDITIVE) if kind == "ets" else SARIMAAuto(12)
    model.fit(y)
    paths = simulate_paths(model, H, N, seed=0)
    assert paths.shape == (H, N) and np.isfinite(paths).all()
    z = z_scores(model, paths)
    for stat, values in z.items():
        assert np.abs(values).max() < 3, (model.variant, stat, np.round(values, 2))


@pytest.mark.slow
def test_simulation_is_reproducible_from_the_seed():
    model = SARIMAAuto(12).fit(pd.Series(SERIES["random_walk"]))
    a = simulate_paths(model, 3, 50, seed=7)
    b = simulate_paths(model, 3, 50, seed=7)
    c = simulate_paths(model, 3, 50, seed=8)
    np.testing.assert_array_equal(a, b)
    assert not np.array_equal(a, c)
