"""gate.yaml: the pre-registration (P1) and the Phase 1 gate rules (spec: Leakage tests,
Phase 1 exit decision; research 8.5).

gate.yaml lives next to the run config and is committed before the first run that scores
test origins. It fixes, per series, the decision horizons and the model list, and for the
run the primary window, the significance level and the thresholds the gate applies. The
manifest records its sha256 and its git commit at run time; the report issues a gate
decision only when the file is unchanged since the run and was committed before it.

Schema (every key required unless noted):

    registered: 2026-09-23            # date, informational
    alpha: 0.05                       # significance for DM-HLN (Holm) and Kupiec
    primary_window: expanding         # the window the verdicts use
    series:
      <id>:
        decision_horizons: [1, 5, 20] # must equal the config's
        models: [...]                 # must equal the config's model list
        expectation: "..."            # optional, informational
    thresholds:
      relative_mae_below: 1.0         # up to h*, vs the dev-chosen best baseline
      coverage_80: [0.75, 0.85]       # per judged bucket
      coverage_95: [0.91, 0.98]
      subperiods_min: 3               # of 4 blocks with relative MAE below 1
      leakage_gain_over_ets: 0.30     # larger gains call for an audit first
"""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from forecasting.config import RunConfig
from forecasting.errors import ConfigError

REQUIRED_TOP = {"registered", "alpha", "primary_window", "series", "thresholds"}
REQUIRED_THRESHOLDS = {
    "relative_mae_below",
    "coverage_80",
    "coverage_95",
    "subperiods_min",
    "leakage_gain_over_ets",
}

# Phases 2 and up ask a calibration question, so they register different thresholds.
# `phase` is optional and defaults to 1, which keeps every Phase 1 gate loading as before.
REQUIRED_TOP_P2 = REQUIRED_TOP | {"phase"}
REQUIRED_THRESHOLDS_P2 = {
    "coverage_80",
    "coverage_95",
    "per_test_alpha",
    "crps_ratio_below",
}
# Phase 4 adds the one parameter the conformal layer has: how many recent eligible
# origins calibrate the width correction.
REQUIRED_THRESHOLDS_P4 = REQUIRED_THRESHOLDS_P2 | {"conformal_window"}
PHASES = (1, 2, 3, 4)


@dataclass(frozen=True)
class SeriesGate:
    decision_horizons: tuple[int, ...]
    models: tuple[str, ...]
    expectation: str = ""
    # Phase 2 only. The one model the gate bets on, and the dev-set rule that picked it.
    # The rule is recorded so the next study inherits the method; the bet is the model
    # name, so it cannot be reinterpreted after the test origins are scored.
    primary_model: str = ""
    chosen_by: str = ""


@dataclass(frozen=True)
class Gate:
    phase: int
    registered: str
    alpha: float
    primary_window: str
    series: dict[str, SeriesGate]
    thresholds: dict[str, Any]
    sha256: str
    path: Path


def gate_path(cfg: RunConfig) -> Path:
    return cfg.base_dir / "gate.yaml"


def _bad(key: str, msg: str) -> ConfigError:
    return ConfigError(f"gate.yaml: {key}", msg)


