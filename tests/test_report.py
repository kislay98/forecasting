"""The Phase 1 report (forecasting/evaluation/report.py) and `forecast report`.

M6 is done when the report answers RQ1 to RQ6 on synthetic data: the end-to-end test
runs the real pipeline on two synthetic series (a seasonal level series and a return
series) and checks every verdict. The verdict rules themselves are tested on small
hand-built tables.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from forecasting.cli import cmd_report, cmd_run
from forecasting.evaluation import report as rp
from forecasting.evaluation.scoring import SeriesLabels
from tests.synthetic import gbm_prices, seasonal_ar1, to_frame

EM_DASH = chr(0x2014)
LAB = SeriesLabels("s", m=12, H=24, target="level", decision_horizons=(1, 12, 24))


def point(rows: list[tuple]) -> pd.DataFrame:
    """(model, h, rel_mae, holm p, mae) rows; the reference is 'ref'."""
    df = pd.DataFrame(rows, columns=["model", "h", "rel_mae", "dm_p_better_holm", "mae"])
    return df.assign(reference="ref", dm_p_better=df["dm_p_better_holm"] / 2)


def test_rq1_untestable_horizons_are_named_and_never_pass():
    """DM-HLN is not run below n / h = 5 (M7): those horizons cannot pass, are named in
    the text, and 'yes' means every testable horizon."""
    rows = point(
        [("ets", 1, 0.8, 0.01, 1.0), ("ets", 12, 0.9, np.nan, 1.0), ("ets", 24, 0.9, np.nan, 1.0)]
    )
    rows.loc[rows["h"] > 1, "dm_p_better"] = np.nan
    v, passes = rp.rq1(rows, LAB, "ref")
    assert v.answer == "yes" and passes["ets"] == [1] and "Untestable at h = 12, 24" in v.text
    rows[["dm_p_better", "dm_p_better_holm"]] = np.nan
    assert rp.rq1(rows, LAB, "ref")[0].answer == "not testable"


# ---------------------------------------------------------------- RQ1 and the exit table


def test_rq1_yes_partly_no_and_not_tested():
    yes = point([("ets", h, 0.8, 0.01, 1.0) for h in (1, 12, 24)] + [("ref", 1, 1.0, np.nan, 1.2)])
    v, passes = rp.rq1(yes, LAB, "ref")
    assert v.answer == "yes" and passes["ets"] == [1, 12, 24]
    partly = point(
        [("ets", 1, 0.8, 0.01, 1.0), ("ets", 12, 0.9, 0.2, 1), ("ets", 24, 1.1, 0.01, 1)]
    )
    v, passes = rp.rq1(partly, LAB, "ref")
    assert v.answer == "partly" and passes["ets"] == [1]  # rel MAE >= 1 is never a win
    no = point([("ets", h, 0.95, 0.3, 1.0) for h in (1, 12, 24)])
    assert rp.rq1(no, LAB, "ref")[0].answer == "no"
    baselines_only = point([("naive", h, 0.9, np.nan, 1.0) for h in (1, 12, 24)])
    assert rp.rq1(baselines_only, LAB, "ref")[0].answer == "not tested"


def test_exit_table_rows():
    """Spec: Phase 1 exit decision. A gain over ETS above 30% overrides everything."""
    gate = {"present": False}
    base = [("ets", h, 0.8, 0.01, 1.0) for h in (1, 12, 24)]
    v1 = rp.rq1(point(base), LAB, "ref")[0]
    assert rp.exit_decision(point(base), LAB, v1, 24, gate).answer == "proceed"
    leak = point([*base, ("combination", 12, 0.5, 0.001, 0.65)])  # 35% better than ETS
    ex = rp.exit_decision(leak, LAB, v1, 24, gate)
    assert ex.answer == "leakage audit" and "combination" in ex.text and "h = 12" in ex.text
    ok = point([*base, ("combination", 12, 0.7, 0.001, 0.75)])  # 25%: fine
    assert rp.exit_decision(ok, LAB, v1, 24, gate).answer == "proceed"
    partly = rp.Verdict("RQ1", "partly", "")
    ex = rp.exit_decision(point(base), LAB, partly, 5, gate)
    assert ex.answer == "proceed, restricted" and "h* = 5" in ex.text
    assert rp.exit_decision(point(base), LAB, rp.Verdict("RQ1", "no", ""), 0, gate).answer == (
        "stop modelling"
    )
    assert "exploratory" in ex.text
    ex = rp.exit_decision(point(base), LAB, v1, 24, {"present": True, "sha256": "ab" * 32})
    assert "gate.yaml present" in ex.text


def test_exit_leakage_check_on_returns_uses_the_reference():
    """No ETS on returns: a 30% gain over the reference itself is the red flag (U5)."""
    lab = SeriesLabels("r", m=1, H=5, target="returns", decision_horizons=(1, 5))
    p = point([("ar", 1, 0.6, 0.001, 0.006), ("ar", 5, 0.99, 0.5, 0.0099),
               ("ref", 1, 1.0, np.nan, 0.01), ("ref", 5, 1.0, np.nan, 0.01)])  # fmt: skip
    p.loc[p["model"] == "ref", "reference"] = "ref"
    v1 = rp.rq1(p, lab, "ref")[0]
    assert rp.exit_decision(p, lab, v1, 1, {}).answer == "leakage audit"


# ---------------------------------------------------------------- RQ2 to RQ6


def test_rq2_names_the_unforecastable_horizons():
    skill = pd.DataFrame({"model": "ets", "h": [1, 2, 3], "skill": [0.3, 0.1, 0.0],
                          "lo": [0.1, 0.01, -0.2], "hi": [0.5, 0.2, 0.2]})  # fmt: skip
    hstar = pd.DataFrame({"model": ["ets"], "h_star": [2]})
    v, hs = rp.rq2(skill, hstar, "ets", 3)
    assert hs == 2 and v.answer == "h* = 2" and "Horizons 3 to 3 are not forecastable" in v.text
    assert rp.rq2(skill, hstar, None, 3)[0].answer == "not answerable"


def buckets(cov: float, n_eff: float, level: float = 0.8) -> pd.DataFrame:
    from forecasting.evaluation.metrics import coverage_band
    from forecasting.evaluation.tests import kupiec

    lo, hi = coverage_band(level, n_eff)
    n = 30
    kp = kupiec(n * (1 - cov) * n_eff / n, n_eff, 1 - level)
    return pd.DataFrame({"model": ["ets"], "bucket": ["very_short"], "level": [level],
                         "n_eff": [n_eff], "coverage": [cov], "band_lo": [lo], "band_hi": [hi],
                         "kupiec_p": [kp.p_value]})  # fmt: skip


def test_rq3_calibrated_miscalibrated_and_uninformative():
    assert rp.rq3(buckets(0.8, 30), "ets").answer == "calibrated"
    assert rp.rq3(buckets(0.5, 30), "ets").answer == "under-covers"
    assert rp.rq3(buckets(1.0, 30), "ets").answer == "over-covers"
    v = rp.rq3(buckets(0.5, 2.5), "ets")  # floor(n / h) = 2 trials: a band of [0, 1]
    assert v.answer == "not answerable" and "effective trials" in v.text
    assert rp.rq3(buckets(0.8, 30), None).answer == "not answerable"


def test_rq4_thresholds():
    """Share above 50%: yes; at most 10%: no; in between: mixed."""
    res = pd.DataFrame({"model": ["ar"], "n_origins": [100], "share_lb": [0.04],
                        "share_lb_2m": [np.nan], "share_arch": [0.97]})  # fmt: skip
    v = rp.rq4(res, "ar", "returns")
    assert v.answer == "autocorrelation no, ARCH yes" and "U6" in v.text
    res["share_lb"] = 0.3
    assert rp.rq4(res, "ar", "level").answer.startswith("autocorrelation mixed")
    assert rp.rq4(res, None, "level").answer == "not answerable"


def test_rq5_directions_and_subperiods():
    gap = pd.DataFrame({"model": ["ets"] * 2, "h": [1, 12], "ratio": [0.8, 1.0],
                        "p_rolling_better_holm": [0.01, 0.5],
                        "p_expanding_better_holm": [0.99, 0.5]})  # fmt: skip
    sub = pd.DataFrame({"model": "ets", "h": 1, "block": [1, 2, 3, 4],
                        "rel_mae": [0.9, 0.8, 1.1, 0.7]})  # fmt: skip
    v = rp.rq5(gap, sub, "ets", "ref")
    assert v.answer == "rolling wins" and "3 of 4 at h = 1" in v.text
    gap["ratio"], gap["p_rolling_better_holm"] = [1.3, 1.0], [0.99, 0.5]
    gap["p_expanding_better_holm"] = [0.01, 0.5]
    assert rp.rq5(gap, sub, "ets", "ref").answer == "stable"
    gap["p_expanding_better_holm"] = [0.2, 0.5]
    assert rp.rq5(gap, sub, "ets", "ref").answer == "no significant gap"
    assert rp.rq5(pd.DataFrame(), sub, "ets", "ref").answer == "not answerable"


def test_rq6_dominant_mixed_and_configured():
    tr = pd.DataFrame(
        {"transform": ["log", "none"], "n_folds": [29, 1], "share": [29 / 30, 1 / 30]}
    )
    assert rp.rq6(tr, "auto").answer == "log"
    tr = pd.DataFrame({"transform": ["log", "none"], "n_folds": [18, 12], "share": [0.6, 0.4]})
    assert rp.rq6(tr, "auto").answer == "mixed"
    assert rp.rq6(tr, "none").answer == "none (configured)"
    assert rp.rq6(tr.iloc[0:0], "auto").answer == "not applicable"


def test_fmt_and_md_table():
    assert rp.fmt(np.nan) == "n/a" and rp.fmt(3) == "3" and rp.fmt(0.12345) == "0.123"
    assert rp.fmt(0.00123) == "0.001" and rp.fmt(0.000123) == "0.00012"
    assert rp.fmt(True) == "yes"
    assert rp.pval(0.0004) == "< 0.001" and rp.pval(np.nan) == "n/a"
    t = rp.md_table(pd.DataFrame({"h": [1, 12], "x": [0.5, np.nan]}))
    assert t.splitlines() == ["| h | x |", "|---|---|", "| 1 | 0.500 |", "| 12 | n/a |"]


# ---------------------------------------------------------------- end to end on synthetic data


@pytest.fixture(scope="module")
def synthetic_run(tmp_path_factory):
    """A seasonal level series and a return series through `forecast run`."""
    tmp = tmp_path_factory.mktemp("synthetic")
    (tmp / "data").mkdir()
    level = to_frame(seasonal_ar1(n=132, seed=3) + np.linspace(0, 20, 132), "monthly")
    level.rename(columns={"ds": "date", "y": "value"})[["date", "value"]].to_csv(
        tmp / "data" / "level.csv", index=False
    )
    prices = to_frame(gbm_prices(n=900, seed=4), "trading_days", "2020-01-01")
    prices.rename(columns={"ds": "date", "y": "value"})[["date", "value"]].to_csv(
        tmp / "data" / "prices.csv", index=False
    )
    cols = {"date": "date", "value": "value"}
    raw = {
        "seed": 7,
        "series": [
            {"id": "seasonal", "source": "csv:data/level.csv", "columns": cols, "freq": "monthly",
             "season": 12, "positive": True, "H": 12, "decision_horizons": [1, 6, 12],
             "models": ["naive", "seasonal_naive", "drift", "sma", "ses", "theta"]},
            {"id": "returns", "source": "csv:data/prices.csv", "columns": cols,
             "freq": "trading_days", "season": "none", "target": "returns", "H": 5,
             "decision_horizons": [1, 5], "initial_window": 250, "n_test_origins": 40,
             "models": ["zero_return", "mean_return", "last_return", "sma", "ar"]},
        ],
    }  # fmt: skip
    cfg_path = tmp / "config.yaml"
    cfg_path.write_text(yaml.safe_dump(raw))
    out = io.StringIO()
    assert cmd_run(cfg_path, out=out) == 0, out.getvalue()
    [run_dir] = list((tmp / "runs").iterdir())
    return cfg_path, run_dir, out.getvalue()


def test_report_answers_rq1_to_rq6_on_synthetic_data(synthetic_run):
    """M6 done-when: every RQ gets a verdict for every series, with its tables and plots."""
    _, run_dir, out = synthetic_run
    md_path = run_dir / "report" / "report.md"
    assert f"report: {md_path}" in out
    md = md_path.read_text()
    for uid in ("seasonal", "returns"):
        assert f"## {uid}" in md
    for rq in range(1, 7):
        assert md.count(f"### RQ{rq}:") == 2
    assert sum(line.startswith("| Phase 1 exit |") for line in md.splitlines()) == 2
    assert EM_DASH not in md and " nan " not in md.lower()
    assert "Exploratory run" in md  # no gate.yaml
    figs = sorted(p.name for p in (run_dir / "report" / "figures").iterdir())
    for uid in ("seasonal", "returns"):
        for kind in ("relative_mae", "skill", "coverage"):
            assert f"{uid}_{kind}.png" in figs
    assert all((run_dir / "report" / "figures" / f).read_bytes()[:4] == b"\x89PNG" for f in figs)


@pytest.mark.slow
def test_report_verdicts_on_synthetic_data(synthetic_run):
    _, run_dir, _ = synthetic_run
    frame, man = _load(run_dir)
    rep = rp.build_report(frame, man, mcs_reps=200, skill_reps=200)
    allowed = {
        "RQ1": {"yes", "partly", "no"},
        "RQ3": {"calibrated", "under-covers", "over-covers", "miscalibrated", "not answerable"},
        "RQ5": {"rolling wins", "stable", "no significant gap"},
    }
    for uid in ("seasonal", "returns"):
        for rq, ok in allowed.items():
            assert rep.verdict(uid, rq).answer in ok, (uid, rq, rep.verdict(uid, rq))
        assert rep.verdict(uid, "RQ2").answer.startswith("h* = ")
        assert rep.verdict(uid, "RQ4").answer.startswith("autocorrelation ")
        assert rep.verdict(uid, "exit").answer in {
            "proceed", "proceed, restricted", "stop modelling", "leakage audit"
        }  # fmt: skip
    assert rep.verdict("seasonal", "RQ6").answer in {"log", "none", "boxcox", "mixed"}
    assert rep.verdict("returns", "RQ6").answer == "none (configured)"
    # the dev-chosen candidate speaks for the series, never a test-chosen one
    cands = man["selections"]["best_candidate"]
    assert {s.uid: s.candidate for s in rep.series} == {
        u: cands[u]["expanding"]["model"] for u in ("seasonal", "returns")
    }


def _load(run_dir: Path):
    from forecasting.backtest.store import ForecastStore

    frame = ForecastStore.read(run_dir / "forecasts.parquet").frame()
    return frame, json.loads((run_dir / "manifest.json").read_text())


@pytest.mark.slow
def test_report_is_reproducible(synthetic_run, tmp_path):
    """Same store, manifest and seed: the same markdown and the same PNG bytes."""
    _, run_dir, _ = synthetic_run
    frame, man = _load(run_dir)
    a = rp.write_report(rp.build_report(frame, man, mcs_reps=200, skill_reps=200), tmp_path / "a")
    b = rp.write_report(rp.build_report(frame, man, mcs_reps=200, skill_reps=200), tmp_path / "b")
    assert a.read_text() == b.read_text()
    for f in (tmp_path / "a" / "figures").iterdir():
        assert f.read_bytes() == (tmp_path / "b" / "figures" / f.name).read_bytes()


def test_L5_report_never_reads_dev_or_warmup_rows(synthetic_run):
    """With the manifest's dev selections fixed, poisoning every dev and warm-up row
    leaves the report text unchanged."""
    _, run_dir, _ = synthetic_run
    frame, man = _load(run_dir)
    clean = rp.build_report(frame, man, mcs_reps=200, skill_reps=200).markdown
    g = frame.copy()
    hit = g["origin_role"] != "test"
    for c in ["y_true", "y_pred", "mase_scale", "lb_p", "arch_p", "lo_80", "hi_80"]:
        g.loc[hit, c] = 1e9
    assert rp.build_report(g, man, mcs_reps=200, skill_reps=200).markdown == clean


@pytest.mark.slow
def test_report_never_refits(synthetic_run, monkeypatch):
    import forecasting.backtest.engine as engine
    import forecasting.models.registry as registry

    def boom(*a, **k):
        raise AssertionError("the report must not fit models")

    monkeypatch.setattr(engine, "run_backtest", boom)
    monkeypatch.setattr(registry, "build_factories", boom)
    _, run_dir, _ = synthetic_run
    frame, man = _load(run_dir)
    assert rp.build_report(frame, man, mcs_reps=100, skill_reps=100).markdown


# ---------------------------------------------------------------- CLI


@pytest.mark.slow
def test_cli_report_from_run_dir_and_from_config(synthetic_run):
    cfg_path, run_dir, _ = synthetic_run
    (run_dir / "report" / "report.md").unlink()
    out = io.StringIO()
    assert cmd_report(run_dir, out=out) == 0, out.getvalue()
    assert (run_dir / "report" / "report.md").exists()
    out = io.StringIO()
    assert cmd_report(cfg_path, out=out) == 0, out.getvalue()
    assert str(run_dir / "report" / "report.md") in out.getvalue()


def test_cli_report_without_a_run(tmp_path):
    out = io.StringIO()
    assert cmd_report(tmp_path, out=out) == 2
    assert "no run at" in out.getvalue()
