"""Conditional variance models for return series (Phase 2 lite).

Phase 1 answered the mean question for Nifty 50: nothing beats a zero-return
forecast (h* = 0), while ARCH-LM rejected at 100% of test origins and the AR
model's nominal 80% intervals covered 94 to 97%. So the point forecast here is
fixed at zero and is not the product. The interval is the product.

Every model in this module forecasts sigma_{t+h|t} for a single-period return h
steps ahead, which is what the store's y_true holds: log(p_{t+h} / p_{t+h-1}),
not a cumulative return. No path simulation is needed for that.

Two things are varied on purpose, so the run can tell them apart:

  variance   constant (Phase 1's zero_return), EWMA, GARCH(1,1), GJR-GARCH(1,1,1)
  tails      Normal quantiles, or the empirical distribution of the standardised
             residuals (filtered historical simulation)

`residuals()` returns STANDARDISED residuals, not raw ones. The engine runs
Ljung-Box and ARCH-LM on whatever residuals() gives it, and for a variance model
the question worth asking is whether the conditional variance removed the
clustering. On a raw return series ARCH-LM would just re-report U6.
"""

from __future__ import annotations

import warnings
from typing import Any

import numpy as np

from forecasting.errors import FitError
from forecasting.models.base import BaseForecaster

# arch fits in percent units; daily log returns are around 0.01 and the optimiser
# behaves badly at that scale. Everything is divided back before it leaves.
SCALE = 100.0

# RiskMetrics: the decay the 1996 technical document fixes for daily data. It is a
# constant, not a fitted parameter, which is the point of having it here.
EWMA_LAMBDA = 0.94

MIN_STD_RESID = 100  # below this the empirical tail quantiles are noise


class VarianceModel(BaseForecaster):
    """Zero mean, a conditional sd per horizon, quantiles from a chosen tail rule."""

    uses_transform = False  # the target is already returns; a log transform is meaningless
    expensive = True
    min_obs = 500  # about two years of trading days before a persistence estimate is worth it
    empirical_tails = True

    def __init__(self) -> None:
        super().__init__()
        self.seed: int = 0
        self.variant = ""
        self._z: np.ndarray | None = None  # standardised residuals

    # -- the two hooks a subclass fills in -------------------------------------
    def _fit_variance(self, v: np.ndarray) -> np.ndarray:
        """Return the in-sample conditional sd, same length as v, in return units."""
        raise NotImplementedError

    def _forecast_sd(self, h: int) -> np.ndarray:
        """Return sigma_{t+1|t} .. sigma_{t+h|t} in return units."""
        raise NotImplementedError

    # -- plumbing --------------------------------------------------------------
    def _fit(self, v: np.ndarray) -> None:
        sd = np.asarray(self._fit_variance(v), dtype=float)
        if sd.shape != v.shape:
            raise FitError(self.name, "conditional sd has the wrong length")
        if not np.isfinite(sd).all() or (sd <= 0).any():
            raise FitError(self.name, "conditional sd is not finite and positive")
        self._z = v / sd

    def _predict(self, h: int):
        return np.zeros(h), self._forecast_sd(h)

    def predict(self, h: int, levels=()):
        res = super().predict(h, levels if not self.empirical_tails else ())
        if not self.empirical_tails or not levels:
            return res
        sd = self._forecast_sd(h)
        z = self._z
        if z is None or len(z) < MIN_STD_RESID:
            raise FitError(self.name, f"need {MIN_STD_RESID} standardised residuals for tails")
        lower: dict[float, np.ndarray] = {}
        upper: dict[float, np.ndarray] = {}
        for lv in levels:
            p = (1.0 - lv) / 2.0
            ql, qu = np.quantile(z, [p, 1.0 - p])
            if ql > 0 or qu < 0:
                raise FitError(self.name, "standardised residual quantiles do not straddle zero")
            lower[lv], upper[lv] = sd * ql, sd * qu
        return type(res)(mean=res.mean, lower=lower, upper=upper, info=self._info())

    def _residuals(self) -> np.ndarray:
        return np.asarray(self._z, dtype=float)

    def _info(self) -> dict[str, Any]:
        return {"variant": self.variant}


