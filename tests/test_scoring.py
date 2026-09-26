"""Scoring layer (forecasting/evaluation/scoring.py): what is scored, against what.

Toy stores are built by hand so the expected numbers are known; the end-to-end test
runs the real pipeline on the example data with the baselines.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from forecasting.backtest.store import ForecastStore, columns_for
from forecasting.evaluation import metrics as mt
from forecasting.evaluation.scoring import scorable, score, score_run

LEVELS = (0.8, 0.95)
ROLES = ["warmup"] * 3 + ["dev"] * 5 + ["test"] * 14
H = 3


def toy_frame(
    target: str = "level", seed: int = 0, windows: tuple[str, ...] = ("expanding",)
) -> pd.DataFrame:
    """One series, every role. y is a noisy trend; 'good' is y + small noise. Test folds
    carry residual diagnostics; a rolling window, if asked for, is the same forecasts
    plus noise."""
    frames = [_toy_window(target, seed, w, i) for i, w in enumerate(windows)]
    store = ForecastStore(LEVELS)
    for df in frames:
        store.append(df)
    return store.frame()


def _toy_window(target: str, seed: int, window: str, k: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    n = len(ROLES) + H + 1
    if target == "returns":
        y = rng.normal(0, 0.01, n)
    else:
        y = 50 + 0.5 * np.arange(n) + rng.normal(0, 1, n)
    rows = []
    for t, role in enumerate(ROLES):
        for h in range(1, H + 1):
            yt = y[t + h]
            preds = {
                "naive": 0.0 if target == "returns" else y[t],
                "drift": y[t] + h * 0.5,
                "sma_2": y[max(0, t - 1) : t + 1].mean(),
                "sma_3": y[max(0, t - 2) : t + 1].mean(),
                "good": yt + rng.normal(0, 0.1 if target == "level" else 0.001),
                "combination": yt + rng.normal(0, 0.2 if target == "level" else 0.002),
            }
            if target == "returns":
                preds["zero_return"] = 0.0
                preds["mean_return"] = y[: t + 1].mean()
                del preds["naive"], preds["drift"]
            diag_rng = np.random.default_rng([seed, t, k])
            for model, yp in preds.items():
                yp = yp + k * rng.normal(0, 0.3 if target == "level" else 0.003)
                has_iv = model != "combination"
                test = role == "test" and has_iv
                rows.append(
                    {
                        "run_id": "toy", "unique_id": "s", "model": model, "window": window,
                        "origin_t": t, "origin_period": str(t), "origin_role": role, "h": h,
                        "target_period": str(t + h), "y_true": yt, "y_true_missing": False,
                        "y_pred": yp,
                        "lo_80": yp - 1.0 if has_iv else np.nan, "hi_80": yp + 1.0 if has_iv else np.nan,
                        "lo_95": yp - 2.0 if has_iv else np.nan, "hi_95": yp + 2.0 if has_iv else np.nan,
                        "es_80": np.nan, "es_95": np.nan,
                        "level_true": np.nan, "level_pred": np.nan, "mase_scale": 1.0,
                        "n_train": t + 1, "n_outliers": 0, "n_resid": 40 if test else 0,
                        "lb_p": diag_rng.uniform() if test else np.nan, "lb_p_2m": np.nan,
                        "arch_p": diag_rng.uniform() if test else np.nan,
                        "transform": "log" if model in ("good", "combination") else "none",
                        "variant": "",
                        "fit_seconds": 0.0, "status": "ok", "error": "",
                    }
                )  # fmt: skip
    return pd.DataFrame(rows)[[c for c, _ in columns_for(LEVELS)]]


def toy_manifest(target: str = "level", seed: int = 1, reference: str = "sma_3") -> dict:
    ref = "zero_return" if target == "returns" and reference == "sma_3" else reference
    return {
        "config": {
            "seed": seed,
            "series": [{"id": "s", "m": 1, "H": H, "target": target, "decision_horizons": [1, 3]}],
        },
        "selections": {
            "sma": {"s": {w: {"model": "sma_3", "k": 3} for w in ("expanding", "rolling")}},
            "best_baseline": {"s": {w: {"model": ref} for w in ("expanding", "rolling")}},
        },
    }


def quick(frame, manifest):
    return score(frame, manifest, mcs_reps=200, skill_reps=200)


def all_tables(s) -> dict[str, pd.DataFrame]:
    return {k: getattr(s, k) for k in s.__dataclass_fields__}


# ---------------------------------------------------------------- what is scored


def test_only_test_rows_with_status_ok_and_y_true_are_scored():
    f = toy_frame()
    test = f["origin_role"] == "test"
    first = test & (f["origin_t"] == f.loc[test, "origin_t"].min())
    f.loc[first & (f["model"] == "good"), "status"] = "failed"
    f.loc[first & (f["model"] == "drift"), ["y_true_missing"]] = True
    f.loc[first & (f["model"] == "drift"), ["y_true"]] = np.nan
    p = quick(f, toy_manifest()).point
    n = p.set_index(["model", "h"])["n"]
    assert (n.loc["naive"] == 14).all()
    assert (n.loc["good"] == 13).all() and (n.loc["drift"] == 13).all()
    assert set(scorable(f)["origin_role"]) == {"test"}


def test_sma_is_the_dev_chosen_k_and_the_reference_is_the_dev_best_baseline():
    s = quick(toy_frame(), toy_manifest())
    models = set(s.point["model"])
    assert "sma" in models and not any(m.startswith("sma_") for m in models)
    assert s.selections.iloc[0][["sma_model", "sma_k", "reference"]].tolist() == ["sma_3", 3, "sma"]
    sma = s.point[s.point["model"] == "sma"]
    assert (sma["rel_mae"] == 1.0).all() and sma["dm_p"].isna().all()


def test_point_metrics_match_a_direct_computation():
    """MAE, relative MAE and the DM inputs recomputed straight from the test rows."""
    f = toy_frame()
    s = quick(f, toy_manifest(reference="naive"))
    t = f[f["origin_role"] == "test"]
    for h in range(1, H + 1):
        g = t[(t["model"] == "good") & (t["h"] == h)]
        r = t[(t["model"] == "naive") & (t["h"] == h)]
        row = s.point.set_index(["model", "h"]).loc[("good", h)]
        assert row["mae"] == pytest.approx(mt.mae(g["y_true"], g["y_pred"]))
        assert row["rel_mae"] == pytest.approx(
            mt.relative_mae(g["y_true"], g["y_pred"], r["y_pred"].to_numpy())
        )
        assert row["n_eff"] == pytest.approx(14 / h)
        assert row["reference"] == "naive"


def test_holm_covers_only_test_horizons_and_candidate_models():
    """RQ1's family: every (candidate, test horizon). Baselines keep their raw DM p-values
    but are not in the family, so they cannot dilute it (M6 revises M5-12)."""
    p = quick(toy_frame(), toy_manifest()).point
    fam = p["dm_p_better_holm"].notna()
    # decision horizons 1 and 3, but 14 origins at h = 3 is n / h = 4.7 < 5: untestable
    assert set(p.loc[fam, "h"]) == {1}
    assert p.loc[p["h"] == 3, "dm_p_better"].isna().all()
    assert set(p.loc[fam, "model"]) == {"good", "combination"}
    assert (p.loc[fam, "dm_p_better_holm"] >= p.loc[fam, "dm_p_better"]).all()
    assert fam.sum() == 2  # 1 testable horizon x (good, combination)
    assert p.loc[(p["model"] == "naive") & (p["h"] < 3), "dm_p_better"].notna().all()


def test_mcs_and_skill_have_the_a5_a6_shape():
    s = quick(toy_frame(), toy_manifest())
    assert s.point["mcs_in"].notna().all()
    for h in range(1, H + 1):
        best = s.point[s.point["h"] == h].sort_values("mae").iloc[0]
        assert best["mcs_in"]  # the lowest-MAE model is never excluded here
    assert set(s.skill["model"]) == {"naive", "drift", "good", "combination"}
    assert (s.skill["lo"] <= s.skill["hi"]).all()
    hs = s.h_star.set_index("model")["h_star"]
    assert hs["good"] == H  # near-perfect forecasts are skilful at every h
    assert set(s.mcs_buckets["bucket"]) == {"very_short", "short"}


def test_combination_and_models_without_bounds_are_skipped_for_intervals():
    f = toy_frame()
    f.loc[f["model"] == "drift", ["lo_80", "hi_80", "lo_95", "hi_95"]] = np.nan
    s = quick(f, toy_manifest())
    assert "combination" not in set(s.intervals["model"])
    assert "drift" not in set(s.intervals["model"])
    assert "combination" in set(s.point["model"])
    good = s.intervals.set_index(["model", "h", "level"]).loc[("good", 1, 0.8)]
    assert good["coverage"] == 1.0  # |error| ~ 0.1, bounds +-1
    # 14 trials cannot rule out 80%: P(14 of 14) = 0.8^14 = 0.044 > 0.025
    assert good["n_eff"] == 14 and good["band_hi"] == 1.0 and good["in_band"]
    good3 = s.intervals.set_index(["model", "h", "level"]).loc[("good", 3, 0.8)]
    assert good3["n_eff"] == pytest.approx(14 / 3) and good3["band_lo"] < good["band_lo"]
    assert set(s.interval_buckets["bucket"]) == {"very_short", "short"}


def test_return_series_get_the_return_metrics_and_no_percentage_metrics():
    f = toy_frame("returns")
    p = quick(f, toy_manifest("returns")).point.set_index(["model", "h"])
    assert p["mape"].isna().all() and p["smape"].isna().all() and p["wape"].isna().all()
    t = f[(f["origin_role"] == "test") & (f["model"] == "good") & (f["h"] == 1)]
    y, yp = t["y_true"].to_numpy(), t["y_pred"].to_numpy()
    row = p.loc[("good", 1)]
    assert row["mae_vs_zero"] == pytest.approx(mt.mae(y, yp) / np.abs(y).mean())
    assert row["dir_acc"] == pytest.approx(mt.directional_accuracy(y, yp))
    assert row["pt_p"] < 0.05  # near-perfect forecasts get the direction right
    assert p.loc[("mean_return", 1), "r2_oos"] == 0.0
    assert row["r2_oos"] > 0.9
    assert np.isnan(p.loc[("zero_return", 1), "pt_stat"])


def test_mape_is_off_when_any_test_actual_is_not_positive():
    f = toy_frame()
    assert quick(f, toy_manifest()).point["mape"].notna().all()
    hit = (f["origin_role"] == "test") & (f["origin_t"] == 10) & (f["h"] == 1)
    f.loc[hit, "y_true"] = 0.0
    assert quick(f, toy_manifest()).point["mape"].isna().all()


def test_bootstrap_results_come_from_the_run_seed():
    f = toy_frame()
    a, b = quick(f, toy_manifest(seed=1)), quick(f, toy_manifest(seed=1))
    for name, t in all_tables(a).items():
        pd.testing.assert_frame_equal(t, all_tables(b)[name])
    c = quick(f, toy_manifest(seed=2))
    assert not np.allclose(a.skill["lo"], c.skill["lo"])
    np.testing.assert_allclose(a.skill["skill"], c.skill["skill"])  # point values unchanged


def test_scoring_never_refits(monkeypatch):
    """Scoring reads the store only: the engine and model factories are made to explode."""
    import forecasting.backtest.engine as engine
    import forecasting.models.registry as registry

    def boom(*a, **k):
        raise AssertionError("scoring must not fit models")

    monkeypatch.setattr(engine, "run_backtest", boom)
    monkeypatch.setattr(registry, "build_factories", boom)
    assert len(quick(toy_frame(), toy_manifest()).point)


# ---------------------------------------------------------------- L5-style quarantine


def _poison(frame: pd.DataFrame, roles: set[str], value: float) -> pd.DataFrame:
    g = frame.copy()
    hit = g["origin_role"].isin(roles)
    num = ["y_true", "y_pred", "mase_scale", "level_true", "level_pred", "n_resid", "lb_p",
           "lb_p_2m", "arch_p", *[c for c in g.columns if c.startswith(("lo_", "hi_"))]]  # fmt: skip
    for c in num:
        g.loc[hit, c] = value
    g.loc[hit, "y_true_missing"] = False
    return g


@pytest.mark.parametrize("target", ["level", "returns"])
@pytest.mark.parametrize("value", [np.nan, 1e9, -1e9, 0.0])
def test_L5_no_metric_reads_dev_or_warmup_rows(target, value):
    """Poison every numeric column of the dev and warm-up rows: every table is identical."""
    f, man = toy_frame(target, windows=("expanding", "rolling")), toy_manifest(target)
    clean = all_tables(quick(f, man))
    assert all(len(t) for t in clean.values()), {k: len(t) for k, t in clean.items()}
    dirty = all_tables(quick(_poison(f, {"dev", "warmup"}, value), man))
    for name, t in clean.items():
        pd.testing.assert_frame_equal(t, dirty[name], obj=name)


def test_L5_failed_or_skipped_dev_rows_change_nothing():
    f, man = toy_frame(), toy_manifest()
    g = f.copy()
    g.loc[g["origin_role"] != "test", "status"] = "failed"
    for name, t in all_tables(quick(f, man)).items():
        pd.testing.assert_frame_equal(t, all_tables(quick(g, man))[name], obj=name)


def test_L5_positive_control_test_rows_do_matter():
    """If poisoning test rows changed nothing, the quarantine test would be vacuous."""
    f, man = toy_frame(), toy_manifest()
    clean = quick(f, man).point
    g = f.copy()
    hit = (g["origin_role"] == "test") & (g["model"] == "naive") & (g["origin_t"] == 12)
    g.loc[hit, "y_pred"] = 1e9
    assert not clean["mae"].equals(quick(g, man).point["mae"])


# ---------------------------------------------------------------- end to end


EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


@pytest.fixture(scope="module")
def example_run(tmp_path_factory):
    """The example config, baselines only, through `forecast run`'s own pipeline."""
    from forecasting.config import load_config
    from forecasting.pipeline import run, validate_all
    from tests.conftest import pin_baselines

    tmp = tmp_path_factory.mktemp("example")
    raw = yaml.safe_load((EXAMPLES / "config.yaml").read_text())
    shutil.copytree(EXAMPLES / "data", tmp / "data")
    raw["series"] = [pin_baselines(s) for s in raw["series"]]
    (tmp / "config.yaml").write_text(yaml.safe_dump(raw))
    cfg = load_config(tmp / "config.yaml")
    return run(cfg, validate_all(cfg))


