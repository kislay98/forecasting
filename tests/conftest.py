from __future__ import annotations

from pathlib import Path

import pytest

from forecasting.config import SeriesConfig, parse_config

FIXTURES = Path(__file__).parent / "fixtures"


def make_scfg(**overrides) -> SeriesConfig:
    """A SeriesConfig through the real parser, so tests exercise the same checks."""
    raw = {"id": "s", "source": "csv:x.csv", "freq": "monthly", "H": 12}
    raw.update(overrides)
    return parse_config({"series": [raw]}).series[0]


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES
