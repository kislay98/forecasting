"""Parameter uncertainty for the GARCH family (Phase 7, OP-3).

The point-estimate models in variance.py run every simulated path with the fitted
parameters, so each path is conditional on the fit being exactly right. The intervals
they publish are therefore too narrow: the sign of that error is known, and this module
exists to measure its size.

Method, and the approximation it makes
---------------------------------------
No refits. `arch` already returns the asymptotic covariance of the estimates
(`param_cov`, the robust sandwich by default). One parameter vector is drawn per
simulated path from a multivariate Normal centred on the point estimate, and that path
runs the variance recursion with its own parameters:

  1. Draw theta_i ~ N(theta_hat, param_cov), i = 1..n_paths, in arch's percent units.
  2. Reject a draw that breaks positivity or stationarity, the same constraints arch
     imposes on the fit (omega > 0, alpha >= 0, alpha + gamma >= 0, beta >= 0,
     alpha + gamma / 2 + beta < 1), and redraw. The rejection rate is reported per
     origin in the row's `variant`, because a high rate is a finding in itself: it says
     the fit sits near the boundary.
  3. Re-run the in-sample variance filter with theta_i, so the starting variance
     sigma^2_{t+1|t} is the one theta_i implies. The filter runs back far enough that
     the point fit's variance where it starts carries a weight below 1e-8 on the last
     day (filter_span); on these fits that is a few hundred days rather than the whole
     slice, which is most of the difference in cost. This is the
     only channel parameter error has at h = 1, and it would be silently lost if every
     path started from the point fit's variance.
  4. Innovations are drawn exactly as the point model draws them (same generator, same
     order), so with a zero covariance matrix this model reproduces the point model bit
     for bit. The parameter draws come from a separate stream seeded from `seed`.

This is an approximation, not a posterior. The covariance is asymptotic, the Normal is
symmetric where the likelihood near the stationarity boundary is not, and rejection
sampling truncates the Normal, which moves the mean of the accepted draws away from the
boundary. The standardised residuals the innovations are resampled from are the point
fit's; under theta_i they would differ slightly, and that second-order effect is ignored.
A bootstrap over refits would avoid most of this and costs a refit per replication,
which is why it is not the choice here.

Step 3 is written so that a draw equal to the point estimate starts from exactly arch's
own one-step variance: the per-draw start is arch's value plus the difference between
this module's filter at theta_i and at theta_hat. The two filters are computed in the
same array, so a zero-covariance draw adds exactly 0.0.
"""

from __future__ import annotations

import warnings
from collections.abc import Sequence

import numpy as np

from forecasting.errors import FitError, NotFittedError
from forecasting.models.base import ForecastResult
from forecasting.models.variance import GARCH, GJRGARCH, SCALE

# Spawn keys that separate the parameter stream and the single-day prediction stream
# from anything else derived from the fold seed.
PARAM_KEY = 7
PREDICT_KEY = 71

# Draw in rounds of n_paths until n_paths are accepted. 200 rounds means giving up only
# when more than 99.5% of draws are rejected, at which point the covariance says nothing
# useful about a stationary model and the fold becomes a typed failure.
MAX_ROUNDS = 200

# The module's own filter at theta_hat must agree with arch's one-step variance. It is
# only used as a difference, so a small disagreement is harmless, but a large one means
# the recursion here is not the model arch fitted.
FILTER_TOLERANCE = 1e-6

# The per-draw filter starts from the point fit's variance far enough back that this
# start carries less than this weight on the last day's variance (filter_span).
START_WEIGHT = 1e-8

# Paths for single-day quantiles when the target is `returns` (not the P7 design).
PREDICT_PATHS = 10000


def split_parameters(draws: np.ndarray, names: Sequence[str]) -> tuple[np.ndarray, ...]:
    """(omega, alpha, gamma, beta) columns from an (n, k) array of arch parameter draws.
    gamma is zero for the symmetric model."""
    idx = {n: i for i, n in enumerate(names)}
    omega = draws[:, idx["omega"]]
    alpha = draws[:, idx["alpha[1]"]]
    beta = draws[:, idx["beta[1]"]]
    gamma = draws[:, idx["gamma[1]"]] if "gamma[1]" in idx else np.zeros(len(draws))
    return omega, alpha, gamma, beta


def admissible(draws: np.ndarray, names: Sequence[str]) -> np.ndarray:
    """Boolean mask: the draw satisfies arch's positivity and stationarity constraints.

    Stationarity uses the same persistence as variance.py (alpha + gamma / 2 + beta, the
    gamma term halved because a symmetric innovation is negative half the time) and the
    same strict inequality, so a draw the point model would have refused is refused here.
    """
    omega, alpha, gamma, beta = split_parameters(draws, names)
    persist = alpha + 0.5 * gamma + beta
    ok = (omega > 0) & (alpha >= 0) & (alpha + gamma >= 0) & (beta >= 0) & (persist < 1.0)
    return ok & np.isfinite(draws).all(axis=1)


