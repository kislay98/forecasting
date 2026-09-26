"""The Phase 2 calibration table and the P2 gate decision.

Everything here implements rules that configs/phase2/gate.yaml fixed before any test
origin was scored. Nothing in this module chooses a threshold, a horizon, a level or a
model: it reads them from the gate.

Rows scored: origin_role test, status ok, y_true present, the gate's primary window.
Dev and warm-up rows are dropped first, exactly as Phase 1's scoring does.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from forecasting.backtest.store import level_tag
from forecasting.config import SeriesConfig
from forecasting.evaluation.calibration import (
    christoffersen,
    crps_grid,
    independence_testable,
    pit_uniformity,
)
from forecasting.evaluation.tests import kupiec
from forecasting.gate import Gate

GATE_LEVELS = (0.80, 0.95)  # the two the thresholds name


def overlap_factor(h: int, origin_step: int) -> float:
    """How many consecutive origins' h-step windows overlap. 1 means none do."""
    return max(1.0, h / origin_step)


def quantile_grid(g: pd.DataFrame, levels: tuple[float, ...]) -> tuple[np.ndarray, np.ndarray]:
    """(n, k) predictive quantiles and their taus, ascending, from the lo_/hi_ columns."""
    taus, cols = [], []
    for lv in levels:
        taus.append((1 - lv) / 2)
        cols.append(g[f"lo_{level_tag(lv)}"].to_numpy(dtype=float))
    for lv in levels:
        taus.append((1 + lv) / 2)
        cols.append(g[f"hi_{level_tag(lv)}"].to_numpy(dtype=float))
    order = np.argsort(taus)
    return np.column_stack(cols)[:, order], np.asarray(taus)[order]


def calibration_table(
    frame: pd.DataFrame,
    scfg: SeriesConfig,
    levels: tuple[float, ...],
    window: str,
    role: str = "test",
) -> pd.DataFrame:
    """One row per (model, h): coverage, Kupiec, Christoffersen, PIT, CRPS.

    `levels` is the run config's interval levels, which is where the quantile grid
    comes from; it is a run-level setting, not a per-series one.
    """
    rows_in = frame[
        (frame["origin_role"] == role)
        & (frame["status"] == "ok")
        & (~frame["y_true_missing"])
        & (frame["window"] == window)
    ]
    out: list[dict[str, Any]] = []
    for (model, h), g in rows_in.groupby(["model", "h"], sort=True):
        g = g.sort_values("origin_t")
        y = g["y_true"].to_numpy(dtype=float)
        h = int(h)
        ov = overlap_factor(h, scfg.origin_step)
        rec: dict[str, Any] = {
            "model": str(model),
            "h": h,
            "n": len(g),
            "n_eff": len(g) / ov,
            "overlapping": ov > 1,
        }
        for lv in GATE_LEVELS:
            tag = level_tag(lv)
            lo = g[f"lo_{tag}"].to_numpy(dtype=float)
            hi = g[f"hi_{tag}"].to_numpy(dtype=float)
            hits = ((y < lo) | (y > hi)).astype(float)
            rec[f"cov_{tag}"] = 1.0 - float(hits.mean())
            rec[f"misses_{tag}"] = int(hits.sum())
            rec[f"expected_{tag}"] = len(g) * (1 - lv)
            rec[f"kupiec_p_{tag}"] = kupiec(hits.sum() / ov, len(g) / ov, 1 - lv).p_value
            if independence_testable(h, scfg.origin_step):
                c = christoffersen(hits, 1 - lv)
                rec[f"ind_p_{tag}"] = c.p_ind
                rec[f"cc_p_{tag}"] = c.p_cc
                rec[f"clustered_{tag}"] = bool(c.clustered)
            else:
                rec[f"ind_p_{tag}"] = np.nan
                rec[f"cc_p_{tag}"] = np.nan
                rec[f"clustered_{tag}"] = None
        q, taus = quantile_grid(g, levels)
        rec["pit_p"] = pit_uniformity(y, q, taus)[1]
        rec["crps"] = crps_grid(y, q, taus)
        out.append(rec)
    table = pd.DataFrame(out)
    if table.empty:
        return table
    return table.sort_values(["h", "model"]).reset_index(drop=True)


@dataclass(frozen=True)
class Check:
    name: str
    h: int
    level: float
    passed: bool
    detail: str


@dataclass(frozen=True)
class P2Decision:
    series: str
    model: str
    go: bool
    checks: tuple[Check, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def failures(self) -> tuple[Check, ...]:
        return tuple(c for c in self.checks if not c.passed)


def gate_decision(
    gate: Gate, scfg: SeriesConfig, table: pd.DataFrame, reference: str = "zero_return"
) -> P2Decision:
    """Apply the registered P2 rules. All four conditions must hold for a go."""
    if gate.phase < 2:
        raise ValueError("gate_decision is for a calibration gate (phase 2 or later)")
    g = gate.series[scfg.id]
    model = g.primary_model
    th = gate.thresholds
    a = float(th["per_test_alpha"])
    checks: list[Check] = []
    notes: list[str] = []

    idx = table.set_index(["model", "h"])
    for h in g.decision_horizons:
        if (model, h) not in idx.index:
            checks.append(Check("rows", h, 0.0, False, f"no scored rows for {model} at h = {h}"))
            continue
        row = idx.loc[(model, h)]
        for lv in GATE_LEVELS:
            tag = level_tag(lv)
            band = th[f"coverage_{tag}"]
            cov = float(row[f"cov_{tag}"])
            checks.append(
                Check(
                    "coverage", h, lv, band[0] <= cov <= band[1],
                    f"{cov:.3f} against [{band[0]}, {band[1]}]",
                )
            )  # fmt: skip
            kp = float(row[f"kupiec_p_{tag}"])
            checks.append(Check("kupiec", h, lv, not (kp < a), f"p = {kp:.4f} against alpha {a}"))
            ip = row[f"ind_p_{tag}"]
            if pd.isna(ip):
                notes.append(
                    f"independence at h = {h} is untestable: origin_step "
                    f"{scfg.origin_step} < h, registered as such (P2-4)"
                )
            else:
                ip = float(ip)
                checks.append(
                    Check("independence", h, lv, not (ip < a), f"p = {ip:.4f} against alpha {a}")
                )
                if kp < a and ip < a:
                    notes.append(
                        f"h = {h}, {int(lv * 100)}%: Kupiec and independence both reject, "
                        f"so per the registered reading rule this is clustering rather "
                        f"than a wrong average rate (P2-3). It is still a failure."
                    )
        ratio_ok = True
        detail = "reference missing"
        if (reference, h) in idx.index:
            ref = float(idx.loc[(reference, h)]["crps"])
            got = float(row["crps"])
            ratio = got / ref if ref else np.inf
            ratio_ok = ratio < float(th["crps_ratio_below"])
            detail = f"{ratio:.4f} of {reference}, must be below {th['crps_ratio_below']}"
        else:
            ratio_ok = False
        checks.append(Check("crps", h, 0.0, ratio_ok, detail))

    return P2Decision(
        series=scfg.id,
        model=model,
        go=all(c.passed for c in checks) and bool(checks),
        checks=tuple(checks),
        notes=tuple(dict.fromkeys(notes)),
    )
