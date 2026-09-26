"""A12: the positive control for the calibration harness (Phases 2 to 4).

Phase 1 had a positive control, US utilities output, whose job was to fail if the
harness could not find structure that was certainly there. Phases 2, 3 and 4 shipped
without one. Their claims are all about calibration, so the control has to be about
calibration too, and the cleanest way to get a known answer is a DGP whose conditional
variance we wrote ourselves.

The series is GARCH(1,1) with Normal innovations (tests.synthetic.garch11_prices),
unconditional volatility 18% annualised, persistence 0.99. Two models are run on it:

  garch_normal      correctly specified. GARCH(1,1), Normal innovations, zero mean.
  zero_return_fhs   wrong in one specific way: no conditional variance at all, with
                    empirical tails taken from the unconditional standardised returns.

Both arms matter. A harness that cannot pass the correct model raises false alarms, and
a harness that cannot catch the flat one is not measuring anything. Both shapes the study
actually registered are run, because the answer turns out to depend on the shape:

  shape A (P2)      single-day returns, origin_step 5, 400 test origins, h = 1 and 5
  shape B (P3, P4)  cumulative returns, origin_step 20, 300 test origins, h = 1, 5, 20

What was measured, over DGP seeds 1, 2 and 3, before these assertions were written:

| Arm                                      | shape A | shape B |
|---|---|---|
| correct model passes every check (seed 1) | yes     | yes     |
| flat model caught by some check           | 3 of 3  | 2 of 3  |
| caught by a coverage band                 | 1 of 3  | 0 of 3  |
| caught by Kupiec                          | 1 of 3  | 0 of 3  |
| caught by Christoffersen independence     | 3 of 3  | 2 of 3  |

A positive control has to have its power measured rather than assumed, which is why the
counts are here and why the assertions below sit at or under them. The finding they
encode is the one worth carrying forward: what catches a missing variance model is the
independence test, not the coverage bands and not Kupiec. A flat model with empirical
tails is right on average and wrong in sequence, and only one of the three registered
checks reads the sequence.

CRPS is excluded when asking whether the flat model was caught. The registered CRPS
check compares a model against zero_return_fhs, so for zero_return_fhs it is a
comparison with itself, always exactly 1.0, and always a failure. It carries no
information about the flat model and would make the negative arm pass for free.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from forecasting.config import parse_config
from forecasting.evaluation.phase2 import calibration_table, gate_decision
from forecasting.gate import Gate, SeriesGate
from tests.acceptance import backtest_synthetic
from tests.synthetic import garch11_prices

pytestmark = pytest.mark.slow

CORRECT = "garch_normal"
FLAT = "zero_return_fhs"
LEVELS = (0.5, 0.8, 0.9, 0.95, 0.99)
SEEDS = (1, 2, 3)
N = 8000

# The registered thresholds of the phases this controls, copied unchanged. The per-test
# alpha is Sidak for the number of tests the shape runs: 8 for two horizons (P2), 12 for
# three (P3, P4).
BANDS = {"coverage_80": [0.75, 0.85], "coverage_95": [0.91, 0.98], "crps_ratio_below": 1.0}
SHAPES = {
    "A": {"target": "returns", "H": 5, "hs": (1, 5), "step": 5, "n_test": 400, "alpha": 0.0064},
    "B": {
        "target": "cumulative_returns",
        "H": 20,
        "hs": (1, 5, 20),
        "step": 20,
        "n_test": 300,
        "alpha": 0.0043,
    },
}


def _series_raw(shape: dict, models: tuple[str, ...]) -> dict:
    return {
        "id": "control_garch",
        "source": "csv:synthetic",
        "freq": "trading_days",
        "season": "none",
        "H": shape["H"],
        "decision_horizons": list(shape["hs"]),
        "target": shape["target"],
        "origin_step": shape["step"],
        "n_dev_origins": 20,
        "n_test_origins": shape["n_test"],
        "models": list(models),
    }


def _table(shape_key: str, seed: int, models: tuple[str, ...]):
    """One control run through the real engine, scored by the real calibration table."""
    shape = SHAPES[shape_key]
    run = backtest_synthetic(
        garch11_prices(n=N, seed=seed),
        models=models,
        uid="control_garch",
        freq="trading_days",
        season="none",
        H=shape["H"],
        decision_horizons=shape["hs"],
        window="expanding",
        seed=20260926,
        n_paths=2000,
        levels=LEVELS,
        target=shape["target"],
        origin_step=shape["step"],
        n_dev_origins=20,
        n_test_origins=shape["n_test"],
    )
    cfg = parse_config({"seed": 1, "levels": list(LEVELS), "series": [_series_raw(shape, models)]})
    return calibration_table(run.frame, cfg.series[0], LEVELS, "expanding", role="test")


def _gate(shape_key: str, primary: str, models: tuple[str, ...]) -> Gate:
    """The registered gate of the phase this shape controls, with one model substituted."""
    shape = SHAPES[shape_key]
    return Gate(
        phase=2,
        registered="2026-09-26",
        alpha=0.05,
        primary_window="expanding",
        series={
            "control_garch": SeriesGate(
                decision_horizons=shape["hs"],
                models=models,
                primary_model=primary,
                chosen_by="A12: the DGP is known, so the correct model is not a choice",
            )
        },
        thresholds={**BANDS, "per_test_alpha": shape["alpha"]},
        sha256="a12control",
        path=Path("tests/test_control.py"),
    )


def _decide(shape_key: str, table, primary: str, models: tuple[str, ...]):
    shape = SHAPES[shape_key]
    cfg = parse_config({"seed": 1, "levels": list(LEVELS), "series": [_series_raw(shape, models)]})
    return gate_decision(_gate(shape_key, primary, models), cfg.series[0], table, reference=FLAT)


def _caught(shape_key: str, table, models: tuple[str, ...]) -> tuple[str, ...]:
    """Which registered checks fail for the flat model, CRPS excluded (see the docstring)."""
    d = _decide(shape_key, table, FLAT, models)
    return tuple(
        f"{c.name} h={c.h} {c.level:g}: {c.detail}" for c in d.failures if c.name != "crps"
    )


@pytest.fixture(scope="module")
def both(request):
    """Both models on one seed, as (shape, table). Two GARCH fits per origin, so the
    run is cached for the whole module and each shape is built at most once."""
    return request.param, _table(request.param, SEEDS[0], (FLAT, CORRECT))


@pytest.fixture(scope="module")
def flat_only(request):
    """The flat model on the remaining seeds. No optimiser, so this arm is cheap."""
    return {s: _table(request.param, s, (FLAT,)) for s in SEEDS[1:]}


def _hits(shape: str, both, flat_only) -> dict[int, tuple[str, ...]]:
    out = {SEEDS[0]: _caught(shape, both[1], (FLAT, CORRECT))}
    out.update({s: _caught(shape, t, (FLAT,)) for s, t in flat_only.items()})
    return out


# ------------------------------------------------------------------ the positive arm


@pytest.mark.parametrize("both", ["A", "B"], indirect=True)
def test_A12_correctly_specified_model_passes_the_registered_gate(both):
    """The known answer: the model that generated the data must clear every check.

    A failure here is a false alarm in the gate, and it would mean every NO-GO the study
    has recorded is suspect, including P3's.
    """
    shape, table = both
    d = _decide(shape, table, CORRECT, (FLAT, CORRECT))
    assert d.go, [f"shape {shape}: {c.name} h={c.h}: {c.detail}" for c in d.failures]


@pytest.mark.parametrize("both", ["A", "B"], indirect=True)
def test_A12_crps_ranks_the_correct_model_first(both):
    """The one check that separates the two models at every horizon in both shapes."""
    shape, table = both
    idx = table.set_index(["model", "h"])["crps"]
    for h in SHAPES[shape]["hs"]:
        assert idx[(CORRECT, h)] < idx[(FLAT, h)], f"shape {shape}, h = {h}"


# ------------------------------------------------------------------ the negative arm


@pytest.mark.parametrize("both,flat_only", [("A", "A")], indirect=True)
def test_A12_flat_model_is_caught_in_the_single_day_shape(both, flat_only):
    """Measured 3 of 3 seeds, by the independence test at h = 5 in every one of them."""
    hits = _hits("A", both, flat_only)
    assert sum(bool(v) for v in hits.values()) >= 2, hits


@pytest.mark.parametrize("both,flat_only", [("B", "B")], indirect=True)
def test_A12_flat_model_is_harder_to_catch_in_the_cumulative_shape(both, flat_only):
    """Measured 2 of 3 seeds, and seed 1 escapes: independence at the 95% level returns
    0.026 at h = 20, which does not clear the corrected 0.0043.

    The assertion is deliberately weaker than the measurement. Raising the origin step to
    20 so that h = 20 is non-overlapping (P3-2) also spreads the exceedance sequence over
    20 days at h = 1 and h = 5, where the step used to be 5, and that costs the
    independence test most of its power. One escaping seed out of three is the measured
    size of that cost, and pinning the count at 3 of 3 would make the control flaky
    rather than strict.
    """
    hits = _hits("B", both, flat_only)
    assert sum(bool(v) for v in hits.values()) >= 1, hits


@pytest.mark.parametrize("both,flat_only", [("B", "B")], indirect=True)
def test_A12_coverage_bands_and_kupiec_are_blind_to_a_missing_variance_model(both, flat_only):
    """A limitation being pinned, not a property being required.

    On the cumulative object at step 20, a model with no conditional variance at all
    lands inside both registered coverage bands and never trips Kupiec, on all three
    seeds. Its errors are in the ordering of the exceedances, which those two checks do
    not read.

    If this test ever fails, the harness has gained power and that is good news: read the
    numbers, then update the control rather than the other way round.
    """
    tables = {SEEDS[0]: both[1], **flat_only}
    alpha = SHAPES["B"]["alpha"]
    for seed, t in tables.items():
        rows = t[t["model"] == FLAT]
        for r in rows.itertuples():
            if r.h not in SHAPES["B"]["hs"]:
                continue
            for tag in ("80", "95"):
                lo, hi = BANDS[f"coverage_{tag}"]
                cov = getattr(r, f"cov_{tag}")
                assert lo <= cov <= hi, f"seed {seed}, h = {r.h}, {tag}%: coverage {cov:.3f}"
                kp = getattr(r, f"kupiec_p_{tag}")
                assert not (kp < alpha), f"seed {seed}, h = {r.h}, {tag}%: Kupiec {kp:.4g}"
