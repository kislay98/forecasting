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
