from __future__ import annotations

from typing import ClassVar

import numpy as np
import pandas as pd
import pytest

from forecasting.backtest.engine import mase_scale, run_backtest
from forecasting.backtest.splits import make_origins
from forecasting.backtest.store import content_hash
from forecasting.config import parse_config
from forecasting.data.validate import validate
from forecasting.errors import FitError
from forecasting.models.base import BaseForecaster
from forecasting.models.baselines import Naive
from tests.synthetic import gbm_prices, seasonal_ar1, to_frame


def setup(df: pd.DataFrame, n_jobs: int = 1, **labels):
    raw = {"id": "s", "source": "csv:x.csv", "freq": "monthly", "H": 12, **labels}
    cfg = parse_config({"series": [raw], "n_jobs": n_jobs})
    scfg = cfg.series[0]
    [(series, _)] = validate(df, scfg)
    return cfg, scfg, series


def monthly(n: int = 110, seed: int = 0) -> pd.DataFrame:
    return to_frame(seasonal_ar1(n=n, seed=seed), "monthly", unique_id="s")


def test_row_count_covers_every_window_origin_model_h():
    cfg, scfg, series = setup(monthly())
    store, plan = run_backtest(series, scfg, cfg, "rid")
    f = store.frame()
    n_models = 3 + len(scfg.sma_windows)
    assert len(f) == 2 * len(plan.origins) * n_models * 12
    assert (f["status"] == "ok").all()
    assert set(f["origin_role"]) == {"warmup", "dev", "test"}


def test_alignment_on_y_equals_t():
    # L4 preview: y_t = t + 1, so naive at origin t predicts t + 1 and drift is exact.
    df = to_frame(np.arange(1.0, 101.0), "monthly", unique_id="s")
    cfg, scfg, series = setup(df, models=["naive", "drift"], transform="none", window="expanding")
    f = run_backtest(series, scfg, cfg, "rid")[0].frame()
    naive = f[f["model"] == "naive"]
    assert (naive["y_pred"] == naive["origin_t"] + 1).all()
    assert (naive["y_true"] == naive["origin_t"] + naive["h"] + 1).all()
    drift = f[f["model"] == "drift"]
    np.testing.assert_allclose(drift["y_pred"], drift["y_true"], atol=1e-9)
    op = pd.PeriodIndex(f["origin_period"], freq="M")
    tp = pd.PeriodIndex(f["target_period"], freq="M")
    assert ((tp - op).map(lambda d: d.n) == f["h"]).all()


def test_rolling_window_length():
    cfg, scfg, series = setup(monthly(), models=["naive"], window="rolling", rolling_length=30)
    f = run_backtest(series, scfg, cfg, "rid")[0].frame()
    assert (f["n_train"] == np.minimum(30, f["origin_t"] + 1)).all()


def test_missing_values_truth_and_skipped_origins():
    df = monthly()
    df.loc[df.index[80], "y"] = np.nan
    cfg, scfg, series = setup(df, models=["naive"], window="expanding")
    f = run_backtest(series, scfg, cfg, "rid")[0].frame()
    tgt = f[f["target_period"] == str(series.y.index[80])]
    assert tgt["y_true_missing"].all() and tgt["y_true"].isna().all()
    skipped = f[f["origin_t"] == 80]
    assert (skipped["status"] == "skipped").all()
    after = f[f["origin_t"] == 81]
    assert (after["status"] == "ok").all()  # the gap is interpolated inside the slice


class Boom(BaseForecaster):
    name = "boom"

    def _fit(self, v):
        raise FitError(self.name, "does not converge")


class NanModel(Naive):
    name = "nan_model"

    def _predict(self, h):
        mean, sd = super()._predict(h)
        return mean * np.nan, sd


class Bug(Naive):
    name = "bug"

    def _fit(self, v):
        raise ZeroDivisionError("a programming error")


def test_model_failures_are_rows_not_aborts():
    cfg, scfg, series = setup(monthly(), window="expanding")
    factories = {"naive": lambda m: Naive(), "boom": lambda m: Boom(), "nan": lambda m: NanModel()}
    f = run_backtest(series, scfg, cfg, "rid", factories=factories)[0].frame()
    assert (f.loc[f["model"] == "naive", "status"] == "ok").all()
    boom = f[f["model"] == "boom"]
    assert (boom["status"] == "failed").all() and boom["y_pred"].isna().all()
    assert boom["error"].str.contains("FitError: boom: does not converge").all()
    assert f.loc[f["model"] == "nan", "error"].str.startswith("ForecastContractError").all()


def test_programming_errors_propagate():
    cfg, scfg, series = setup(monthly(), window="expanding")
    with pytest.raises(ZeroDivisionError):
        run_backtest(series, scfg, cfg, "rid", factories={"bug": lambda m: Bug()})


