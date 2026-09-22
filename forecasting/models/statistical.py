"""Statistical forecasters on statsmodels (D2) with our own selection (D3).

All of them set uses_transform, so the engine hands them the fold's variance-
transformed slice (auto, log or Box-Cox, fitted on the slice only) and back-transforms
their points and bounds (D8). Every test and selection step (KPSS, seasonal strength,
AICc, STL) runs inside fit, on the slice it is given (leakage sources 7 and 8).

statsmodels pitfalls handled here (both hit during the research experiment):
  - array input breaks ETS prediction: every model is fitted on a pandas Series
  - ETS simulation takes `rng`, not `random_state`: multiplicative intervals are
    simulated with a generator seeded per (series, window, origin, model) (D7)

| name   | model                                                   | intervals              |
| ses    | ETS(A,N,N)                                              | analytic               |
| ets    | ETS by AICc over error {A,M}, trend {N,A,Ad},           | analytic, or simulated |
|        | season {N,A,M}; M only if y > 0; no (A,*,M)             | for multiplicative     |
| sarima | D by seasonal strength, d by KPSS, (p,q) <= 2,          | analytic               |
|        | (P,Q) <= 1 by AICc; constant/drift only if d + D <= 1   |                        |
| theta  | statsmodels ThetaModel, theta = 2, 90% ACF season test  | analytic               |
| ar     | AR(p) on returns, p <= 5 by AICc on a common sample     | analytic               |
Seasonal ETS and SARIMA need m <= 24; above that (weekly) both run on an STL-adjusted
series and the seasonal part is continued with seasonal naive (STLAdjusted).
"""

from __future__ import annotations

import warnings
from collections.abc import Callable, Sequence
from contextlib import contextmanager
from typing import Any

import numpy as np
import pandas as pd
from statsmodels.tsa.ar_model import AutoReg
from statsmodels.tsa.exponential_smoothing.ets import ETSModel
from statsmodels.tsa.forecasting.theta import ThetaModel
from statsmodels.tsa.seasonal import STL
from statsmodels.tsa.statespace.sarimax import SARIMAX
from statsmodels.tsa.stattools import kpss

from forecasting.errors import FitError, NotFittedError
from forecasting.models.base import BaseForecaster, ForecastResult

MAX_SEASONAL_M = 24  # spec: seasonal ETS / SARIMA only when m <= 24
SEASONAL_STRENGTH_D = 0.64  # fpp3 nsdiffs threshold on STL seasonal strength
KPSS_ALPHA = 0.05
MAX_D = 2
AR_MAX_P = 5

# Numerical failures statsmodels raises on hard slices. Recorded as FitError rows.
NUMERIC_ERRORS = (ValueError, np.linalg.LinAlgError, ZeroDivisionError, IndexError, OverflowError)


@contextmanager
def quiet():
    """statsmodels warns on almost every hard fit; the outcome is judged by AICc and the
    contract check instead."""
    with warnings.catch_warnings(), np.errstate(all="ignore"):
        warnings.simplefilter("ignore")
        yield


def as_series(v: np.ndarray) -> pd.Series:
    return pd.Series(np.asarray(v, dtype=float))


def aicc_ok(x: Any) -> bool:
    return x is not None and np.isfinite(x)


class StatModel(BaseForecaster):
    """Shared plumbing: statsmodels intervals instead of Normal sd, a seed, quiet fits."""

    uses_transform = True
    expensive = True  # skipped at warm-up origins unless warmup_models: all
    min_obs = 4

    def __init__(self) -> None:
        super().__init__()
        self.seed: int = 0
        self.variant = ""

    def predict(self, h: int, levels: Sequence[float] = ()) -> ForecastResult:
        if not self._fitted:
            raise NotFittedError(f"{self.name}: predict before fit")
        try:
            with quiet():
                mean, lower, upper = self._forecast(h, tuple(levels))
        except NUMERIC_ERRORS as e:
            raise FitError(self.name, f"forecast failed: {e}") from e
        return ForecastResult(
            mean=np.asarray(mean, dtype=float),
            lower={k: np.asarray(v, dtype=float) for k, v in lower.items()},
            upper={k: np.asarray(v, dtype=float) for k, v in upper.items()},
            info={"variant": self.variant},
        )

    def _fit(self, v: np.ndarray) -> None:
        try:
            with quiet():
                self._fit_model(v)
        except NUMERIC_ERRORS as e:
            raise FitError(self.name, f"fit failed: {e}") from e

    def _residuals(self) -> np.ndarray:
        return np.asarray(self._res.resid, dtype=float)

    # hooks
    def _fit_model(self, v: np.ndarray) -> None:  # pragma: no cover
        raise NotImplementedError

    def _forecast(self, h: int, levels: tuple[float, ...]):  # pragma: no cover
        raise NotImplementedError