def test_example_config_gives_a5_and_a6_tables(example_run):
    s = score_run(example_run.path)
    man = json.loads((example_run.path / "manifest.json").read_text())
    assert set(s.point["unique_id"]) == {"index_like", "electricity_like"}
    for uid, H_, n_test in [("index_like", 20, 250), ("electricity_like", 24, 30)]:
        p = s.point[s.point["unique_id"] == uid]
        for window in ("expanding", "rolling"):
            w = p[p["window"] == window]
            ref = man["selections"]["best_baseline"][uid][window]["model"]
            ref = "sma" if ref.startswith("sma_") else ref
            assert (w["reference"] == ref).all()
            assert set(w["h"]) == set(range(1, H_ + 1))
            assert (w["n"] == n_test).all()
            # A5: per-horizon relative MAE, DM-HLN p-values, MCS membership
            others = w[w["model"] != ref]
            assert others["rel_mae"].notna().all()
            testable = others["n"] >= 5 * others["h"]  # DM-HLN needs n / h >= 5 (M7)
            assert others.loc[testable, ["dm_p", "dm_p_better"]].notna().all().all()
            assert others.loc[~testable, "dm_p_better"].isna().all()
            assert w["mcs_in"].notna().all() and w.groupby("h")["mcs_in"].any().all()
            # A6: skill curve with CI at every h, and h*
            sk = s.skill[(s.skill["unique_id"] == uid) & (s.skill["window"] == window)]
            assert set(sk["h"]) == set(range(1, H_ + 1))
            hs = s.h_star[(s.h_star["unique_id"] == uid) & (s.h_star["window"] == window)]
            assert hs["h_star"].between(0, H_).all()
    ret = s.point[s.point["unique_id"] == "index_like"]
    assert ret["dir_acc"].notna().all() and ret["mape"].isna().all()
    # A7 shape: coverage per bucket with bands, 80% and 95%
    ib = s.interval_buckets
    assert set(ib["level"]) == {0.8, 0.95}
    assert {"coverage", "band_lo", "band_hi", "kupiec_p"} <= set(ib.columns)


