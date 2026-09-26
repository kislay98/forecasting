"""Threshold-exceedance probabilities from the stored quantile grid (OP-4).

Section 8 of the original research prompt asked the risk output to include the
probability of exceeding and of falling below a threshold. The engine summarises the
simulated paths into a quantile grid and discards them, and the store's schema is part
of the identity every recorded `content_hash` rests on (P4-8), so a new column is not an
option mid-study. What is left is the grid itself, and this module reads the answer off
it.

The grid. Levels (0.5, 0.8, 0.9, 0.95, 0.99) give ten quantiles of the forecast
distribution, at probabilities 0.005, 0.025, 0.05, 0.1, 0.25, 0.75, 0.9, 0.95, 0.975
and 0.995. The mean is stored too but it is not a quantile and is not used.

Between grid points. The CDF is interpolated linearly in Normal-score space: between two
stored quantiles, the probit of the probability is taken to be linear in the value. That
is monotone by construction, it is exact for any Normal distribution (whose probit CDF
is a straight line), and in the tails it is much closer than interpolating the
probability itself, which treats the density as flat between the 0.5% and 2.5% points.
`method="linear"` is kept for comparison. Either way the number is an interpolation,
not a simulated frequency, and its measured error is in docs/threshold_probabilities.md.

Beyond the grid. A threshold further out than the 0.5% or 99.5% quantile cannot be
answered. The honest output is a refusal: the state is `outside_grid` and the
probability is NaN. What the grid does say is a bound, P < 0.005 or P > 0.995, and that
is returned as the bracket [p_lo, p_hi] rather than as a point.

Crossed grids. A grid whose quantiles decrease somewhere is not a distribution. The raw
engine output never crosses, because every level is a quantile of the same paths, but
the conformal layer (P4) corrects each level separately and can cross them. By default
such a row is refused with state `crossed_grid`; `rearrange=True` sorts the values first,
which is the standard repair for crossed quantile estimates and never moves a quantile
further from the truth (Chernozhukov, Fernandez-Val and Galichon, 2010).

The loss scale. A threshold L is a loss as a fraction of the position, as in
`risk.simple_loss`: L = 0.05 is a 5% loss and L = -0.05 a 5% gain. The stored quantity
is a log return y, and loss > L exactly when y < log(1 - L), so

    P(loss > L) = F(log(1 - L))

and P(loss < L) is its complement: the distribution is continuous, so "exceeding" and
"falling below" a threshold are one number and its complement. The column that matters
is `p_beyond`, the probability of landing beyond the threshold, away from no change:
P(loss > L) for a loss threshold, and P(loss < L), a gain larger than |L|, for a gain
threshold. That is the number anyone asking about a threshold means.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

from forecasting.backtest.store import level_tag

OK = "ok"
OUTSIDE = "outside_grid"
CROSSED = "crossed_grid"
NO_GRID = "no_grid"
STATES = (OK, OUTSIDE, CROSSED, NO_GRID)
METHODS = ("probit", "linear")

KEYS = [
    "unique_id",
    "model",
    "window",
    "origin_t",
    "origin_period",
    "origin_role",
    "h",
    "y_true",
    "y_true_missing",
]


def grid_probabilities(levels) -> np.ndarray:
    """The cumulative probabilities the stored quantiles sit at, ascending."""
    lv = sorted(float(v) for v in levels)
    # Rounded so that a 99% level gives 0.005 rather than 0.0050000000000000044: the
    # bounds a refusal reports are these numbers, and they should print as themselves.
    return np.round([(1 - v) / 2 for v in reversed(lv)] + [(1 + v) / 2 for v in lv], 12)


def grid_columns(levels) -> list[str]:
    """Store columns holding the quantiles, in the order of `grid_probabilities`."""
    lv = sorted(float(v) for v in levels)
    return [f"lo_{level_tag(v)}" for v in reversed(lv)] + [f"hi_{level_tag(v)}" for v in lv]


def loss_to_log_return(loss) -> np.ndarray:
    """The log return at which the loss equals `loss`: the inverse of simple_loss."""
    loss = np.asarray(loss, dtype=float)
    if np.any(loss >= 1):
        raise ValueError(
            "a loss threshold must be below 1: a position cannot lose more than itself"
        )
    return np.log1p(-loss)


def cdf_from_grid(
    probs,
    q,
    x,
    method: str = "probit",
    rearrange: bool = False,
) -> tuple[np.ndarray, np.ndarray]:
    """F(x) for each row of a quantile grid, and the state of each answer.

    `probs` is the ascending (K,) vector of grid probabilities, `q` an (n, K) array of
    quantiles, `x` one value per row or a scalar. Returns (F, state): F is NaN wherever
    state is not `ok`.
    """
    if method not in METHODS:
        raise ValueError(f"method must be one of {METHODS}, not {method!r}")
    probs = np.asarray(probs, dtype=float)
    q = np.atleast_2d(np.asarray(q, dtype=float)).copy()
    n, k = q.shape
    if k != len(probs) or k < 2:
        raise ValueError(f"grid has {k} columns for {len(probs)} probabilities")
    x = np.broadcast_to(np.asarray(x, dtype=float), (n,))

    state = np.full(n, OK, dtype=object)
    finite = np.isfinite(q).all(axis=1)
    state[~finite | ~np.isfinite(x)] = NO_GRID
    crossed = finite & (np.diff(q, axis=1) < 0).any(axis=1)
    if rearrange:
        q[crossed] = np.sort(q[crossed], axis=1)
    else:
        state[crossed] = CROSSED
    live = state == OK
    outside = live & ((x < q[:, 0]) | (x > q[:, -1]))
    state[outside] = OUTSIDE
    live &= ~outside

    F = np.full(n, np.nan)
    if live.any():
        ql, xl = q[live], x[live]
        # Index of the segment [q_i, q_i+1] holding x; ties resolve to the upper side,
        # so x equal to a stored quantile gets exactly that quantile's probability.
        i = np.clip((ql <= xl[:, None]).sum(axis=1) - 1, 0, k - 2)
        rows = np.arange(len(xl))
        a, b = ql[rows, i], ql[rows, i + 1]
        width = b - a
        with np.errstate(invalid="ignore", divide="ignore"):
            t = np.where(width > 0, (xl - a) / width, 1.0)
        t = np.clip(t, 0.0, 1.0)
        if method == "probit":
            z = stats.norm.ppf(probs)
            F[live] = stats.norm.cdf(z[i] + t * (z[i + 1] - z[i]))
        else:
            F[live] = probs[i] + t * (probs[i + 1] - probs[i])
    return F, state


def threshold_probabilities(
    frame: pd.DataFrame,
    levels,
    thresholds,
    method: str = "probit",
    rearrange: bool = False,
) -> pd.DataFrame:
    """P(loss beyond L) for every forecast row and every loss threshold L.

    One output row per (input row, threshold), carrying the frame's keys and:

    - `p_exceed`, P(loss > L), NaN unless state is `ok`; P(loss < L) is 1 - p_exceed
    - `p_beyond`, the probability of landing beyond L away from no change: p_exceed for
      a loss threshold (L >= 0), 1 - p_exceed for a gain threshold (L < 0)
    - `p_lo`, `p_hi`, the bracket the grid supports for p_beyond: both equal to it when
      answered, [0, p_min] or [1 - p_min, 1] when L lies beyond the grid, NaN otherwise
    - `state`, one of ok, outside_grid, crossed_grid, no_grid
    - `realised`, 1.0 if the realised outcome landed beyond L, 0.0 if not, NaN where
      the truth is missing

    Only rows with status ok carry a grid. Failed rows are dropped, as everywhere else.
    """
    thresholds = [float(v) for v in np.atleast_1d(thresholds)]
    if not thresholds:
        raise ValueError("at least one threshold is needed")
    probs = grid_probabilities(levels)
    cols = grid_columns(levels)
    rows = frame[frame["status"] == "ok"]
    keys = [c for c in KEYS if c in rows.columns]
    q = rows[cols].to_numpy(dtype=float)
    y = rows["y_true"].to_numpy(dtype=float)
    missing = ~np.isfinite(y)
    if "y_true_missing" in rows:
        missing |= rows["y_true_missing"].to_numpy(dtype=bool)
    out = []
    for L in thresholds:
        r = float(loss_to_log_return(L))
        F, state = cdf_from_grid(probs, q, r, method=method, rearrange=rearrange)
        # P(loss > L) = P(y < r) = F(r). Off the grid, which end the threshold fell off
        # decides the bracket: past the far end of its own tail the event is rarer than
        # the outermost quantile, past the other end it is near certain. The grid is
        # symmetric by construction (lo_ and hi_ in pairs), so both tails are probs[0].
        tail = float(probs[0])
        off = state == OUTSIDE
        low = off & (r < q[:, 0])
        high = off & ~low
        if L >= 0:
            p_beyond, rare, certain = F, low, high
            beyond = y < r
        else:
            p_beyond, rare, certain = 1.0 - F, high, low
            beyond = y > r
        p_lo, p_hi = p_beyond.copy(), p_beyond.copy()
        p_lo[rare], p_hi[rare] = 0.0, tail
        p_lo[certain], p_hi[certain] = 1.0 - tail, 1.0
        block = rows[keys].reset_index(drop=True).copy()
        block["loss_threshold"] = L
        block["p_exceed"] = F
        block["p_beyond"] = p_beyond
        block["p_lo"] = p_lo
        block["p_hi"] = p_hi
        block["state"] = state.astype(str)
        block["realised"] = np.where(missing, np.nan, beyond.astype(float))
        out.append(block)
    return pd.concat(out, ignore_index=True)


def summarise(probs: pd.DataFrame, role: str = "test", window: str | None = None) -> pd.DataFrame:
    """Per model, horizon and threshold: how often the grid could answer, and how the
    answered probabilities compare with what happened on the same origins.

    `p_mean` and `realised` are averaged over the same answered rows, so they are a like
    for like comparison: whether an origin is answered depends only on its forecast, not
    on its outcome, so a calibrated model has p_mean close to realised. `outside_hits`
    counts refused origins on the rare side, where the grid only says P < p_min, whose
    outcome landed beyond the threshold anyway.
    """
    d = probs[probs["origin_role"] == role]
    if window is not None:
        d = d[d["window"] == window]
    d = d[d["realised"].notna()]
    out = []
    for (model, h, L), g in d.groupby(["model", "h", "loss_threshold"], sort=True):
        ok = g["state"] == OK
        off = g["state"] == OUTSIDE
        rare = off & (g["p_lo"] == 0.0)
        out.append(
            {
                "model": str(model),
                "h": int(h),
                "loss_threshold": float(L),
                "n": len(g),
                "n_answered": int(ok.sum()),
                "n_outside": int(off.sum()),
                "n_crossed": int((g["state"] == CROSSED).sum()),
                "p_mean": float(g.loc[ok, "p_beyond"].mean()) if ok.any() else float("nan"),
                "realised": float(g.loc[ok, "realised"].mean()) if ok.any() else float("nan"),
                "outside_hits": int(g.loc[rare, "realised"].sum()),
            }
        )
    return pd.DataFrame(out)


__all__ = [
    "CROSSED",
    "METHODS",
    "NO_GRID",
    "OK",
    "OUTSIDE",
    "STATES",
    "cdf_from_grid",
    "grid_columns",
    "grid_probabilities",
    "loss_to_log_return",
    "summarise",
    "threshold_probabilities",
]
