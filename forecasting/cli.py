"""Command line entry points. No logic here beyond printing (spec: cli).

  forecast validate config.yaml   print a ValidationReport per series
  forecast run config.yaml        validate, backtest, write runs/<run_id>/

Exit codes: 0 success, 1 a series failed a data rule, 2 the config or a source
could not be read.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from forecasting.config import load_config
from forecasting.errors import AdapterError, ConfigError
from forecasting.pipeline import compute_run_id, run, validate_all


def _load(config_path: Path, out):
    try:
        cfg = load_config(config_path)
    except ConfigError as e:
        print(f"config error: {e}", file=out)
        return None, None
    try:
        validated = validate_all(cfg)
    except AdapterError as e:
        print(f"source error: {e}", file=out)
        return cfg, None
    return cfg, validated


def _print_validation(validated, out, reports: bool = True) -> None:
    for _scfg, series, report in validated.ok:
        print(f"\n[OK] {series.unique_id}", file=out)
        if reports:
            print(report.format(), file=out)
    for scfg, e in validated.failed:
        print(f"\n[FAIL] {scfg.id}: {type(e).__name__} ({e})", file=out)


def cmd_validate(config_path: Path, out=None) -> int:
    out = out or sys.stdout
    cfg, validated = _load(config_path, out)
    if validated is None:
        return 2
    print(f"config: {config_path}", file=out)
    _print_validation(validated, out)
    print("", file=out)
    if validated.failed:
        n = len(cfg.series)
        print(
            f"{len(validated.failed)} of {n} series failed validation; no run_id issued", file=out
        )
        return 1
    rid, git = compute_run_id(cfg, [s for _, s, _ in validated.ok])
    print(f"run_id: {rid}  (git {git[:12]})", file=out)
    return 0


def cmd_run(config_path: Path, force: bool = False, out=None) -> int:
    out = out or sys.stdout
    cfg, validated = _load(config_path, out)
    if validated is None:
        return 2
    if validated.failed:
        _print_validation(validated, out, reports=False)
        print("\nvalidation failed; nothing was run", file=out)
        return 1
    result = run(cfg, validated, force=force)
    man = result.manifest
    if result.existed:
        print(
            f"run {result.run_id} already exists at {result.path} (use --force to redo)", file=out
        )
        return 0
    print(f"run_id: {result.run_id}  (git {man['git'][:12]})", file=out)
    for uid, plan in man["plans"].items():
        sma = man["selections"]["sma"].get(uid, {})
        ks = ", ".join(f"{w}: k={v['k']}" for w, v in sma.items()) or "n/a"
        print(
            f"  {uid}: {plan['n_origins']} origins ({plan['n_warmup']} warm-up, "
            f"{plan['n_dev']} dev, {plan['n_test']} test), step {plan['origin_step']}, "
            f"windows {'/'.join(plan['windows'])}{', REDUCED' if plan['reduced'] else ''}; "
            f"SMA on dev: {ks}",
            file=out,
        )
    rows = man["rows"]
    print(
        f"rows: {rows['total']} (ok {rows.get('ok', 0)}, failed {rows.get('failed', 0)}, "
        f"skipped {rows.get('skipped', 0)})",
        file=out,
    )
    print(f"store: {result.path / 'forecasts.parquet'}", file=out)
    if not man["gate"]["present"]:
        print(
            "note: no gate.yaml next to the config. Treat this run as exploratory and do not "
            "study test-origin results on real data before P1 (commit gate.yaml first).",
            file=out,
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="forecast")
    sub = parser.add_subparsers(dest="command", required=True)
    v = sub.add_parser("validate", help="print a ValidationReport per series")
    v.add_argument("config", type=Path)
    r = sub.add_parser("run", help="validate, backtest every series, write the forecast store")
    r.add_argument("config", type=Path)
    r.add_argument("--force", action="store_true", help="redo a run whose directory exists")
    args = parser.parse_args(argv)
    if args.command == "validate":
        return cmd_validate(args.config)
    if args.command == "run":
        return cmd_run(args.config, force=args.force)
    return 2  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
