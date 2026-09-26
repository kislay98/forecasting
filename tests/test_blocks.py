"""Block-bootstrap innovations (Phase 6, forecasting/models/blocks.py).

Known answers, in the style of tests/test_calibration.py. Three claims are pinned:

1. The sampler reproduces a known autocorrelation. Fed an AR(1) series, the lag-k
   autocorrelation of what it draws is the series' own lag-k autocorrelation times the
   share of lag-k pairs that fall inside one block, which is fixed by the block layout
   and computable before any draw. The iid sampler gives zero at every lag.
2. A block length of 1 is the iid sampler, exactly: the same random draws in the same
   order, so the same paths to the last bit, for every block model against its parent.
3. The five registered variance models are untouched (P6-1). Their draws are checked
   against an independent reimplementation of the iid sampler, their classes against
   the registry, and their forecasts in an engine run against a run that also contains
   the block models.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from forecasting.backtest.engine import run_backtest
from forecasting.config import RETURN_VARIANCE, parse_config
from forecasting.data.validate import validate
from forecasting.models.blocks import (
    BLOCK_LENGTH,
    BLOCK_REGISTRY,
    EWMABlock,
    GARCHBlock,
    GJRGARCHBlock,
    block_draw,
    block_indices,
)
from forecasting.models.registry import REGISTRY
from forecasting.models.variance import (
    EWMA,
    EWMA_LAMBDA,
    GARCH,
    GJRGARCH,
    ConstantSigma,
    GARCHNormal,
    VarianceModel,
)
from tests.synthetic import garch11_prices, garch11_returns, to_frame

REGISTERED = {
    "zero_return_fhs": ConstantSigma,
    "ewma": EWMA,
    "garch_normal": GARCHNormal,
    "garch": GARCH,
    "gjr_garch": GJRGARCH,
}
PARENTS = {EWMABlock: EWMA, GARCHBlock: GARCH, GJRGARCHBlock: GJRGARCH}


def ar1(n: int, phi: float, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    e = rng.standard_normal(n)
    x = np.empty(n)
    x[0] = e[0] / np.sqrt(1 - phi**2)
    for i in range(1, n):
        x[i] = phi * x[i - 1] + e[i]
    return x


def acf_at(x: np.ndarray, k: int) -> float:
    x = x - x.mean()
    return float(x[:-k] @ x[k:]) / float(x @ x)


def pooled_path_acf(paths: np.ndarray, k: int, mu: float, var: float) -> float:
    """Lag-k autocorrelation pooled over every within-path pair, centred on the source
    series' own mean and variance, so it estimates what a single path carries."""
    a = paths[:, :-k] - mu
    b = paths[:, k:] - mu
    return float(np.mean(a * b)) / var


def same_block_share(h: int, b: int, k: int) -> float:
    """Of the h - k lag-k pairs in a path of length h, the share inside one block."""
    layout = np.arange(h) // b
    return float(np.mean(layout[:-k] == layout[k:]))


# ------------------------------------------------------------------ the sampler itself


def test_block_indices_are_runs_of_consecutive_positions():
    rng = np.random.default_rng(0)
    idx = block_indices(500, 20, 1000, rng, 10)
    assert idx.shape == (1000, 20)
    assert idx.min() >= 0 and idx.max() <= 499
    steps = np.diff(idx, axis=1)
    inside = np.ones(19, dtype=bool)
    inside[9] = False  # position 9 to 10 is the one join in a path of two blocks
    assert (steps[:, inside] == 1).all()
    # The join is a fresh start: it is a run of +1 only by coincidence.
    assert np.mean(steps[:, 9] == 1) < 0.01


def test_block_indices_truncate_the_last_block():
    idx = block_indices(300, 7, 50, np.random.default_rng(1), 3)
    assert idx.shape == (50, 7)
    assert (np.diff(idx[:, :3], axis=1) == 1).all()
    assert (np.diff(idx[:, 3:6], axis=1) == 1).all()


def test_block_length_longer_than_the_series_is_capped():
    idx = block_indices(5, 12, 10, np.random.default_rng(2), 50)
    assert idx.min() == 0 and idx.max() == 4


@pytest.mark.parametrize("bad", [0, -3])
def test_block_length_must_be_positive(bad):
    with pytest.raises(ValueError):
        block_indices(100, 20, 10, np.random.default_rng(0), bad)


def test_block_length_one_is_the_iid_draw_exactly():
    """numpy's Generator.choice with replacement is rng.integers(0, n, size) indexed
    into the array. The block sampler at b = 1 makes that call, so the draws match to
    the bit, from the same seed, not merely in distribution."""
    z = np.random.default_rng(3).standard_normal(777)
    for seed in (0, 1, 20260926):
        iid = np.random.default_rng(seed).choice(z, size=(2000, 20), replace=True)
        blk = block_draw(z, 20, 2000, np.random.default_rng(seed), b=1)
        np.testing.assert_array_equal(iid, blk)


