from __future__ import annotations

from pathlib import Path

import pytest

from forecasting.config import LEVEL_BASELINES, RETURN_BASELINES, SeriesConfig, parse_config

FIXTURES = Path(__file__).parent / "fixtures"


def make_scfg(**overrides) -> SeriesConfig:
    """A SeriesConfig through the real parser, so tests exercise the same checks."""
    raw = {"id": "s", "source": "csv:x.csv", "freq": "monthly", "H": 12}
    raw.update(overrides)
    return parse_config({"series": [raw]}).series[0]


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES


def pin_baselines(raw: dict) -> dict:
    """Pin a raw series config to the baselines unless it names its models.

    Tests about the engine, store or leakage machinery use the baselines so the suite stays
    fast; the statistical models have their own tests (test_statistical.py)."""
    if "models" in raw:
        return raw
    if raw.get("target") == "returns":
        models = list(RETURN_BASELINES)
    else:
        seasonal = raw.get("freq") != "trading_days" and raw.get("season") != "none"
        models = [x for x in LEVEL_BASELINES if seasonal or x != "seasonal_naive"]
    return {**raw, "models": models}


def write_gate(cfg_path: Path, **overrides) -> Path:
    """A valid gate.yaml next to a config, matching its series (P1 in tests)."""
    import yaml

    from forecasting.config import load_config

    cfg = load_config(cfg_path)
    gate = {
        "registered": "2026-09-23",
        "alpha": 0.05,
        "primary_window": "expanding",
        "series": {
            s.id: {"decision_horizons": list(s.decision_horizons), "models": list(s.models)}
            for s in cfg.series
        },
        "thresholds": {
            "relative_mae_below": 1.0,
            "coverage_80": [0.75, 0.85],
            "coverage_95": [0.91, 0.98],
            "subperiods_min": 3,
            "leakage_gain_over_ets": 0.30,
        },
    }
    gate.update(overrides)
    path = cfg_path.parent / "gate.yaml"
    path.write_text(yaml.safe_dump(gate, sort_keys=False))
    return path
