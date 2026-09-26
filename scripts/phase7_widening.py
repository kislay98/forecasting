"""Phase 7 (OP-3): how much wider the intervals get when parameter uncertainty is propagated.

Two measurements, both on the expanding window the decisions are read from.

`paired` refits the point model at each origin of one role and simulates it twice with
the SAME innovation draws (common random numbers): once with the fitted parameters, once
with one parameter vector per path. The only difference between the two path sets is the
parameters, so the width ratio at each origin is the parameter effect with no Monte Carlo
noise from the innovations. The seeds are the engine's own, so the point model's paths are
exactly the ones its store rows were built from, and the twin's parameter draws are exactly
the twin's. This is the mode used on dev origins to register P7, and on test origins for
the split between innovation and parameter uncertainty.

`store` reads a finished run: width ratios from the stored bounds, coverage, and the
registered decisions re-applied with each primary model in turn, both uncorrected (the P3
rule) and with the conformal layer (the P4 rule, which is what the P7 gate registers).

  uv run python scripts/phase7_widening.py paired configs/phase7/phase7.yaml --role dev
  uv run python scripts/phase7_widening.py store configs/phase7/runs/<run_id>
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

from forecasting.backtest.engine import fold_seed
from forecasting.backtest.splits import make_origins
from forecasting.config import load_config
from forecasting.models.parameter_uncertainty import split_parameters
from forecasting.models.registry import REGISTRY
from forecasting.pipeline import validate_all
from forecasting.transforms import Interpolator, LogReturn

PAIRS = {"garch": "garch_pu", "gjr_garch": "gjr_garch_pu"}
HS = (1, 5, 20)
LEVELS = (0.8, 0.95, 0.99)


def _width(paths: np.ndarray, lv: float) -> np.ndarray:
    lo, hi = np.quantile(paths, [(1 - lv) / 2, (1 + lv) / 2], axis=0)
    return hi - lo


def _vol20(draws: np.ndarray, names: list[str], var0: np.ndarray, h: int = 20) -> np.ndarray:
    """sqrt(sum_s E[sigma^2_{t+s} | theta]) per draw: the h-day volatility each parameter
    vector implies, from the analytic mean-reverting forecast (percent units)."""
    omega, alpha, gamma, beta = split_parameters(draws, names)
    rho = alpha + 0.5 * gamma + beta
    lr = omega / (1 - rho)
    tot = np.zeros(len(draws))
    for s in range(h):
        tot += lr + rho**s * (var0 - lr)
    return np.sqrt(tot)


def paired(config: Path, role: str, window: str = "expanding") -> pd.DataFrame:
    cfg = load_config(config)
    validated = validate_all(cfg)
    rows = []
    for _scfg, series, _report in validated.ok:
        scfg = _scfg
        plan = make_origins(len(series.y), scfg)
        values = series.y.to_numpy(dtype=float)
        index = series.y.index
        uid = series.unique_id
        for o in plan.origins:
            if o.role != role:
                continue
            t = o.t
            raw = pd.Series(values[: t + 1], index=index[: t + 1])
            filled = Interpolator().fit(raw).transform(raw)
            z = LogReturn().fit(filled).transform(filled)
            for point_name, pu_name in PAIRS.items():
                if point_name not in scfg.models:
                    continue
                point = REGISTRY[point_name](1)
                point.seed = fold_seed(cfg.seed, uid, window, t, point_name)
                twin = REGISTRY[pu_name](1)
                twin.seed = fold_seed(cfg.seed, uid, window, t, pu_name)
                try:
                    t0 = time.perf_counter()
                    point.fit(z)
                    rng_seed = fold_seed(cfg.seed, uid, window, t, f"{point_name}|paths")
                    a = np.cumsum(
                        point.simulate(scfg.H, cfg.n_paths, np.random.default_rng(rng_seed)), 1
                    )
                    t_point = time.perf_counter() - t0
                    t0 = time.perf_counter()
                    twin.fit(z)
                    b = np.cumsum(
                        twin.simulate(scfg.H, cfg.n_paths, np.random.default_rng(rng_seed)), 1
                    )
                    t_twin = time.perf_counter() - t0
                    draws, names, rate = twin.parameter_draws(cfg.n_paths)
                    var0 = twin._start_per_draw(draws, names)
                except Exception as e:  # a failed fit is a failed row in the run too
                    rows.append({"origin_t": t, "model": point_name, "error": str(e)})
                    continue
                _, al, ga, be = split_parameters(draws, names)
                v20 = _vol20(draws, names, var0)
                row = {
                    "origin_t": t,
                    "period": str(index[t].date()) if hasattr(index[t], "date") else str(index[t]),
                    "model": point_name,
                    "n_train": len(z),
                    "rejection": rate,
                    "persist_point": point.persistence,
                    "persist_draws": float(np.mean(al + 0.5 * ga + be)),
                    "cv_sigma1": float(np.std(np.sqrt(var0)) / np.mean(np.sqrt(var0))),
                    "cv_vol20": float(np.std(v20) / np.mean(v20)),
                    "sec_point": t_point,
                    "sec_twin": t_twin,
                }
                for h in HS:
                    for lv in LEVELS:
                        wa, wb = _width(a[:, h - 1], lv), _width(b[:, h - 1], lv)
                        row[f"ratio_{int(lv * 100)}_h{h}"] = float(wb / wa)
                    row[f"sd_ratio_h{h}"] = float(np.std(b[:, h - 1]) / np.std(a[:, h - 1]))
                rows.append(row)
    return pd.DataFrame(rows)


def summarise_paired(df: pd.DataFrame) -> str:
    out = []
    ok = df[df.get("error").isna()] if "error" in df else df
    for model, g in ok.groupby("model"):
        out.append(f"## {model} vs {PAIRS[model]}: {len(g)} origins")
        out.append("")
        out.append("| h | level | mean ratio | median | 5th pct | 95th pct |")
        out.append("|---|---|---|---|---|---|")
        for h in HS:
            for lv in LEVELS:
                x = g[f"ratio_{int(lv * 100)}_h{h}"]
                out.append(
                    f"| {h} | {int(lv * 100)}% | {x.mean():.4f} | {x.median():.4f} | "
                    f"{x.quantile(0.05):.4f} | {x.quantile(0.95):.4f} |"
                )
        out.append("")
        for h in HS:
            x = g[f"sd_ratio_h{h}"]
            out.append(f"- sd ratio at h = {h}: mean {x.mean():.4f}, median {x.median():.4f}")
        w80 = g["ratio_80_h20"]
        w95 = g["ratio_95_h20"]
        out.append(
            f"- parameter share of h = 20 width (1 - point / with): 80% {1 - (1 / w80).mean():.4f}, "
            f"95% {1 - (1 / w95).mean():.4f}; as a variance share (1 - ratio^-2): "
            f"80% {(1 - w80**-2).mean():.4f}, 95% {(1 - w95**-2).mean():.4f}"
        )
        out.append(
            f"- rejection rate per origin: mean {g.rejection.mean():.4f}, median "
            f"{g.rejection.median():.4f}, max {g.rejection.max():.4f} "
            f"(at {g.loc[g.rejection.idxmax(), 'period']})"
        )
        out.append(
            f"- mean persistence of accepted draws minus the point estimate: "
            f"{(g.persist_draws - g.persist_point).mean():+.5f}"
        )
        out.append(
            f"- parameter uncertainty on the volatility forecast itself (CV across draws): "
            f"sigma_t+1 {g.cv_sigma1.mean():.4f}, 20-day {g.cv_vol20.mean():.4f}"
        )
        out.append(
            f"- seconds per origin, fit plus simulate: point {g.sec_point.mean():.3f}, "
            f"with draws {g.sec_twin.mean():.3f}, multiple {g.sec_twin.sum() / g.sec_point.sum():.2f}x"
        )
        out.append("")
    if "error" in df and df["error"].notna().any():
        out.append(f"Failed fits: {int(df['error'].notna().sum())}")
    return "\n".join(out)


def store(run_dir: Path) -> str:
    from forecasting.backtest.store import ForecastStore
    from forecasting.evaluation.conformal import conformalise
    from forecasting.evaluation.phase2 import calibration_table, gate_decision
    from forecasting.gate import load_gate

    manifest = json.loads((run_dir / "manifest.json").read_text())
    cfg = load_config(Path(manifest["config"]))
    scfg = cfg.series[0]
    gate = load_gate(cfg.base_dir / "gate.yaml")
    window = gate.primary_window
    frame = ForecastStore.read(run_dir / "forecasts.parquet").frame()
    out = [f"# Run {manifest['run_id']} ({scfg.id})", ""]

    test = frame[
        (frame.origin_role == "test") & (frame.window == window) & (frame.status == "ok")
    ].copy()
    out.append("## Width of the stored intervals, with draws over without, test origins")
    out.append("")
    out.append("| pair | h | 50% | 80% | 90% | 95% | 99% |")
    out.append("|---|---|---|---|---|---|---|")
    for pt, tw in PAIRS.items():
        a = test[test.model == pt].set_index(["origin_t", "h"])
        b = test[test.model == tw].set_index(["origin_t", "h"])
        common = a.index.intersection(b.index)
        for h in HS:
            idx = [i for i in common if i[1] == h]
            cells = []
            for tag in ("50", "80", "90", "95", "99"):
                wa = (a.loc[idx, f"hi_{tag}"] - a.loc[idx, f"lo_{tag}"]).to_numpy()
                wb = (b.loc[idx, f"hi_{tag}"] - b.loc[idx, f"lo_{tag}"]).to_numpy()
                cells.append(f"{np.mean(wb / wa):.4f}")
            out.append(f"| {tw} / {pt} | {h} | " + " | ".join(cells) + " |")
    out.append("")
    rej = test[test.model.isin(PAIRS.values())].drop_duplicates(["model", "origin_t"])
    rate = rej.variant.str.extract(r"draws rejected ([0-9.]+)")[0].astype(float)
    for m, r in rate.groupby(rej.model):
        out.append(
            f"- {m}: rejection rate per test origin mean {r.mean():.4f}, median "
            f"{r.median():.4f}, max {r.max():.4f}, above 0.5 at {int((r > 0.5).sum())} origins"
        )
    fs = frame[frame.window == window].drop_duplicates(["model", "origin_t"])
    fs = fs[fs.origin_role != "warmup"].groupby("model").fit_seconds.sum()
    for pt, tw in PAIRS.items():
        if pt in fs and tw in fs:
            out.append(f"- runtime multiple {tw} / {pt}: {fs[tw] / fs[pt]:.2f}x (fit_seconds)")
    fails = frame[frame.status != "ok"].groupby(["window", "model"]).size()
    out.append(f"- failed rows by window and model: {fails.to_dict()}")
    out.append("")

    uncorrected = dataclasses.replace(gate, phase=3)
    k = int(gate.thresholds["conformal_window"])
    corr = conformalise(frame, cfg.levels, k, window, roles=("test",))
    corr = corr[(corr["origin_role"] != "test") | (corr["conformal_n"] > 0)]
    reference = str(gate.thresholds.get("crps_reference", "zero_return_fhs"))
    for label, g, fr in (
        ("uncorrected, the P3 rule", uncorrected, frame),
        ("conformal, the P4 rule (the rule P7 registers)", gate, corr),
    ):
        cal = calibration_table(fr, scfg, cfg.levels, window, role="test")
        out.append(f"## Registered checks, {label}")
        out.append("")
        out.append("| model | h | cov_80 | cov_95 | kupiec_p_80 | ind_p_80 | crps |")
        out.append("|---|---|---|---|---|---|---|")
        sub = cal[
            cal.h.isin(scfg.decision_horizons) & cal.model.isin(list(PAIRS) + list(PAIRS.values()))
        ]
        for _, r in sub.sort_values(["h", "model"]).iterrows():
            out.append(
                f"| {r.model} | {r.h} | {r.cov_80:.3f} | {r.cov_95:.3f} | "
                f"{r.kupiec_p_80:.4f} | {r.ind_p_80 if pd.notna(r.ind_p_80) else float('nan'):.4f} | {r.crps:.5f} |"
            )
        out.append("")
        for primary in [*PAIRS, *PAIRS.values()]:
            series = dict(g.series)
            series[scfg.id] = dataclasses.replace(g.series[scfg.id], primary_model=primary)
            d = gate_decision(dataclasses.replace(g, series=series), scfg, cal, reference=reference)
            fails = [f"{c.name} h={c.h} {c.detail}" for c in d.failures]
            out.append(
                f"- primary {primary}: {'GO' if d.go else 'NO-GO'}, "
                f"{len(d.checks) - len(d.failures)} of {len(d.checks)} checks"
                + (f"; fails: {'; '.join(fails)}" if fails else "")
            )
        out.append("")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("paired")
    a.add_argument("config", type=Path)
    a.add_argument("--role", default="dev", choices=("dev", "test"))
    a.add_argument("--csv", type=Path, default=None)
    s = sub.add_parser("store")
    s.add_argument("run_dir", type=Path)
    args = p.parse_args(argv)
    if args.cmd == "paired":
        df = paired(args.config, args.role)
        if args.csv:
            df.to_csv(args.csv, index=False)
        print(summarise_paired(df))
    else:
        print(store(args.run_dir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
