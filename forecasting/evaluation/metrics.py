"""Point and interval metrics: pure, vectorised functions over arrays (spec: Metrics).

Every function takes equal-shaped, NaN-free float arrays and raises ValueError
otherwise (spec: Behaviour and errors). The error is e = y - y_hat (research 5.7), so a
positive bias means the model forecasts too low.

| Function            | Definition                                               |
| mae, rmse           | mean |e|, sqrt(mean e^2)                                 |
| mase                | mean(|e| / scale); scale is each origin's own in-sample  |
|                     | (seasonal) naive MAE, the store's mase_scale             |
| relative_mae        | MAE(model) / MAE(reference) on the same rows             |
| relative_rmse       | RMSE(model) / RMSE(reference) on the same rows           |
| wape                | sum |e| / sum |y|                                        |
| smape               | 100 mean(2 |e| / (|y| + |y_hat|)), a 0 / 0 term is 0     |
| mape                | 100 mean(|e / y|), only if every y > 0                   |
| bias                | mean e                                                   |
| coverage            | share of y inside [lo, hi]                               |
| coverage_band       | binomial band for coverage at an effective n = n / h     |
| winkler             | mean of (u - l) + (2 / a)(l - y)+ + (2 / a)(y - u)+      |
| directional_accuracy| share of rows where y > 0 and y_hat > 0 agree (returns)  |
| r2_oos              | 1 - SSE(model) / SSE(benchmark) (returns: vs mean return)|
| horizon_buckets     | report buckets derived from m (spec: Horizons)           |
"""

from __future__ import annotations

import math

import numpy as np
from scipy import stats


def as_arrays(*xs, names: tuple[str, ...] | None = None) -> list[np.ndarray]:
    """Float arrays of one shape, non-empty and free of NaN or inf, or ValueError."""
    arrs = [np.asarray(x, dtype="float64") for x in xs]
    names = names or tuple(f"argument {i}" for i in range(len(arrs)))
    shape = arrs[0].shape
    for a, n in zip(arrs, names, strict=True):
        if a.shape != shape:
            raise ValueError(f"shape mismatch: {n} has shape {a.shape}, expected {shape}")
        if not np.isfinite(a).all():
            raise ValueError(f"{n} contains NaN or inf")
    if arrs[0].size == 0:
        raise ValueError("empty input")
    return arrs


def mae(y, y_hat) -> float:
    y, y_hat = as_arrays(y, y_hat, names=("y", "y_hat"))
    return float(np.mean(np.abs(y - y_hat)))


def rmse(y, y_hat) -> float:
    y, y_hat = as_arrays(y, y_hat, names=("y", "y_hat"))
    return float(np.sqrt(np.mean((y - y_hat) ** 2)))


def mase(y, y_hat, scale) -> float:
    """Mean of |e| / scale, where scale comes from each row's own training slice."""
    y, y_hat, scale = as_arrays(y, y_hat, scale, names=("y", "y_hat", "scale"))
    if (scale <= 0).any():
        raise ValueError("scale must be > 0 (a constant training slice has no naive MAE)")
    return float(np.mean(np.abs(y - y_hat) / scale))


def _ratio(num: float, den: float, what: str) -> float:
    if den == 0:
        raise ValueError(f"reference {what} is 0, ratio undefined")
    return num / den


def relative_mae(y, y_hat, y_ref) -> float:
    y, y_hat, y_ref = as_arrays(y, y_hat, y_ref, names=("y", "y_hat", "y_ref"))
    return _ratio(mae(y, y_hat), mae(y, y_ref), "MAE")


def relative_rmse(y, y_hat, y_ref) -> float:
    y, y_hat, y_ref = as_arrays(y, y_hat, y_ref, names=("y", "y_hat", "y_ref"))
    return _ratio(rmse(y, y_hat), rmse(y, y_ref), "RMSE")


def wape(y, y_hat) -> float:
    y, y_hat = as_arrays(y, y_hat, names=("y", "y_hat"))
    return _ratio(float(np.sum(np.abs(y - y_hat))), float(np.sum(np.abs(y))), "sum |y|")


def smape(y, y_hat) -> float:
    y, y_hat = as_arrays(y, y_hat, names=("y", "y_hat"))
    den = np.abs(y) + np.abs(y_hat)
    num = 2 * np.abs(y - y_hat)
    terms = np.divide(num, den, out=np.zeros_like(num), where=den > 0)
    return float(100 * np.mean(terms))


def mape(y, y_hat) -> float:
    y, y_hat = as_arrays(y, y_hat, names=("y", "y_hat"))
    if (y <= 0).any():
        raise ValueError("MAPE needs every actual > 0")
    return float(100 * np.mean(np.abs((y - y_hat) / y)))


