from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from forecasting.config import git_sha, load_config, parse_config, run_id
from forecasting.errors import ConfigError

GOOD = {
    "seed": 1,
    "series": [
        {
            "id": "nifty50",
            "source": "csv:data/nifty50.csv",
            "columns": {"date": "Date", "value": "Close"},
            "freq": "trading_days",
            "season": "none",
            "target": "returns",
            "H": 20,
            "decision_horizons": [1, 5, 20],
        },
        {
            "id": "electricity",
            "source": "fred:IPG2211A2N",
            "freq": "monthly",
            "season": 12,
            "target": "level",
            "positive": True,
            "H": 24,
            "decision_horizons": [1, 12, 24],
        },
    ],
}


def series(**over):
    s = {"id": "s", "source": "csv:x.csv", "freq": "monthly", "H": 12}
    s.update(over)
    return {"series": [s]}


def test_good_config_parses_spec_example():
    cfg = parse_config(GOOD)
    nifty, elec = cfg.series
    assert nifty.m == 1 and nifty.target == "returns" and nifty.columns.value == "Close"
    assert elec.m == 12 and elec.positive and elec.source_kind == "fred"
    assert elec.decision_horizons == (1, 12, 24)


def test_load_config_from_yaml(tmp_path: Path):
    p = tmp_path / "c.yaml"
    p.write_text(yaml.safe_dump(GOOD))
    cfg = load_config(p)
    assert cfg.base_dir == tmp_path.resolve()
    assert len(cfg.series) == 2


def test_config_is_frozen():
    cfg = parse_config(GOOD)
    with pytest.raises(AttributeError):
        cfg.seed = 2  # type: ignore[misc]


@pytest.mark.parametrize(
    "freq,expected_m", [("monthly", 12), ("quarterly", 4), ("weekly", 52), ("trading_days", 1)]
)
def test_season_defaults_from_freq(freq, expected_m):
    cfg = parse_config(series(freq=freq, H=1))
    assert cfg.series[0].m == expected_m


def test_missing_H_is_refused():
    raw = series()
    del raw["series"][0]["H"]
    with pytest.raises(ConfigError) as e:
        parse_config(raw)
    assert e.value.key == "series[0].H"
    assert "no default" in str(e.value)


def test_null_H_is_refused():
    with pytest.raises(ConfigError) as e:
        parse_config(series(H=None))
    assert e.value.key == "series[0].H"


@pytest.mark.parametrize(
    "raw,key",
    [
        ({"series": [], "extra": 1}, "extra"),
        (series(horizon=12), "series[0].horizon"),
        (series(columns={"date": "d", "valu": "v"}), "series[0].columns.valu"),
    ],
)
def test_unknown_keys_rejected(raw, key):
    with pytest.raises(ConfigError) as e:
        parse_config(raw)
    assert e.value.key == key


@pytest.mark.parametrize(
    "over,key",
    [
        ({"freq": "daily"}, "series[0].freq"),
        ({"target": "price"}, "series[0].target"),
        ({"H": 0}, "series[0].H"),
        ({"H": 12.5}, "series[0].H"),
        ({"H": True}, "series[0].H"),
        ({"H": 37}, "series[0].H"),  # > 3m for monthly
        ({"decision_horizons": [1, 13]}, "series[0].decision_horizons"),
        ({"decision_horizons": [1, 1]}, "series[0].decision_horizons"),
        ({"season": 0}, "series[0].season"),
        ({"positive": "yes"}, "series[0].positive"),
        ({"duplicates": "first"}, "series[0].duplicates"),
        ({"source": "x.csv"}, "series[0].source"),
        ({"source": "s3:bucket/x"}, "series[0].source"),
        ({"start": "last year"}, "series[0].start"),
        ({"id": ""}, "series[0].id"),
    ],
)
def test_invalid_values_name_the_key(over, key):
    with pytest.raises(ConfigError) as e:
        parse_config(series(**over))
    assert e.value.key == key


def test_H_cap_only_applies_to_seasonal_series():
    # Nifty: trading days, no season, H = 20 is valid (scope update overrides H <= 3m).
    assert parse_config(series(freq="trading_days", H=20)).series[0].H == 20
    assert parse_config(series(freq="monthly", season="none", H=24)).series[0].m == 1


def test_H_below_m_warns():
    s = parse_config(series(H=6)).series[0]
    assert any("H = 6 < m = 12" in w for w in s.warnings)