def test_sampler_reproduces_a_known_autocorrelation():
    """Source: AR(1) with phi = 0.6. The block draw's lag-k autocorrelation must equal
    rho_k of the source times the share of lag-k pairs inside one block. With b = 10 and
    h = 20 that share is 2 (10 - k) / (20 - k): 18/19 at lag 1, 10/15 at lag 5, and 0 at
    lag 10 and beyond, where no pair shares a block. The iid draw gives 0 everywhere.

    The expected values come from the source series and the block layout, both fixed
    before a single draw, so this is a known answer and not a self-consistency check.
    Tolerance: 200,000 paths put the pooled estimate's standard error near 0.002.
    """
    z = ar1(60_000, 0.6, seed=11)
    mu, var = float(z.mean()), float(z.var())
    n_paths, h, b = 200_000, 20, 10
    blk = block_draw(z, h, n_paths, np.random.default_rng(12), b)
    iid = block_draw(z, h, n_paths, np.random.default_rng(12), 1)
    for k in (1, 2, 5, 9, 10, 15):
        expected = acf_at(z, k) * same_block_share(h, b, k)
        assert pooled_path_acf(blk, k, mu, var) == pytest.approx(expected, abs=0.01), k
        assert pooled_path_acf(iid, k, mu, var) == pytest.approx(0.0, abs=0.01), k
    # Spot values, so the expected formula itself is pinned to numbers: phi^k x share.
    assert acf_at(z, 1) * same_block_share(h, b, 1) == pytest.approx(0.6 * 18 / 19, abs=0.01)
    assert same_block_share(h, b, 10) == 0.0


def test_sum_of_a_block_path_has_the_variance_the_autocorrelation_implies():
    """The quantity Phase 6 is about: the spread of the h-step sum. For a path of two
    blocks of 10 from AR(1) with phi = 0.6, Var(sum) / (h var) = 1 + (2 / h) sum over
    k of rho_k x (pairs at lag k inside a block), which is about 3.2 here, against 1 for
    the iid draw. Computed from the source's own autocorrelations."""
    z = ar1(60_000, 0.6, seed=21)
    h, b = 20, 10
    rho = np.array([acf_at(z, k) for k in range(1, b)])
    pairs = np.array([2 * (b - k) for k in range(1, b)])
    implied = 1 + 2 * float(rho @ pairs) / h
    s_blk = block_draw(z, h, 100_000, np.random.default_rng(22), b).sum(axis=1)
    s_iid = block_draw(z, h, 100_000, np.random.default_rng(22), 1).sum(axis=1)
    assert s_blk.var() / (h * z.var()) == pytest.approx(implied, rel=0.03)
    assert s_iid.var() / (h * z.var()) == pytest.approx(1.0, rel=0.03)
    assert implied == pytest.approx(3.2, abs=0.15)


# --------------------------------------------------------- the models, against parents


@pytest.fixture(scope="module")
def returns() -> pd.Series:
    return pd.Series(garch11_returns(n=2500, seed=5))


@pytest.mark.parametrize("block_cls", list(PARENTS))
def test_block_model_at_length_one_equals_its_parent_exactly(block_cls, returns):
    """Same fit, same seed, block length 1: the simulated paths are the parent's paths,
    bit for bit, including through the GARCH and EWMA recursions."""
    parent = PARENTS[block_cls]().fit(returns)
    blk = block_cls()
    blk.block_length = 1
    blk.fit(returns)
    for seed in (0, 7):
        a = parent.simulate(20, 3000, np.random.default_rng(seed))
        b = blk.simulate(20, 3000, np.random.default_rng(seed))
        np.testing.assert_array_equal(a, b)


@pytest.mark.parametrize("block_cls", list(PARENTS))
def test_block_model_changes_only_the_draw(block_cls, returns):
    """At the registered length the fit, the residuals, the variance forecast and the
    single-period quantiles are the parent's. Only the simulated paths differ, and they
    differ because the draw does."""
    parent = PARENTS[block_cls]().fit(returns)
    blk = block_cls().fit(returns)
    assert blk.block_length == BLOCK_LENGTH == 10
    np.testing.assert_array_equal(parent.residuals(), blk.residuals())
    np.testing.assert_array_equal(parent._forecast_sd(20), blk._forecast_sd(20))
    levels = (0.5, 0.8, 0.95)
    pa, pb = parent.predict(20, levels), blk.predict(20, levels)
    np.testing.assert_array_equal(pa.mean, pb.mean)
    for lv in levels:
        np.testing.assert_array_equal(pa.lower[lv], pb.lower[lv])
        np.testing.assert_array_equal(pa.upper[lv], pb.upper[lv])
    assert blk.variant == f"{parent.variant}, block 10"
    a = parent.simulate(20, 2000, np.random.default_rng(1))
    b = blk.simulate(20, 2000, np.random.default_rng(1))
    assert not np.array_equal(a, b)
    # The first step of a block path is one draw from (almost) the marginal, so the
    # one-day spread is the parent's up to simulation noise.
    assert b[:, 0].std() == pytest.approx(a[:, 0].std(), rel=0.05)


# ------------------------------------------------ the registered five are untouched


