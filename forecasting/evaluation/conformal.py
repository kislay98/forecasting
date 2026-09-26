"""Split conformal calibration on a rolling window (Phase 4).

Phase 3 returned NO-GO on one thing: the 80% interval covered 0.865 at h = 20 where
[0.75, 0.85] was registered. The model was not wrong about the shape of the
distribution, it was wrong about its width, and by a roughly constant amount. That is
the failure conformal prediction exists to fix.

The idea, in one line: watch how far outside its own interval the truth has been
landing lately, and move the interval by exactly that much.

For an interval [lo, hi] the conformity score of a past observation is

    E = max(lo - y, y - hi)

which is positive when the truth fell outside and negative when it fell inside, by the
distance to the nearer bound. Taking the (1 - alpha) empirical quantile of recent
scores gives a width correction Q, and the adjusted interval is [lo - Q, hi + Q]. When
the model has been over-covering, most scores are negative, Q is negative, and the
interval SHRINKS. Nothing here assumes the model was any good; it only assumes recent
errors resemble the next one.

Exchangeability and why the window is rolling. The finite-sample guarantee of split
conformal needs exchangeable scores, which a volatility-clustered return series does
not give. A rolling window of the most recent K eligible origins trades that exact
guarantee for adaptivity, which is the standard move for time series and the honest
description of what this does: it is approximate, and it tracks drift.

Leak-freeness. At origin t, an interval for horizon h is scored only against origins
whose outcome was already observable at t, which means t' + h <= t. This is the whole
correctness condition and it is enforced in one place, `eligible_mask`.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from forecasting.backtest.store import level_tag

MIN_CALIBRATION = 20  # below this the empirical quantile is a couple of points


def eligible_mask(origins: np.ndarray, t: int, h: int) -> np.ndarray:
    """Which past origins had their h-step outcome observable by origin t.

    The single rule the whole module's correctness rests on. An origin at t' produces
    its h-step truth at t' + h, so it can inform a forecast made at t only if
    t' + h <= t. Using t' < t instead would leak up to h periods of the future.
    """
    return (origins + h) <= t


def correction(scores: np.ndarray, level: float) -> float:
    """The conformal width adjustment: the (1 - alpha) quantile of the scores.

    Uses the finite-sample corrected rank (n + 1)(1 - alpha) / n, which is what makes
    split conformal's guarantee exact under exchangeability rather than asymptotic.
    """
    s = np.asarray(scores, dtype=float)
    s = s[np.isfinite(s)]
    n = len(s)
    if n < MIN_CALIBRATION:
        return float("nan")
    q = min(1.0, np.ceil((n + 1) * level) / n)
    return float(np.quantile(s, q, method="higher"))


def conformalise(
    frame: pd.DataFrame,
    levels: tuple[float, ...],
    window: int,
    model_window: str,
    roles: tuple[str, ...] = ("dev", "test"),
) -> pd.DataFrame:
    """Return a copy of `frame` with lo_/hi_ replaced by their conformal versions.

    `window` is K, the number of most recent eligible origins used to calibrate. Rows
    that cannot be calibrated, because fewer than MIN_CALIBRATION eligible origins
    exist yet, keep their original bounds and are marked conformal_n = 0, so they can
    be excluded from scoring rather than silently counted as corrected.
    """
    out = frame.copy()
    out["conformal_n"] = 0
    out["conformal_q"] = np.nan
    usable = out["status"].eq("ok") & ~out["y_true_missing"] & out["window"].eq(model_window)

    for (_model, h), idx in out[usable].groupby(["model", "h"]).groups.items():
        g = out.loc[idx].sort_values("origin_t")
        origins = g["origin_t"].to_numpy()
        y = g["y_true"].to_numpy(dtype=float)
        h = int(h)
        for lv in levels:
            tag = level_tag(lv)
            lo = g[f"lo_{tag}"].to_numpy(dtype=float)
            hi = g[f"hi_{tag}"].to_numpy(dtype=float)
            scores = np.maximum(lo - y, y - hi)
            new_lo = lo.copy()
            new_hi = hi.copy()
            ns = np.zeros(len(g), dtype=int)
            qs = np.full(len(g), np.nan)
            for i, t in enumerate(origins):
                ok = eligible_mask(origins, int(t), h) & np.isfinite(scores)
                if g.iloc[i]["origin_role"] not in roles:
                    continue
                past = scores[ok]
                if len(past) > window:
                    past = past[-window:]
                q = correction(past, lv)
                if not np.isfinite(q):
                    continue
                new_lo[i], new_hi[i] = lo[i] - q, hi[i] + q
                ns[i], qs[i] = len(past), q
            out.loc[g.index, f"lo_{tag}"] = new_lo
            out.loc[g.index, f"hi_{tag}"] = new_hi
            if lv == max(levels):
                out.loc[g.index, "conformal_n"] = ns
                out.loc[g.index, "conformal_q"] = qs
    return out


__all__ = ["MIN_CALIBRATION", "conformalise", "correction", "eligible_mask"]