# ---------------------------------------------------------------- ETS


def ets_label(error: str, trend: str | None, damped: bool, season: str | None) -> str:
    t = "N" if trend is None else ("Ad" if damped else "A")
    return f"ETS({error[0].upper()},{t},{'N' if season is None else season[0].upper()})"


class ETSAuto(StatModel):
    """ETS selected by AICc. With allowed=(("add", None, False, None),) it is SES."""

    name = "ets"

    def __init__(self, m: int, variants: Sequence[tuple] | None = None) -> None:
        super().__init__()
        self.m = m
        self._fixed = tuple(variants) if variants is not None else None

    def allowed(self, v: np.ndarray) -> list[tuple[str, str | None, bool, str | None]]:
        if self._fixed is not None:
            return list(self._fixed)
        positive = bool((v > 0).all())
        errors = ["add", "mul"] if positive else ["add"]
        trends = [(None, False), ("add", False), ("add", True)]
        seasons: list[str | None] = [None]
        if 1 < self.m <= MAX_SEASONAL_M and len(v) >= 2 * self.m:
            seasons += ["add", "mul"] if positive else ["add"]
        out = []
        for e in errors:
            for tr, d in trends:
                for s in seasons:
                    if e == "add" and s == "mul":
                        continue  # unstable: excluded as in fpp3 / forecast::ets
                    out.append((e, tr, d, s))
        return out

    def _fit_model(self, v: np.ndarray) -> None:
        y = as_series(v)
        best = None
        for e, tr, d, s in self.allowed(v):
            try:
                res = ETSModel(
                    y,
                    error=e,
                    trend=tr,
                    damped_trend=d,
                    seasonal=s,
                    seasonal_periods=self.m if s else None,
                ).fit(disp=False)
            except NUMERIC_ERRORS:
                continue
            if aicc_ok(res.aicc) and (best is None or res.aicc < best[0]):
                best = (res.aicc, res, (e, tr, d, s))
        if best is None:
            raise FitError(self.name, "no ETS variant could be fitted")
        _, self._res, spec = best
        self.variant = ets_label(*spec)
        self._n = len(v)

    def _forecast(self, h, levels):
        pred = self._res.get_prediction(
            start=self._n, end=self._n + h - 1, rng=np.random.default_rng(self.seed)
        )
        mean = pred.predicted_mean.to_numpy()
        lower, upper = {}, {}
        for lv in levels:
            frame = pred.summary_frame(alpha=1 - lv)
            lower[lv] = frame["pi_lower"].to_numpy()
            upper[lv] = frame["pi_upper"].to_numpy()
        return mean, lower, upper


class SES(ETSAuto):
    name = "ses"

    def __init__(self) -> None:
        super().__init__(m=1, variants=[("add", None, False, None)])


# ---------------------------------------------------------------- SARIMA


def seasonal_strength(v: np.ndarray, m: int) -> float:
    """F_S = max(0, 1 - Var(R) / Var(S + R)) from a robust STL fitted on this slice."""
    with quiet():
        res = STL(as_series(v), period=m, robust=True).fit()
    r, s = np.asarray(res.resid), np.asarray(res.seasonal)
    denom = np.var(s + r)
    return 0.0 if denom <= 0 else max(0.0, 1 - np.var(r) / denom)


def kpss_ndiffs(v: np.ndarray, max_d: int = MAX_D) -> int:
    """Difference while KPSS rejects level stationarity at 5% (fpp3 ndiffs)."""
    d, w = 0, np.asarray(v, dtype=float)
    while d < max_d and len(w) > 10 and np.ptp(w) > 0:
        with quiet():  # InterpolationWarning outside the table; FutureWarning in 0.15
            _, pvalue, *_ = kpss(w, regression="c", nlags="auto")
        if pvalue >= KPSS_ALPHA:
            break
        w = np.diff(w)
        d += 1
    return d


