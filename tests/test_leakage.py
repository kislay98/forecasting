"""Leakage tests L1, L2, L4, L5 (spec: Leakage prevention). Run on every push.

L1 proves the pipeline is blind to the future; L2 proves L1 can see a leak when there
is one. Together they are the core of the project's credibility.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from forecasting.backtest.engine import run_backtest
from forecasting.backtest.selection import select_best_baseline, select_sma_k
from forecasting.config import parse_config
from forecasting.data.validate import validate
from forecasting.models.baselines import Drift, Naive
from forecasting.models.registry import build_factories
from forecasting.transforms import TRANSFORM_BUILDERS
from tests.leakage import (
    POISONS,
    CentreOnTransformedScale,
    PeekModel,
    global_zscore_builder,
    l1_check,
    poison,
)
from tests.synthetic import gbm_prices, seasonal_ar1, to_frame


class TransformedNaive(Naive):
    name = "t_naive"
    uses_transform = True


class TransformedDrift(Drift):
    name = "t_drift"
    uses_transform = True


def setup(df: pd.DataFrame, **labels):
    raw = {"id": "s", "source": "csv:x", "freq": "monthly", "H": 12, **labels}
    cfg = parse_config({"series": [raw]})
    scfg = cfg.series[0]
    [(series, _)] = validate(df, scfg)
    return cfg, scfg, series


def level_series() -> pd.DataFrame:
    """Positive, seasonal, with two interior gaps so interpolation is exercised."""
    y = seasonal_ar1(n=130, seed=11) + np.linspace(0, 40, 130)
    df = to_frame(y, "monthly", unique_id="s")
    df.loc[[60, 95], "y"] = np.nan
    return df


def returns_series() -> pd.DataFrame:
    df = to_frame(gbm_prices(n=1100, seed=12), "trading_days", "2019-01-01", unique_id="s")
    return df.drop(index=df.index[::23]).reset_index(drop=True)


def honest_factories(scfg):
    def make(series):
        f = dict(build_factories(scfg))
        f["t_naive"] = lambda m: TransformedNaive()
        f["t_drift"] = lambda m: TransformedDrift()
        f["centre"] = lambda m: CentreOnTransformedScale()
        return f

    return make


# ---------------------------------------------------------------------------- L1


@pytest.mark.parametrize("transform", ["boxcox", "auto", "log"])
def test_L1_level_series_is_blind_to_the_future(transform):
    cfg, scfg, series = setup(level_series(), transform=transform)
    result = l1_check(series, scfg, cfg, honest_factories(scfg), n_origins=10)
    assert result.leaking_models == set(), result.leaks
    expected = set(build_factories(scfg)) | {"t_naive", "t_drift", "centre"}
    assert set(result.leaks) == expected  # every model was compared
    assert result.n_rows_compared == 10 * 2 * len(expected) * 12  # 10 origins, 2 windows


def test_L1_return_series_is_blind_to_the_future():
    cfg, scfg, series = setup(
        returns_series(),
        freq="trading_days",
        target="returns",
        H=20,
        initial_window=250,
        n_test_origins=60,
    )
    result = l1_check(series, scfg, cfg, lambda s: build_factories(scfg), n_origins=10)
    assert result.leaking_models == set(), result.leaks
    assert set(result.leaks) == set(build_factories(scfg))


def test_poison_changes_only_the_future():
    _, _, series = setup(level_series())
    for kind in POISONS:
        bad = poison(series, 70, kind)
        pd.testing.assert_series_equal(bad.y.iloc[:71], series.y.iloc[:71])
        assert not bad.y.iloc[71:].equals(series.y.iloc[71:])


# ---------------------------------------------------------------------------- L2


def test_L2_peek_model_is_caught():
    cfg, scfg, series = setup(level_series(), transform="none")

    def make(s):
        return {"naive": lambda m: Naive(), "peek": lambda m: PeekModel(s.y)}

    result = l1_check(series, scfg, cfg, make, n_origins=10)
    assert result.leaking_models == {"peek"}
    assert result.leaks["peek"] == set(POISONS)


def test_L2_peek_model_is_caught_on_returns():
    cfg, scfg, series = setup(
        returns_series(),
        freq="trading_days",
        target="returns",
        H=5,
        initial_window=250,
        n_test_origins=60,
    )

    def make(s):
        return {
            "zero_return": build_factories(scfg)["zero_return"],
            "peek": lambda m: PeekModel(s.y, returns=True),
        }

    result = l1_check(series, scfg, cfg, make, n_origins=10)
    assert result.leaking_models == {"peek"}
    assert result.leaks["peek"] == set(POISONS)


def test_L2_global_transform_is_caught(monkeypatch):
    cfg, scfg, series = setup(level_series())
    scfg = replace(scfg, transform="global_zscore")  # registered only in this test

    def make(s):
        monkeypatch.setitem(TRANSFORM_BUILDERS, "global_zscore", global_zscore_builder(s.y))
        return {"naive": lambda m: Naive(), "centre": lambda m: CentreOnTransformedScale()}

    result = l1_check(series, scfg, cfg, make, n_origins=10)
    assert result.leaking_models == {"centre"}
    assert result.leaks["centre"] == set(POISONS)


def test_L2_same_model_with_a_slice_transform_is_clean():
    # Control for the test above: the model is honest; only the transform leaked.
    cfg, scfg, series = setup(level_series(), transform="none")
    result = l1_check(series, scfg, cfg, lambda s: {"centre": lambda m: CentreOnTransformedScale()})
    assert result.leaking_models == set()


# ---------------------------------------------------------------------------- L4


def test_L4_alignment_level():
    n = 100
    df = to_frame(np.arange(1.0, n + 1), "monthly", unique_id="s")  # y_t = t
    cfg, scfg, series = setup(df, models=["naive", "drift"], transform="none")
    f = run_backtest(series, scfg, cfg, "l4")[0].frame()
    naive = f[f["model"] == "naive"]
    # y at 0-based position t is t + 1
    assert (naive["y_pred"] == naive["origin_t"] + 1).all()
    assert (naive["y_true"] == naive["origin_t"] + naive["h"] + 1).all()
    drift = f[f["model"] == "drift"]
    np.testing.assert_allclose(drift["y_pred"] - drift["y_true"], 0.0, atol=1e-9)
    op = pd.PeriodIndex(f["origin_period"], freq="M")
    tp = pd.PeriodIndex(f["target_period"], freq="M")
    assert [d.n for d in (tp - op)] == f["h"].tolist()
    assert (op == series.y.index[f["origin_t"]]).all()


def test_L4_alignment_trading_days_and_returns():
    prices = np.exp(np.arange(1, 801) / 1000.0)  # log price rises by exactly 0.001 a day
    df = to_frame(prices, "trading_days", "2020-01-01", unique_id="s")
    df = df.drop(index=[300, 301]).reset_index(drop=True)  # a two-day closure
    cfg, scfg, series = setup(
        df,
        freq="trading_days",
        target="returns",
        H=5,
        initial_window=250,
        n_test_origins=60,
        models=["last_return", "zero_return"],
    )
    f = run_backtest(series, scfg, cfg, "l4")[0].frame()
    labels = [str(d.date()) for d in series.y.index]
    # target_period is the h-th next trading row: closures are skipped, not counted
    assert (
        f["target_period"] == [labels[t + h] for t, h in zip(f["origin_t"], f["h"], strict=True)]
    ).all()
    v = series.y.to_numpy()
    t, h = f["origin_t"].to_numpy(), f["h"].to_numpy()
    np.testing.assert_allclose(f["y_true"], np.log(v[t + h] / v[t + h - 1]))
    # across the closure the true return spans three days of drift, not one
    across = f[f["target_period"] == labels[300]]
    np.testing.assert_allclose(across["y_true"], 0.003, rtol=1e-9)
    last = f[f["model"] == "last_return"]
    np.testing.assert_allclose(
        last["y_pred"], np.log(v[last["origin_t"]] / v[last["origin_t"] - 1])
    )
    zero = f[f["model"] == "zero_return"]
    np.testing.assert_allclose(zero["level_pred"], v[zero["origin_t"]])


# ---------------------------------------------------------------------------- L5


def _poison_test_rows(frame: pd.DataFrame, value: float) -> pd.DataFrame:
    g = frame.copy()
    test = g["origin_role"] == "test"
    num = [c for c in g.columns if c.startswith(("lo_", "hi_"))]
    for c in ["y_true", "y_pred", "mase_scale", "level_true", "level_pred", *num]:
        g.loc[test, c] = value
    g.loc[test, "y_true_missing"] = False
    return g


@pytest.fixture(scope="module")
def stores():
    out = []
    cfg, scfg, series = setup(level_series())
    out.append(run_backtest(series, scfg, cfg, "l5")[0].frame())
    cfg, scfg, series = setup(
        returns_series(),
        freq="trading_days",
        target="returns",
        H=20,
        initial_window=250,
        n_test_origins=60,
    )
    out.append(run_backtest(series, scfg, cfg, "l5")[0].frame())
    return out


@pytest.mark.parametrize("value", [np.nan, 1e9, -1e9, 0.0])
def test_L5_selections_ignore_test_rows(stores, value):
    for frame in stores:
        clean_sma, clean_best = select_sma_k(frame), select_best_baseline(frame)
        assert clean_sma and clean_best
        bad = _poison_test_rows(frame, value)
        assert select_sma_k(bad) == clean_sma
        assert select_best_baseline(bad) == clean_best


def test_L5_positive_control_dev_rows_do_matter(stores):
    # If poisoning dev rows did not move the choice, L5 would be passing vacuously.
    frame = stores[0]
    best = select_best_baseline(frame)["s"]["expanding"]["model"]
    g = frame.copy()
    hit = (g["origin_role"] == "dev") & (g["model"] == best) & (g["window"] == "expanding")
    g.loc[hit, "y_pred"] = 1e9
    assert select_best_baseline(g)["s"]["expanding"]["model"] != best


def test_best_baseline_uses_only_the_chosen_sma(stores):
    frame = stores[0]
    sma = select_sma_k(frame)["s"]
    for window, choice in select_best_baseline(frame)["s"].items():
        smas = [m for m in choice["scores"] if m.startswith("sma_")]
        assert smas == [sma[window]["model"]]
