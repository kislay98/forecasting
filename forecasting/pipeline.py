"""Orchestration shared by the CLI: validate every series, then backtest and write a run.

A run directory is <output_dir>/<run_id>/ with:
  forecasts.parquet   the forecast store (all series)
  manifest.json       config, data hashes, git, versions, origin plans, selections,
                      row counts, content hash, gate.yaml status
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import platform
import shutil
from dataclasses import dataclass, field
from importlib.metadata import version
from pathlib import Path
from typing import Any

import pandas as pd

from forecasting.backtest.engine import run_backtest
from forecasting.backtest.selection import (
    select_best_baseline,
    select_best_candidate,
    select_sma_k,
)
from forecasting.backtest.store import ForecastStore, content_hash
from forecasting.config import RunConfig, SeriesConfig, git_sha, run_id
from forecasting.data.adapters import Fetcher, load_series
from forecasting.data.validate import Series, ValidationReport, validate
from forecasting.errors import DataValidationError
from forecasting.gate import gate_status as gate_status_for

PACKAGE_DIR = Path(__file__).resolve().parent


@dataclass
class Validated:
    ok: list[tuple[SeriesConfig, Series, ValidationReport]] = field(default_factory=list)
    failed: list[tuple[SeriesConfig, DataValidationError]] = field(default_factory=list)


def validate_all(cfg: RunConfig, fetch: Fetcher | None = None) -> Validated:
    """Load and validate every series. AdapterError propagates; rule failures are collected."""
    out = Validated()
    for scfg in cfg.series:
        try:
            df = load_series(scfg, cfg.base_dir, fetch)
            for series, report in validate(df, scfg):
                out.ok.append((scfg, series, report))
        except DataValidationError as e:
            out.failed.append((scfg, e))
    return out


def combined_data_hash(series: list[Series]) -> str:
    parts = sorted(f"{s.unique_id}:{s.data_hash}" for s in series)
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


def compute_run_id(cfg: RunConfig, series: list[Series]) -> tuple[str, str]:
    git = git_sha(PACKAGE_DIR)
    return run_id(cfg, combined_data_hash(series), git), git


def gate_status(cfg: RunConfig) -> dict[str, Any]:
    """P1: what the manifest records about gate.yaml (forecasting/gate.py)."""
    return gate_status_for(cfg)


@dataclass
class RunResult:
    run_id: str
    path: Path
    existed: bool
    manifest: dict[str, Any]


def _versions() -> dict[str, str]:
    pkgs = ["forecasting", "numpy", "pandas", "scipy", "pyarrow", "joblib"]
    return {"python": platform.python_version(), **{p: version(p) for p in pkgs}}


def run(cfg: RunConfig, validated: Validated, force: bool = False) -> RunResult:
    """Backtest every validated series and write the run directory."""
    if validated.failed:
        raise ValueError("cannot run: some series failed validation")
    series = [s for _, s, _ in validated.ok]
    gate = gate_status(cfg)  # P1: a gate that fails to load or match the config stops here
    rid, git = compute_run_id(cfg, series)
    out_dir = cfg.output_path / rid
    manifest_path = out_dir / "manifest.json"
    if manifest_path.exists() and not force:
        return RunResult(rid, out_dir, True, json.loads(manifest_path.read_text()))
    if out_dir.exists():
        shutil.rmtree(out_dir)

    store = ForecastStore(cfg.levels)
    plans: dict[str, Any] = {}
    for scfg, s, report in validated.ok:
        part, plan = run_backtest(s, scfg, cfg, rid)
        store.extend(part)
        plans[s.unique_id] = plan_summary(plan, scfg, report.mode)

    frame = store.frame()
    store.write(out_dir / "forecasts.parquet")
    manifest = build_manifest(
        cfg,
        frame,
        plans,
        run_id=rid,
        git=git,
        data_hashes={s.unique_id: s.data_hash for s in series},
        gate=gate,
        warnings=sorted({w for _, _, r in validated.ok for w in r.warnings}),
    )
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return RunResult(rid, out_dir, False, manifest)


def plan_summary(plan, scfg: SeriesConfig, mode: str = "full") -> dict[str, Any]:
    return {
        "initial_window": plan.initial_window,
        "reduced": plan.reduced,
        "n_origins": len(plan.origins),
        "n_warmup": plan.n_warmup,
        "n_dev": plan.n_dev,
        "n_test": plan.n_test,
        "origin_step": scfg.origin_step,
        "windows": list(scfg.windows),
        "mode": mode,
    }


A4_MAX_FAILED_SHARE = 0.01


def a4_status(frame: pd.DataFrame) -> dict[str, Any]:
    """Acceptance check A4, measured on every real run instead of taken on trust.

    The spec's pass condition has two halves: every (origin, model, h) yields a row or a
    logged, typed failure, and failures stay under 1% of rows. The synthetic acceptance
    test covers the first half. Until this function existed the second half was checked
    by reading the run summary, which is not a check.

    Reported per window as well as overall, because a decision is read from one window
    and a failure in the other cannot reach it. Phase 5b is the case that forced this:
    1.29% of its rows failed, all of them rolling-window GARCH fits at the stationarity
    boundary, with 0.000% on the expanding window the decision came from.
    """
    failed = frame["status"].ne("ok")
    share = float(failed.mean()) if len(frame) else 0.0
    by_window = {str(w): round(float(g.mean()), 5) for w, g in failed.groupby(frame["window"])}
    return {
        "failed_share": round(share, 5),
        "limit": A4_MAX_FAILED_SHARE,
        "passed": share < A4_MAX_FAILED_SHARE,
        "failed_share_by_window": by_window,
    }


def build_manifest(
    cfg: RunConfig,
    frame,
    plans: dict[str, Any],
    run_id: str = "",
    git: str = "",
    data_hashes: dict[str, str] | None = None,
    gate: dict[str, Any] | None = None,
    warnings=(),
) -> dict[str, Any]:
    """The manifest for a store frame: config, plans, dev-origin selections, row counts.

    `forecast run` writes it; the acceptance tests build one for in-memory backtests
    so the report sees exactly what a run directory would carry.
    """
    counts = frame.groupby("status").size().to_dict()
    return {
        "run_id": run_id,
        "git": git,
        "created_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "versions": _versions(),
        "config": json.loads(cfg.to_canonical_json()),
        "data_hashes": data_hashes or {},
        "plans": plans,
        "selections": {
            "sma": select_sma_k(frame),
            "best_baseline": select_best_baseline(frame),
            "best_candidate": select_best_candidate(frame),
        },
        "rows": {"total": len(frame), **{k: int(v) for k, v in counts.items()}},
        "a4": a4_status(frame),
        "content_hash": content_hash(frame),
        "gate": gate if gate is not None else {"present": False},
        "warnings": list(warnings),
    }
