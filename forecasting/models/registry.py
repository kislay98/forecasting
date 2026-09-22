"""Name -> factory. 'sma' expands to one model per candidate window (sma_3, sma_6, ...);
the window is chosen later on dev origins only (backtest/selection.py)."""

from __future__ import annotations

from forecasting.config import SeriesConfig
from forecasting.models.base import ModelFactory
from forecasting.models.baselines import (
    SMA,
    Drift,
    LastReturn,
    MeanReturn,
    Naive,
    SeasonalNaive,
    ZeroReturn,
)

REGISTRY: dict[str, ModelFactory] = {
    "naive": lambda m: Naive(),
    "seasonal_naive": lambda m: SeasonalNaive(m),
    "drift": lambda m: Drift(),
    "zero_return": lambda m: ZeroReturn(),
    "mean_return": lambda m: MeanReturn(),
    "last_return": lambda m: LastReturn(),
}


def _sma_factory(k: int) -> ModelFactory:
    return lambda m: SMA(k)


def build_factories(scfg: SeriesConfig) -> dict[str, ModelFactory]:
    out: dict[str, ModelFactory] = {}
    for name in scfg.models:
        if name == "sma":
            for k in scfg.sma_windows:
                out[f"sma_{k}"] = _sma_factory(k)
        else:
            out[name] = REGISTRY[name]
    return out
