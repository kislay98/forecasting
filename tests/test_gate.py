"""gate.yaml (P1): loading, matching the config, the committed check, and the report's
gate decision (A10) with its refusal rules."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml

from forecasting.config import load_config
from forecasting.errors import ConfigError
from forecasting.evaluation import report as rp
from forecasting.gate import check_gate, gate_status, load_gate
from tests.conftest import write_gate
from tests.test_scoring import toy_frame, toy_manifest


@pytest.fixture
def cfg_dir(tmp_path: Path) -> Path:
    raw = {
        "series": [
            {"id": "s", "source": "csv:x.csv", "freq": "monthly", "H": 12,
             "decision_horizons": [1, 6, 12], "models": ["naive", "seasonal_naive", "ses"]}
        ]
    }  # fmt: skip
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(raw))
    return tmp_path


def test_load_and_check_a_valid_gate(cfg_dir):
    path = write_gate(cfg_dir / "config.yaml")
    gate = load_gate(path)
    assert gate.series["s"].decision_horizons == (1, 6, 12)
    assert gate.thresholds["coverage_80"] == [0.75, 0.85] and len(gate.sha256) == 64
    check_gate(gate, load_config(cfg_dir / "config.yaml"))


@pytest.mark.parametrize(
    "overrides, key",
    [
        ({"alpha": 0.7}, "alpha"),
        ({"primary_window": "both"}, "primary_window"),
        ({"thresholds": {"relative_mae_below": 1.0}}, "thresholds"),
        ({"extra": 1}, "unknown keys"),
    ],
)
def test_invalid_gates_are_config_errors(cfg_dir, overrides, key):
    path = write_gate(cfg_dir / "config.yaml", **overrides)
    with pytest.raises(ConfigError, match=key):
        load_gate(path)


def test_gate_must_match_the_config(cfg_dir):
    cfg = load_config(cfg_dir / "config.yaml")
    path = write_gate(cfg_dir / "config.yaml")
    g = yaml.safe_load(path.read_text())
    g["series"]["s"]["decision_horizons"] = [1, 12]
    path.write_text(yaml.safe_dump(g))
    with pytest.raises(ConfigError, match="decision_horizons"):
        check_gate(load_gate(path), cfg)
    g["series"]["s"]["decision_horizons"] = [1, 6, 12]
    g["series"]["s"]["models"] = ["naive"]
    path.write_text(yaml.safe_dump(g))
    with pytest.raises(ConfigError, match="models"):
        check_gate(load_gate(path), cfg)
    g["series"] = {"other": g["series"]["s"]}
    path.write_text(yaml.safe_dump(g))
    with pytest.raises(ConfigError, match="series"):
        check_gate(load_gate(path), cfg)


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def test_gate_status_knows_whether_the_file_is_committed(cfg_dir):
    cfg = load_config(cfg_dir / "config.yaml")
    path = write_gate(cfg_dir / "config.yaml")
    assert gate_status(cfg)["committed"] is False  # not a repository
    git(cfg_dir, "init", "-q")
    git(cfg_dir, "-c", "user.email=t@t", "-c", "user.name=t", "add", "gate.yaml")
    assert gate_status(cfg)["committed"] is False  # staged, not committed
    git(cfg_dir, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "gate")
    st = gate_status(cfg)
    assert st["committed"] is True and len(st["commit"]) == 40
    path.write_text(path.read_text() + "# edited\n")
    assert gate_status(cfg)["committed"] is False  # changed after the commit


# ---------------------------------------------------------------- the report's decision


def gated_manifest(tmp_path: Path, **status) -> tuple[Path, dict]:
    """The toy manifest with a gate.yaml written for it and its status recorded."""
    man = toy_manifest()
    gate = {
        "registered": "2026-09-23", "alpha": 0.05, "primary_window": "expanding",
        "series": {"s": {"decision_horizons": [1, 3], "models": ["naive", "good"]}},
        "thresholds": {"relative_mae_below": 1.0, "coverage_80": [0.75, 0.85],
                       "coverage_95": [0.91, 0.98], "subperiods_min": 3,
                       "leakage_gain_over_ets": 0.30},
    }  # fmt: skip
    path = tmp_path / "gate.yaml"
    path.write_text(yaml.safe_dump(gate))
    man["gate"] = {"path": str(path), "present": True, "sha256": load_gate(path).sha256,
                   "committed": True, "commit": "a" * 40, **status}  # fmt: skip
    return path, man


def test_report_issues_a_decision_only_for_a_committed_unchanged_gate(tmp_path):
    frame = toy_frame()
    path, man = gated_manifest(tmp_path)
    rep = rp.build_report(frame, man, mcs_reps=100, skill_reps=100)
    a10 = rep.series[0].gate
    assert a10 is not None and a10.rq == "A10"
    assert a10.answer in {"GO", "GO, restricted", "NO-GO", "AUDIT FIRST"}
    assert "## Gate decision (A10)" in rep.markdown and "Registered (" in rep.series[0].exit.text
    assert "Criteria: 1 accuracy" in a10.text and "3 calibration" in a10.text

    _, man2 = gated_manifest(tmp_path, committed=False)
    rep2 = rp.build_report(frame, man2, mcs_reps=100, skill_reps=100)
    assert rep2.series[0].gate is None and "uncommitted changes at run time" in rep2.markdown

    path.write_text(path.read_text() + "# edited after the run\n")
    rep3 = rp.build_report(frame, man, mcs_reps=100, skill_reps=100)
    assert rep3.series[0].gate is None and "changed after this run" in rep3.markdown
    assert "Provisional (" in rep3.series[0].exit.text


def test_gate_primary_window_and_thresholds_are_used(tmp_path):
    frame = toy_frame(windows=("expanding", "rolling"))
    path, man = gated_manifest(tmp_path)
    g = yaml.safe_load(path.read_text())
    g["primary_window"] = "rolling"
    g["thresholds"]["leakage_gain_over_ets"] = 0.001  # anything better than ETS is suspect
    path.write_text(yaml.safe_dump(g))
    man["gate"]["sha256"] = load_gate(path).sha256
    rep = rp.build_report(frame, man, mcs_reps=100, skill_reps=100)
    assert rep.series[0].window == "rolling"
    # no ETS on the toy level series: the leakage check is skipped, so GO or NO-GO
    assert rep.series[0].gate.answer != "AUDIT FIRST"
    man_json = json.dumps(man)  # the manifest is JSON-serialisable with the gate block
    assert "sha256" in man_json


def test_crps_reference_is_optional_and_validated(cfg_dir):
    """Phase 2 registered zero_return as the CRPS comparator and Phases 3 and 4 registered
    zero_return_fhs, which the report had hardcoded. The key makes it explicit without
    invalidating the four gates already committed, so it is optional, not required."""
    path = write_gate(cfg_dir / "config.yaml")
    g = yaml.safe_load(path.read_text())
    assert "crps_reference" not in g["thresholds"]

    g["thresholds"]["crps_reference"] = "zero_return"
    path.write_text(yaml.safe_dump(g))
    assert load_gate(path).thresholds["crps_reference"] == "zero_return"

    g["thresholds"]["crps_reference"] = ""
    path.write_text(yaml.safe_dump(g))
    with pytest.raises(ConfigError, match="crps_reference"):
        load_gate(path)

    del g["thresholds"]["crps_reference"]
    g["thresholds"]["not_a_threshold"] = 1
    path.write_text(yaml.safe_dump(g))
    with pytest.raises(ConfigError, match="thresholds"):
        load_gate(path)