class SARIMAAuto(StatModel):
    """SARIMA with d, D from tests on the slice and orders by AICc.

    search='stepwise' (default) walks the bounded space the Hyndman-Khandakar way;
    search='grid' fits all of it (36 models when seasonal). DECISIONS.md M4-2.
    """

    name = "sarima"
    MAX_P = MAX_Q = 2
    MAX_SP = MAX_SQ = 1

    def __init__(self, m: int, search: str = "stepwise") -> None:
        super().__init__()
        if search not in ("stepwise", "grid"):
            raise ValueError(search)
        self.m = m
        self.search = search
        self.n_fits = 0

    def _fit_one(self, y, order, sorder, trend):
        self.n_fits += 1
        try:
            res = SARIMAX(y, order=order, seasonal_order=sorder, trend=trend).fit(disp=False)
        except NUMERIC_ERRORS:
            return None
        return res if aicc_ok(res.aicc) else None

    def _fit_model(self, v: np.ndarray) -> None:
        m = self.m
        seasonal = 1 < m <= MAX_SEASONAL_M and len(v) >= 2 * m + 1
        D = 1 if seasonal and seasonal_strength(v, m) > SEASONAL_STRENGTH_D else 0
        w = v[m:] - v[:-m] if D else v
        d = kpss_ndiffs(w)
        trend = "c" if d + D <= 1 else "n"  # 'c' is the mean (d+D=0) or the drift (d+D=1)
        y = as_series(v)
        max_sp, max_sq = (self.MAX_SP, self.MAX_SQ) if seasonal else (0, 0)

        def spec(p, q, P, Q):
            sorder = (P, D, Q, m) if seasonal else (0, 0, 0, 0)
            return (p, d, q), sorder

        tried: dict[tuple, Any] = {}

        def score(key):
            if key not in tried:
                tried[key] = self._fit_one(y, *spec(*key), trend)
            r = tried[key]
            return np.inf if r is None else r.aicc

        def inside(k):
            p, q, P, Q = k
            return (
                0 <= p <= self.MAX_P
                and 0 <= q <= self.MAX_Q
                and 0 <= P <= max_sp
                and 0 <= Q <= max_sq
            )

        if self.search == "grid":
            keys = [
                (p, q, P, Q)
                for p in range(self.MAX_P + 1)
                for q in range(self.MAX_Q + 1)
                for P in range(max_sp + 1)
                for Q in range(max_sq + 1)
            ]
            for k in keys:
                score(k)
        else:
            starts = [(2, 2, 1, 1), (0, 0, 0, 0), (1, 0, 1, 0), (0, 1, 0, 1)]
            starts = list(
                dict.fromkeys((p, q, min(P, max_sp), min(Q, max_sq)) for p, q, P, Q in starts)
            )
            best = min(starts, key=score)
            moves = [
                (1, 0, 0, 0), (-1, 0, 0, 0), (0, 1, 0, 0), (0, -1, 0, 0),
                (1, 1, 0, 0), (-1, -1, 0, 0),
                (0, 0, 1, 0), (0, 0, -1, 0), (0, 0, 0, 1), (0, 0, 0, -1),
                (0, 0, 1, 1), (0, 0, -1, -1),
            ]  # fmt: skip
            improved = True
            while improved:
                improved = False
                for mv in moves:
                    k = tuple(a + b for a, b in zip(best, mv, strict=True))
                    if inside(k) and score(k) < score(best):
                        best, improved = k, True
                        break
        ok = {k: r for k, r in tried.items() if r is not None}
        if not ok:
            raise FitError(self.name, "no SARIMA order could be fitted")
        key = min(ok, key=lambda k: (ok[k].aicc, k))
        self._res = ok[key]
        p, q, P, Q = key
        s = f"({P},{D},{Q})[{m}]" if seasonal else ""
        self.variant = f"SARIMA({p},{d},{q}){s}" + ("+c" if trend == "c" else "")

    def _forecast(self, h, levels):
        fc = self._res.get_forecast(h)
        mean = np.asarray(fc.predicted_mean)
        lower, upper = {}, {}
        for lv in levels:
            ci = np.asarray(fc.conf_int(alpha=1 - lv))
            lower[lv], upper[lv] = ci[:, 0], ci[:, 1]
        return mean, lower, upper

    def _residuals(self) -> np.ndarray:
        # The first d + D m residuals come from the diffuse initialisation, not the model.
        burn = int(getattr(self._res, "loglikelihood_burn", 0))
        return np.asarray(self._res.resid, dtype=float)[burn:]


# ---------------------------------------------------------------- Theta


