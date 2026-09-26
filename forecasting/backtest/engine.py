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
  6 diagnose   test origins only: Ljung-Box and ARCH-LM on the fitted model's one-step
               in-sample residuals (RQ4), recorded on the fold's rows
  7 record     one row per h with truth taken from the full series by position

Models and transforms never receive anything after position t. Model failures
(FitError, TransformError, contract breaks) are data: rows with status 'failed'.
Anything else is a programming error and propagates.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Mapping
from typing import Any

import numpy as np
import pandas as pd
from joblib import Parallel, delayed

from forecasting.backtest.splits import Origin, OriginPlan, make_origins
from forecasting.backtest.store import ForecastStore, level_tag
from forecasting.config import RunConfig, SeriesConfig, is_return_target
from forecasting.data.validate import Series
from forecasting.errors import FitError, ForecastContractError, TransformError
from forecasting.evaluation.diagnostics import residual_tests
from forecasting.models.base import ForecastResult, ModelFactory, check_result
from forecasting.models.combination import MEMBERS, combine
from forecasting.models.registry import build_factories
from forecasting.transforms import (
    Interpolator,
    LogReturn,
    OutlierFlagger,
    select_transform,
    transform_label,
)

MODEL_FAILURES = (FitError, TransformError, ForecastContractError)
NO_DIAG = {"n_resid": 0, "lb_p": np.nan, "lb_p_2m": np.nan, "arch_p": np.nan}


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


