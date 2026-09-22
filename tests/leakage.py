"""Leakage harness (spec: Deliberate leakage tests). Reused by every milestone that adds
a model or transform: M4's statistical models must pass l1_check too.

L1 future poisoning: for sampled origins, rerun the backtest with every value after the
origin replaced by 1e9, by NaN, and by a random permutation. The forecast columns must
be identical to the clean run. Truth columns (which legitimately come from the future)
and timing are excluded from the comparison.

The planted leaks at the bottom exist only here; they prove L1 can fail (L2).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

from forecasting.backtest.engine import run_backtest
from forecasting.backtest.splits import Origin, OriginPlan, make_origins
from forecasting.config import RunConfig, SeriesConfig
from forecasting.data.validate import Series
from forecasting.models.base import BaseForecaster, ModelFactory
from forecasting.models.baselines import ZeroReturn
from forecasting.transforms import Transform, _Fitted

TRUTH_COLUMNS = ["y_true", "y_true_missing", "level_true"]
TIMING_COLUMNS = ["fit_seconds"]
KEYS = ["unique_id", "window", "model", "origin_t", "h"]
POISONS = ("1e9", "nan", "permute")

FactoryMaker = Callable[[Series], Mapping[str, ModelFactory]]


def poison(series: Series, t: int, kind: str, seed: int = 0) -> Series:
    """Copy of series with every value after position t replaced."""
    v = series.y.to_numpy(dtype=float).copy()
    if kind == "1e9":
        v[t + 1 :] = 1e9
    elif kind == "nan":
        v[t + 1 :] = np.nan
    elif kind == "permute":
        rng = np.random.default_rng(seed)
        v[t + 1 :] = rng.permutation(v[t + 1 :])
    else:
        raise ValueError(kind)
    y = pd.Series(v, index=series.y.index, name=series.y.name)
    return replace(series, y=y, data_hash=f"poisoned-{kind}")


def forecast_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """The columns that must not depend on the future, in a stable order."""
    drop = TRUTH_COLUMNS + TIMING_COLUMNS
    return frame.drop(columns=drop).sort_values(KEYS, kind="stable").reset_index(drop=True)


def sample_origins(plan: OriginPlan, n: int, seed: int) -> list[Origin]:
    """n origins spread over all roles, drawn with a fixed seed; always includes the last."""
    rng = np.random.default_rng(seed)
    pool = list(plan.origins)
    k = min(n, len(pool))
    picked = rng.choice(len(pool) - 1, size=k - 1, replace=False) if k > 1 else []
    return sorted([pool[i] for i in picked] + [pool[-1]], key=lambda o: o.t)


@dataclass
class L1Result:
    n_origins: int
    n_rows_compared: int
    leaks: dict[str, set[str]]  # model -> poisons under which its forecasts changed

    @property
    def leaking_models(self) -> set[str]:
        return {m for m, kinds in self.leaks.items() if kinds}


def l1_check(
    series: Series,
    scfg: SeriesConfig,
    cfg: RunConfig,
    make_factories: FactoryMaker,
    n_origins: int = 10,
    seed: int = 20260922,
) -> L1Result:
    """Run L1 for every model and window at n sampled origins."""
    full_plan = make_origins(len(series.y), scfg)
    leaks: dict[str, set[str]] = {}
    compared = 0
    for origin in sample_origins(full_plan, n_origins, seed):
        plan = replace(full_plan, origins=(origin,))
        clean = forecast_columns(
            run_backtest(series, scfg, cfg, "l1", make_factories(series), plan)[0].frame()
        )
        for name in clean["model"].unique():
            leaks.setdefault(name, set())
        compared += len(clean)
        for kind in POISONS:
            bad = poison(series, origin.t, kind, seed=origin.t)
            other = forecast_columns(
                run_backtest(bad, scfg, cfg, "l1", make_factories(bad), plan)[0].frame()
            )
            for name in clean["model"].unique():
                a = clean[clean["model"] == name].reset_index(drop=True)
                b = other[other["model"] == name].reset_index(drop=True)
                if not a.equals(b):
                    leaks[name].add(kind)
    return L1Result(n_origins=n_origins, n_rows_compared=compared, leaks=leaks)


# ---------------------------------------------------------------- planted leaks (L2)


class GlobalZScore(_Fitted):
    """LEAKY: standardises with the mean and SD of the whole series, not the slice."""

    name = "global_zscore"

    def __init__(self, full: pd.Series) -> None:
        v = full.to_numpy(dtype=float)
        v = v[np.isfinite(v)]
        self.mu, self.sd = float(v.mean()), float(v.std())

    def fit(self, y: pd.Series):  # ignores the slice on purpose
        self._fitted = True
        return self

    def transform(self, y: pd.Series) -> pd.Series:
        return (y - self.mu) / self.sd

    def inverse(self, x: np.ndarray) -> np.ndarray:
        return np.asarray(x, dtype=float) * self.sd + self.mu


def global_zscore_builder(full: pd.Series) -> Callable[[pd.Series, int], Transform]:
    return lambda y, m: GlobalZScore(full).fit(y)


class CentreOnTransformedScale(ZeroReturn):
    """Honest model: forecasts 0 on whatever scale it is given. With an affine transform
    fitted on the slice this is harmless; with GlobalZScore it forecasts the global mean,
    so the leak shows up in its output."""

    name = "centre"
    uses_transform = True


class PeekModel(BaseForecaster):
    """LEAKY: looks up the value after its last training point in the full series.

    For a return series (returns=True) it peeks at the next log return, so its forecasts
    stay on the right scale and the leak is realistic rather than an overflow.
    """

    name = "peek"
    min_obs = 2

    def __init__(self, full: pd.Series, returns: bool = False) -> None:
        super().__init__()
        self._full = full
        self._returns = returns

    def fit(self, y: pd.Series):
        self._last_label = y.index[-1]
        return super().fit(y)

    def _fit(self, v: np.ndarray) -> None:
        pos = self._full.index.get_loc(self._last_label)
        full = self._full.to_numpy(dtype=float)
        nxt = full[pos + 1] if pos + 1 < len(full) else v[-1]
        if self._returns and pos + 1 < len(full):
            with np.errstate(invalid="ignore", divide="ignore"):
                nxt = np.log(full[pos + 1] / full[pos])
        self._next = nxt if np.isfinite(nxt) else 0.0  # stays finite so the row is 'ok'

    def _predict(self, h: int):
        return np.full(h, self._next), None

    def _residuals(self) -> np.ndarray:
        return np.array([])
