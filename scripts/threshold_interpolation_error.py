"""How wrong is a threshold probability read off the stored quantile grid? (OP-4)

Usage:
    uv run python scripts/threshold_interpolation_error.py

Reproduces the error table in docs/threshold_probabilities.md. Takes about a minute.

The risk report computes P(loss > L) by interpolating between the ten stored quantiles
(forecasting/evaluation/thresholds.py), because the paths are discarded. This script
keeps the paths and compares the two. For each of 20 Phase 4 test origins (every tenth,
expanding window) it fits gjr_garch on the same slice the engine would, simulates the
registered 10,000 paths, builds the grid exactly as the engine does, and then asks both
the grid and the paths for P(loss > L) at thresholds spread evenly in probability across
the grid's range. The grid and the count use the same paths, so the difference is the
interpolation error alone, with no simulation noise in it.

Simulation noise is printed beside it for scale: the binomial standard error of a
frequency estimated from 10,000 paths, which is the error the count itself carries
against the model's true distribution.
"""

import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits

from forecasting.backtest.store import ForecastStore
from forecasting.config import load_config
from forecasting.data.adapters import load_series
from forecasting.data.validate import validate
from forecasting.evaluation.risk import simple_loss
from forecasting.evaluation.thresholds import cdf_from_grid, grid_probabilities
from forecasting.models.variance import GJRGARCH

RUN = "configs/phase4/runs/4a5899ec5e37c363/forecasts.parquet"
N_PATHS = 10_000
HORIZONS = (1, 5, 20)
REPORT_LOSSES = (0.02, 0.05, 0.10)  # the risk report's default loss thresholds
# Probabilities at which to place test thresholds: 400 points spread across the grid's
# range, so the tail segments are sampled as densely as the centre.
P_TEST = np.linspace(0.0051, 0.9949, 400)

cfg = load_config("configs/phase4/phase4.yaml")
scfg = cfg.series[0]
[(series, _)] = validate(load_series(scfg, cfg.base_dir), scfg)
prices = np.asarray(series.y, dtype=float)

frame = ForecastStore.read(RUN).frame()
test = frame[
    (frame["model"] == "gjr_garch")
    & (frame["window"] == "expanding")
    & (frame["origin_role"] == "test")
    & (frame["h"] == 1)
]
origins = np.sort(test["origin_t"].unique())[::10]

probs = grid_probabilities(cfg.levels)
rows, at_report = [], []
with threadpool_limits(limits=1, user_api="blas"):
    for k, t in enumerate(origins):
        r = np.diff(np.log(prices[: int(t) + 1]))
        m = GJRGARCH()
        m.fit(r)
        rng = np.random.default_rng(20260926 + k)
        paths = np.cumsum(m.simulate(max(HORIZONS), N_PATHS, rng), axis=1)
        for h in HORIZONS:
            y = paths[:, h - 1]
            q = np.quantile(y, probs)  # what the engine stores, level by level
            x = np.quantile(y, P_TEST)  # test thresholds, inside the grid by construction
            direct = (y[None, :] < x[:, None]).mean(axis=1)
            for method in ("probit", "linear"):
                F, state = cdf_from_grid(probs, np.tile(q, (len(x), 1)), x, method=method)
                assert (state == "ok").all()
                rows.append(
                    pd.DataFrame(
                        {"origin": int(t), "h": h, "method": method, "p": direct, "err": F - direct}
                    )
                )
            for L in REPORT_LOSSES:
                xr = np.log1p(-L)
                count = float((y < xr).mean())
                for method in ("probit", "linear"):
                    F, state = cdf_from_grid(probs, q[None, :], xr, method=method)
                    at_report.append(
                        {
                            "origin": int(t),
                            "h": h,
                            "loss": L,
                            "method": method,
                            "state": state[0],
                            "count": count,
                            "grid": F[0],
                            "lowest_q_loss": float(simple_loss(q[0])),
                        }
                    )

d = pd.concat(rows, ignore_index=True)
d["region"] = np.select(
    [d["p"] < 0.05, d["p"] > 0.95, (d["p"] > 0.25) & (d["p"] < 0.75)],
    ["lower tail, p < 0.05", "upper tail, p > 0.95", "centre, 0.25 to 0.75"],
    "shoulders",
)
d["abs_err"] = d["err"].abs()
d["rel_err"] = d["abs_err"] / np.minimum(d["p"], 1 - d["p"]).clip(lower=1e-4)
d["mc_se"] = np.sqrt(d["p"] * (1 - d["p"]) / N_PATHS)

pd.set_option("display.width", 200)
print(f"{len(origins)} origins, {len(P_TEST)} thresholds each, horizons {HORIZONS}\n")
summary = (
    d.groupby(["method", "region"])
    .agg(
        mean_abs=("abs_err", "mean"),
        p95_abs=("abs_err", lambda v: v.quantile(0.95)),
        max_abs=("abs_err", "max"),
        mean_rel=("rel_err", "mean"),
        p95_rel=("rel_err", lambda v: v.quantile(0.95)),
        mean_mc_se=("mc_se", "mean"),
    )
    .reset_index()
)
print(summary.to_string(index=False, float_format=lambda v: f"{v:.5f}"))
print()
worst = d.loc[d[d["method"] == "probit"]["rel_err"].idxmax()]
print(f"largest probit relative error: {worst.to_dict()}")
print()
print(
    d.groupby(["method", "h"])
    .agg(max_abs=("abs_err", "max"), mean_abs=("abs_err", "mean"))
    .reset_index()
    .to_string(index=False, float_format=lambda v: f"{v:.5f}")
)
print()
a = pd.DataFrame(at_report)
a["abs_err"] = (a["grid"] - a["count"]).abs()
print("At the report's default thresholds (answered origins only):")
print(
    a.groupby(["method", "h", "loss"])
    .agg(
        answered=("state", lambda s: int((s == "ok").sum())),
        of=("state", "size"),
        mean_count=("count", "mean"),
        max_abs=("abs_err", "max"),
        mean_abs=("abs_err", "mean"),
    )
    .reset_index()
    .to_string(index=False, float_format=lambda v: f"{v:.5f}")
)