def test_registry_still_maps_the_registered_names_to_the_registered_classes():
    for name, cls in REGISTERED.items():
        assert type(REGISTRY[name](1)) is cls
    for name, factory in BLOCK_REGISTRY.items():
        assert REGISTRY[name] is factory
        assert name in RETURN_VARIANCE
    assert set(RETURN_VARIANCE) == set(REGISTERED) | set(BLOCK_REGISTRY)


def test_registered_models_keep_the_iid_draw():
    for cls in REGISTERED.values():
        assert cls._draw is VarianceModel._draw
        assert not hasattr(cls, "block_length")


def test_registered_iid_paths_match_an_independent_reimplementation(returns):
    """Known answer for the two registered simulators with no optimiser in them. The
    constant model's paths are rng.choice(z) x sd, and EWMA's are the RiskMetrics
    recursion run on rng.choice(z), both rebuilt here from the formula rather than by
    calling the model, so an accidental change to the registered sampler fails here."""
    rng_seed = 99
    flat = ConstantSigma().fit(returns)
    z = flat.residuals()
    draws = np.random.default_rng(rng_seed).choice(z, size=(500, 20), replace=True)
    np.testing.assert_array_equal(
        flat.simulate(20, 500, np.random.default_rng(rng_seed)), draws * flat._sd
    )

    ew = EWMA().fit(returns)
    z = ew.residuals()
    draws = np.random.default_rng(rng_seed).choice(z, size=(500, 20), replace=True)
    var = np.full(500, float(ew._next))
    expect = np.empty((500, 20))
    for s in range(20):
        expect[:, s] = np.sqrt(var) * draws[:, s]
        var = EWMA_LAMBDA * var + (1 - EWMA_LAMBDA) * expect[:, s] ** 2
    np.testing.assert_array_equal(ew.simulate(20, 500, np.random.default_rng(rng_seed)), expect)


def _cumulative_run(models: tuple[str, ...], target: str = "cumulative_returns"):
    raw = {
        "id": "blk",
        "source": "csv:synthetic",
        "freq": "trading_days",
        "season": "none",
        "H": 20,
        "decision_horizons": [1, 5, 20],
        "target": target,
        "origin_step": 20,
        "n_dev_origins": 3,
        "n_test_origins": 5,
        "window": "expanding",
        "models": list(models),
    }
    cfg = parse_config({"seed": 7, "n_paths": 1000, "levels": [0.8, 0.95], "series": [raw]})
    scfg = cfg.series[0]
    [(series, _)] = validate(
        to_frame(garch11_prices(n=900, seed=3), "trading_days", unique_id="blk"), scfg
    )
    store, _ = run_backtest(series, scfg, cfg, "t")
    return store.frame()


VALUE_COLS = ["y_pred", "lo_80", "hi_80", "lo_95", "hi_95", "es_80", "es_95", "level_pred"]


def test_adding_block_models_to_a_run_leaves_the_registered_rows_bit_identical():
    """Through the real engine. Each (origin, model) has its own seed (D7), so adding
    models to a run must not move a single value of the ones already there. The
    registered five are run alone and alongside the three block models; every value
    column is compared, not a hash (P4-8)."""
    five = tuple(REGISTERED)
    alone = _cumulative_run(five)
    both = _cumulative_run(five + tuple(BLOCK_REGISTRY))
    key = ["model", "origin_t", "h"]
    a = alone.sort_values(key).reset_index(drop=True)
    b = both[both["model"].isin(five)].sort_values(key).reset_index(drop=True)
    assert len(a) == len(b) > 0
    assert (a["status"] == "ok").all()
    for col in VALUE_COLS:
        np.testing.assert_array_equal(a[col].to_numpy(), b[col].to_numpy(), err_msg=col)
    # And the block rows are different forecasts, not copies.
    blk = both[both["model"] == "gjr_garch_block"].sort_values(key).reset_index(drop=True)
    iid = both[both["model"] == "gjr_garch"].sort_values(key).reset_index(drop=True)
    assert not np.array_equal(blk["hi_80"].to_numpy(), iid["hi_80"].to_numpy())


def test_on_the_single_day_shape_block_models_equal_their_parents():
    """A single-period target never simulates: the quantiles come from the variance
    forecast and the empirical residual quantiles. So on the P2 and P5a shape the block
    models are their parents, value for value, and the sampler cannot have changed a
    conclusion drawn there. This is why Phase 6 runs only the cumulative shape."""
    frame = _cumulative_run(("ewma", "gjr_garch", "ewma_block", "gjr_garch_block"), "returns")
    key = ["origin_t", "h"]
    for blk, parent in (("ewma_block", "ewma"), ("gjr_garch_block", "gjr_garch")):
        a = frame[frame["model"] == parent].sort_values(key).reset_index(drop=True)
        b = frame[frame["model"] == blk].sort_values(key).reset_index(drop=True)
        for col in ["y_pred", "lo_80", "hi_80", "lo_95", "hi_95"]:
            np.testing.assert_array_equal(a[col].to_numpy(), b[col].to_numpy(), err_msg=col)
