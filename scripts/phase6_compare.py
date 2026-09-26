"""Phase 6: the registered verdict for the block model beside the iid model's, same run.

Usage:
    uv run python scripts/phase6_compare.py CONFIG

    CONFIG  one of the four configs under configs/phase6/

The registered gate decides gjr_garch_block. P6 counts a conclusion as changed where that
verdict differs from the one gjr_garch (iid) gets under the same gate in the same store
(configs/phase6/gate.yaml), so this applies the gate twice: once as registered, and once
with the primary model swapped to gjr_garch as a diagnostic, exactly as P5b computed its
uncorrected verdict. For a phase 4 gate the conformal layer is applied first, as the risk
report does. Nothing is refitted; everything is read from the run's store.

Printed: both verdicts with every failed check, the per-cell comparison at the decision
horizons, each coverage cell's margin in origins, and the CRPS of block over iid with a
paired bootstrap interval over origins.
"""

from __future__ import annotations

import dataclasses
import json
import math
import sys

import numpy as np
import pandas as pd

from forecasting.backtest.store import ForecastStore
from forecasting.config import load_config
from forecasting.evaluation.calibration import crps_grid
from forecasting.evaluation.conformal import conformalise
from forecasting.evaluation.phase2 import calibration_table, gate_decision, quantile_grid
from forecasting.gate import gate_path, load_gate

BLOCK, IID = "gjr_garch_block", "gjr_garch"
BOOT = 5000


def main(argv: list[str]) -> int:
    cfg = load_config(argv[0])
    scfg = cfg.series[0]
    gate = load_gate(gate_path(cfg))
    # The run this gate registered: its manifest records this gate's sha256 as committed.
    # Looked up rather than recomputed, because run_id includes the git state and the tree
    # moves on after the run.
    runs = [
        p.parent
        for p in sorted(cfg.output_path.glob("*/manifest.json"), key=lambda p: p.stat().st_mtime)
        if json.loads(p.read_text()).get("gate", {}).get("sha256") == gate.sha256
        and json.loads(p.read_text()).get("gate", {}).get("committed")
    ]
    if not runs:
        print(f"no run of {argv[0]} registered against this gate.yaml; run it first")
        return 2
    run_dir = runs[-1]
    rid = run_dir.name
    frame = ForecastStore.read(run_dir / "forecasts.parquet").frame()
    window = gate.primary_window
    layer = "uncorrected"
    if gate.phase >= 4:
        k = int(gate.thresholds["conformal_window"])
        frame = conformalise(frame, cfg.levels, k, window, roles=("test",))
        keep = (frame["origin_role"] != "test") | (frame["conformal_n"] > 0)
        frame = frame[keep]
        layer = f"conformal {k}"
    cal = calibration_table(frame, scfg, cfg.levels, window, role="test")
    ref = str(gate.thresholds.get("crps_reference", "zero_return_fhs"))

    print(f"{scfg.id}, {layer}, run {rid}, gate phase {gate.phase} sha256 {gate.sha256[:12]}")
    for model in (BLOCK, IID):
        g = dataclasses.replace(gate.series[scfg.id], primary_model=model)
        gg = dataclasses.replace(gate, series={scfg.id: g})
        d = gate_decision(gg, scfg, cal, reference=ref)
        n_pass = sum(c.passed for c in d.checks)
        tag = "registered" if model == BLOCK else "diagnostic"
        print(f"  {model:16s} ({tag}): {'GO' if d.go else 'NO-GO'}, {n_pass} of {len(d.checks)}")
        for c in d.checks:
            if not c.passed:
                lv = f"{int(c.level * 100)}%" if c.level else "-"
                print(f"      FAIL {c.name} h = {c.h} {lv}: {c.detail}")

    hs = list(scfg.decision_horizons)
    t = cal[cal["h"].isin(hs) & cal["model"].isin([BLOCK, IID])].set_index(["h", "model"])
    rows = []
    for h in hs:
        b, i = t.loc[(h, BLOCK)], t.loc[(h, IID)]
        rows.append(
            {
                "h": h,
                "cov80_iid": i["cov_80"],
                "cov80_block": b["cov_80"],
                "cov95_iid": i["cov_95"],
                "cov95_block": b["cov_95"],
                "ind80_iid": i["ind_p_80"],
                "ind80_block": b["ind_p_80"],
                "ind95_iid": i["ind_p_95"],
                "ind95_block": b["ind_p_95"],
                "crps_block_over_iid": b["crps"] / i["crps"],
            }
        )
    pd.set_option("display.width", 200)
    print(pd.DataFrame(rows).to_string(index=False, float_format=lambda v: f"{v:.4f}"))

    # Margins in origins: covered count, and how many more (+) or fewer (-) covered origins
    # the cell could take before leaving its band. Negative means outside by that many.
    print("\n  coverage margins in origins (covered / n, room to the nearest band edge)")
    th = gate.thresholds
    for h in hs:
        for tag in ("80", "95"):
            lo_b, hi_b = th[f"coverage_{tag}"]
            parts = []
            for model in (IID, BLOCK):
                r = t.loc[(h, model)]
                n = int(r["n"])
                covered = n - int(r[f"misses_{tag}"])
                floor, ceil = math.ceil(lo_b * n - 1e-9), math.floor(hi_b * n + 1e-9)
                room = min(covered - floor, ceil - covered)
                parts.append(f"{model} {covered}/{n} room {room:+d}")
            print(f"    h = {h:2d} {tag}%: " + ", ".join(parts))

    # How solid the CRPS ratio is: a paired bootstrap over origins of the ratio of means,
    # per-origin CRPS on the same quantile grid the gate scores.
    print("\n  CRPS block / iid, paired over origins (95% bootstrap interval, share block lower)")
    rows_in = frame[
        (frame["origin_role"] == "test")
        & (frame["status"] == "ok")
        & (~frame["y_true_missing"])
        & (frame["window"] == window)
    ]
    rng = np.random.default_rng(cfg.seed)
    for h in hs:
        per = {}
        for model in (BLOCK, IID):
            g = rows_in[(rows_in["model"] == model) & (rows_in["h"] == h)].sort_values("origin_t")
            q, taus = quantile_grid(g, cfg.levels)
            y = g["y_true"].to_numpy(dtype=float)
            per[model] = pd.Series(
                [crps_grid(y[i : i + 1], q[i : i + 1], taus) for i in range(len(g))],
                index=g["origin_t"].to_numpy(),
            )
        both = pd.concat(per, axis=1, join="inner")
        b, i = both[BLOCK].to_numpy(), both[IID].to_numpy()
        idx = rng.integers(0, len(b), size=(BOOT, len(b)))
        ratios = b[idx].mean(axis=1) / i[idx].mean(axis=1)
        lo, hi = np.quantile(ratios, [0.025, 0.975])
        print(
            f"    h = {h:2d}: {b.mean() / i.mean():.4f} [{lo:.4f}, {hi:.4f}], "
            f"block lower at {np.mean(b < i):.3f} of {len(b)} origins"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