def load_gate(path: str | Path) -> Gate:
    path = Path(path)
    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, dict):
        raise _bad("", "not a mapping")
    phase = raw.get("phase", 1)
    if phase not in PHASES:
        raise _bad("phase", f"must be one of {list(PHASES)}")
    required_top = REQUIRED_TOP_P2 if phase >= 2 else REQUIRED_TOP
    missing = required_top - set(raw)
    extra = set(raw) - required_top
    if missing or extra:
        raise _bad("", f"missing keys {sorted(missing)}, unknown keys {sorted(extra)}")
    alpha = raw["alpha"]
    if not isinstance(alpha, int | float) or not 0 < alpha < 0.5:
        raise _bad("alpha", "must be a number in (0, 0.5)")
    if raw["primary_window"] not in ("expanding", "rolling"):
        raise _bad("primary_window", "must be expanding or rolling")
    th = raw["thresholds"]
    if phase >= 4:
        required_th = REQUIRED_THRESHOLDS_P4
    elif phase >= 2:
        required_th = REQUIRED_THRESHOLDS_P2
    else:
        required_th = REQUIRED_THRESHOLDS
    if not isinstance(th, dict) or set(th) != required_th:
        raise _bad("thresholds", f"keys must be exactly {sorted(required_th)}")
    if phase >= 4:
        k = th["conformal_window"]
        if not isinstance(k, int) or isinstance(k, bool) or k < 20:
            raise _bad(
                "thresholds.conformal_window",
                "must be an integer of at least 20; below that the empirical quantile "
                "of the conformity scores is a couple of points",
            )
    if phase >= 2:
        pta = th["per_test_alpha"]
        if not isinstance(pta, int | float) or not 0 < pta <= alpha:
            raise _bad(
                "thresholds.per_test_alpha",
                "must be in (0, alpha]. Passing a calibration gate means failing to "
                "reject, so requiring several tests to pass is a conjunction: the "
                "per-test level has to be tightened below the family rate, not loosened",
            )
    for key in ("coverage_80", "coverage_95"):
        band = th[key]
        if not (isinstance(band, list) and len(band) == 2 and 0 < band[0] < band[1] < 1):
            raise _bad(f"thresholds.{key}", "must be [low, high] inside (0, 1)")
    series: dict[str, SeriesGate] = {}
    if not isinstance(raw["series"], dict) or not raw["series"]:
        raise _bad("series", "must map series ids to their gate")
    for uid, s in raw["series"].items():
        if not isinstance(s, dict) or not {"decision_horizons", "models"} <= set(s):
            raise _bad(f"series.{uid}", "needs decision_horizons and models")
        allowed_keys = {"decision_horizons", "models", "expectation"}
        if phase >= 2:
            allowed_keys |= {"primary_model", "chosen_by"}
        if set(s) - allowed_keys:
            raise _bad(f"series.{uid}", f"unknown key; allowed: {sorted(allowed_keys)}")
        if phase >= 2 and "primary_model" not in s:
            raise _bad(
                f"series.{uid}.primary_model", "a phase 2 gate must name the model it bets on"
            )
        series[uid] = SeriesGate(
            decision_horizons=tuple(int(h) for h in s["decision_horizons"]),
            models=tuple(str(m) for m in s["models"]),
            expectation=str(s.get("expectation", "")),
            primary_model=str(s.get("primary_model", "")),
            chosen_by=str(s.get("chosen_by", "")),
        )
    return Gate(
        phase=int(phase),
        registered=str(raw["registered"]),
        alpha=float(alpha),
        primary_window=str(raw["primary_window"]),
        series=series,
        thresholds=dict(th),
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        path=path,
    )


def check_gate(gate: Gate, cfg: RunConfig) -> None:
    """The gate must describe exactly the run: same series, decision horizons, models,
    and a primary window the run includes. Raises ConfigError otherwise."""
    ids = {s.id for s in cfg.series}
    if set(gate.series) != ids:
        raise _bad("series", f"gate lists {sorted(gate.series)}, config has {sorted(ids)}")
    for scfg in cfg.series:
        g = gate.series[scfg.id]
        if g.decision_horizons != tuple(scfg.decision_horizons):
            raise _bad(
                f"series.{scfg.id}.decision_horizons",
                f"{list(g.decision_horizons)} differs from the config's {list(scfg.decision_horizons)}",
            )
        if g.models != tuple(scfg.models):
            raise _bad(
                f"series.{scfg.id}.models",
                f"{list(g.models)} differs from the config's {list(scfg.models)}",
            )
        if gate.primary_window not in scfg.windows:
            raise _bad("primary_window", f"{gate.primary_window} is not run for {scfg.id}")
        if gate.phase >= 2 and g.primary_model not in g.models:
            raise _bad(
                f"series.{scfg.id}.primary_model",
                f"'{g.primary_model}' is not in the registered model list {list(g.models)}",
            )


def _git(args: list[str], cwd: Path) -> str:
    try:
        return subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def gate_status(cfg: RunConfig) -> dict[str, Any]:
    """What the manifest records about gate.yaml at run time (P1).

    present, sha256, committed (the file is tracked and has no uncommitted changes),
    commit (the last commit that touched it) and, when the file loads, its registered
    date and primary window. A gate that fails to load or to match the config is an
    error: the run must not proceed on a gate that says something else.
    """
    path = gate_path(cfg)
    if not path.exists():
        return {"path": str(path), "present": False}
    gate = load_gate(path)
    check_gate(gate, cfg)
    cwd = path.resolve().parent
    dirty = _git(["status", "--porcelain", "--", path.name], cwd)
    tracked = _git(["ls-files", "--error-unmatch", "--", path.name], cwd) != ""
    commit = _git(["log", "-1", "--format=%H", "--", path.name], cwd) if tracked else ""
    return {
        "path": str(path),
        "present": True,
        "sha256": gate.sha256,
        "committed": bool(tracked and not dirty),
        "commit": commit,
        "registered": gate.registered,
        "primary_window": gate.primary_window,
        "alpha": gate.alpha,
    }


def current_sha(path: str | Path) -> str | None:
    p = Path(path)
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