def _truth(
    values: np.ndarray, t: int, H: int, returns: bool, cumulative: bool = False
) -> tuple[np.ndarray, np.ndarray]:
    """(y_true, level_true) for h = 1..H, read by position from the full series.

    For a cumulative target the truth at h is the whole move from the origin,
    log(p_{t+h} / p_t), not the single day's return at t+h. That is the object a
    holding-period loss is about, and it is why h-step windows from nearby origins
    overlap and the origin step has to be at least h for the sequence tests.
    """
    pos = np.arange(t + 1, t + H + 1)
    level = values[pos]
    if returns:
        base = values[t] if cumulative else values[pos - 1]
        with np.errstate(invalid="ignore", divide="ignore"):
            y = np.log(values[pos] / base)
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
    seed: int = 0,
    n_paths: int = 10000,
) -> dict[str, list[Any]]:
    t, H, m = origin.t, scfg.H, scfg.m
    returns = is_return_target(scfg.target)
    cumulative = scfg.target == "cumulative_returns"
    offset = 1 if returns else 0
    start = 0 if window == "expanding" else max(0, t - scfg.rolling_length + 1 - offset)

    y_true, level_true = _truth(values, t, H, returns, cumulative)
    missing = ~np.isfinite(y_true)
    hs = np.arange(1, H + 1)
    tags = [level_tag(lv) for lv in levels]
    cols: dict[str, list[Any]] = {}
    series_uid = index.name if index.name is not None else scfg.id

    def emit(
        model: str, pred, lo, hi, level_pred, status, error, transform, variant, secs, info,
        diag=NO_DIAG, es=None,
    ):  # fmt: skip
        es = es or {}
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
            rows[f"es_{tag}"] = list(es.get(tag, [np.nan] * H))
        rows.update(
            {
                "level_true": list(level_true),
                "level_pred": list(level_pred),
                "mase_scale": [scale] * H,
                "n_train": [n_train] * H,
                "n_outliers": [n_out] * H,
                **{k: [v] * H for k, v in diag.items()},
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

    # Which models run here: expensive models skip warm-up origins (never scored) unless
    # warmup_models: all. The combination is derived from its members (M4-4).
    active: list[tuple[str, Any]] = []
    for name, factory in factories.items():
        model = factory(m)
        if origin.role == "warmup" and scfg.warmup_models == "baselines" and model.expensive:
            continue
        active.append((name, model))
    names = [n for n, _ in active]
    with_combination = "combination" in scfg.models and all(x in names for x in MEMBERS)
    out_names = names + (["combination"] if with_combination else [])
    if not out_names:
        return cols

    # 1-3: slice, impute, target transform. Shared by every model at this fold.
    raw = pd.Series(values[start : t + 1], index=index[start : t + 1])
    if not np.isfinite(values[t]):
        for name in out_names:
            emit(name, nan, {}, {}, nan, "skipped", "last observation missing", "none", "",
                 0.0, (len(raw), 0, np.nan))  # fmt: skip
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
        for name in out_names:
            emit(name, nan, {}, {}, nan, "failed", f"TransformError: {e}", "none", "", 0.0,
                 (len(raw), 0, np.nan))  # fmt: skip
        return cols

    zv = z.to_numpy(dtype=float)
    info = (len(zv), OutlierFlagger(m).fit(z).n_flags, mase_scale(zv, m))

    # The variance transform is fitted once per fold on the slice and shared by every model
    # that uses one, so combination members are averaged on the same scale.
    fold_tr: dict[str, Any] = {}

    def variance_transform():
        if not fold_tr:
            try:
                tr = select_transform(z, scfg.transform, m)
                fold_tr.update(tr=tr, label=transform_label(tr), z=tr.transform(z))
            except TransformError as e:
                fold_tr.update(tr=None, label=f"none (fallback: {e})", z=z)
        return fold_tr["tr"], fold_tr["label"], fold_tr["z"]

    def back(tr, mean, lo, hi):
        if tr is None:
            return mean, lo, hi
        return (
            tr.inverse(mean),
            {k: tr.inverse(v) for k, v in lo.items()},
            {k: tr.inverse(v) for k, v in hi.items()},
        )

    def implied_prices(mean):
        if lr is None:
            return nan
        with np.errstate(over="ignore"):
            # A cumulative mean is already the move from the origin, so accumulating it
            # again would compound the same returns twice.
            prices = values[t] * np.exp(mean) if cumulative else lr.inverse(mean)
        if not np.isfinite(prices).all():
            raise ForecastContractError("implied price path overflows (non-finite)")
        return prices

    def simulated_result(model, name: str) -> ForecastResult:
        """Cumulative quantiles from simulated paths.

        The model only knows how to step one period forward; accumulating is the
        engine's job, the same way slicing is. Paths are drawn with the fold's own
        seed, so a rerun reproduces them exactly.
        """
        if not hasattr(model, "simulate"):
            raise FitError(name, "a cumulative target needs a model that can simulate")
        rng = np.random.default_rng(fold_seed(seed, series_uid, window, t, f"{name}|paths"))
        paths = np.cumsum(np.asarray(model.simulate(H, n_paths, rng), dtype=float), axis=1)
        if paths.shape != (n_paths, H):
            raise ForecastContractError(f"{name}: simulate returned {paths.shape}")
        lower, upper, shortfall = {}, {}, {}
        for lv in levels:
            lo_q, hi_q = np.quantile(paths, [(1 - lv) / 2, (1 + lv) / 2], axis=0)
            lower[lv], upper[lv] = lo_q, hi_q
            # Expected shortfall: the mean of the paths at or below the lower bound, per
            # horizon. Computed here because this is the only place the paths exist; a
            # ten-point quantile grid cannot recover it afterwards.
            below = paths <= lo_q[None, :]
            counts = below.sum(axis=0)
            with np.errstate(invalid="ignore"):
                shortfall[lv] = np.where(counts > 0, (paths * below).sum(axis=0) / counts, np.nan)
        return ForecastResult(
            mean=paths.mean(axis=0),
            lower=lower,
            upper=upper,
            info={
                "variant": getattr(model, "variant", ""),
                "n_paths": n_paths,
                "es": shortfall,
            },
        )

    # 5: one fresh model per (window, origin, model).
    member_means: dict[str, np.ndarray | None] = {}
    for name, model in active:
        t0 = time.perf_counter()
        tr_label, variant = "none", ""
        try:
            if hasattr(model, "seed"):
                model.seed = fold_seed(seed, series_uid, window, t, name)
            tr, z_in = None, z
            if model.uses_transform:
                tr, tr_label, z_in = variance_transform()
            model.fit(z_in)
            res = simulated_result(model, name) if cumulative else model.predict(H, levels)
            check_result(res, H, levels)
            variant = str(res.info.get("variant", ""))
            mean_t = np.asarray(res.mean, dtype=float)
            lo = {level_tag(k): np.asarray(v, dtype=float) for k, v in res.lower.items()}
            hi = {level_tag(k): np.asarray(v, dtype=float) for k, v in res.upper.items()}
            mean, lo, hi = back(tr, mean_t, lo, hi)
            es_out = {
                level_tag(k): np.asarray(v, dtype=float)
                for k, v in (res.info.get("es") or {}).items()
            }
            level_pred = implied_prices(mean)
            if name in MEMBERS:
                member_means[name] = mean_t
            diag = residual_tests(model.residuals(), m) if origin.role == "test" else NO_DIAG
            emit(name, mean, lo, hi, level_pred, "ok", "", tr_label, variant,
                 time.perf_counter() - t0, info, diag, es_out)  # fmt: skip
        except MODEL_FAILURES as e:
            if name in MEMBERS:
                member_means[name] = None
            emit(name, nan, {}, {}, nan, "failed", f"{type(e).__name__}: {e}", tr_label,
                 variant, time.perf_counter() - t0, info)  # fmt: skip

    if with_combination:
        t0 = time.perf_counter()
        tr, tr_label, _ = variance_transform()
        try:
            mean_t, variant = combine(member_means)
            mean, _, _ = back(tr, mean_t, {}, {})
            emit("combination", mean, {}, {}, implied_prices(mean), "ok", "", tr_label,
                 variant, time.perf_counter() - t0, info)  # fmt: skip
        except MODEL_FAILURES as e:
            emit("combination", nan, {}, {}, nan, "failed", f"{type(e).__name__}: {e}",
                 tr_label, "", time.perf_counter() - t0, info)  # fmt: skip
    return cols


def fold_seed(seed: int, uid: str, window: str, t: int, model: str) -> int:
    """D7: one child seed per (series, window, origin, model), independent of run order."""
    digest = hashlib.sha256(f"{uid}|{window}|{t}|{model}".encode()).digest()
    key = tuple(int.from_bytes(digest[i : i + 4], "little") for i in range(0, 16, 4))
    return int(np.random.SeedSequence(seed, spawn_key=key).generate_state(1)[0])


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
        delayed(_run_fold)(
            values, index, labels, o, w, scfg, cfg.levels, factories, run_id, cfg.seed, cfg.n_paths
        )
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
