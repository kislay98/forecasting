"""Block-bootstrap innovations for the path simulators (Phase 6).

Filtered historical simulation draws each simulated day's innovation independently from
the fit's standardised residuals. That assumes the standardised residuals are iid, and
on this data they are not: Ljung-Box rejects on every variance model's standardised
residuals (OP-2, the prompt's section 7C). The models here are the same variance models
with one change: the innovations for a path are drawn as runs of consecutive standardised
residuals instead of one at a time, so whatever serial dependence the variance model left
behind is carried into the simulated path.

The scheme is the moving-block bootstrap (Kunsch 1989), not the stationary bootstrap of
Politis and Romano (1994). Three reasons, recorded as P6-2:

  - With a block length of 1 it makes exactly the random draws the iid sampler makes, in
    the same order, so the degenerate case is an identity rather than an approximation.
    The block models are then provably a one-parameter extension of the registered ones.
  - The stationary bootstrap's advantage is that the resampled series is stationary.
    That matters for a long resampled series; here each path is H = 20 days long and
    starts afresh at every origin. Its random block lengths add variance for the same
    mean block length (Lahiri 1999), and buy nothing at this path length.
  - It is the scheme the project already uses, with the same index rule (M5-15): block
    starts uniform on 0 .. n - b, not circular, truncated to the path length.

The block length is fixed at 10, by a rule stated before any test origin was scored
(P6-3): it is the Ljung-Box lag that established the dependence (M6-2 registers lag 10
for m = 1), so a block of 10 carries every autocovariance that test reads and no longer.
At H = 20 that is two blocks per path. The textbook n^(1/3) rate (Hall, Horowitz and
Jing 1995) gives 8 to 20 at the training sizes in these runs, so 10 is inside it, at the
short end, which is the conservative end for the risk below.

The risk, which is registered with Phase 6 rather than discovered after it: a GARCH or
EWMA path already has memory, because a large drawn innovation raises the next step's
variance. Feeding the recursion blocks of innovations that are themselves clustered in
size counts that persistence twice, and can make the cumulative distribution too wide.
`scripts/block_vs_iid.py` measures it on the same fitted model.

A known cost of the non-circular scheme: residual i can be drawn from min(i + 1, b,
n - i) of the n - b + 1 block starts, so the first and last b - 1 standardised residuals
are drawn less often than the rest. The last nine are the most recent days of the
training window. At n of 500 or more this is a small reweighting, and it is stated
rather than corrected, because a circular scheme would join the newest residual to the
oldest one, which is a dependence the data never had.

Nothing in forecasting/models/variance.py is changed. The five registered models keep
their names and their draws; these are new names (P6-1).
"""

from __future__ import annotations

import numpy as np

from forecasting.errors import FitError
from forecasting.models.variance import EWMA, GARCH, GJRGARCH, MIN_STD_RESID

BLOCK_LENGTH = 10  # registered in configs/phase6/gate.yaml; see the module docstring


def block_indices(n: int, h: int, n_paths: int, rng: np.random.Generator, b: int) -> np.ndarray:
    """(n_paths, h) indices into a series of length n, in moving blocks of length b.

    Each path is ceil(h / b) blocks laid end to end and truncated to h. Block starts are
    uniform on 0 .. n - b. With b = 1 this is rng.integers(0, n, (n_paths, h)), which is
    the call numpy's Generator.choice makes for a sample with replacement, so the iid
    sampler's draws are reproduced exactly.
    """
    if n < 1 or h < 1 or n_paths < 1:
        raise ValueError("n, h and n_paths must be positive")
    if b < 1:
        raise ValueError("block length must be at least 1")
    b = min(b, n)
    k = -(-h // b)  # blocks per path
    starts = rng.integers(0, n - b + 1, size=(n_paths, k))
    if b == 1:
        return starts
    idx = starts[:, :, None] + np.arange(b)[None, None, :]
    return idx.reshape(n_paths, k * b)[:, :h]


def block_draw(
    z: np.ndarray, h: int, n_paths: int, rng: np.random.Generator, b: int = BLOCK_LENGTH
) -> np.ndarray:
    """(n_paths, h) innovations drawn from z in moving blocks of length b."""
    z = np.asarray(z, dtype=float)
    return z[block_indices(len(z), h, n_paths, rng, b)]


class _BlockSampler:
    """Replaces the iid draw of standardised residuals with a moving-block draw.

    Everything else, the fit, the variance recursion along the path, the analytic
    single-period quantiles, is inherited unchanged. A single-period target never
    simulates, so on that shape a block model's forecasts equal its iid parent's
    (tests/test_blocks.py pins this): the sampler only exists on the cumulative shape.
    """

    block_length: int = BLOCK_LENGTH

    def _fit_variance(self, v: np.ndarray) -> np.ndarray:
        sd = super()._fit_variance(v)  # type: ignore[misc]
        self.variant = f"{self.variant}, block {self.block_length}"
        return sd

    def _draw(self, h: int, n_paths: int, rng: np.random.Generator) -> np.ndarray:
        z = self._z  # type: ignore[attr-defined]
        if z is None or len(z) < MIN_STD_RESID:
            raise FitError(self.name, f"need {MIN_STD_RESID} standardised residuals to simulate")  # type: ignore[attr-defined]
        return block_draw(z, h, n_paths, rng, self.block_length)


class EWMABlock(_BlockSampler, EWMA):
    name = "ewma_block"


class GARCHBlock(_BlockSampler, GARCH):
    name = "garch_block"


class GJRGARCHBlock(_BlockSampler, GJRGARCH):
    name = "gjr_garch_block"


# Name -> factory, merged into models/registry.py's REGISTRY in one line.
BLOCK_REGISTRY = {
    "ewma_block": lambda m: EWMABlock(),
    "garch_block": lambda m: GARCHBlock(),
    "gjr_garch_block": lambda m: GJRGARCHBlock(),
}

__all__ = [
    "BLOCK_LENGTH",
    "BLOCK_REGISTRY",
    "EWMABlock",
    "GARCHBlock",
    "GJRGARCHBlock",
    "block_draw",
    "block_indices",
]