def test_fresh_instance_per_fold():
    calls = []

    def factory(m):
        calls.append(m)
        return Naive()

    cfg, scfg, series = setup(monthly())
    _, plan = run_backtest(series, scfg, cfg, "rid", factories={"naive": factory})
    assert len(calls) == 2 * len(plan.origins)


class SeesTransformed(Naive):
    """Records the training values it was given, to check the transform plumbing."""

    name = "sees"
    uses_transform = True
    seen: ClassVar[list] = []

    def _fit(self, v):
        SeesTransformed.seen.append(v.copy())
        super()._fit(v)


def test_variance_transform_applies_only_to_models_that_ask():
    y = gbm_prices(n=110, seed=1) / 100
    cfg, scfg, series = setup(
        to_frame(y, "monthly", unique_id="s"), transform="log", window="expanding"
    )
    SeesTransformed.seen = []
    factories = {"sees": lambda m: SeesTransformed(), "naive": lambda m: Naive()}
    f = run_backtest(series, scfg, cfg, "rid", factories=factories)[0].frame()
    first = f[(f["model"] == "sees")].iloc[0]
    np.testing.assert_allclose(SeesTransformed.seen[0][-1], np.log(y[first["origin_t"]]))
    # back-transformed: naive on logs, exp'd, equals naive on the level
    a = f[f["model"] == "sees"]["y_pred"].to_numpy()
    b = f[f["model"] == "naive"]["y_pred"].to_numpy()
    np.testing.assert_allclose(a, b, rtol=1e-12)
    assert set(f.loc[f["model"] == "sees", "transform"]) == {"log"}
    assert set(f.loc[f["model"] == "naive", "transform"]) == {"none"}


def test_log_transform_failure_falls_back_and_is_recorded():
    y = seasonal_ar1(n=110, seed=2) - 100  # crosses zero
    cfg, scfg, series = setup(
        to_frame(y, "monthly", unique_id="s"), transform="log", window="expanding"
    )
    f = run_backtest(series, scfg, cfg, "rid", factories={"sees": lambda m: SeesTransformed()})[0]
    fr = f.frame()
    assert (fr["status"] == "ok").all()
    assert fr["transform"].str.startswith("none (fallback").all()


def test_returns_target():
    prices = gbm_prices(n=900, seed=3)
    df = to_frame(prices, "trading_days", "2020-01-01", unique_id="s")
    cfg, scfg, series = setup(
        df,
        freq="trading_days",
        target="returns",
        H=5,
        window="expanding",
        initial_window=250,
        n_test_origins=30,
        sma_windows=[5, 20],
    )
    f = run_backtest(series, scfg, cfg, "rid")[0].frame()
    t, h = f["origin_t"].to_numpy(), f["h"].to_numpy()
    np.testing.assert_allclose(f["y_true"], np.log(prices[t + h] / prices[t + h - 1]))
    np.testing.assert_allclose(f["level_true"], prices[t + h])
    zero = f[f["model"] == "zero_return"]
    np.testing.assert_allclose(zero["level_pred"], prices[zero["origin_t"]])  # price random walk
    last = f[f["model"] == "last_return"]
    lt = last["origin_t"].to_numpy()
    np.testing.assert_allclose(last["y_pred"], np.log(prices[lt] / prices[lt - 1]))
    mr = f[(f["model"] == "mean_return")]
    lp = mr["level_pred"].to_numpy()
    np.testing.assert_allclose(lp, prices[mr["origin_t"]] * np.exp(mr["y_pred"] * mr["h"]))
    assert (f["n_train"] == f["origin_t"]).all()  # t returns at origin t


def test_mase_scale_uses_seasonal_lag():
    z = np.array([1.0, 2.0, 4.0, 1.0, 2.0, 5.0])
    assert mase_scale(z, 3) == pytest.approx(np.mean([0.0, 0.0, 1.0]))
    assert mase_scale(z, 1) == pytest.approx(np.mean(np.abs(np.diff(z))))


def test_parallel_and_repeat_runs_are_identical():
    df = monthly(n=100, seed=4)
    cfg1, scfg, series = setup(df)
    cfg2, _, _ = setup(df, n_jobs=2)
    a = run_backtest(series, scfg, cfg1, "rid")[0].frame()
    b = run_backtest(series, scfg, cfg1, "rid")[0].frame()
    c = run_backtest(series, scfg, cfg2, "rid")[0].frame()
    assert content_hash(a) == content_hash(b) == content_hash(c)


def test_explicit_plan_is_respected():
    cfg, scfg, series = setup(monthly(), models=["naive"])
    plan = make_origins(len(series.y), scfg)
    _, used = run_backtest(series, scfg, cfg, "rid", plan=plan)
    assert used is plan
