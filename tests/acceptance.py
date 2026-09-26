"""Shared machinery for the M7 acceptance checks (A3, A4, L3).

backtest_synthetic runs one synthetic series through the real engine (no CSV, no run
directory) and returns the store frame with a manifest built by the same function
`forecast run` uses, so scoring and the report see exactly what a run would carry.

beats_naive applies the report's RQ1 rule against naive itself (the L3 statistic):
for one series, a hit is any model whose Holm-adjusted one-sided DM-HLN p-value
against naive is below alpha at any decision horizon.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from forecasting.backtest.engine import run_backtest
from forecasting.config import parse_config
from forecasting.data.validate import validate
from forecasting.evaluation.scoring import pairwise
from forecasting.evaluation.tests import holm
from forecasting.pipeline import build_manifest, plan_summary
from tests.synthetic import to_frame

LEVEL_MODELS_FULL = (
    "naive", "seasonal_naive", "drift", "sma", "ses", "ets", "sarima", "theta", "combination",
)  # fmt: skip
LEVEL_MODELS_CHEAP = ("naive", "seasonal_naive", "drift", "sma", "ses", "theta")


@dataclass
class SyntheticRun:
    frame: pd.DataFrame
    manifest: dict
    uid: str
    window: str

    @property
    def test_horizons(self) -> tuple[int, ...]:
        return tuple(self.manifest["config"]["series"][0]["decision_horizons"])


def backtest_synthetic(
    y: np.ndarray,
    models=LEVEL_MODELS_FULL,
    uid: str = "s",
    freq: str = "monthly",
    season: int | str = 12,
    H: int = 12,
    decision_horizons=(1, 6, 12),
    window: str = "expanding",
    seed: int = 20260922,
    n_paths: int | None = None,
    levels: tuple[float, ...] | None = None,
    **labels,
) -> SyntheticRun:
    raw = {
        "id": uid,
        "source": "csv:synthetic",
        "freq": freq,
        "season": season,
        "H": H,
        "decision_horizons": list(decision_horizons),
        "window": window,
        "models": list(models),
        **labels,
    }
    top: dict = {"seed": seed, "series": [raw]}
    if n_paths is not None:
        top["n_paths"] = n_paths
    if levels is not None:
        top["levels"] = list(levels)
    cfg = parse_config(top)
    scfg = cfg.series[0]
    [(series, report)] = validate(to_frame(y, freq, unique_id=uid), scfg)
    store, plan = run_backtest(series, scfg, cfg, "synthetic")
    frame = store.frame()
    manifest = build_manifest(cfg, frame, {uid: plan_summary(plan, scfg, report.mode)})
    return SyntheticRun(frame, manifest, uid, window if window != "both" else "expanding")


def beats_naive(run: SyntheticRun, alpha: float = 0.05) -> pd.DataFrame:
    """Every model against naive at the decision horizons, with Holm over the family.

    Columns: model, h, n, rel_mae, p_better (one-sided DM-HLN: the model is more
    accurate than naive), p_holm, hit (p_holm < alpha). The family is every
    (model, decision horizon) of the series, so a hit anywhere is a false positive at
    family-wise level alpha on a random walk.
    """
    hs = set(run.test_horizons)
    # the store holds sma_k rows; pairwise collapses them to the dev-chosen 'sma'
    models = sorted({"sma" if m.startswith("sma_") else m for m in run.frame["model"]} - {"naive"})
    rows = []
    for name in models:
        pw = pairwise(run.frame, run.manifest, run.uid, run.window, name, "naive")
        if pw.empty:
            continue
        pw = pw[pw["h"].isin(hs) & pw["dm_p_better"].notna()]
        for r in pw.itertuples():
            rows.append(
                {"model": name, "h": r.h, "n": r.n, "rel_mae": r.rel_mae, "p_better": r.dm_p_better}
            )
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    out["p_holm"] = holm(out["p_better"].to_numpy())
    out["hit"] = out["p_holm"] < alpha
    return out
