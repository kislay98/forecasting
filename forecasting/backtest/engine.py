"""run_backtest: the only function that slices data (D4).

For each (window, origin), in order:
  1 slice      expanding y[0..t] or rolling y[t-L+1..t] (returns: one extra price)
  2 impute     linear interpolation inside the slice; origins whose last value is
               missing are skipped and recorded as status 'skipped'
  3 target     returns: LogReturn fitted on the slice's prices
  4 flags      trailing robust z on the slice (recorded, never used)
  5 per model  fresh instance from its factory; variance transform (auto, log, Box-Cox)
               fitted on the slice only for models with uses_transform; fit; predict;
               contract check; back-transform points and bounds
  6 record     one row per h with truth taken from the full series by position

Models and transforms never receive anything after position t. Model failures
(FitError, TransformError, contract breaks) are data: rows with status 'failed'.
Anything else is a programming error and propagates.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

import numpy as np
import pandas as pd
from joblib import Parallel, delayed

from forecasting.backtest.splits import Origin, OriginPlan, make_origins
from forecasting.backtest.store import ForecastStore, level_tag
from forecasting.config import RunConfig, SeriesConfig
from forecasting.data.validate import Series
from forecasting.errors import FitError, ForecastContractError, TransformError
from forecasting.models.base import ModelFactory, check_result
from forecasting.models.registry import build_factories
from forecasting.transforms import (
    Interpolator,
    LogReturn,
    OutlierFlagger,
    select_transform,
    transform_label,
)

MODEL_FAILURES = (FitError, TransformError, ForecastContractError)


def mase_scale(z: np.ndarray, m: int) -> float:
    """In-sample MAE of the seasonal naive (naive if m = 1) on the training slice."""
    lag = m if m > 1 and len(z) > m else 1
    if len(z) <= lag:
        return float("nan")
    return float(np.mean(np.abs(z[lag:] - z[:-lag])))


def _labels(index: pd.Index) -> list[str]:
    if isinstance(index, pd.DatetimeIndex):
        return [str(d.date()) for d in index]
    return [str(p) for p in index]


def _truth(values: np.ndarray, t: int, H: int, returns: bool) -> tuple[np.ndarray, np.ndarray]:
    """(y_true, level_true) for h = 1..H, read by position from the full series."""
    pos = np.arange(t + 1, t + H + 1)
    level = values[pos]
    if returns:
        with np.errstate(invalid="ignore", divide="ignore"):
            y = np.log(values[pos] / values[pos - 1])
        return y, level
    return level.copy(), np.full(H, np.nan)


def _run_fold(
    values: np.ndarray,
    index: pd.Index,
    labels: list[str],
    origin: Origin,
    window: str,
    scfg: SeriesConfig,
    levels: tuple[float, ...],
    factories: Mapping[str, ModelFactory],
    run_id: str,
) -> dict[str, list[Any]]:
    t, H, m = origin.t, scfg.H, scfg.m
    returns = scfg.target == "returns"
    offset = 1 if returns else 0
    start = 0 if window == "expanding" else max(0, t - scfg.rolling_length + 1 - offset)

    y_true, level_true = _truth(values, t, H, returns)
    missing = ~np.isfinite(y_true)
    hs = np.arange(1, H + 1)
    tags = [level_tag(lv) for lv in levels]
    cols: dict[str, list[Any]] = {}
    series_uid = index.name if index.name is not None else scfg.id

    def emit(model: str, pred, lo, hi, level_pred, status, error, transform, variant, secs, info):
        n_train, n_out, scale = info
        rows = {
            "run_id": [run_id] * H,
            "unique_id": [series_uid] * H,
            "model": [model] * H,
            "window": [window] * H,
            "origin_t": [t] * H,
            "origin_period": [labels[t]] * H,
            "origin_role": [origin.role] * H,
            "h": list(hs),
            "target_period": labels[t + 1 : t + H + 1],
            "y_true": list(y_true),
            "y_true_missing": list(missing),
            "y_pred": list(pred),
        }
        for tag in tags:
            rows[f"lo_{tag}"] = list(lo.get(tag, [np.nan] * H))
            rows[f"hi_{tag}"] = list(hi.get(tag, [np.nan] * H))
        rows.update(
            {
                "level_true": list(level_true),
                "level_pred": list(level_pred),
                "mase_scale": [scale] * H,
                "n_train": [n_train] * H,
                "n_outliers": [n_out] * H,
                "transform": [transform] * H,
                "variant": [variant] * H,
                "fit_seconds": [secs] * H,
                "status": [status] * H,
                "error": [error] * H,
            }
        )
        for k, v in rows.items():
            cols.setdefault(k, []).extend(v)

    nan = np.full(H, np.nan)

    # 1-3: slice, impute, target transform. Shared by every model at this fold.
    raw = pd.Series(values[start : t + 1], index=index[start : t + 1])
    if not np.isfinite(values[t]):
        for name in factories:
            emit(
                name,
                nan,
                {},
                {},
                nan,
                "skipped",
                "last observation missing",
                "none",
                "",
                0.0,
                (len(raw), 0, np.nan),
            )
        return cols
    try:
        filled = Interpolator().fit(raw).transform(raw)
        lr = None
        if returns:
            lr = LogReturn().fit(filled)
            z = lr.transform(filled)
        else:
            z = filled
    except TransformError as e:
        for name in factories:
            emit(
                name,
                nan,
                {},
                {},
                nan,
                "failed",
                f"TransformError: {e}",
                "none",
                "",
                0.0,
                (len(raw), 0, np.nan),
            )
        return cols

    zv = z.to_numpy(dtype=float)
    info = (len(zv), OutlierFlagger(m).fit(z).n_flags, mase_scale(zv, m))

    # 5: one fresh model per (window, origin, model).
    for name, factory in factories.items():
        t0 = time.perf_counter()
        tr_label, variant = "none", ""
        try:
            model = factory(m)
            tr = None
            z_in = z
            if model.uses_transform:
                try:
                    tr = select_transform(z, scfg.transform, m)
                    tr_label = transform_label(tr)
                    z_in = tr.transform(z)
                except TransformError as e:
                    tr, tr_label = None, f"none (fallback: {e})"
            model.fit(z_in)
            res = model.predict(H, levels)
            check_result(res, H, levels)
            mean = np.asarray(res.mean, dtype=float)
            lo = {level_tag(k): np.asarray(v, dtype=float) for k, v in res.lower.items()}
            hi = {level_tag(k): np.asarray(v, dtype=float) for k, v in res.upper.items()}
            if tr is not None:
                mean = tr.inverse(mean)
                lo = {k: tr.inverse(v) for k, v in lo.items()}
                hi = {k: tr.inverse(v) for k, v in hi.items()}
            level_pred = nan
            if lr is not None:
                with np.errstate(over="ignore"):
                    level_pred = lr.inverse(mean)
                if not np.isfinite(level_pred).all():
                    raise ForecastContractError("implied price path overflows (non-finite)")
            variant = str(res.info.get("variant", ""))
            emit(
                name,
                mean,
                lo,
                hi,
                level_pred,
                "ok",
                "",
                tr_label,
                variant,
                time.perf_counter() - t0,
                info,
            )
        except MODEL_FAILURES as e:
            emit(
                name,
                nan,
                {},
                {},
                nan,
                "failed",
                f"{type(e).__name__}: {e}",
                tr_label,
                variant,
                time.perf_counter() - t0,
                info,
            )
    return cols


def run_backtest(
    series: Series,
    scfg: SeriesConfig,
    cfg: RunConfig,
    run_id: str,
    factories: Mapping[str, ModelFactory] | None = None,
    plan: OriginPlan | None = None,
) -> tuple[ForecastStore, OriginPlan]:
    """Backtest one validated series over every (window, origin, model)."""
    factories = dict(factories) if factories is not None else build_factories(scfg)
    plan = plan or make_origins(len(series.y), scfg)
    values = series.y.to_numpy(dtype=float)
    index = series.y.index.copy()
    index.name = series.unique_id
    labels = _labels(index)

    tasks = [(o, w) for w in scfg.windows for o in plan.origins]
    parallel = Parallel(n_jobs=cfg.n_jobs, backend="loky" if cfg.n_jobs != 1 else "sequential")
    chunks = parallel(
        delayed(_run_fold)(values, index, labels, o, w, scfg, cfg.levels, factories, run_id)
        for o, w in tasks
    )
    store = ForecastStore(cfg.levels)
    names = [c for c, _ in store.columns]
    merged: dict[str, list[Any]] = {c: [] for c in names}
    for ch in chunks:
        for c in names:
            merged[c].extend(ch.get(c, []))
    if merged["run_id"]:
        store.append(pd.DataFrame(merged, columns=names))
    return store, plan
