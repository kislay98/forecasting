"""Seeded synthetic processes with known answers (research section 9.2).

Each generator returns the canonical table (unique_id, ds, y) so it can go straight
through validate. Same seed, same series, on every platform (numpy PCG64).

| Process                         | Known answer used later (A3)                          |
| random_walk                     | naive is optimal; any win over naive signals leakage  |
| local_linear_trend              | damped ETS beats naive at medium horizons             |
| seasonal_ar1                    | only seasonal models beat seasonal naive              |
| trend_reversal_variance_jump    | coverage drops after the break (robustness, Phase 4)  |
"""

from __future__ import annotations

import numpy as np
import pandas as pd

LEVEL = 100.0


def to_frame(
    y: np.ndarray, freq: str = "monthly", start: str = "2000-01-01", unique_id: str = "synthetic"
) -> pd.DataFrame:
    """Wrap values in the canonical table with period-start dates for freq."""
    alias = {"monthly": "MS", "quarterly": "QS", "weekly": "W-MON", "trading_days": "B"}[freq]
    ds = pd.date_range(start=start, periods=len(y), freq=alias)
    return pd.DataFrame({"unique_id": unique_id, "ds": ds, "y": np.asarray(y, dtype="float64")})


def random_walk(n: int = 150, sigma: float = 1.0, seed: int = 0) -> np.ndarray:
    """y_t = y_{t-1} + e_t, e_t ~ N(0, sigma^2), y_0 = LEVEL."""
    rng = np.random.default_rng(seed)
    return LEVEL + np.cumsum(rng.normal(0.0, sigma, n))


def local_linear_trend(
    n: int = 240,
    level_sigma: float = 0.5,
    slope_sigma: float = 0.05,
    noise_sigma: float = 1.0,
    slope0: float = 0.3,
    seed: int = 0,
) -> np.ndarray:
    """Level and slope both follow random walks; y is the level plus noise.

    l_t = l_{t-1} + b_{t-1} + u_t,  b_t = b_{t-1} + v_t,  y_t = l_t + e_t.
    """
    rng = np.random.default_rng(seed)
    u = rng.normal(0.0, level_sigma, n)
    v = rng.normal(0.0, slope_sigma, n)
    e = rng.normal(0.0, noise_sigma, n)
    level = np.empty(n)
    slope = np.empty(n)
    lvl, b = LEVEL, slope0
    for t in range(n):
        lvl = lvl + b + u[t]
        b = b + v[t]
        level[t], slope[t] = lvl, b
    return level + e


def seasonal_ar1(
    n: int = 240,
    m: int = 12,
    amplitude: float = 10.0,
    phi: float = 0.5,
    sigma: float = 1.0,
    seed: int = 0,
) -> np.ndarray:
    """Fixed seasonal profile plus AR(1) noise around a constant level.

    y_t = LEVEL + amplitude * sin(2 pi t / m) + x_t,  x_t = phi x_{t-1} + e_t.
    """
    rng = np.random.default_rng(seed)
    e = rng.normal(0.0, sigma, n)
    x = np.empty(n)
    prev = 0.0
    for t in range(n):
        prev = phi * prev + e[t]
        x[t] = prev
    season = amplitude * np.sin(2 * np.pi * np.arange(n) / m)
    return LEVEL + season + x


def trend_reversal_variance_jump(
    n: int = 240,
    break_frac: float = 0.7,
    slope_before: float = 0.5,
    slope_after: float = -0.5,
    sigma: float = 1.0,
    variance_factor: float = 2.5,
    seed: int = 0,
) -> tuple[np.ndarray, int]:
    """Linear trend whose slope flips and whose noise SD jumps at break_at.

    Returns (y, break_at). The noise SD is multiplied by variance_factor after the
    break (research 9.1: slope reversal, variance x 2.5).
    """
    rng = np.random.default_rng(seed)
    break_at = int(n * break_frac)
    t = np.arange(n)
    trend = np.where(
        t < break_at,
        LEVEL + slope_before * t,
        LEVEL + slope_before * break_at + slope_after * (t - break_at),
    )
    sd = np.where(t < break_at, sigma, sigma * variance_factor)
    return trend + rng.normal(0.0, 1.0, n) * sd, break_at


def gbm_prices(n: int = 2000, mu: float = 0.0003, sigma: float = 0.01, seed: int = 0) -> np.ndarray:
    """Positive index-like prices for trading_days tests: exp of a random walk in logs."""
    rng = np.random.default_rng(seed)
    return 10_000.0 * np.exp(np.cumsum(rng.normal(mu, sigma, n)))
