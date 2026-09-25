"""Calibration statistics for Phase 2, where the interval is the product.

Phase 1 scored accuracy: MAE, MASE, relative MAE, DM-HLN. None of that is the
question here. Phase 1 already established that the conditional mean of Nifty
returns is not forecastable, so a Phase 2 model is judged on whether its stated
uncertainty is honest.

What "honest" decomposes into, and the statistic for each:

  right on average   the nominal 80% interval contains 80% of actuals
                     -> coverage, and tests.kupiec for whether the gap is noise
  right in sequence  the misses do not arrive in clusters, which is what a model
                     with the wrong conditional variance looks like
                     -> christoffersen_independence
  right in shape     the whole predictive distribution, not two of its quantiles
                     -> pit_bins, and pit_uniformity
  sharp              among honest models, prefer the narrowest
                     -> pinball, crps_grid

Kupiec and independence answer different questions and a model can pass one while
failing the other badly. A flat unconditional interval on a series with volatility
clustering is the standard example: it covers 80% over the whole sample and misses
five times in a week during a crash.

Overlap: at horizon h, forecasts made at origins closer together than h describe
overlapping windows and their misses are mechanically dependent. Kupiec is given an
effective sample of n / h, as in Phase 1. The independence test cannot be repaired
that way, because it reads the sequence itself, so it only runs where the origin
step is at least h (independence_testable).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy import stats

from forecasting.evaluation.metrics import as_arrays

MIN_HITS_IND = 30  # below this the 2x2 transition table is too sparse to read


def independence_testable(h: int, origin_step: int) -> bool:
    """True when consecutive origins' h-step windows do not overlap."""
    return origin_step >= h


# ---------------------------------------------------------------- PIT


def pit(y, quantiles, taus) -> np.ndarray:
    """Probability integral transform on a quantile grid.

    quantiles is (n, k), taus is (k,) increasing. Returns u in [0, 1]: where each
    actual falls in its own predictive distribution, by linear interpolation between
    grid points. Outside the grid the value is clipped to the end tau, so a run of
    exactly tau_min or tau_max means the tails are being missed and the histogram
    will show it as a spike at the edge rather than hiding it.

    A calibrated model gives u uniform on (0, 1). Any other shape is diagnostic: a
    hump in the middle means intervals too wide, mass at both edges means too narrow,
    a tilt means bias.
    """
    y = np.asarray(y, dtype=float)
    q = np.asarray(quantiles, dtype=float)
    t = np.asarray(taus, dtype=float)
    if q.ndim != 2 or q.shape[0] != len(y) or q.shape[1] != len(t):
        raise ValueError("quantiles must be (n, k) matching y and taus")
    if not np.all(np.diff(t) > 0):
        raise ValueError("taus must be strictly increasing")
    out = np.empty(len(y))
    for i in range(len(y)):
        row = q[i]
        if not np.isfinite(row).all() or not np.isfinite(y[i]):
            out[i] = np.nan
            continue
        # A predictive quantile function must be non-decreasing; a model that breaks
        # that is broken, not merely miscalibrated.
        if np.any(np.diff(row) < -1e-12):
            raise ValueError("predictive quantiles are not monotone")
        out[i] = float(np.interp(y[i], row, t))
    return out


def pit_bins(y, quantiles, taus) -> tuple[np.ndarray, np.ndarray]:
    """(observed counts, expected probabilities) for the bins the quantile grid defines.

    With k grid quantiles there are k + 1 bins: below the lowest, between each
    consecutive pair, and above the highest. A calibrated model puts tau_0 of its
    actuals in the first, tau_{j+1} - tau_j in each middle one, and 1 - tau_k in the
    last. This uses only what the grid actually states, so unlike an interpolated PIT
    histogram it does not charge the model for the curvature between grid points.
    """
    y = np.asarray(y, dtype=float)
    q = np.asarray(quantiles, dtype=float)
    t = np.asarray(taus, dtype=float)
    if q.ndim != 2 or q.shape[0] != len(y) or q.shape[1] != len(t):
        raise ValueError("quantiles must be (n, k) matching y and taus")
    if not np.all(np.diff(t) > 0):
        raise ValueError("taus must be strictly increasing")
    ok = np.isfinite(y) & np.isfinite(q).all(axis=1)
    y, q = y[ok], q[ok]
    idx = np.array([int(np.searchsorted(q[i], y[i], side="right")) for i in range(len(y))])
    counts = np.bincount(idx, minlength=len(t) + 1).astype(float)
    expected = np.diff(np.concatenate(([0.0], t, [1.0])))
    return counts, expected


def pit_uniformity(y, quantiles, taus) -> tuple[float, float]:
    """Pearson chi-square of the grid bin counts against their nominal probabilities.

    Returns (stat, p) with k degrees of freedom for k + 1 bins. The counts are not
    independent when horizons overlap, so read the p-value as a summary of the
    histogram rather than as a test with a defended size.
    """
    counts, expected = pit_bins(y, quantiles, taus)
    n = counts.sum()
    # Pearson needs about 5 expected in the smallest bin. Compared as a count rather
    # than as n < 5 / min(expected), which makes the boundary a floating-point coin
    # flip: at ten levels the smallest bin is 0.025 and 5 / 0.025 evaluates to
    # 200.00000000000026, so a sample of exactly 200 was silently returning nan.
    if n * float(np.min(expected)) < 5.0 - 1e-9:
        return float("nan"), float("nan")
    exp = expected * n
    stat = float(np.sum((counts - exp) ** 2 / exp))
    return stat, float(stats.chi2.sf(stat, len(counts) - 1))


