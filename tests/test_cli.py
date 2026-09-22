from __future__ import annotations

import io
import json
import shutil
from pathlib import Path

import yaml

from forecasting.backtest.store import ForecastStore, content_hash
from forecasting.cli import cmd_run, cmd_validate, main

ROOT = Path(__file__).resolve().parents[1]


def run_cli(path: Path) -> tuple[int, str]:
    out = io.StringIO()
    code = cmd_validate(path, out=out)
    return code, out.getvalue()


def test_example_config_passes():
    code, text = run_cli(ROOT / "examples" / "config.yaml")
    assert code == 0, text
    assert "[OK] index_like" in text and "[OK] electricity_like" in text
    assert "run_id:" in text
    assert "weekdays without a row" in text


def test_bad_fixture_fails_with_rule_name(tmp_path: Path):
    cfg = {
        "series": [
            {
                "id": "dup",
                "source": f"csv:{ROOT / 'tests' / 'fixtures' / 'bad_duplicates.csv'}",
                "columns": {"date": "date", "value": "value"},
                "freq": "monthly",
                "H": 12,
            }
        ]
    }
    p = tmp_path / "c.yaml"
    p.write_text(yaml.safe_dump(cfg))
    code, text = run_cli(p)
    assert code == 1
    assert "DuplicateTimestampError" in text and "no run_id" in text


def test_config_error_exit_code(tmp_path: Path):
    p = tmp_path / "c.yaml"
    p.write_text("series:\n  - {id: a, source: 'csv:x.csv', freq: monthly}\n")
    code, text = run_cli(p)
    assert code == 2 and "series[0].H" in text


def test_main_entry_point(capsys):
    code = main(["validate", str(ROOT / "examples" / "config.yaml")])
    assert code == 0
    assert "run_id" in capsys.readouterr().out


# ------------------------------------------------------------------ forecast run (M2)


def example_copy(tmp_path: Path) -> Path:
    dst = tmp_path / "ex"
    shutil.copytree(ROOT / "examples", dst, ignore=shutil.ignore_patterns("runs"))
    return dst / "config.yaml"


def test_run_writes_store_and_manifest(tmp_path: Path):
    cfg_path = example_copy(tmp_path)
    out = io.StringIO()
    assert cmd_run(cfg_path, out=out) == 0, out.getvalue()
    text = out.getvalue()
    assert "index_like: 298 origins (28 warm-up, 20 dev, 250 test), step 5" in text
    assert "no gate.yaml" in text
    [run_dir] = list((cfg_path.parent / "runs").iterdir())
    man = json.loads((run_dir / "manifest.json").read_text())
    assert man["run_id"] == run_dir.name
    assert man["rows"]["total"] == man["rows"]["ok"]  # no failures on clean synthetic data
    assert set(man["plans"]) == {"index_like", "electricity_like"}
    assert set(man["selections"]["sma"]["electricity_like"]) == {"expanding", "rolling"}
    store = ForecastStore.read(run_dir / "forecasts.parquet")
    assert content_hash(store.frame()) == man["content_hash"]
    assert len(store) == man["rows"]["total"]

    # Same config, data and code: same run_id, not recomputed.
    out2 = io.StringIO()
    assert cmd_run(cfg_path, out=out2) == 0
    assert "already exists" in out2.getvalue()

    # --force recomputes and reproduces the same contents (A1, timing excluded).
    assert cmd_run(cfg_path, force=True, out=io.StringIO()) == 0
    man2 = json.loads((run_dir / "manifest.json").read_text())
    assert man2["content_hash"] == man["content_hash"]


def test_run_records_gate_yaml(tmp_path: Path):
    cfg_path = example_copy(tmp_path)
    (cfg_path.parent / "gate.yaml").write_text("thresholds: {}\n")
    out = io.StringIO()
    assert cmd_run(cfg_path, out=out) == 0
    assert "no gate.yaml" not in out.getvalue()
    [run_dir] = list((cfg_path.parent / "runs").iterdir())
    gate = json.loads((run_dir / "manifest.json").read_text())["gate"]
    assert gate["present"] and len(gate["sha256"]) == 64


def test_run_refuses_invalid_data(tmp_path: Path):
    cfg = {
        "series": [
            {
                "id": "dup",
                "source": f"csv:{ROOT / 'tests' / 'fixtures' / 'bad_duplicates.csv'}",
                "columns": {"date": "date", "value": "value"},
                "freq": "monthly",
                "H": 12,
            }
        ]
    }
    p = tmp_path / "c.yaml"
    p.write_text(yaml.safe_dump(cfg))
    out = io.StringIO()
    assert cmd_run(p, out=out) == 1
    assert "nothing was run" in out.getvalue()
    assert not (tmp_path / "runs").exists()


def test_main_run_entry_point(tmp_path: Path, capsys):
    cfg_path = example_copy(tmp_path)
    assert main(["run", str(cfg_path)]) == 0
    assert "store:" in capsys.readouterr().out