class ConstantSigma(VarianceModel):
    """No conditional variance at all, empirical tails. Isolates what the tails buy
    on their own, against Phase 1's zero_return, which is this with Normal tails."""

    name = "zero_return_fhs"
    expensive = False
    min_obs = MIN_STD_RESID

    def _fit_variance(self, v: np.ndarray) -> np.ndarray:
        sd = float(np.sqrt(np.mean(v**2)))
        if sd <= 0:
            raise FitError(self.name, "training returns are all zero")
        self.variant = "constant sigma"
        self._sd = sd
        return np.full(len(v), sd)

    def _forecast_sd(self, h: int) -> np.ndarray:
        return np.full(h, self._sd)


class EWMA(VarianceModel):
    """RiskMetrics. sigma^2_t = lambda sigma^2_{t-1} + (1 - lambda) r^2_{t-1}, with
    lambda fixed at 0.94 rather than fitted. Flat in h: the EWMA forecast of variance
    at every horizon is the current level, since the recursion has no mean to revert to."""

    name = "ewma"
    expensive = False
    min_obs = MIN_STD_RESID

    def _fit_variance(self, v: np.ndarray) -> np.ndarray:
        lam = EWMA_LAMBDA
        var = np.empty(len(v))
        var[0] = float(np.mean(v**2))
        for i in range(1, len(v)):
            var[i] = lam * var[i - 1] + (1 - lam) * v[i - 1] ** 2
        self._next = lam * var[-1] + (1 - lam) * v[-1] ** 2
        if not np.isfinite(self._next) or self._next <= 0:
            raise FitError(self.name, "EWMA variance collapsed")
        self.variant = f"EWMA(lambda={lam})"
        return np.sqrt(var)

    def _forecast_sd(self, h: int) -> np.ndarray:
        return np.full(h, float(np.sqrt(self._next)))


class GARCH(VarianceModel):
    """GARCH(1,1), or GJR-GARCH(1,1,1) when asymmetric, zero mean, fitted by arch.

    The multi-step forecast is arch's analytic one, which decays from the current
    conditional variance towards the unconditional level at rate (alpha + beta).
    """

    name = "garch"
    asymmetric = False

    def _fit_variance(self, v: np.ndarray) -> np.ndarray:
        from arch import arch_model  # imported here so the module loads without a fit

        o = 1 if self.asymmetric else 0
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            am = arch_model(
                v * SCALE, mean="Zero", vol="GARCH", p=1, o=o, q=1, dist="normal", rescale=False
            )
            try:
                self._res = am.fit(disp="off", show_warning=False)
            except Exception as e:  # arch raises a wide range of numeric errors
                raise FitError(self.name, f"fit failed: {e}") from e
        params = self._res.params
        if not np.isfinite(self._res.params.to_numpy()).all():
            raise FitError(self.name, "fitted parameters are not finite")
        persist = float(params.get("alpha[1]", 0.0)) + float(params.get("beta[1]", 0.0))
        if self.asymmetric:
            persist += 0.5 * float(params.get("gamma[1]", 0.0))
        if persist >= 1.0:
            raise FitError(self.name, f"non-stationary fit, persistence {persist:.4f}")
        self.persistence = persist
        self.variant = f"{'GJR-' if self.asymmetric else ''}GARCH(1,1) persistence {persist:.3f}"
        return np.asarray(self._res.conditional_volatility, dtype=float) / SCALE

    def _forecast_sd(self, h: int) -> np.ndarray:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            f = self._res.forecast(horizon=h, reindex=False)
        var = np.asarray(f.variance.to_numpy()[-1], dtype=float)
        if var.shape != (h,) or not np.isfinite(var).all() or (var <= 0).any():
            raise FitError(self.name, "variance forecast is not finite and positive")
        return np.sqrt(var) / SCALE

    def _info(self) -> dict[str, Any]:
        return {"variant": self.variant, "persistence": getattr(self, "persistence", float("nan"))}


class GARCHNormal(GARCH):
    """GARCH(1,1) with Normal quantiles. The ablation that says whether the gain comes
    from the conditional variance or from the shape of the standardised residuals."""

    name = "garch_normal"
    empirical_tails = False


class GJRGARCH(GARCH):
    """Leverage: a negative return raises tomorrow's variance more than a positive one
    of the same size. On equity index returns this is the best documented asymmetry."""

    name = "gjr_garch"
    asymmetric = True


__all__ = ["EWMA", "GARCH", "GJRGARCH", "ConstantSigma", "GARCHNormal"]
