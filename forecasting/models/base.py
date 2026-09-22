"""Forecaster contract and result type.

Contract (spec: Behaviour and errors): deterministic given input; predict before fit
raises NotFittedError; the mean is finite; bounds satisfy lower <= mean <= upper;
failures raise FitError and are never hidden as NaN.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from statistics import NormalDist
from typing import Any, Protocol, Self

import numpy as np
import pandas as pd

from forecasting.errors import FitError, ForecastContractError, NotFittedError


@dataclass(frozen=True)
class ForecastResult:
    mean: np.ndarray  # shape (h,)
    lower: dict[float, np.ndarray] = field(default_factory=dict)  # level -> (h,)
    upper: dict[float, np.ndarray] = field(default_factory=dict)
    info: dict[str, Any] = field(default_factory=dict)


class Forecaster(Protocol):
    name: str
    uses_transform: bool  # True: the engine fits the fold's variance transform first

    def fit(self, y: pd.Series) -> Self: ...  # y: gap-free training slice
    def predict(self, h: int, levels: Sequence[float] = ()) -> ForecastResult: ...
    def residuals(self) -> np.ndarray: ...  # one-step, in-sample


ModelFactory = Callable[[int], Forecaster]  # m -> fresh, unfitted instance


def z_value(level: float) -> float:
    return NormalDist().inv_cdf(0.5 + level / 2)


def check_result(res: ForecastResult, h: int, levels: Sequence[float]) -> None:
    """Raise ForecastContractError if a result breaks the contract."""
    mean = np.asarray(res.mean)
    if mean.shape != (h,):
        raise ForecastContractError(f"mean has shape {mean.shape}, expected ({h},)")
    if not np.isfinite(mean).all():
        raise ForecastContractError("mean contains NaN or inf")
    for lv in res.lower:
        lo, hi = np.asarray(res.lower[lv]), np.asarray(res.upper[lv])
        if lo.shape != (h,) or hi.shape != (h,):
            raise ForecastContractError(f"interval {lv} has the wrong shape")
        if not (np.isfinite(lo).all() and np.isfinite(hi).all()):
            raise ForecastContractError(f"interval {lv} contains NaN or inf")
        tol = 1e-9 * np.maximum(1.0, np.abs(mean))
        if (lo > mean + tol).any() or (hi < mean - tol).any():
            raise ForecastContractError(f"interval {lv} does not contain the mean")
    if res.lower and set(res.lower) != set(levels):
        raise ForecastContractError("intervals returned for the wrong levels")


class BaseForecaster:
    """Shared plumbing: input checks, fitted guard, Normal intervals from sd_h."""

    name = "base"
    uses_transform = False
    min_obs = 2

    def __init__(self) -> None:
        self._fitted = False
        self._v: np.ndarray | None = None

    def fit(self, y: pd.Series) -> Self:
        v = np.asarray(y, dtype=float)
        if v.ndim != 1 or len(v) < self.min_obs:
            raise FitError(self.name, f"needs at least {self.min_obs} observations, got {len(v)}")
        if not np.isfinite(v).all():
            raise FitError(self.name, "training data contains NaN or inf")
        self._v = v
        self._fit(v)
        self._fitted = True
        return self

    def predict(self, h: int, levels: Sequence[float] = ()) -> ForecastResult:
        if not self._fitted:
            raise NotFittedError(f"{self.name}: predict before fit")
        if h < 1:
            raise ValueError("h must be >= 1")
        mean, sd = self._predict(h)
        lower: dict[float, np.ndarray] = {}
        upper: dict[float, np.ndarray] = {}
        if sd is not None:
            for lv in levels:
                z = z_value(lv)
                lower[lv], upper[lv] = mean - z * sd, mean + z * sd
        return ForecastResult(mean=mean, lower=lower, upper=upper, info=self._info())

    def residuals(self) -> np.ndarray:
        if not self._fitted:
            raise NotFittedError(f"{self.name}: residuals before fit")
        return self._residuals()

    # subclass hooks
    def _fit(self, v: np.ndarray) -> None:  # pragma: no cover
        raise NotImplementedError

    def _predict(self, h: int) -> tuple[np.ndarray, np.ndarray | None]:  # pragma: no cover
        raise NotImplementedError

    def _residuals(self) -> np.ndarray:  # pragma: no cover
        raise NotImplementedError

    def _info(self) -> dict[str, Any]:
        return {}


def residual_sigma(e: np.ndarray, n_params: int, model: str) -> float:
    """sigma = sqrt(sum e^2 / (n - K)), the fpp3 residual standard deviation."""
    dof = len(e) - n_params
    if dof < 1:
        raise FitError(model, "too few residuals to estimate sigma")
    return float(np.sqrt(np.sum(e**2) / dof))
