"""Significance tests over forecast losses (spec: Metrics and statistics; research 8.4).

| Function              | What it answers                                              |
| dm_hln                | Is model A more accurate than B at horizon h? (DM with the   |
|                       | Harvey-Leybourne-Newbold correction, Student-t, n - 1 df)    |
| holm                  | Holm step-down adjustment for a family of p-values           |
| kupiec                | Is an interval's miss rate the nominal one? (LR, chi2(1))    |
| pesaran_timmermann    | Is directional accuracy better than chance? (PT 1992)        |
| model_confidence_set  | Which models are indistinguishable from the best? (Hansen,   |
|                       | Lunde and Nason 2011, T_max, moving-block bootstrap)         |
| skill_bootstrap       | SS(h) = 1 - relative MAE with a moving-block bootstrap CI    |
| h_star                | Last h before the first h whose lower skill bound is <= 0    |

Inputs must be NaN-free (ValueError otherwise). Bootstrap randomness always comes from
a Generator the caller passes, which the scoring layer derives from the run seed (D7).
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass

import numpy as np
from scipy import stats
from scipy.special import xlogy

from forecasting.evaluation.metrics import as_arrays

# DM-HLN is not run below this effective sample n / h. The L3 canary (M7) measured its
# one-sided size on 200 random walks with 30 origins: 9 to 15% at n / h = 2.5 (nominal
# 5%), 2.5 to 4.5% at n / h = 5. The rectangular kernel with h - 1 lags cannot estimate
# the long-run variance from 2.5 effective observations.
MIN_N_EFF_DM = 5


def dm_testable(n: int, h: int) -> bool:
    return n >= MIN_N_EFF_DM * h


def child_rng(seed: int, *labels: object) -> np.random.Generator:
    """D7: one Generator per labelled statistic, independent of the order of computation."""
    digest = hashlib.sha256("|".join(map(str, labels)).encode()).digest()
    key = tuple(int.from_bytes(digest[i : i + 4], "little") for i in range(0, 16, 4))
    return np.random.default_rng(np.random.SeedSequence(seed, spawn_key=key))


# ---------------------------------------------------------------- Diebold-Mariano


@dataclass(frozen=True)
class DMResult:
    stat: float  # DM* (HLN-corrected)
    p_value: float  # two-sided
    p_better: float  # one-sided, alternative: A has the lower expected loss
    n: int
    h: int
    lags: int  # autocovariance lags used (h - 1, or 0 after the fallback)


def dm_hln(loss_a, loss_b, h: int) -> DMResult:
    """Diebold-Mariano on d_t = loss_a - loss_b with the HLN (1997) correction.

    DM = mean(d) / sqrt((g0 + 2 sum_{k=1}^{h-1} g_k) / n), g_k the lag-k autocovariance
    with divisor n; DM* = DM sqrt((n + 1 - 2h + h(h - 1) / n) / n), against t(n - 1).
    If the long-run variance estimate is not positive, the test is recomputed with
    h = 1, as R's forecast::dm.test does.
    """
    a, b = as_arrays(loss_a, loss_b, names=("loss_a", "loss_b"))
    if a.ndim != 1:
        raise ValueError("losses must be 1-D")
    n = a.size
    if h < 1:
        raise ValueError("h must be >= 1")
    if n <= h:
        raise ValueError(f"DM-HLN needs n > h (n = {n}, h = {h})")
    d = a - b
    dbar = float(d.mean())
    dc = d - dbar
    gam = [float(dc @ dc) / n] + [float(dc[k:] @ dc[:-k]) / n for k in range(1, h)]
    var = (gam[0] + 2 * sum(gam[1:])) / n
    lags = h - 1
    if var <= 0 and h > 1:
        return dm_hln(a, b, 1)
    if var <= 0:  # every d_t equal: no variation to test against
        stat = 0.0 if dbar == 0 else math.copysign(math.inf, dbar)
    else:
        k = math.sqrt((n + 1 - 2 * h + h * (h - 1) / n) / n)
        stat = dbar / math.sqrt(var) * k
    df = n - 1
    return DMResult(
        stat=stat,
        p_value=float(min(1.0, 2 * stats.t.sf(abs(stat), df))),
        p_better=float(stats.t.cdf(stat, df)),
        n=n,
        h=h,
        lags=lags,
    )


# ---------------------------------------------------------------- Holm


def holm(pvalues) -> np.ndarray:
    """Holm (1979) step-down adjusted p-values, in the input order."""
    (p,) = as_arrays(pvalues, names=("pvalues",))
    if p.ndim != 1:
        raise ValueError("pvalues must be 1-D")
    if ((p < 0) | (p > 1)).any():
        raise ValueError("p-values must be in [0, 1]")
    k = p.size
    order = np.argsort(p, kind="stable")
    adj = np.maximum.accumulate((k - np.arange(k)) * p[order])
    out = np.empty(k)
    out[order] = np.minimum(adj, 1.0)
    return out


# ---------------------------------------------------------------- Kupiec


@dataclass(frozen=True)
class KupiecResult:
    lr: float
    p_value: float
    miss_rate: float


def kupiec(misses: float, n: float, p: float) -> KupiecResult:
    """Kupiec (1995) unconditional coverage LR for `misses` in `n` at nominal miss rate p.

    LR = -2 ln[(1 - p)^(n - x) p^x / ((1 - x/n)^(n - x) (x/n)^x)] ~ chi2(1). Counts may
    be fractional, which is how an effective sample (n / h, x / h) is passed.
    """
    if not 0 < p < 1:
        raise ValueError("p must be in (0, 1)")
    if not (math.isfinite(misses) and math.isfinite(n)) or n <= 0 or not 0 <= misses <= n:
        raise ValueError("need 0 <= misses <= n and n > 0")
    x = float(misses)
    ph = x / n
    ll_null = xlogy(n - x, 1 - p) + xlogy(x, p)
    ll_alt = xlogy(n - x, 1 - ph) + xlogy(x, ph)
    lr = max(0.0, float(-2 * (ll_null - ll_alt)))
    return KupiecResult(lr=lr, p_value=float(stats.chi2.sf(lr, 1)), miss_rate=ph)


# ---------------------------------------------------------------- Pesaran-Timmermann


@dataclass(frozen=True)
class PTResult:
    stat: float  # N(0, 1) under independence; NaN if a direction never varies
    p_value: float  # one-sided: better than chance
    hit_rate: float
    expected: float  # hit rate expected by chance, P*


def pesaran_timmermann(y, y_hat) -> PTResult:
    """Pesaran and Timmermann (1992) test of directional accuracy against chance.

    Direction is value > 0. With P the hit rate, py = mean(y > 0), px = mean(y_hat > 0):
    P* = py px + (1 - py)(1 - px),
    V(P) = P*(1 - P*) / n,
    V(P*) = (2py - 1)^2 px(1 - px) / n + (2px - 1)^2 py(1 - py) / n
            + 4 py px (1 - py)(1 - px) / n^2,
    S = (P - P*) / sqrt(V(P) - V(P*)) ~ N(0, 1). The statistic is undefined (NaN) when
    the forecast or the actual never changes direction, e.g. the zero-return forecast.
    """
    y, y_hat = as_arrays(y, y_hat, names=("y", "y_hat"))
    n = y.size
    uy, ux = y > 0, y_hat > 0
    P = float(np.mean(uy == ux))
    py, px = float(uy.mean()), float(ux.mean())
    ps = py * px + (1 - py) * (1 - px)
    v_p = ps * (1 - ps) / n
    v_ps = (
        (2 * py - 1) ** 2 * px * (1 - px) / n
        + (2 * px - 1) ** 2 * py * (1 - py) / n
        + 4 * py * px * (1 - py) * (1 - px) / n**2
    )
    var = v_p - v_ps
    if var <= 0 or px in (0.0, 1.0) or py in (0.0, 1.0):
        return PTResult(math.nan, math.nan, P, ps)
    s = (P - ps) / math.sqrt(var)
    return PTResult(s, float(stats.norm.sf(s)), P, ps)


# ---------------------------------------------------------------- bootstrap


def moving_block_indices(n: int, block: int, reps: int, rng: np.random.Generator) -> np.ndarray:
    """(reps, n) indices of a moving-block bootstrap (Kunsch 1989), not circular.

    ceil(n / block) blocks per replicate, starts uniform on 0 .. n - block, truncated
    to n. The same scheme as arch's MovingBlockBootstrap. block is capped at n.
    """
    if n < 1 or block < 1 or reps < 1:
        raise ValueError("n, block and reps must be >= 1")
    b = min(block, n)
    nb = -(-n // b)
    starts = rng.integers(0, n - b + 1, size=(reps, nb))
    idx = (starts[:, :, None] + np.arange(b)).reshape(reps, nb * b)
    return idx[:, :n]


# ---------------------------------------------------------------- Model Confidence Set


@dataclass(frozen=True)
class MCSResult:
    p_values: np.ndarray  # MCS p-value per model, input order
    included: np.ndarray  # bool: p-value >= alpha
    eliminated: tuple[int, ...]  # model indices in elimination order (survivor last)
    alpha: float


def model_confidence_set(
    losses,
    alpha: float = 0.10,
    reps: int = 1000,
    block: int = 1,
    rng: np.random.Generator | None = None,
    indices: np.ndarray | None = None,
) -> MCSResult:
    """Hansen, Lunde and Nason (2011) MCS with the T_max statistic.

    losses: (n, M), one row per origin. At each step, for the models still in the set,
    d_i = mean loss of i minus the mean over the set, t_i = d_i / sd*(d_i) with sd* from
    the bootstrap, T_max = max t_i, and the p-value is the share of bootstrap
    T*_max = max_i (d*_i - d_i) / sd*(d_i) above T_max. The model with the largest t_i
    is removed; a model's MCS p-value is the running maximum of the step p-values, and
    the last survivor gets 1. Tied models leave together, as in arch; if the tie covers
    every model left (identical forecasts, such as naive and seasonal naive at h = m),
    they all survive with p-value 1 instead of leaving an empty set. Indices come from `indices` if given (reps, n), else a
    moving-block bootstrap with `block` and `rng`.
    """
    (L,) = as_arrays(losses, names=("losses",))
    if L.ndim != 2 or L.shape[1] < 1:
        raise ValueError("losses must be (n, M)")
    if not 0 < alpha < 1:
        raise ValueError("alpha must be in (0, 1)")
    n, M = L.shape
    if indices is None:
        if rng is None:
            raise ValueError("pass rng (seeded from the run seed) or indices")
        indices = moving_block_indices(n, block, reps, rng)
    indices = np.asarray(indices)
    if indices.ndim != 2 or indices.shape[1] != n:
        raise ValueError("indices must be (reps, n)")
    means = L.mean(axis=0)  # (M,)
    boot = L[indices].mean(axis=1)  # (reps, M)
    alive = np.ones(M, dtype=bool)
    order: list[int] = []
    step_p: list[float] = []
    while alive.sum() > 1:
        ids = np.flatnonzero(alive)
        d = means[ids] - means[ids].mean()
        dstar = boot[:, ids] - boot[:, ids].mean(axis=1, keepdims=True)
        sd = np.sqrt(((dstar - d) ** 2).mean(axis=0))
        t = np.divide(d, sd, out=np.zeros_like(d), where=sd > 0)
        tstar = np.divide(dstar - d, sd, out=np.zeros_like(dstar), where=sd > 0)
        tmax = t.max()
        p = float((tstar.max(axis=1) > tmax).mean())
        worst = ids[np.flatnonzero(t == tmax)]  # ties leave together
        if len(worst) == len(ids):  # every model left is tied (e.g. identical forecasts)
            break
        for i in worst:
            order.append(int(i))
            step_p.append(p)
        alive[worst] = False
    for i in np.flatnonzero(alive):
        order.append(int(i))
        step_p.append(1.0)
    pv = np.empty(M)
    pv[order] = np.maximum.accumulate(step_p)
    return MCSResult(p_values=pv, included=pv >= alpha, eliminated=tuple(order), alpha=alpha)


# ---------------------------------------------------------------- skill and h*


@dataclass(frozen=True)
class SkillResult:
    skill: float  # 1 - MAE(model) / MAE(reference)
    lo: float
    hi: float
    n: int
    block: int


def skill_bootstrap(
    abs_err_model,
    abs_err_ref,
    h: int,
    rng: np.random.Generator,
    reps: int = 2000,
    conf: float = 0.95,
) -> SkillResult:
    """SS(h) = 1 - relative MAE with a percentile CI from a moving-block bootstrap over
    origins, block length h (spec). Rows are paired: row i of both inputs is origin i."""
    a, r = as_arrays(abs_err_model, abs_err_ref, names=("abs_err_model", "abs_err_ref"))
    if a.ndim != 1:
        raise ValueError("errors must be 1-D")
    if (a < 0).any() or (r < 0).any():
        raise ValueError("absolute errors must be >= 0")
    if r.sum() == 0:
        raise ValueError("reference MAE is 0, skill undefined")
    idx = moving_block_indices(a.size, h, reps, rng)
    ref_b = r[idx].mean(axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        ss_b = 1 - a[idx].mean(axis=1) / ref_b
    ss_b = np.where(ref_b > 0, ss_b, -np.inf)  # a replicate with a perfect reference
    q = (1 - conf) / 2
    lo, hi = np.quantile(ss_b, [q, 1 - q])
    return SkillResult(
        skill=float(1 - a.mean() / r.mean()),
        lo=float(lo),
        hi=float(hi),
        n=a.size,
        block=min(h, a.size),
    )


def h_star(horizons, lower) -> int:
    """Last h before the first h whose lower skill bound is <= 0 (spec: Metrics).

    horizons are sorted ascending; a NaN bound (no data at that h) counts as not shown
    to have skill. 0 means no horizon is forecastable; max(horizons) means all are.
    """
    hs = np.asarray(horizons)
    lb = np.asarray(lower, dtype="float64")
    if hs.shape != lb.shape or hs.ndim != 1 or hs.size == 0:
        raise ValueError("horizons and lower must be equal-length, non-empty 1-D arrays")
    if (np.diff(hs) <= 0).any():
        raise ValueError("horizons must be strictly increasing")
    fail = ~(lb > 0)  # NaN counts as a failure
    if not fail.any():
        return int(hs[-1])
    first = int(np.argmax(fail))
    return 0 if first == 0 else int(hs[first - 1])
