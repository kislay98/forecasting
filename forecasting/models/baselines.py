"""Baselines (spec: Model set; scope update for return series).

Level series: naive, seasonal naive, drift, SMA(k).
Return series: zero return, historical mean return, last return, SMA(k) of returns.
The price random walk equals the zero-return forecast in log space (p_hat = p_T is
r_hat = 0), so it is stored once, as zero_return; its level_pred column is the random
walk price forecast (DECISIONS.md M2-4).

Formulas, with sigma from one-step in-sample residuals and k_h = floor((h - 1) / m):
  naive           y_T                              sd_h = sigma sqrt(h)
  seasonal naive  y_{T+h-m(k_h+1)}                 sd_h = sigma sqrt(k_h + 1)
  drift           y_T + h (y_T - y_1) / (T - 1)     sd_h = sigma sqrt(h (1 + h / (T - 1)))
  SMA(k)          mean of the last k                no interval
  zero return     0                                 sd_h = sqrt(mean r^2)
  mean return     mean of the slice                 sd_h = s sqrt(1 + 1/T)
  last return     r_T (naive on returns)            sd_h = sigma sqrt(h)
"""

from __future__ import annotations

import numpy as np

from forecasting.models.base import BaseForecaster, residual_sigma


class Naive(BaseForecaster):
    name = "naive"
    min_obs = 2

    def _fit(self, v: np.ndarray) -> None:
        self._e = np.diff(v)
        self._sigma = residual_sigma(self._e, 0, self.name)

    def _predict(self, h: int):
        steps = np.arange(1, h + 1)
        return np.full(h, self._v[-1]), self._sigma * np.sqrt(steps)

    def _residuals(self) -> np.ndarray:
        return self._e


class LastReturn(Naive):
    name = "last_return"


class SeasonalNaive(BaseForecaster):
    name = "seasonal_naive"

    def __init__(self, m: int) -> None:
        super().__init__()
        if m < 2:
            raise ValueError("seasonal naive needs m >= 2")
        self.m = m
        self.min_obs = m + 1

    def _fit(self, v: np.ndarray) -> None:
        self._e = v[self.m :] - v[: -self.m]
        self._sigma = residual_sigma(self._e, 0, self.name)

    def _predict(self, h: int):
        n, m = len(self._v), self.m
        steps = np.arange(1, h + 1)
        k = (steps - 1) // m
        idx = (n - 1) + steps - m * (k + 1)
        return self._v[idx], self._sigma * np.sqrt(k + 1)

    def _residuals(self) -> np.ndarray:
        return self._e

    def _info(self):
        return {"m": self.m}


class Drift(BaseForecaster):
    name = "drift"
    min_obs = 3

    def _fit(self, v: np.ndarray) -> None:
        n = len(v)
        self._slope = (v[-1] - v[0]) / (n - 1)
        self._e = np.diff(v) - self._slope
        self._sigma = residual_sigma(self._e, 1, self.name)

    def _predict(self, h: int):
        n = len(self._v)
        steps = np.arange(1, h + 1)
        mean = self._v[-1] + steps * self._slope
        return mean, self._sigma * np.sqrt(steps * (1 + steps / (n - 1)))

    def _residuals(self) -> np.ndarray:
        return self._e

    def _info(self):
        return {"slope": float(self._slope)}


class SMA(BaseForecaster):
    """Trailing simple moving average (never centred: leakage source 3). No interval."""

    def __init__(self, k: int) -> None:
        super().__init__()
        if k < 1:
            raise ValueError("k must be >= 1")
        self.k = k
        self.min_obs = k
        self.name = f"sma_{k}"

    def _fit(self, v: np.ndarray) -> None:
        self._level = float(v[-self.k :].mean())

    def _predict(self, h: int):
        return np.full(h, self._level), None

    def _residuals(self) -> np.ndarray:
        v, k = self._v, self.k
        if len(v) <= k:
            return np.array([])
        trailing = np.convolve(v, np.ones(k) / k, mode="valid")[:-1]  # mean of v[t-k..t-1]
        return v[k:] - trailing

    def _info(self):
        return {"k": self.k}


class ZeroReturn(BaseForecaster):
    name = "zero_return"
    min_obs = 2

    def _fit(self, v: np.ndarray) -> None:
        self._sigma = residual_sigma(v, 0, self.name)

    def _predict(self, h: int):
        return np.zeros(h), np.full(h, self._sigma)

    def _residuals(self) -> np.ndarray:
        return self._v


class MeanReturn(BaseForecaster):
    """Historical mean return over the training slice (expanding, or rolling in that window)."""

    name = "mean_return"
    min_obs = 3

    def _fit(self, v: np.ndarray) -> None:
        self._mu = float(v.mean())
        self._e = v - self._mu
        s = residual_sigma(self._e, 1, self.name)
        self._sd = s * np.sqrt(1 + 1 / len(v))

    def _predict(self, h: int):
        return np.full(h, self._mu), np.full(h, self._sd)

    def _residuals(self) -> np.ndarray:
        return self._e

    def _info(self):
        return {"mu": self._mu}
