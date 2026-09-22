"""Selections made from the store using dev origins only (leakage sources 10, 11; test L5).

select_sma_k picks the SMA window per (series, window) by mean MASE over all h on dev
origins, then it is frozen: the report treats 'sma' as that sma_k.
select_best_baseline picks the reference for relative MAE the same way, among the
baselines with SMA represented by its chosen sma_k.
Test rows are dropped before anything is computed, so poisoning them cannot change
either choice.
"""

from __future__ import annotations

import pandas as pd

from forecasting.config import LEVEL_MODELS, RETURN_MODELS

BASELINES = frozenset(LEVEL_MODELS) | frozenset(RETURN_MODELS)


def dev_rows(frame: pd.DataFrame) -> pd.DataFrame:
    return frame[frame["origin_role"] == "dev"]


def _scorable(dev: pd.DataFrame) -> pd.DataFrame:
    return dev[(dev["status"] == "ok") & ~dev["y_true_missing"] & (dev["mase_scale"] > 0)]


def _dev_mase(rows: pd.DataFrame) -> pd.Series:
    """Mean MASE over all h per (unique_id, window, model)."""
    scaled = (rows["y_true"] - rows["y_pred"]).abs() / rows["mase_scale"]
    return scaled.groupby([rows["unique_id"], rows["window"], rows["model"]]).mean()


def select_sma_k(frame: pd.DataFrame) -> dict[str, dict[str, dict]]:
    """{unique_id: {window: {"model", "k", "dev_mase", "scores"}}}. Ties go to the smaller k."""
    dev = _scorable(dev_rows(frame))
    sma = dev[dev["model"].str.startswith("sma_")]
    out: dict[str, dict[str, dict]] = {}
    if sma.empty:
        return out
    scores = _dev_mase(sma)
    for (uid, window), s in scores.groupby(level=[0, 1]):
        s = s.droplevel([0, 1])
        ks = {name: int(name.split("_", 1)[1]) for name in s.index}
        best = min(s.index, key=lambda n: (s[n], ks[n]))
        out.setdefault(uid, {})[window] = {
            "model": best,
            "k": ks[best],
            "dev_mase": float(s[best]),
            "scores": {n: float(v) for n, v in sorted(s.items(), key=lambda x: ks[x[0]])},
        }
    return out


def select_best_baseline(frame: pd.DataFrame) -> dict[str, dict[str, dict]]:
    """{unique_id: {window: {"model", "dev_mase", "scores"}}}: lowest mean dev MASE.

    Candidates are the baselines present, with SMA entered once as its dev-chosen
    sma_k. One reference per (series, window), used at every h by relative MAE (M5).
    Ties go to the name first in alphabetical order, so the choice is deterministic.
    """
    sma_choice = select_sma_k(frame)
    dev = _scorable(dev_rows(frame))
    out: dict[str, dict[str, dict]] = {}
    for (uid, window), rows in dev.groupby(["unique_id", "window"]):
        chosen_sma = sma_choice.get(uid, {}).get(window, {}).get("model")
        keep = rows["model"].isin(BASELINES) | (rows["model"] == chosen_sma)
        cand = rows[keep]
        if cand.empty:
            continue
        s = _dev_mase(cand).droplevel([0, 1])
        best = min(s.index, key=lambda n: (s[n], n))
        out.setdefault(uid, {})[window] = {
            "model": best,
            "dev_mase": float(s[best]),
            "scores": {n: float(v) for n, v in sorted(s.items())},
        }
    return out
