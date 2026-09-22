"""Fold-safe, data-dependent transforms: fit on the training slice only (D4).

Every class follows the Transform protocol: fit(y) learns state from the slice,
transform(y) applies it, inverse(x) maps model output (points and interval bounds)
back. The backtest engine is the only caller and only ever passes the slice ending
at the origin, which is what makes these leak-free (leakage sources 4, 5, 6).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, Self

import numpy as np
import pandas as pd
from scipy import stats
from scipy.special import inv_boxcox

from forecasting.errors import NotFittedError, TransformError


class Transform(Protocol):
    name: str

    def fit(self, y: pd.Series) -> Self: ...
    def transform(self, y: pd.Series) -> pd.Series: ...
    def inverse(self, x: np.ndarray) -> np.ndarray: ...


class _Fitted:
    _fitted: bool = False

    def _check(self) -> None:
        if not self._fitted:
            raise NotFittedError(f"{type(self).__name__} used before fit")


# ------------------------------------------------------------ variance transforms


class Identity(_Fitted):
    name = "none"

    def fit(self, y: pd.Series) -> Self:
        self._fitted = True
        return self

    def transform(self, y: pd.Series) -> pd.Series:
        self._check()
        return y

    def inverse(self, x: np.ndarray) -> np.ndarray:
        self._check()
        return np.asarray(x, dtype=float)


class Log(_Fitted):
    name = "log"

    def fit(self, y: pd.Series) -> Self:
        if (y <= 0).any():
            raise TransformError("log needs y > 0 on the training slice")
        self._fitted = True
        return self

    def transform(self, y: pd.Series) -> pd.Series:
        self._check()
        return np.log(y)

    def inverse(self, x: np.ndarray) -> np.ndarray:
        """Back-transform points and bounds. For a median forecast this is the median (D8)."""
        self._check()
        return np.exp(np.asarray(x, dtype=float))


class BoxCox(_Fitted):
    name = "boxcox"

    def __init__(self) -> None:
        self.lmbda: float | None = None

    def fit(self, y: pd.Series) -> Self:
        if (y <= 0).any():
            raise TransformError("Box-Cox needs y > 0 on the training slice")
        if y.nunique() < 3:
            raise TransformError("Box-Cox needs at least 3 distinct values")
        self.lmbda = float(stats.boxcox_normmax(y.to_numpy(dtype=float), method="mle"))
        self._fitted = True
        return self

    def transform(self, y: pd.Series) -> pd.Series:
        self._check()
        return pd.Series(stats.boxcox(y.to_numpy(dtype=float), self.lmbda), index=y.index)

    def inverse(self, x: np.ndarray) -> np.ndarray:
        self._check()
        return inv_boxcox(np.asarray(x, dtype=float), self.lmbda)

    @property
    def label(self) -> str:
        return f"boxcox({self.lmbda:.3f})"


def variance_grows_with_level(y: pd.Series, m: int) -> bool:
    """Spec auto rule: regress log spread on log level over windows of m (8 if m < 8).

    Windows are non-overlapping and aligned to the origin. The spread in each window is
    the SD of first differences, not of the raw values: a trend inside a window would
    otherwise dominate the SD and hide multiplicative noise (DECISIONS.md M2-6).
    Slope above 0.5 means the spread grows with the level: use log.
    """
    w = m if m >= 8 else 8
    v = y.to_numpy(dtype=float)
    n_blocks = len(v) // w
    if n_blocks < 3:
        return False
    blocks = v[len(v) - n_blocks * w :].reshape(n_blocks, w)
    mean = blocks.mean(axis=1)
    sd = np.diff(blocks, axis=1).std(axis=1, ddof=1)
    ok = (sd > 0) & (mean > 0)
    if ok.sum() < 3:
        return False
    slope = np.polyfit(np.log(mean[ok]), np.log(sd[ok]), 1)[0]
    return bool(slope > 0.5)


def _auto(y: pd.Series, m: int) -> Transform:
    if (y <= 0).any():
        return Identity().fit(y)
    return Log().fit(y) if variance_grows_with_level(y, m) else Identity().fit(y)


# Mode name -> builder(slice, m) returning a fitted transform. Config only accepts the
# four modes below; the leakage tests register a deliberately leaky one here (L2).
TRANSFORM_BUILDERS: dict[str, Callable[[pd.Series, int], Transform]] = {
    "none": lambda y, m: Identity().fit(y),
    "log": lambda y, m: Log().fit(y),
    "boxcox": lambda y, m: BoxCox().fit(y),
    "auto": _auto,
}


def select_transform(y: pd.Series, mode: str, m: int) -> Transform:
    """Choose and fit the variance transform on the slice. Raises TransformError on failure."""
    try:
        builder = TRANSFORM_BUILDERS[mode]
    except KeyError:
        raise ValueError(f"unknown transform mode {mode!r}") from None
    return builder(y, m)


def transform_label(tr: Transform) -> str:
    return getattr(tr, "label", tr.name)


# ------------------------------------------------------------ target transform


class LogReturn(_Fitted):
    """Prices to log returns r_t = ln(p_t / p_{t-1}); inverse maps a return path to prices.

    fit remembers the last training price p_T; inverse(r_hat) = p_T * exp(cumsum(r_hat)),
    so a path of return forecasts becomes a path of price forecasts (scope update).
    """

    name = "log_return"

    def __init__(self) -> None:
        self.last_price: float | None = None

    def fit(self, prices: pd.Series) -> Self:
        if len(prices) < 2:
            raise TransformError("log returns need at least 2 prices")
        if (prices <= 0).any() or prices.isna().any():
            raise TransformError("log returns need positive, gap-free prices on the slice")
        self.last_price = float(prices.iloc[-1])
        self._fitted = True
        return self

    def transform(self, prices: pd.Series) -> pd.Series:
        self._check()
        r = np.log(prices).diff().iloc[1:]
        r.name = prices.name
        return r

    def inverse(self, x: np.ndarray) -> np.ndarray:
        self._check()
        return self.last_price * np.exp(np.cumsum(np.asarray(x, dtype=float)))


# ------------------------------------------------------------ slice repair and flags


class Interpolator(_Fitted):
    """Linear interpolation between observed neighbours inside the slice.

    Never extrapolates: leading NaN (before the first observation) are dropped, and a
    trailing NaN is an error because the engine skips origins whose last value is
    missing. Nothing after the origin is ever seen (leakage source 6).
    """

    name = "interpolate"

    def fit(self, y: pd.Series) -> Self:
        if y.isna().all():
            raise TransformError("slice has no observed values")
        if pd.isna(y.iloc[-1]):
            raise TransformError("last value of the slice is missing; origin must be skipped")
        self.n_filled = 0
        self._fitted = True
        return self

    def transform(self, y: pd.Series) -> pd.Series:
        self._check()
        first = y.first_valid_index()
        y = y.loc[first:]
        filled = y.interpolate(method="linear", limit_area="inside")
        self.n_filled = int(y.isna().sum())
        return filled

    def inverse(self, x: np.ndarray) -> np.ndarray:
        return np.asarray(x, dtype=float)


class OutlierFlagger(_Fitted):
    """Trailing robust z-score: z_t = (y_t - median of the previous w) / (1.4826 MAD).

    w = max(2m, 20). Flags |z| > 4. Flags are recorded, never used to change values
    (Phase 1: flag only). Only past values inside the slice enter each z_t (source 5).
    """

    name = "outlier_flags"
    THRESHOLD = 4.0

    def __init__(self, m: int) -> None:
        self.window = max(2 * m, 20)
        self.flags: np.ndarray | None = None

    def fit(self, y: pd.Series) -> Self:
        v = y.to_numpy(dtype=float)
        w = self.window
        flags = np.zeros(len(v), dtype=bool)
        if len(v) > w:
            windows = np.lib.stride_tricks.sliding_window_view(v[:-1], w)  # past w, per t
            med = np.median(windows, axis=1)
            mad = np.median(np.abs(windows - med[:, None]), axis=1)
            scale = 1.4826 * mad
            cur = v[w:]
            with np.errstate(divide="ignore", invalid="ignore"):
                z = np.where(scale > 0, np.abs(cur - med) / scale, 0.0)
            flags[w:] = z > self.THRESHOLD
        self.flags = flags
        self._fitted = True
        return self

    @property
    def n_flags(self) -> int:
        self._check()
        return int(self.flags.sum())

    def transform(self, y: pd.Series) -> pd.Series:
        self._check()
        return y

    def inverse(self, x: np.ndarray) -> np.ndarray:
        return np.asarray(x, dtype=float)