# ---------------------------------------------------------------- RQ4 to RQ6 tables


def test_fold_tables_rq4_to_rq6():
    """Residual shares come from one row per test fold; transforms count candidate folds;
    the window gap pairs rolling with expanding; sub-periods split the test origins."""
    f = toy_frame(windows=("expanding", "rolling"))
    s = quick(f, toy_manifest())
    res = s.residuals.set_index(["window", "model"])
    test = f[(f["origin_role"] == "test") & (f["h"] == 1)]
    good = test[(test["window"] == "expanding") & (test["model"] == "good")]
    assert res.loc[("expanding", "good"), "n_origins"] == 14
    assert res.loc[("expanding", "good"), "share_lb"] == pytest.approx((good["lb_p"] < 0.05).mean())
    assert "combination" not in set(res.index.get_level_values("model"))  # no residuals
    tr = s.transforms.set_index(["window", "transform"])
    assert tr.loc[("expanding", "log"), "n_folds"] == 14
    gap = s.window_gap
    assert set(gap["h"]) == {1, 3} and set(gap["model"]) >= {"good", "naive", "sma"}
    assert gap["ratio"].gt(0).all()
    sub = s.subperiods
    assert set(sub["block"]) == {1, 2, 3, 4} and set(sub["h"]) == {1, 3}
    assert sub.groupby(["window", "model", "h"])["n"].sum().eq(14).all()