def draw_parameters(
    theta: np.ndarray,
    cov: np.ndarray,
    names: Sequence[str],
    n: int,
    rng: np.random.Generator,
    max_rounds: int = MAX_ROUNDS,
) -> tuple[np.ndarray, float]:
    """n admissible draws from N(theta, cov), and the share of draws rejected.

    The covariance is factorised by eigendecomposition with negative eigenvalues clipped
    to zero, so a positive semi-definite matrix, including the zero matrix, is fine.
    A zero matrix returns theta exactly, n times: the noise term is an exact 0.0.
    """
    theta = np.asarray(theta, dtype=float)
    cov = np.asarray(cov, dtype=float)
    k = len(theta)
    if cov.shape != (k, k) or not np.isfinite(cov).all() or not np.isfinite(theta).all():
        raise ValueError("parameter covariance must be a finite k x k matrix")
    cov = 0.5 * (cov + cov.T)
    s, u = np.linalg.eigh(cov)
    root = u * np.sqrt(np.clip(s, 0.0, None))[None, :]
    kept: list[np.ndarray] = []
    have = drawn = 0
    for _ in range(max_rounds):
        z = rng.standard_normal((n, k))
        d = theta[None, :] + z @ root.T
        ok = admissible(d, names)
        drawn += n
        kept.append(d[ok])
        have += int(ok.sum())
        if have >= n:
            return np.concatenate(kept)[:n], 1.0 - have / drawn
    raise FitError(
        "parameter_uncertainty",
        f"only {have} of {drawn} parameter draws were admissible "
        f"(rejection rate {1 - have / drawn:.4f})",
    )


def filter_span(beta: np.ndarray, n: int, weight: float = START_WEIGHT) -> int:
    """How many trailing observations the per-draw filter has to run over.

    The filter starts from the point fit's variance on the first day it covers, and that
    start enters the last day's variance with weight beta^k after k steps (each step
    multiplies it by beta and adds nothing else of it). Running back far enough that
    max(beta)^k < weight bounds the point fit's influence on any draw's starting variance
    by that weight. With beta around 0.9 that is about 130 days; the whole slice is used
    when max(beta) is so close to 1 that no shorter span is enough.
    """
    b = float(np.max(beta)) if len(beta) else 0.0
    if b <= 0.0:
        return min(n, 1)
    if b >= 1.0:
        return n
    return int(min(n, np.ceil(np.log(weight) / np.log(b))))


def filtered_variance(
    e: np.ndarray,
    var_first: float,
    omega: np.ndarray,
    alpha: np.ndarray,
    gamma: np.ndarray,
    beta: np.ndarray,
) -> np.ndarray:
    """sigma^2_{T+1|T} for each parameter vector, filtering the training residuals e.

    arch's recursion for a zero-mean GJR-GARCH(1,1,1) (gamma = 0 for GARCH(1,1)):
        sigma^2_t = omega + (alpha + gamma * 1[e_{t-1} < 0]) e_{t-1}^2 + beta sigma^2_{t-1}
    started from var_first, the point fit's variance on the first day e covers. Using
    the point value for every draw is the one place theta_hat leaks into a draw's
    filter; its weight on the last day is beta^len(e), which filter_span bounds.
    """
    e = np.asarray(e, dtype=float)
    e2 = e**2
    neg = e < 0
    a_neg = alpha + gamma
    var = np.full(len(omega), float(var_first))
    for t in range(len(e)):
        var = omega + (a_neg if neg[t] else alpha) * e2[t] + beta * var
    return var


def run_paths(
    z: np.ndarray,
    var0: np.ndarray,
    omega: np.ndarray,
    alpha: np.ndarray,
    gamma: np.ndarray,
    beta: np.ndarray,
) -> np.ndarray:
    """The recursion of variance.GARCH.simulate, one parameter vector per path, in arch's
    percent units. Written with the same operations in the same order, so equal
    parameters give equal paths bit for bit (a test pins this)."""
    n_paths, h = z.shape
    var = np.asarray(var0, dtype=float)
    out = np.empty((n_paths, h))
    for s in range(h):
        e = np.sqrt(var) * z[:, s]
        out[:, s] = e
        var = omega + (alpha + gamma * (e < 0)) * e**2 + beta * var
    return out


