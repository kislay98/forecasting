from __future__ import annotations

import numpy as np
import pandas as pd

from forecasting.backtest.engine import run_backtest
from forecasting.backtest.selection import select_sma_k
from forecasting.config import parse_config
from forecasting.data.validate import validate
from tests.synthetic import random_walk, to_frame


def frame() -> pd.DataFrame:
    cfg = parse_config(
        {
            "series": [
                {
                    "id": "s",
                    "source": "csv:x",
                    "freq": "monthly",
                    "season": "none",
                    "H": 6,
                    "models": ["sma"],
                    "sma_windows": [2, 3, 6, 12],
                }
            ]
        }
    )
    scfg = cfg.series[0]
    [(series, _)] = validate(to_frame(random_walk(n=120, seed=9), "monthly", unique_id="s"), scfg)
    return run_backtest(series, scfg, cfg, "rid")[0].frame()


def test_picks_lowest_dev_mase_per_window():
    f = frame()
    sel = select_sma_k(f)
    assert set(sel["s"]) == {"expanding", "rolling"}
    for window, choice in sel["s"].items():
        scores = choice["scores"]
        assert choice["dev_mase"] == min(scores.values())
        assert choice["model"] == f"sma_{choice['k']}"
        # on a random walk the shortest window should win
        assert choice["k"] == 2, (window, scores)


def test_test_rows_cannot_change_the_choice():
    # L5 preview: poison every test row; the selection must not move.
    f = frame()
    clean = select_sma_k(f)
    for poison in (np.nan, 1e9, -1e9):
        g = f.copy()
        test = g["origin_role"] == "test"
        g.loc[test, "y_true"] = poison
        g.loc[test, "y_pred"] = poison
        assert select_sma_k(g) == clean


def test_ties_go_to_the_smaller_window():
    f = frame()
    dev = f["origin_role"] == "dev"
    for k in (3, 6):
        m = dev & (f["model"] == f"sma_{k}")
        f.loc[m, "y_pred"] = f.loc[m, "y_true"]  # perfect forecasts
    assert select_sma_k(f)["s"]["expanding"]["k"] == 3


def test_no_sma_rows_gives_empty_selection():
    f = frame()
    assert select_sma_k(f[f["origin_role"] != "dev"]) == {}


# ---------------------------------------------------------------- best candidate


def candidate_frame() -> pd.DataFrame:
    """The M5 toy store: baselines, 'good' (y + small noise, bounds), 'combination'
    (y + larger noise, no bounds)."""
    from tests.test_scoring import toy_frame

    return toy_frame()


def test_best_candidate_is_ranked_on_dev_and_skips_baselines():
    from forecasting.backtest.selection import select_best_candidate

    sel = select_best_candidate(candidate_frame())["s"]["expanding"]
    assert sel["ranking"] == ["good", "combination"]
    assert sel["model"] == "good" and sel["with_intervals"] == "good"
    assert set(sel["scores"]) == {"good", "combination"}


def test_best_candidate_with_intervals_skips_the_combination():
    from forecasting.backtest.selection import select_best_candidate

    f = candidate_frame()
    dev = f["origin_role"] == "dev"
    f.loc[dev & (f["model"] == "combination"), "y_pred"] = f.loc[
        dev & (f["model"] == "combination"), "y_true"
    ]  # a perfect combination on dev
    sel = select_best_candidate(f)["s"]["expanding"]
    assert sel["model"] == "combination" and sel["with_intervals"] == "good"


def test_L5_best_candidate_ignores_test_rows():
    from forecasting.backtest.selection import select_best_candidate

    f = candidate_frame()
    clean = select_best_candidate(f)
    for value in (np.nan, 1e9, -1e9, 0.0):
        g = f.copy()
        test = g["origin_role"] == "test"
        for c in ["y_true", "y_pred", "mase_scale", "lo_80", "hi_80", "lo_95", "hi_95"]:
            g.loc[test, c] = value
        assert select_best_candidate(g) == clean
    # positive control: dev rows do matter
    g = f.copy()
    hit = (g["origin_role"] == "dev") & (g["model"] == "good")
    g.loc[hit, "y_pred"] = 1e9
    assert select_best_candidate(g)["s"]["expanding"]["model"] == "combination"