def bias(y, y_hat) -> float:
    y, y_hat = as_arrays(y, y_hat, names=("y", "y_hat"))
    return float(np.mean(y - y_hat))


def coverage(y, lo, hi) -> float:
    y, lo, hi = as_arrays(y, lo, hi, names=("y", "lo", "hi"))
    if (lo > hi).any():
        raise ValueError("lo > hi")
    return float(np.mean((lo <= y) & (y <= hi)))


def n_effective(n: int, h: int) -> float:
    """Effective sample of n overlapping h-step errors, about n / h (spec: Horizons)."""
    if n < 1 or h < 1:
        raise ValueError("n and h must be >= 1")
    return n / h


def coverage_band(level: float, n_eff: float, conf: float = 0.95) -> tuple[float, float]:
    """Central binomial band for the observed coverage of a `level` interval.

    The band is the conf-central interval of Binomial(k, level) / k with
    k = max(1, floor(n_eff)): the range of coverage a calibrated interval would show
    in that many independent trials.
    """
    if not 0 < level < 1 or not 0 < conf < 1:
        raise ValueError("level and conf must be in (0, 1)")
    k = max(1, math.floor(n_eff))
    a = (1 - conf) / 2
    lo = stats.binom.ppf(a, k, level) / k
    hi = stats.binom.ppf(1 - a, k, level) / k
    return float(lo), float(hi)


def winkler(y, lo, hi, level: float) -> float:
    """Mean interval (Winkler) score; lower is better. a = 1 - level."""
    y, lo, hi = as_arrays(y, lo, hi, names=("y", "lo", "hi"))
    if (lo > hi).any():
        raise ValueError("lo > hi")
    if not 0 < level < 1:
        raise ValueError("level must be in (0, 1)")
    a = 1 - level
    score = (hi - lo) + (2 / a) * np.maximum(lo - y, 0) + (2 / a) * np.maximum(y - hi, 0)
    return float(np.mean(score))


def mean_width(lo, hi) -> float:
    lo, hi = as_arrays(lo, hi, names=("lo", "hi"))
    return float(np.mean(hi - lo))


# ---------------------------------------------------------------- returns only


def directional_accuracy(y, y_hat) -> float:
    """Share of rows where the direction is right, direction meaning value > 0.

    The Pesaran-Timmermann (1992) convention: 0 counts as 'not up'. A forecast that
    is never positive (zero return) scores the share of non-positive actuals.
    """
    y, y_hat = as_arrays(y, y_hat, names=("y", "y_hat"))
    return float(np.mean((y > 0) == (y_hat > 0)))


def r2_oos(y, y_hat, y_bench) -> float:
    """Out-of-sample R^2 (Campbell and Thompson 2008): 1 - SSE / SSE of the benchmark."""
    y, y_hat, y_bench = as_arrays(y, y_hat, y_bench, names=("y", "y_hat", "y_bench"))
    sse_b = float(np.sum((y - y_bench) ** 2))
    return 1 - _ratio(float(np.sum((y - y_hat) ** 2)), sse_b, "SSE")


# ---------------------------------------------------------------- horizons


BUCKET_NAMES = ("very_short", "short", "medium", "long")


def horizon_buckets(m: int, H: int) -> dict[str, tuple[int, ...]]:
    """Report buckets (spec: Horizons), empty ones omitted, clipped at H.

    very short {1}; short {2 .. ceil(m/4)}; medium {ceil(m/4)+1 .. m}; long {m+1 .. H}.
    For m = 1 the spec fixes {1}, {2..3}, {4..H}; {4..H} lies past m, so it is 'long'.
    """
    if m < 1 or H < 1:
        raise ValueError("m and H must be >= 1")
    if m == 1:
        edges = [(1, 1), (2, 3), (4, 3), (4, H)]
    else:
        q = math.ceil(m / 4)
        edges = [(1, 1), (2, q), (q + 1, m), (m + 1, H)]
    out: dict[str, tuple[int, ...]] = {}
    for name, (a, b) in zip(BUCKET_NAMES, edges, strict=True):
        hs = tuple(range(a, min(b, H) + 1))
        if hs:
            out[name] = hs
    return out


def bucket_of(m: int, H: int) -> dict[int, str]:
    return {h: name for name, hs in horizon_buckets(m, H).items() for h in hs}


def test_horizons(m: int, H: int, decision_horizons: tuple[int, ...] = ()) -> tuple[int, ...]:
    """Where significance tests run: decision horizons, else the last h of each bucket."""
    if decision_horizons:
        return tuple(sorted(set(decision_horizons)))
    return tuple(hs[-1] for hs in horizon_buckets(m, H).values())


test_horizons.__test__ = False  # not a pytest test, despite the name