class Theta(StatModel):
    """statsmodels ThetaModel with theta = 2. Deseasonalises when its 90% ACF test at lag m
    passes (multiplicative if y > 0, else additive: method='auto')."""

    name = "theta"
    THETA = 2.0

    def __init__(self, m: int) -> None:
        super().__init__()
        self.m = m

    def _fit_model(self, v: np.ndarray) -> None:
        deseason = self.m > 1 and len(v) >= 2 * self.m
        model = ThetaModel(
            as_series(v),
            period=self.m if self.m > 1 else None,
            deseasonalize=deseason,
            use_test=True,
            method="auto",
        )
        self._res = model.fit()
        seasonal = bool(deseason and getattr(model, "_has_seasonality", False))
        kind = {"mul": "multiplicative", "add": "additive"}.get(getattr(model, "_method", ""), "")
        if seasonal and not kind:
            kind = "multiplicative" if (v > 0).all() else "additive"  # method='auto' rule
        self.variant = f"Theta({kind if seasonal else 'nonseasonal'})"

    def _forecast(self, h, levels):
        mean = self._res.forecast(h, theta=self.THETA).to_numpy()
        lower, upper = {}, {}
        for lv in levels:
            pi = self._res.prediction_intervals(h, theta=self.THETA, alpha=1 - lv)
            lower[lv], upper[lv] = pi["lower"].to_numpy(), pi["upper"].to_numpy()
        return mean, lower, upper

    def _residuals(self) -> np.ndarray:
        return np.array([])  # statsmodels' ThetaModel exposes no in-sample residuals


# ---------------------------------------------------------------- AR(p) on returns


class ARAuto(StatModel):
    """AR(p) with a constant; p in 0..5 chosen by AICc on a common sample (the first 5
    observations held back for every p), then refitted on the whole slice."""

    name = "ar"
    min_obs = 3 * AR_MAX_P

    def _fit_model(self, v: np.ndarray) -> None:
        y = as_series(v)
        best = None
        for p in range(AR_MAX_P + 1):
            res = AutoReg(y, lags=p, trend="c", hold_back=AR_MAX_P).fit()
            if aicc_ok(res.aicc) and (best is None or res.aicc < best[0]):
                best = (res.aicc, p)
        if best is None:
            raise FitError(self.name, "no AR order could be fitted")
        self.p = best[1]
        self._res = AutoReg(y, lags=self.p, trend="c").fit()
        self._n = len(v)
        self.variant = f"AR({self.p})"

    def _forecast(self, h, levels):
        pred = self._res.get_prediction(start=self._n, end=self._n + h - 1)
        mean = np.asarray(pred.predicted_mean)
        lower, upper = {}, {}
        for lv in levels:
            ci = np.asarray(pred.conf_int(alpha=1 - lv))
            lower[lv], upper[lv] = ci[:, 0], ci[:, 1]
        return mean, lower, upper


# ---------------------------------------------------------------- STL wrapper (m > 24)


class STLAdjusted(StatModel):
    """Fit a robust STL on the slice, model the seasonally adjusted series with the inner
    model, and continue the seasonal component with seasonal naive (STLForecast's rule).
    Interval bounds are shifted by the same seasonal term; seasonal uncertainty is not
    added (as in STLForecast)."""

    def __init__(self, inner: StatModel, m: int) -> None:
        super().__init__()
        self.inner = inner
        self.m = m
        self.name = inner.name
        self.min_obs = 2 * m + 1

    def _fit_model(self, v: np.ndarray) -> None:
        stl = STL(as_series(v), period=self.m, robust=True).fit()
        self._seasonal = np.asarray(stl.seasonal)
        self.inner.seed = self.seed
        self.inner.fit(as_series(v - self._seasonal))
        self.variant = f"STL+{self.inner.variant}"
        self._res = stl

    def _forecast(self, h, levels):
        r = self.inner.predict(h, levels)
        steps = np.arange(1, h + 1)
        k = (steps - 1) // self.m
        s = self._seasonal[len(self._seasonal) - 1 + steps - self.m * (k + 1)]
        return (
            r.mean + s,
            {lv: r.lower[lv] + s for lv in r.lower},
            {lv: r.upper[lv] + s for lv in r.upper},
        )

    def _residuals(self) -> np.ndarray:
        # The inner model's one-step residuals on the adjusted series, not the STL remainder.
        return self.inner.residuals()


def seasonal_or_stl(make: Callable[[int], StatModel], m: int) -> StatModel:
    """The model itself for m <= 24; above that, the non-seasonal model on STL-adjusted data."""
    return STLAdjusted(make(1), m) if m > MAX_SEASONAL_M else make(m)
