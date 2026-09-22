"""Equal-weight combination of ETS-auto, SARIMA and Theta point forecasts.

Membership is fixed here, in code, before any run (leakage source 11); weights are equal
and never learned (Wang et al. 2023). The mean is taken on the transformed scale and then
back-transformed, like every other model. If a member fails at an origin, the combination
is the mean of the remaining members and says so in its variant. No intervals in Phase 1
(they need pooled paths, Phase 2).

The engine combines the members it already fitted at each fold (combine), so members are
never fitted twice. EqualWeight is the same rule as a standalone Forecaster; a test checks
both give identical forecasts.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import numpy as np

from forecasting.errors import FitError, NotFittedError
from forecasting.models.base import BaseForecaster, Forecaster, ForecastResult

MEMBERS: tuple[str, ...] = ("ets", "sarima", "theta")
NAME = "combination"


def combine(member_means: Mapping[str, np.ndarray | None]) -> tuple[np.ndarray, str]:
    """(mean, variant) from members' transformed-scale means; None marks a failed member."""
    used = [name for name in MEMBERS if member_means.get(name) is not None]
    if not used:
        raise FitError(NAME, "every member failed at this origin")
    mean = np.mean([member_means[name] for name in used], axis=0)
    failed = [name for name in MEMBERS if name not in used]
    variant = f"mean({','.join(used)})" + (f"; failed: {','.join(failed)}" if failed else "")
    return mean, variant


class EqualWeight(BaseForecaster):
    name = NAME
    uses_transform = True
    expensive = True
    min_obs = 1

    def __init__(self, members: Mapping[str, Forecaster]) -> None:
        super().__init__()
        self.members = dict(members)
        self.seed = 0

    def _fit(self, v: np.ndarray) -> None:
        import pandas as pd

        self._ok: dict[str, Forecaster] = {}
        for name, model in self.members.items():
            if hasattr(model, "seed"):
                model.seed = self.seed
            try:
                model.fit(pd.Series(v))
                self._ok[name] = model
            except FitError:
                continue
        if not self._ok:
            raise FitError(self.name, "every member failed to fit")

    def predict(self, h: int, levels: Sequence[float] = ()) -> ForecastResult:
        if not self._fitted:
            raise NotFittedError(f"{self.name}: predict before fit")
        means: dict[str, np.ndarray | None] = {n: None for n in MEMBERS}
        for name, model in self._ok.items():
            try:
                means[name] = model.predict(h).mean
            except FitError:
                means[name] = None
        mean, variant = combine(means)
        return ForecastResult(mean=mean, info={"variant": variant})

    def _residuals(self) -> np.ndarray:
        return np.array([])