class _ParameterUncertainty:
    """Mixin over a fitted variance.GARCH subclass. The fit is the point model's fit,
    unchanged: only simulation (and single-day prediction) differs."""

    rejection_rate: float = float("nan")

    # -- what is drawn ---------------------------------------------------------
    def _param_cov(self) -> np.ndarray:
        """The covariance the draws use. A hook so tests can substitute a known one."""
        return np.asarray(self._res.param_cov, dtype=float)

    def _start_variance(self) -> float:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            f = self._res.forecast(horizon=1, reindex=False)
        return float(np.asarray(f.variance.to_numpy())[-1, 0])

    def parameter_draws(self, n: int) -> tuple[np.ndarray, list[str], float]:
        """n admissible draws (percent units), the parameter names, the rejection rate.
        Drawn from a generator seeded from `seed` alone, so repeated calls agree."""
        names = [str(x) for x in self._res.params.index]
        theta = self._res.params.to_numpy(dtype=float)
        rng = np.random.default_rng(np.random.SeedSequence(self.seed, spawn_key=(PARAM_KEY,)))
        try:
            draws, rate = draw_parameters(theta, self._param_cov(), names, n, rng)
        except ValueError as e:
            raise FitError(self.name, str(e)) from e
        except FitError as e:
            raise FitError(self.name, e.reason) from e
        return draws, names, rate

    def _start_per_draw(self, draws: np.ndarray, names: list[str]) -> np.ndarray:
        """sigma^2_{t+1|t} for each draw: arch's value plus (filter at draw - filter at
        theta_hat), both filters computed in one array."""
        theta = self._res.params.to_numpy(dtype=float)[None, :]
        both = np.vstack([theta, draws])
        omega, alpha, gamma, beta = split_parameters(both, names)
        e = np.asarray(self._v, dtype=float) * SCALE
        cv = np.asarray(self._res.conditional_volatility, dtype=float)
        start = len(e) - filter_span(beta, len(e))
        f = filtered_variance(e[start:], float(cv[start]) ** 2, omega, alpha, gamma, beta)
        v_hat = self._start_variance()
        if abs(f[0] - v_hat) > FILTER_TOLERANCE * max(1.0, v_hat):
            raise FitError(
                self.name, f"variance filter disagrees with arch: {f[0]:.6g} vs {v_hat:.6g}"
            )
        var0 = v_hat + (f[1:] - f[0])
        if not np.isfinite(var0).all() or (var0 <= 0).any():
            raise FitError(self.name, "a parameter draw gave a non-positive starting variance")
        return var0

    def _paths(self, h: int, n_paths: int, rng: np.random.Generator) -> np.ndarray:
        if not self._fitted:
            raise NotFittedError(f"{self.name}: simulate before fit")
        # Innovations first, from the engine's generator, exactly as the point model does.
        z = self._draw(h, n_paths, rng)
        draws, names, rate = self.parameter_draws(n_paths)
        var0 = self._start_per_draw(draws, names)
        omega, alpha, gamma, beta = split_parameters(draws, names)
        self.rejection_rate = rate
        self.variant = f"{self._point_variant} | draws rejected {rate:.4f}"
        return run_paths(z, var0, omega, alpha, gamma, beta) / SCALE

    # -- the model interface ---------------------------------------------------
    def _fit_variance(self, v: np.ndarray) -> np.ndarray:
        sd = super()._fit_variance(v)
        self._point_variant = self.variant
        self.variant = f"{self.variant} | parameter draws"
        return sd

    def simulate(self, h: int, n_paths: int, rng: np.random.Generator) -> np.ndarray:
        """(n_paths, h) simulated one-step returns, one parameter vector per path."""
        return self._paths(h, n_paths, rng)

    def predict(self, h: int, levels: Sequence[float] = ()) -> ForecastResult:
        """Single-day quantiles for the `returns` target, from simulated paths.

        The point model's single-day intervals are analytic (the variance forecast times
        an empirical quantile of the standardised residuals). Under parameter uncertainty
        the predictive distribution is a mixture over parameters, which has no closed
        form here, so the quantiles are read from PREDICT_PATHS simulated single-day
        returns per horizon. Comparing this against the point model therefore mixes the
        parameter effect with simulation noise; Phase 7 reads the h = 1 effect from the
        cumulative shape instead, where both models simulate.
        """
        if not levels:
            return super().predict(h, ())
        if not self._fitted:
            raise NotFittedError(f"{self.name}: predict before fit")
        rng = np.random.default_rng(np.random.SeedSequence(self.seed, spawn_key=(PREDICT_KEY,)))
        paths = self._paths(h, PREDICT_PATHS, rng)
        lower: dict[float, np.ndarray] = {}
        upper: dict[float, np.ndarray] = {}
        for lv in levels:
            lo, hi = np.quantile(paths, [(1 - lv) / 2, (1 + lv) / 2], axis=0)
            if (lo > 0).any() or (hi < 0).any():
                raise FitError(self.name, "simulated quantiles do not straddle zero")
            lower[lv], upper[lv] = lo, hi
        return ForecastResult(mean=np.zeros(h), lower=lower, upper=upper, info=self._info())

    def _info(self):
        info = super()._info()
        info["rejection_rate"] = self.rejection_rate
        return info


class GARCHPU(_ParameterUncertainty, GARCH):
    """GARCH(1,1) with empirical tails, parameters drawn per path."""

    name = "garch_pu"


class GJRGARCHPU(_ParameterUncertainty, GJRGARCH):
    """GJR-GARCH(1,1,1) with empirical tails, parameters drawn per path. The P7 primary."""

    name = "gjr_garch_pu"


__all__ = [
    "GARCHPU",
    "GJRGARCHPU",
    "admissible",
    "draw_parameters",
    "filtered_variance",
    "run_paths",
    "split_parameters",
]
