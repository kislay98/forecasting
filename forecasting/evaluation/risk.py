"""Value at risk and expected shortfall from a cumulative-loss run (Phase 3).

VaR at level p is a quantile of the h-day loss distribution, so it is already in the
store: the lower bound of the (2p - 1) interval. Expected shortfall is not, because it
is the mean beyond that quantile and a ten-point grid cannot recover it. The engine
computes it from the simulated paths at fold time and stores it as es_<tag>.

Why both are reported. VaR says how far the loss reaches before a breach; it says
nothing about how bad the breach is. Two models with identical breach rates can have
very different losses when they do breach, and the one that matters for a holding
period is the second. ES is also why the Phase 3 target had to become cumulative: a
one-day ES scaled by sqrt(20) is not a 20-day ES when volatility clusters.

Backtests: the breach count goes to Kupiec, as in Phase 2. ES gets its own, because a
correct breach rate tells you nothing about whether the predicted tail mean was right.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from forecasting.backtest.store import level_tag
from forecasting.evaluation.tests import kupiec

BOOTSTRAP_REPS = 5000
MIN_BREACHES = 10  # below this the ES mean is one or two observations wearing a p-value


def simple_loss(log_return) -> np.ndarray:
    """Log return to the loss as a fraction of the position: 1 - exp(r).

    Positive means a loss. A 20-day log return of -0.10 is a 9.5% loss, not 10%, and at
    the sizes a tail quantile reaches the difference stops being cosmetic.
    """
    return 1.0 - np.exp(np.asarray(log_return, dtype=float))


@dataclass(frozen=True)
class ESResult:
    n_breaches: int
    mean_realised: float
    mean_predicted: float
    bias: float  # realised minus predicted, in log-return units; negative is optimistic
    ci_low: float
    ci_high: float
    covers_zero: bool


def es_backtest(y, var, es, reps: int = BOOTSTRAP_REPS, seed: int = 0) -> ESResult:
    """Is the predicted tail mean right, given that the tail was reached?

    Under a correct ES, E[y | y <= VaR] equals the predicted ES, so the mean of
    (realised - predicted) over breaches is zero. The confidence interval is a plain
    bootstrap over breaches rather than an asymptotic one: the breach sample is small by
    construction and its distribution is skewed by construction.

    An interval that excludes zero on the low side means the model understated how bad
    a breach would be, which is the failure mode that matters.
    """
    y = np.asarray(y, dtype=float)
    var = np.asarray(var, dtype=float)
    es = np.asarray(es, dtype=float)
    ok = np.isfinite(y) & np.isfinite(var) & np.isfinite(es)
    breach = ok & (y <= var)
    n = int(breach.sum())
    if n < MIN_BREACHES:
        nan = float("nan")
        return ESResult(n, nan, nan, nan, nan, nan, True)
    d = y[breach] - es[breach]
    rng = np.random.default_rng(seed)
    means = rng.choice(d, size=(reps, n), replace=True).mean(axis=1)
    lo, hi = np.quantile(means, [0.025, 0.975])
    return ESResult(
        n_breaches=n,
        mean_realised=float(y[breach].mean()),
        mean_predicted=float(es[breach].mean()),
        bias=float(d.mean()),
        ci_low=float(lo),
        ci_high=float(hi),
        covers_zero=bool(lo <= 0 <= hi),
    )


def risk_table(
    frame: pd.DataFrame,
    origin_step: int,
    levels: tuple[float, ...],
    window: str,
    role: str = "test",
    seed: int = 0,
) -> pd.DataFrame:
    """One row per (model, h, level): VaR, ES, breaches, and both backtests."""
    rows_in = frame[
        (frame["origin_role"] == role)
        & (frame["status"] == "ok")
        & (~frame["y_true_missing"])
        & (frame["window"] == window)
    ]
    out = []
    for (model, h), g in rows_in.groupby(["model", "h"], sort=True):
        g = g.sort_values("origin_t")
        y = g["y_true"].to_numpy(dtype=float)
        h = int(h)
        ov = max(1.0, h / origin_step)
        for lv in levels:
            tag = level_tag(lv)
            p_tail = (1 - lv) / 2  # the lower tail the VaR sits at
            var = g[f"lo_{tag}"].to_numpy(dtype=float)
            es = g.get(f"es_{tag}")
            es = es.to_numpy(dtype=float) if es is not None else np.full(len(g), np.nan)
            if not np.isfinite(var).any():
                continue
            breaches = float(np.sum(y <= var))
            r = es_backtest(y, var, es, seed=seed)
            out.append(
                {
                    "model": str(model),
                    "h": h,
                    "var_p": round(1 - p_tail, 4),  # a 0.005 tail is a 99.5% VaR
                    "n": len(g),
                    "var_mean_loss": float(np.nanmean(simple_loss(var))),
                    "es_mean_loss": float(np.nanmean(simple_loss(es))),
                    "worst_loss": float(np.nanmax(simple_loss(y))),
                    "breaches": int(breaches),
                    "expected": len(g) * p_tail,
                    "breach_rate": breaches / len(g),
                    "kupiec_p": kupiec(breaches / ov, len(g) / ov, p_tail).p_value,
                    "es_breaches": r.n_breaches,
                    "es_bias": r.bias,
                    "es_ci_low": r.ci_low,
                    "es_ci_high": r.ci_high,
                    "es_ok": r.covers_zero,
                }
            )
    table = pd.DataFrame(out)
    return (
        table if table.empty else table.sort_values(["h", "var_p", "model"]).reset_index(drop=True)
    )


__all__ = ["ESResult", "es_backtest", "risk_table", "simple_loss"]
