"""Selections made from the store using dev origins only (leakage source 10, test L5).

select_sma_k picks the SMA window per (series, window) by mean MASE over all h on dev
origins, then it is frozen: the report treats 'sma' as that sma_k. Test rows are
filtered out before anything is computed, so poisoning them cannot change the choice.
"""

from __future__ import annotations

import pandas as pd


def dev_rows(frame: pd.DataFrame) -> pd.DataFrame:
    return frame[frame["origin_role"] == "dev"]


def select_sma_k(frame: pd.DataFrame) -> dict[str, dict[str, dict]]:
    """{unique_id: {window: {"model", "k", "dev_mase", "scores"}}}. Ties go to the smaller k."""
    dev = dev_rows(frame)
    sma = dev[
        dev["model"].str.startswith("sma_")
        & (dev["status"] == "ok")
        & ~dev["y_true_missing"]
        & (dev["mase_scale"] > 0)
    ]
    out: dict[str, dict[str, dict]] = {}
    if sma.empty:
        return out
    scaled = (sma["y_true"] - sma["y_pred"]).abs() / sma["mase_scale"]
    scores = scaled.groupby([sma["unique_id"], sma["window"], sma["model"]]).mean()
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