# ---------------------------------------------------------------- Christoffersen


@dataclass(frozen=True)
class ChristoffersenResult:
    lr_ind: float
    p_ind: float
    lr_cc: float
    p_cc: float
    n00: int
    n01: int
    n10: int
    n11: int

    @property
    def clustered(self) -> bool:
        """P(miss | miss yesterday) above P(miss | no miss yesterday)."""
        d0, d1 = self.n00 + self.n01, self.n10 + self.n11
        if d0 == 0 or d1 == 0:
            return False
        return self.n11 / d1 > self.n01 / d0


def christoffersen(hits, p: float) -> ChristoffersenResult:
    """Christoffersen (1998) independence and conditional coverage.

    hits is a 0/1 sequence in origin order: 1 where the actual fell outside the
    interval. p is the nominal miss rate. Independence is a likelihood ratio against
    a first-order Markov chain; conditional coverage adds Kupiec's unconditional
    part, so LR_cc ~ chi2(2).
    """
    x = np.asarray(hits, dtype=float)
    x = x[np.isfinite(x)]
    if len(x) < MIN_HITS_IND:
        nan = float("nan")
        return ChristoffersenResult(nan, nan, nan, nan, 0, 0, 0, 0)
    if not np.isin(x, (0.0, 1.0)).all():
        raise ValueError("hits must be 0 or 1")
    prev, cur = x[:-1], x[1:]
    n00 = int(np.sum((prev == 0) & (cur == 0)))
    n01 = int(np.sum((prev == 0) & (cur == 1)))
    n10 = int(np.sum((prev == 1) & (cur == 0)))
    n11 = int(np.sum((prev == 1) & (cur == 1)))

    n = n00 + n01 + n10 + n11
    pi = (n01 + n11) / n if n else 0.0
    pi0 = n01 / (n00 + n01) if (n00 + n01) else 0.0
    pi1 = n11 / (n10 + n11) if (n10 + n11) else 0.0

    def _ll(n_zero: int, n_one: int, prob: float) -> float:
        a = n_zero * math.log(1 - prob) if n_zero and prob < 1 else 0.0
        b = n_one * math.log(prob) if n_one and prob > 0 else 0.0
        return a + b

    ll_markov = _ll(n00, n01, pi0) + _ll(n10, n11, pi1)
    ll_iid = _ll(n00 + n10, n01 + n11, pi)
    lr_ind = max(0.0, 2.0 * (ll_markov - ll_iid))

    xs = float(np.sum(x))
    ns = float(len(x))
    ph = xs / ns
    ll_null = (ns - xs) * math.log(1 - p) + (xs * math.log(p) if xs else 0.0)
    ll_alt = ((ns - xs) * math.log(1 - ph) if ph < 1 and ns > xs else 0.0) + (
        xs * math.log(ph) if xs else 0.0
    )
    lr_uc = max(0.0, 2.0 * (ll_alt - ll_null))
    lr_cc = lr_uc + lr_ind
    return ChristoffersenResult(
        lr_ind=lr_ind,
        p_ind=float(stats.chi2.sf(lr_ind, 1)),
        lr_cc=lr_cc,
        p_cc=float(stats.chi2.sf(lr_cc, 2)),
        n00=n00,
        n01=n01,
        n10=n10,
        n11=n11,
    )


# ---------------------------------------------------------------- sharpness


def pinball(y, q, tau: float) -> float:
    """Mean quantile loss at level tau. Minimised by the true tau-quantile, which is
    what makes it a proper score for one quantile rather than a description of one."""
    if not 0 < tau < 1:
        raise ValueError("tau must be in (0, 1)")
    y, q = as_arrays(y, q, names=("y", "q"))
    d = y - q
    return float(np.mean(np.where(d >= 0, tau * d, (tau - 1) * d)))


def crps_grid(y, quantiles, taus) -> float:
    """CRPS approximated from a quantile grid, via CRPS = 2 * integral of pinball dtau.

    Trapezoid over the supplied taus only. The grid does not reach 0 or 1, so this
    understates the true CRPS by whatever the tails contribute; it is comparable
    between models scored on the same grid and is not an absolute number.
    """
    y = np.asarray(y, dtype=float)
    q = np.asarray(quantiles, dtype=float)
    t = np.asarray(taus, dtype=float)
    if q.ndim != 2 or q.shape[0] != len(y) or q.shape[1] != len(t):
        raise ValueError("quantiles must be (n, k) matching y and taus")
    losses = np.array([pinball(y, q[:, k], float(t[k])) for k in range(len(t))])
    return float(2.0 * np.trapezoid(losses, t))


__all__ = [
    "ChristoffersenResult",
    "christoffersen",
    "crps_grid",
    "independence_testable",
    "pinball",
    "pit",
    "pit_bins",
    "pit_uniformity",
]
