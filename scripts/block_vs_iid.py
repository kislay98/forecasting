"""Block against iid innovations, on the same fitted model (Phase 6, the registered trap).

Usage:
    uv run python scripts/block_vs_iid.py CONFIG ROLE [--every K] [--paths N]

    CONFIG  a cumulative-shape run config, e.g. configs/phase6/phase6.yaml
    ROLE    dev or test. Registration reads dev only (P2-10); test is for the decision.

At each origin of ROLE (expanding window, every K-th origin), each variance model is
fitted once on the fold's log returns exactly as the engine slices them. Two sets of
paths are then simulated from that one fit with the same seed: the registered iid draw
and the moving-block draw of forecasting/models/blocks.py. Nothing differs between the
two sets except the sampler, so any change in the 20-day distribution is the sampler's.

Printed per model, over origins:

  sd_ratio     sd of the h = 20 cumulative return, block over iid (median and mean)
  kurt_iid     excess kurtosis of the h = 20 cumulative return under the iid draw
               (median over origins: a sample kurtosis from 10,000 paths is itself
               heavy tailed, and its mean over origins is carried by a few of them)
  kurt_block   the same under the block draw
  q80_ratio    width of the central 80% interval at h = 20, block over iid
  pred_linear  the sd ratio a constant-variance model would show from the standardised
               residuals' own autocorrelations alone (lags 1 to b - 1), with no recursion.
               The gap between it and sd_ratio for a recursive model is the part the
               recursion adds: the double counting the gate names as its risk.

Then, against the realised h-day outcome at the same origins, for h = 1, 5 and 20:
coverage of the central 80% and 95% intervals under each sampler, and the ratio of mean
sample CRPS, block over iid. On dev origins this is what the registration may learn from
(P2-10); on test origins it is description beside the gate, not the gate.

The constant-variance row (zero_return_fhs) has no recursion, so its block effect is the
autocorrelation effect and nothing else.
"""

from __future__ import annotations

import argparse
import sys

import numpy as np
import pandas as pd
from scipy import stats

from forecasting.backtest.engine import fold_seed
from forecasting.backtest.splits import make_origins
from forecasting.config import load_config
from forecasting.data.adapters import load_series
from forecasting.data.validate import validate
from forecasting.errors import FitError
from forecasting.models.blocks import BLOCK_LENGTH, block_draw
from forecasting.models.variance import EWMA, GARCH, GJRGARCH, ConstantSigma

H = 20
HS = (1, 5, 20)
MODELS = {
    "zero_return_fhs": ConstantSigma,
    "ewma": EWMA,
    "garch": GARCH,
    "gjr_garch": GJRGARCH,
}


def acf(z: np.ndarray, lags: int) -> np.ndarray:
    z = z - z.mean()
    d = float(z @ z)
    return np.array([float(z[:-k] @ z[k:]) / d for k in range(1, lags + 1)])


def linear_ratio(z: np.ndarray, b: int, h: int) -> float:
    """sd ratio of a sum of h block-drawn values against h iid ones, from the ACF.

    Pairs at lag k inside one block: (b - k) per full block. A path is ceil(h / b)
    blocks truncated to h, so the count is taken on the actual layout.
    """
    rho = acf(z, b - 1)
    layout = np.arange(h) // b  # which block each position is in
    extra = 0.0
    for k in range(1, b):
        same = int(np.sum(layout[:-k] == layout[k:]))
        extra += 2 * rho[k - 1] * same
    return float(np.sqrt(1 + extra / h))


