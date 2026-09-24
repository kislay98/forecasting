"""Name -> factory. 'sma' expands to one model per candidate window (sma_3, sma_6, ...);
the window is chosen later on dev origins only (backtest/selection.py). 'combination'
has no factory: the engine forms it from its members at each fold (models/combination.py).
"""

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
from forecasting.models.statistical import (
    SES,
    ARAuto,
    ETSAuto,
    SARIMAAuto,
    Theta,
    seasonal_or_stl,
)
from forecasting.models.variance import (
    EWMA,
    GARCH,
    GJRGARCH,
    ConstantSigma,
    GARCHNormal,
)

REGISTRY: dict[str, ModelFactory] = {
    "naive": lambda m: Naive(),
    "seasonal_naive": lambda m: SeasonalNaive(m),
    "drift": lambda m: Drift(),
    "zero_return": lambda m: ZeroReturn(),
    "mean_return": lambda m: MeanReturn(),
    "last_return": lambda m: LastReturn(),
    "ses": lambda m: SES(),
    "ets": lambda m: seasonal_or_stl(lambda k: ETSAuto(k), m),
    "sarima": lambda m: seasonal_or_stl(lambda k: SARIMAAuto(k), m),
    "theta": lambda m: Theta(m),
    "ar": lambda m: ARAuto(),
    "zero_return_fhs": lambda m: ConstantSigma(),
    "ewma": lambda m: EWMA(),
    "garch_normal": lambda m: GARCHNormal(),
    "garch": lambda m: GARCH(),
    "gjr_garch": lambda m: GJRGARCH(),
}
DERIVED = ("combination",)


def _sma_factory(k: int) -> ModelFactory:
    return lambda m: SMA(k)


def _sarima_factory(search: str) -> ModelFactory:
    def make(m: int):
        return seasonal_or_stl(lambda k: SARIMAAuto(k, search), m)

    return make


def build_factories(scfg: SeriesConfig) -> dict[str, ModelFactory]:
    out: dict[str, ModelFactory] = {}
    for name in scfg.models:
        if name == "sma":
            for k in scfg.sma_windows:
                out[f"sma_{k}"] = _sma_factory(k)
        elif name == "sarima":
            out[name] = _sarima_factory(scfg.sarima_search)
        elif name in DERIVED:
            continue
        else:
            out[name] = REGISTRY[name]
    return out
