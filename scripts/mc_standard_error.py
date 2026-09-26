"""Is 1,000 Monte Carlo trials enough? Section 8 of the original research prompt.

Usage:
    uv run python scripts/mc_standard_error.py

Reproduces the table in docs/original_prompt_audit.md. Takes about two minutes: one
GJR-GARCH fit, then 30 repeated simulations at each of three path counts.

Monte Carlo standard error of the quantities the risk report publishes, as a function of
n_paths, measured by repeating the same simulation from one fitted model with different
RNG seeds. Nothing about the model changes between repetitions, so the spread is pure
simulation noise.
"""

import numpy as np
import pandas as pd

from forecasting.config import load_config
from forecasting.data.adapters import load_series
from forecasting.data.validate import validate
from forecasting.models.variance import GJRGARCH

cfg = load_config("configs/phase4/phase4.yaml")
scfg = cfg.series[0]
[(series, _)] = validate(load_series(scfg, cfg.base_dir), scfg)
y = np.asarray(series.y, dtype=float)
r = np.diff(np.log(y))
m = GJRGARCH()
m.fit(r[-3000:])

H, REPS = 20, 30
rows = []
for n in (1_000, 10_000, 100_000):
    lo_q, hi_q, es = [], [], []
    for rep in range(REPS):
        rng = np.random.default_rng(1000 + rep)
        cum = np.cumsum(m.simulate(H, n, rng), axis=1)[:, -1]
        a, b = np.quantile(cum, [0.025, 0.975])
        lo_q.append(a)
        hi_q.append(b)
        es.append(cum[cum <= np.quantile(cum, 0.05)].mean())
    rows.append(
        {
            "n_paths": n,
            "mean_lo95": np.mean(lo_q),
            "sd_lo95": np.std(lo_q, ddof=1),
            "mean_hi95": np.mean(hi_q),
            "sd_hi95": np.std(hi_q, ddof=1),
            "mean_ES5": np.mean(es),
            "sd_ES5": np.std(es, ddof=1),
        }
    )
d = pd.DataFrame(rows)
pd.set_option("display.width", 220)
print(d.to_string(index=False, float_format=lambda v: f"{v: .6f}"))
print()
for _, x in d.iterrows():
    w = x.mean_hi95 - x.mean_lo95
    print(
        f"n={int(x.n_paths):7d}  95% band edges +/- {1.96 * max(x.sd_lo95, x.sd_hi95):.5f} "
        f"({100 * 1.96 * max(x.sd_lo95, x.sd_hi95) / w:.2f}% of the band width), "
        f"ES +/- {1.96 * x.sd_ES5:.5f} ({100 * 1.96 * x.sd_ES5 / abs(x.mean_ES5):.2f}% of ES)"
    )
