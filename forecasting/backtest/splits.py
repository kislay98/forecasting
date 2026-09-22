"""Forecast origins and their roles. Looks only at the series length, never at values.

An origin t is the 0-based position of the last training observation. Every origin
has all H targets, so the last origin is t = n - 1 - H and origins step back from
there by origin_step. The last n_test origins are test, the n_dev before them dev,
and any earlier ones warm-up (stored, never scored).

For target: returns, the model sees returns r_1..r_t, so an origin needs t returns;
for levels it needs t + 1 values. The numbers match data/validate.volume_thresholds.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from forecasting.config import SeriesConfig

Role = Literal["warmup", "dev", "test"]


@dataclass(frozen=True)
class Origin:
    t: int
    role: Role


@dataclass(frozen=True)
class OriginPlan:
    origins: tuple[Origin, ...]
    initial_window: int
    reduced: bool
    n_dev: int
    n_test: int

    @property
    def n_warmup(self) -> int:
        return len(self.origins) - self.n_dev - self.n_test


def _offset(scfg: SeriesConfig) -> int:
    return 1 if scfg.target == "returns" else 0


def _count(n_rows: int, initial: int, scfg: SeriesConfig) -> int:
    first = initial - 1 + _offset(scfg)
    last = n_rows - 1 - scfg.H
    return 0 if last < first else (last - first) // scfg.origin_step + 1


def make_origins(n_rows: int, scfg: SeriesConfig) -> OriginPlan:
    step, H = scfg.origin_step, scfg.H
    n_dev, n_test = scfg.n_dev_origins, scfg.n_test_origins
    need = n_dev + n_test
    initial, reduced = scfg.initial_window, False

    if _count(n_rows, initial, scfg) < need:
        # Reduced mode: shorten the first window just enough, down to the floor.
        reduced = True
        t_model = n_rows - _offset(scfg)
        initial = max(scfg.floor_initial, t_model - H - step * (need - 1))
        initial = min(initial, scfg.initial_window)
        count = _count(n_rows, initial, scfg)
        if count < need:
            # Still short: dev and test shrink proportionally (spec: reduced mode).
            if count < 2:
                raise ValueError(
                    f"{scfg.id}: only {count} origins fit; validate should have refused this series"
                )
            n_test = max(1, round(count * n_test / need))
            n_dev = count - n_test
            if n_dev < 1:
                n_dev, n_test = 1, count - 1

    count = _count(n_rows, initial, scfg)
    last = n_rows - 1 - H
    ts = sorted(last - step * j for j in range(count))
    roles: list[Role] = ["warmup"] * (count - n_dev - n_test) + ["dev"] * n_dev + ["test"] * n_test
    return OriginPlan(
        origins=tuple(Origin(t, r) for t, r in zip(ts, roles, strict=True)),
        initial_window=initial,
        reduced=reduced,
        n_dev=n_dev,
        n_test=n_test,
    )