def sample_crps(x: np.ndarray, y: float) -> float:
    """CRPS of the empirical distribution of x at the outcome y (energy form)."""
    x = np.sort(x)
    n = len(x)
    spread = float(np.sum((2 * np.arange(1, n + 1) - n - 1) * x)) / n**2
    return float(np.mean(np.abs(x - y))) - spread


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("config")
    ap.add_argument("role", choices=["dev", "test"])
    ap.add_argument("--every", type=int, default=1)
    ap.add_argument("--paths", type=int, default=10_000)
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    scfg = cfg.series[0]
    [(series, _)] = validate(load_series(scfg, cfg.base_dir), scfg)
    prices = np.asarray(series.y, dtype=float)
    plan = make_origins(len(prices), scfg)
    ts = [o.t for o in plan.origins if o.role == args.role][:: args.every]

    rows = []
    for t in ts:
        r = np.diff(np.log(prices[: t + 1]))
        for name, cls in MODELS.items():
            m = cls()
            try:
                m.fit(pd.Series(r))
            except FitError:
                continue
            seed = fold_seed(cfg.seed, scfg.id, "expanding", t, f"{name}|p6")
            iid_paths = np.cumsum(m.simulate(H, args.paths, np.random.default_rng(seed)), axis=1)
            original = m._draw
            m._draw = lambda h, n, rng, _m=m: block_draw(_m._z, h, n, rng, BLOCK_LENGTH)
            blk_paths = np.cumsum(m.simulate(H, args.paths, np.random.default_rng(seed)), axis=1)
            m._draw = original
            iid, blk = iid_paths[:, -1], blk_paths[:, -1]
            scored = {}
            for h in HS:
                y = float(np.log(prices[t + h] / prices[t]))
                for tag, p in (("iid", iid_paths[:, h - 1]), ("blk", blk_paths[:, h - 1])):
                    lo, hi = np.quantile(p, [0.1, 0.9])
                    scored[f"cov80_{tag}_{h}"] = float(lo <= y <= hi)
                    lo, hi = np.quantile(p, [0.025, 0.975])
                    scored[f"cov95_{tag}_{h}"] = float(lo <= y <= hi)
                    scored[f"crps_{tag}_{h}"] = sample_crps(p, y)
            qi = np.quantile(iid, [0.1, 0.9])
            qb = np.quantile(blk, [0.1, 0.9])
            rows.append(
                {
                    "t": t,
                    "period": str(series.y.index[t])[:10],
                    "model": name,
                    "n": len(r),
                    "sd_ratio": blk.std() / iid.std(),
                    "q80_ratio": (qb[1] - qb[0]) / (qi[1] - qi[0]),
                    "kurt_iid": stats.kurtosis(iid),
                    "kurt_block": stats.kurtosis(blk),
                    "pred_linear": linear_ratio(m._z, BLOCK_LENGTH, H),
                    "rho1": acf(m._z, 1)[0],
                    "rho1_sq": acf(m._z**2, 1)[0],
                    **scored,
                }
            )
    d = pd.DataFrame(rows)
    pd.set_option("display.width", 220)
    print(
        f"{scfg.id}, {args.role} origins {len(ts)} ({d['period'].min()} to {d['period'].max()}), "
        f"block {BLOCK_LENGTH}, {args.paths:,} paths, h = {H}"
    )
    g = d.groupby("model", sort=False)
    out = pd.DataFrame(
        {
            "sd_ratio_med": g["sd_ratio"].median(),
            "sd_ratio_mean": g["sd_ratio"].mean(),
            "sd_ratio_min": g["sd_ratio"].min(),
            "sd_ratio_max": g["sd_ratio"].max(),
            "q80_ratio_mean": g["q80_ratio"].mean(),
            "pred_linear_mean": g["pred_linear"].mean(),
            "kurt_iid_med": g["kurt_iid"].median(),
            "kurt_block_med": g["kurt_block"].median(),
            "rho1_mean": g["rho1"].mean(),
            "rho1_sq_mean": g["rho1_sq"].mean(),
            "share_wider": g["sd_ratio"].apply(lambda s: float((s > 1).mean())),
        }
    )
    print(out.to_string(float_format=lambda v: f"{v: .4f}"))
    print()
    print(
        "Against the realised outcome at the same origins: coverage of the central 80% and "
        "95% intervals, and sample CRPS of block over iid (below 1: block is better)."
    )
    cal = []
    for model, x in g:
        for h in HS:
            cal.append(
                {
                    "model": model,
                    "h": h,
                    "cov80_iid": x[f"cov80_iid_{h}"].mean(),
                    "cov80_block": x[f"cov80_blk_{h}"].mean(),
                    "cov95_iid": x[f"cov95_iid_{h}"].mean(),
                    "cov95_block": x[f"cov95_blk_{h}"].mean(),
                    "crps_ratio": x[f"crps_blk_{h}"].mean() / x[f"crps_iid_{h}"].mean(),
                }
            )
    print(pd.DataFrame(cal).to_string(index=False, float_format=lambda v: f"{v: .4f}"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
