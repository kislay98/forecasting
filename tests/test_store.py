from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import pytest

from forecasting.backtest.engine import run_backtest
from forecasting.backtest.store import ForecastStore, columns_for, content_hash
from forecasting.config import parse_config
from forecasting.data.validate import validate
from forecasting.errors import SchemaError
from tests.synthetic import seasonal_ar1, to_frame


@pytest.fixture
def store() -> ForecastStore:
    cfg = parse_config(
        {
            "series": [
                {
                    "id": "s",
                    "source": "csv:x",
                    "freq": "monthly",
                    "H": 12,
                    "models": ["naive", "sma"],
                }
            ]
        }
    )
    scfg = cfg.series[0]
    [(series, _)] = validate(to_frame(seasonal_ar1(n=100), "monthly", unique_id="s"), scfg)
    return run_backtest(series, scfg, cfg, "rid")[0]


def test_schema_columns():
    names = [c for c, _ in columns_for((0.8, 0.95))]
    for c in [
        "run_id",
        "unique_id",
        "model",
        "window",
        "origin_t",
        "origin_period",
        "origin_role",
        "h",
        "target_period",
        "y_true",
        "y_true_missing",
        "y_pred",
        "lo_80",
        "hi_80",
        "lo_95",
        "hi_95",
        "transform",
        "variant",
        "fit_seconds",
        "status",
        "error",
    ]:
        assert c in names


def test_write_read_round_trip(store: ForecastStore, tmp_path: Path):
    path = store.write(tmp_path / "f.parquet")
    back = ForecastStore.read(path)
    pd.testing.assert_frame_equal(back.frame(), store.frame())
    table = pq.read_table(path)
    assert table.schema.metadata is None
    # SMA has no interval: stored as nulls, not NaN
    sma = table.filter(
        pq.ParquetFile(path).read().column("model").to_pandas().str.startswith("sma").to_numpy()
    )
    assert sma.column("lo_80").null_count == sma.num_rows


def test_append_rejects_wrong_columns(store: ForecastStore):
    bad = store.frame().drop(columns=["variant"])
    with pytest.raises(SchemaError):
        ForecastStore(store.levels).append(bad)
    reordered = store.frame()[list(reversed(store.frame().columns))]
    with pytest.raises(SchemaError):
        ForecastStore(store.levels).append(reordered)


def test_append_rejects_unknown_status_and_bad_types(store: ForecastStore):
    f = store.frame()
    f.loc[0, "status"] = "maybe"
    with pytest.raises(SchemaError):
        ForecastStore(store.levels).append(f)
    g = store.frame()
    g["h"] = "one"
    with pytest.raises(SchemaError):
        ForecastStore(store.levels).append(g)


def test_read_rejects_foreign_parquet(tmp_path: Path):
    p = tmp_path / "x.parquet"
    pd.DataFrame({"a": [1]}).to_parquet(p)
    with pytest.raises(SchemaError):
        ForecastStore.read(p)


def test_store_has_no_update_or_delete(store: ForecastStore):
    public = {n for n in dir(store) if not n.startswith("_")}
    assert public <= {
        "append",
        "extend",
        "frame",
        "write",
        "read",
        "levels",
        "columns",
        "arrow_schema",
    }


def test_content_hash_ignores_timing_only(store: ForecastStore):
    f = store.frame()
    g = f.copy()
    g["fit_seconds"] = 99.0
    assert content_hash(f) == content_hash(g)
    g.loc[0, "y_pred"] = np.nextafter(g.loc[0, "y_pred"], np.inf)
    assert content_hash(f) != content_hash(g)


def test_combining_stores_with_different_levels_fails(store: ForecastStore):
    with pytest.raises(SchemaError):
        ForecastStore((0.5,)).extend(store)
