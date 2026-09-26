"""Command line entry points. No logic here beyond printing (spec: cli).

  forecast validate config.yaml   print a ValidationReport per series
  forecast run config.yaml        validate, backtest, write runs/<run_id>/ and its report
  forecast report RUN_DIR         rebuild runs/<run_id>/report/ from the store; no refits
  forecast report config.yaml     the same, for the run this config and data map to

Exit codes: 0 success, 1 a series failed a data rule, 2 the config, a source or a run
directory could not be read.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from forecasting.config import load_config
from forecasting.errors import AdapterError, ConfigError, SchemaError
from forecasting.evaluation.report import report_run
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
    try:
        result = run(cfg, validated, force=force)
    except ConfigError as e:
        print(f"gate error: {e}", file=out)
        return 2
    man = result.manifest
    if result.existed:
        print(
            f"run {result.run_id} already exists at {result.path} (use --force to redo)", file=out
        )
        if not (result.path / "report" / "report.md").exists():
            return _report(result.path, out)
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
    a4 = man.get("a4")
    if a4 is not None:
        by_window = ", ".join(f"{w} {v:.3%}" for w, v in a4["failed_share_by_window"].items())
        verdict = "within" if a4["passed"] else "OVER"
        print(
            f"A4: {a4['failed_share']:.2%} of rows failed, {verdict} the "
            f"{a4['limit']:.0%} the spec allows ({by_window})",
            file=out,
        )
    print(f"store: {result.path / 'forecasts.parquet'}", file=out)
    print(f"report: {report_run(result.path)}", file=out)
    gate = man["gate"]
    if not gate["present"]:
        print(
            "note: no gate.yaml next to the config. Treat this run as exploratory and do not "
            "study test-origin results on real data before P1 (commit gate.yaml first).",
            file=out,
        )
    elif not gate.get("committed"):
        print(
            "note: gate.yaml has uncommitted changes. P1 needs it committed before the run; "
            "the report will not issue a gate decision for this run.",
            file=out,
        )
    else:
        print(f"gate.yaml: sha256 {gate['sha256'][:12]}, commit {gate['commit'][:12]}", file=out)
    return 0


def _report(run_dir: Path, out) -> int:
    try:
        path = report_run(run_dir)
    except SchemaError as e:
        print(
            f"cannot read {run_dir}: {e}. Written by older code? Redo it: forecast run --force",
            file=out,
        )
        return 2
    print(f"report: {path}", file=out)
    return 0


def cmd_report(target: Path, out=None) -> int:
    """Rebuild the report of a run directory, or of the run a config maps to."""
    out = out or sys.stdout
    if target.is_dir():
        run_dir = target
    else:
        cfg, validated = _load(target, out)
        if validated is None:
            return 2
        if validated.failed:
            _print_validation(validated, out, reports=False)
            return 1
        rid, _ = compute_run_id(cfg, [s for _, s, _ in validated.ok])
        run_dir = cfg.output_path / rid
    if not (run_dir / "manifest.json").exists():
        print(f"no run at {run_dir}: run `forecast run` first", file=out)
        return 2
    return _report(run_dir, out)


def cmd_risk(target: Path, out=None) -> int:
    """Phase 3: write the holding-period risk report for a run."""
    import json

    from forecasting.evaluation import phase3_report
    from forecasting.evaluation.report import gate_state
    from forecasting.gate import gate_path, load_gate

    out = out or sys.stdout
    if target.is_dir():
        run_dir = target
        cfg = load_config(Path(json.loads((run_dir / "manifest.json").read_text())["config"]))
    else:
        cfg, validated = _load(target, out)
        if validated is None:
            return 2
        if validated.failed:
            _print_validation(validated, out, reports=False)
            return 1
        rid, _ = compute_run_id(cfg, [s for _, s, _ in validated.ok])
        run_dir = cfg.output_path / rid
    if not (run_dir / "manifest.json").exists():
        print(f"no run at {run_dir}: run `forecast run` first", file=out)
        return 2
    manifest = json.loads((run_dir / "manifest.json").read_text())
    gate, why = gate_state(manifest)
    if gate is None and manifest.get("gate", {}).get("committed"):
        # gate_state declines a calibration gate on purpose, because the Phase 1 report
        # cannot issue its decision. This report can.
        try:
            g = load_gate(gate_path(cfg))
            if g.phase >= 2 and g.sha256 == manifest["gate"].get("sha256"):
                gate, why = (
                    g,
                    f"gate.yaml {g.sha256[:12]}, commit {manifest['gate'].get('commit', '')[:12]}",
                )
        except Exception as e:
            why = f"{why}; and it does not load now: {e}"
    path = phase3_report.write(run_dir, cfg, gate, why)
    print(f"risk report: {path}", file=out)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="forecast")
    sub = parser.add_subparsers(dest="command", required=True)
    v = sub.add_parser("validate", help="print a ValidationReport per series")
    v.add_argument("config", type=Path)
    r = sub.add_parser("run", help="validate, backtest every series, write the forecast store")
    r.add_argument("config", type=Path)
    r.add_argument("--force", action="store_true", help="redo a run whose directory exists")
    k = sub.add_parser("risk", help="Phase 3: holding-period VaR and expected shortfall")
    k.add_argument("target", type=Path, help="a config path or a run directory")
    p = sub.add_parser("report", help="rebuild a run's report from its store (no refits)")
    p.add_argument("target", type=Path, help="a run directory, or the config of the run")
    args = parser.parse_args(argv)
    if args.command == "validate":
        return cmd_validate(args.config)
    if args.command == "run":
        return cmd_run(args.config, force=args.force)
    if args.command == "report":
        return cmd_report(args.target)
    if args.command == "risk":
        return cmd_risk(args.target)
    return 2  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
