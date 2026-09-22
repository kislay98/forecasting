from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from forecasting.data.adapters import load_series, read_csv, read_fred
from forecasting.errors import AdapterError, MissingColumnError
from tests.conftest import make_scfg

FRED_TEXT = "observation_date,IPG2211A2N\n2020-01-01,101.5\n2020-02-01,.\n2020-03-01,99.25\n"


def test_csv_column_mapping(tmp_path: Path):
    p = tmp_path / "nse.csv"
    p.write_text("Date,Open,Close\n01-Jan-2024,1,21000.5\n02-Jan-2024,2,21100\n")
    scfg = make_scfg(freq="trading_days", H=1, columns={"date": "Date", "value": "Close"})
    df = read_csv(p, scfg)
    assert list(df.columns[:3]) == ["unique_id", "ds", "y"]
    assert (df["unique_id"] == "s").all()
    # Adapters never parse: values are returned exactly as read.
    assert df["ds"].tolist() == ["01-Jan-2024", "02-Jan-2024"]
    assert df["y"].tolist() == ["21000.5", "21100"]
    assert "Open" in df.columns  # passed through so validate can warn


def test_csv_panel_id_column(tmp_path: Path):
    p = tmp_path / "panel.csv"
    p.write_text("sym,d,v\nA,2024-01-01,1\nB,2024-01-01,2\n")
    scfg = make_scfg(columns={"date": "d", "value": "v", "id": "sym"})
    df = read_csv(p, scfg)
    assert sorted(df["unique_id"].unique()) == ["A", "B"]


def test_csv_missing_mapped_column(tmp_path: Path):
    p = tmp_path / "x.csv"
    p.write_text("Date,Price\n2024-01-01,1\n")
    with pytest.raises(MissingColumnError, match="Close"):
        read_csv(p, make_scfg(columns={"date": "Date", "value": "Close"}))


def test_csv_not_found(tmp_path: Path):
    with pytest.raises(AdapterError, match="not found"):
        read_csv(tmp_path / "nope.csv", make_scfg())


def test_fred_fetches_once_then_uses_cache(tmp_path: Path):
    calls = []

    def fake_fetch(series_id: str) -> str:
        calls.append(series_id)
        return FRED_TEXT

    scfg = make_scfg(source="fred:IPG2211A2N")
    df = read_fred("IPG2211A2N", scfg, tmp_path, fetch=fake_fetch)
    df2 = read_fred("IPG2211A2N", scfg, tmp_path, fetch=fake_fetch)
    assert calls == ["IPG2211A2N"]
    assert (tmp_path / "IPG2211A2N.csv").exists()
    assert list(df.columns) == ["unique_id", "ds", "y"]
    assert df["y"].isna().tolist() == [False, True, False]  # FRED '.' is missing
    pd.testing.assert_frame_equal(df, df2)


def test_fred_old_date_header(tmp_path: Path):
    text = FRED_TEXT.replace("observation_date", "DATE")
    df = read_fred("IPG2211A2N", make_scfg(), tmp_path, fetch=lambda _: text)
    assert len(df) == 3


def test_fred_rejects_wrong_file(tmp_path: Path):
    with pytest.raises(AdapterError, match="not a FRED csv"):
        read_fred("X", make_scfg(), tmp_path, fetch=lambda _: "a,b\n1,2\n")


def test_fred_offline_error_is_typed(tmp_path: Path):
    def offline(_: str) -> str:
        raise AdapterError("FRED download failed")

    with pytest.raises(AdapterError, match="download failed"):
        read_fred("X", make_scfg(), tmp_path, fetch=offline)


def test_load_series_resolves_relative_to_config(tmp_path: Path):
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "a.csv").write_text("ds,y\n2024-01-01,1\n")
    df = load_series(make_scfg(source="csv:d/a.csv"), tmp_path)
    assert len(df) == 1

    df = load_series(make_scfg(source="fred:IPG2211A2N"), tmp_path, fetch=lambda _: FRED_TEXT)
    assert (tmp_path / "data" / "fred" / "IPG2211A2N.csv").exists()
    assert len(df) == 3