def test_duplicate_series_ids_rejected():
    raw = {"series": [series()["series"][0], series()["series"][0]]}
    with pytest.raises(ConfigError) as e:
        parse_config(raw)
    assert e.value.key == "series"


def test_empty_series_list_rejected():
    with pytest.raises(ConfigError):
        parse_config({"series": []})


def test_run_id_is_deterministic_and_sensitive():
    cfg = parse_config(GOOD)
    base = run_id(cfg, "d1", "g1")
    assert base == run_id(parse_config(GOOD), "d1", "g1")
    assert len(base) == 16
    assert run_id(cfg, "d2", "g1") != base
    assert run_id(cfg, "d1", "g2") != base
    changed = {**GOOD, "seed": 2}
    assert run_id(parse_config(changed), "d1", "g1") != base


def test_run_id_ignores_config_location(tmp_path: Path):
    a = parse_config(GOOD, base_dir=tmp_path / "a")
    b = parse_config(GOOD, base_dir=tmp_path / "b")
    assert run_id(a, "d", "g") == run_id(b, "d", "g")


def test_git_sha_outside_repo(tmp_path: Path):
    assert git_sha(tmp_path) == "nogit"


# ------------------------------------------------------------------ backtest keys (M2)


def test_backtest_defaults_depend_on_freq_and_target():
    m = parse_config(series()).series[0]
    assert (m.initial_window, m.rolling_length, m.origin_step) == (36, 36, 1)
    assert (m.n_dev_origins, m.n_test_origins, m.transform, m.window) == (20, 30, "auto", "both")
    assert m.models == ("naive", "seasonal_naive", "drift", "sma")
    assert m.sma_windows == (3, 6, 12, 24)
    t = parse_config(series(freq="trading_days", target="returns", H=20)).series[0]
    assert (t.initial_window, t.rolling_length, t.origin_step) == (500, 500, 5)
    assert (t.n_test_origins, t.transform) == (250, "none")
    assert t.models == ("zero_return", "mean_return", "last_return", "sma")
    assert t.sma_windows == (5, 20, 60, 250)
    ns = parse_config(series(season="none")).series[0]
    assert "seasonal_naive" not in ns.models and ns.sma_windows == (2, 3, 6)


@pytest.mark.parametrize(
    "over,key",
    [
        ({"target": "returns", "transform": "log"}, "series[0].transform"),
        ({"transform": "sqrt"}, "series[0].transform"),
        ({"window": "sliding"}, "series[0].window"),
        ({"origin_step": 0}, "series[0].origin_step"),
        ({"n_test_origins": 2.5}, "series[0].n_test_origins"),
        ({"models": ["naive", "zero_return"]}, "series[0].models"),
        ({"models": ["seasonal_naive"], "season": "none"}, "series[0].models"),
        ({"models": []}, "series[0].models"),
        ({"sma_windows": [1, 3]}, "series[0].sma_windows"),
        ({"initial_window": 1}, "series[0].initial_window"),
    ],
)
def test_backtest_keys_validated(over, key):
    with pytest.raises(ConfigError) as e:
        parse_config(series(**over))
    assert e.value.key == key


@pytest.mark.parametrize(
    "raw,key",
    [
        ({**series(), "levels": [80]}, "levels"),
        ({**series(), "levels": [0.8, 0.801]}, "levels"),
        ({**series(), "n_jobs": 0}, "n_jobs"),
        ({**series(), "output_dir": 5}, "output_dir"),
    ],
)
def test_top_level_keys_validated(raw, key):
    with pytest.raises(ConfigError) as e:
        parse_config(raw)
    assert e.value.key == key


def test_long_sma_windows_dropped_with_warning():
    s = parse_config(series(sma_windows=[3, 50], initial_window=40)).series[0]
    assert s.sma_windows == (3,)
    assert any("[50]" in w for w in s.warnings)


def test_few_test_origins_warn():
    s = parse_config(series(n_test_origins=10)).series[0]
    assert any("underpowered" in w for w in s.warnings)


def test_run_id_ignores_output_dir_and_n_jobs():
    a = parse_config({**GOOD, "output_dir": "a", "n_jobs": 1})
    b = parse_config({**GOOD, "output_dir": "b", "n_jobs": 4})
    assert run_id(a, "d", "g") == run_id(b, "d", "g")
    c = parse_config({**GOOD, "levels": [0.5, 0.9]})
    assert run_id(c, "d", "g") != run_id(a, "d", "g")
