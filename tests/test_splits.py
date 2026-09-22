from __future__ import annotations

from itertools import pairwise

import pytest

from forecasting.backtest.splits import make_origins
from forecasting.data.validate import volume_thresholds
from tests.conftest import make_scfg


def test_roles_and_positions_monthly():
    scfg = make_scfg(H=12)
    plan = make_origins(120, scfg)
    ts = [o.t for o in plan.origins]
    assert ts[-1] == 120 - 1 - 12  # every origin has all H targets
    assert ts[0] == scfg.initial_window - 1  # 36 values in the first window
    assert ts == sorted(ts) and all(b - a == 1 for a, b in pairwise(ts))
    roles = [o.role for o in plan.origins]
    assert roles[-30:] == ["test"] * 30 and roles[-50:-30] == ["dev"] * 20
    assert set(roles[:-50]) == {"warmup"} and plan.n_warmup == len(ts) - 50
    assert not plan.reduced


def test_step_is_anchored_at_the_last_origin():
    scfg = make_scfg(freq="trading_days", target="returns", H=20)
    plan = make_origins(2008, scfg)
    ts = [o.t for o in plan.origins]
    assert ts[-1] == 2008 - 1 - 20
    assert all(b - a == 5 for a, b in pairwise(ts))
    assert ts[0] >= scfg.initial_window  # t returns r_1..r_t, at least 500 of them
    assert (plan.n_dev, plan.n_test) == (20, 250)


@pytest.mark.parametrize(
    "labels",
    [
        {"H": 12},
        {"freq": "quarterly", "H": 8},
        {"freq": "weekly", "H": 13},
        {"freq": "trading_days", "target": "returns", "H": 20},
        {"freq": "trading_days", "H": 5},
    ],
)
def test_minimum_T_gives_exactly_dev_plus_test_origins(labels):
    scfg = make_scfg(**labels)
    _, t_min = volume_thresholds(scfg)
    n_rows = t_min + (1 if scfg.target == "returns" else 0)
    plan = make_origins(n_rows, scfg)
    assert len(plan.origins) == scfg.n_dev_origins + scfg.n_test_origins
    assert plan.n_warmup == 0 and not plan.reduced
    # one row fewer: reduced mode, but still full dev and test by shortening the first window
    plan2 = make_origins(n_rows - 1, scfg)
    assert plan2.reduced and plan2.initial_window == scfg.initial_window - 1
    assert (plan2.n_dev, plan2.n_test) == (scfg.n_dev_origins, scfg.n_test_origins)


def test_floor_shrinks_dev_and_test_proportionally():
    scfg = make_scfg(H=12)
    t_floor, _ = volume_thresholds(scfg)
    plan = make_origins(t_floor, scfg)
    assert plan.reduced and plan.initial_window == scfg.floor_initial
    assert len(plan.origins) >= 20
    assert plan.n_test == round(len(plan.origins) * 30 / 50)
    assert plan.n_dev + plan.n_test == len(plan.origins)


def test_below_floor_is_a_programming_error():
    with pytest.raises(ValueError):
        make_origins(20, make_scfg(H=12))
