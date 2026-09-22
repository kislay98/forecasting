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

from forecasting.backtest.engine import run_backtest
from forecasting.backtest.selection import select_sma_k
from forecasting.backtest.store import ForecastStore, content_hash
from forecasting.config import RunConfig, SeriesConfig, git_sha, run_id
from forecasting.data.adapters import Fetcher, load_series
from forecasting.data.validate import Series, ValidationReport, validate
from forecasting.errors import DataValidationError

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
    """P1: gate.yaml next to the config must exist before results on test origins matter."""
    path = cfg.base_dir / "gate.yaml"
    if not path.exists():
        return {"path": str(path), "present": False}
    return {
        "path": str(path),
        "present": True,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


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
        plans[s.unique_id] = {
            "initial_window": plan.initial_window,
            "reduced": plan.reduced,
            "n_origins": len(plan.origins),
            "n_warmup": plan.n_warmup,
            "n_dev": plan.n_dev,
            "n_test": plan.n_test,
            "origin_step": scfg.origin_step,
            "windows": list(scfg.windows),
            "mode": report.mode,
        }

    frame = store.frame()
    store.write(out_dir / "forecasts.parquet")
    counts = frame.groupby("status").size().to_dict()
    manifest = {
        "run_id": rid,
        "git": git,
        "created_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "versions": _versions(),
        "config": json.loads(cfg.to_canonical_json()),
        "data_hashes": {s.unique_id: s.data_hash for s in series},
        "plans": plans,
        "selections": {"sma": select_sma_k(frame)},
        "rows": {"total": len(frame), **{k: int(v) for k, v in counts.items()}},
        "content_hash": content_hash(frame),
        "gate": gate_status(cfg),
        "warnings": sorted({w for _, _, r in validated.ok for w in r.warnings}),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return RunResult(rid, out_dir, False, manifest)
