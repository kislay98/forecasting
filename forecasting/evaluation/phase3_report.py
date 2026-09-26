"""The Phase 3 deliverable: a holding-period risk report.

This is the first output of the project that is not a verdict. It answers "how bad can
the next month be, and how much of that should you believe", using a distribution whose
calibration has been tested rather than assumed.

Everything it shows is read from a completed run and its gate. Nothing here fits a
model, chooses a threshold, or decides anything the gate has not already fixed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from forecasting.backtest.store import ForecastStore, level_tag
from forecasting.config import RunConfig
from forecasting.evaluation.conformal import conformalise
from forecasting.evaluation.phase2 import calibration_table, gate_decision
from forecasting.evaluation.risk import risk_table, simple_loss
from forecasting.gate import Gate

FAN_LEVELS = (0.5, 0.8, 0.95)


def _fmt(v: Any, nd: int = 4) -> str:
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return "not tested"
    if isinstance(v, bool):
        return "yes" if v else "NO"
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return str(v)


def _table(df: pd.DataFrame, cols: list[str], nd: int = 4) -> str:
    head = "| " + " | ".join(cols) + " |"
    rule = "|" + "|".join(["---"] * len(cols)) + "|"
    body = ["| " + " | ".join(_fmt(r[c], nd) for c in cols) + " |" for _, r in df[cols].iterrows()]
    return "\n".join([head, rule, *body])


def fan_chart(frame: pd.DataFrame, model: str, window: str, out: Path) -> Path | None:
    """The cumulative loss distribution from the most recent origin, as a fan.

    Drawn in loss terms rather than log returns, because that is the unit the number
    gets used in, and the two differ by enough in the tail to matter.
    """
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    g = frame[(frame["model"] == model) & (frame["window"] == window) & (frame["status"] == "ok")]
    if g.empty:
        return None
    last = g[g["origin_t"] == g["origin_t"].max()].sort_values("h")
    if last.empty:
        return None
    h = last["h"].to_numpy()
    fig, ax = plt.subplots(figsize=(7.5, 4.2))
    shades = ["#c8d8e8", "#9fbcd8", "#6f9ac4"]
    for lv, shade in zip(sorted(FAN_LEVELS, reverse=True), shades, strict=False):
        tag = level_tag(lv)
        if f"lo_{tag}" not in last:
            continue
        ax.fill_between(
            h,
            simple_loss(last[f"hi_{tag}"].to_numpy()) * 100,
            simple_loss(last[f"lo_{tag}"].to_numpy()) * 100,
            color=shade,
            label=f"{int(lv * 100)}%",
            linewidth=0,
        )
    ax.plot(h, simple_loss(last["y_pred"].to_numpy()) * 100, color="#1b3a57", lw=1.6, label="mean")
    ax.axhline(0, color="#444", lw=0.8)
    ax.set_xlabel("trading days ahead")
    ax.set_ylabel("loss from today, %")
    ax.set_title(
        f"{model}: distribution of the cumulative loss, origin {last.iloc[0]['origin_period']}"
    )
    ax.legend(loc="upper left", frameon=False, fontsize=8)
    ax.invert_yaxis()  # losses downward reads as losses
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=140)
    plt.close(fig)
    return out


def build(run_dir: Path, cfg: RunConfig, gate: Gate | None, gate_why: str) -> str:
    frame = ForecastStore.read(run_dir / "forecasts.parquet").frame()
    manifest = json.loads((run_dir / "manifest.json").read_text())
    scfg = cfg.series[0]
    window = gate.primary_window if gate else "expanding"

    lines: list[str] = []
    a = lines.append
    horizon = "Holding-period" if scfg.target == "cumulative_returns" else "Single-period"
    a(f"# {horizon} risk: {scfg.id}")
    a("")
    a(
        f"Run `{manifest.get('run_id')}`, git `{str(manifest.get('git'))[:12]}`, "
        f"{window} window, {cfg.n_paths:,} simulated paths per origin."
    )
    a("")
    quantity = (
        "the cumulative move over h trading days, log(p_t+h / p_t)"
        if scfg.target == "cumulative_returns"
        else "the single-period return at t + h, log(p_t+h / p_t+h-1)"
    )
    a(
        f"The quantity forecast is {quantity}, converted to a loss as 1 - exp(r). Phase 1 "
        "established that the mean of this series is not forecastable, so the point "
        "forecast is fixed at zero and everything below is about the spread."
    )
    a("")

    if gate is not None and gate.phase >= 4:
        k = int(gate.thresholds["conformal_window"])
        # Correct test rows only. Their calibration set is past origins, dev and test
        # alike, restricted to those whose h-step outcome had already landed.
        frame = conformalise(frame, cfg.levels, k, window, roles=("test",))
        scored = (frame["origin_role"] == "test") & (frame["window"] == window)
        keep = (frame["origin_role"] != "test") | (frame["conformal_n"] > 0)
        before, after = int(scored.sum()), int((scored & keep).sum())
        frame = frame[keep]
        a(f"## Conformal calibration (P4), window {k}")
        a("")
        a(
            f"Interval widths are corrected from the most recent {k} eligible past "
            f"origins, eligible meaning origins whose h-step outcome had already landed "
            f"by the origin being corrected. The correction is applied to the {window} "
            f"window only, which is the window the decision is read from. Rows from the "
            f"other window are dropped below; they were never scored either way, so the "
            f"row count falls by about half without any evidence being discarded."
        )
        a("")
        if before == after:
            a(
                f"All {after} scored test rows had the full calibration set available, "
                f"so none were excluded."
            )
        else:
            a(
                f"{before - after} of {before} scored test rows are dropped because fewer "
                f"than 20 eligible origins existed yet; they are excluded rather than "
                f"scored uncorrected."
            )
        a("")

    cal = calibration_table(frame, scfg, cfg.levels, window, role="test")
    if gate is not None:
        reference = str(gate.thresholds.get("crps_reference", "zero_return_fhs"))
        d = gate_decision(gate, scfg, cal, reference=reference)
        a(f"## Gate decision: {'GO' if d.go else 'NO-GO'}")
        a("")
        a(f"Primary model `{d.model}`, registered as {gate.registered} ({gate_why}).")
        a("")
        rows = pd.DataFrame(
            [
                {
                    "check": c.name,
                    "h": c.h,
                    "level": f"{int(c.level * 100)}%" if c.level else "-",
                    "result": "pass" if c.passed else "FAIL",
                    "detail": c.detail,
                }
                for c in d.checks
            ]
        )
        a(_table(rows, ["check", "h", "level", "result", "detail"]))
        a("")
        for n in d.notes:
            a(f"- {n}")
        if d.notes:
            a("")
    else:
        a("## No gate decision")
        a("")
        a(f"{gate_why}. The numbers below are description, not a verdict.")
        a("")

    a("## Calibration of the loss distribution")
    a("")
    cols = ["model", "h", "n", "cov_80", "kupiec_p_80", "ind_p_80", "cov_95", "kupiec_p_95", "crps"]
    a(_table(cal[cal["h"].isin(scfg.decision_horizons)], cols))
    a("")

    a("## Value at risk and expected shortfall")
    a("")
    a(
        "Losses are fractions of the position, so 0.05 is a 5% loss. `var_mean_loss` is "
        "the average VaR the model quoted; `es_mean_loss` is the average loss it expected "
        "given a breach. `es_ok` is whether a bootstrap interval on realised minus "
        "predicted covers zero: NO, with a negative bias, means breaches were worse than "
        "the model said. `not tested` means fewer than ten breaches, which is not a pass: "
        "at these sample sizes most tail rows say nothing either way."
    )
    a("")
    risk = risk_table(frame, scfg.origin_step, cfg.levels, window, role="test", seed=cfg.seed)
    risk = risk[risk["h"].isin(scfg.decision_horizons) & risk["var_p"].isin([0.95, 0.975, 0.995])]
    rcols = [
        "model", "h", "var_p", "var_mean_loss", "es_mean_loss", "breaches", "expected",
        "kupiec_p", "es_breaches", "es_bias", "es_ok",
    ]  # fmt: skip
    a(_table(risk, rcols))
    a("")

    model = gate.series[scfg.id].primary_model if gate else "ewma"
    fig = fan_chart(frame, model, window, run_dir / "risk" / "fan.png")
    if fig is not None:
        a("## The distribution from the latest origin")
        a("")
        a(f"![cumulative loss fan chart]({fig.name})")
        a("")

    a("## What this does not tell you")
    a("")
    a(
        "- The distribution is conditional on the volatility state at the origin and on "
        "the assumption that tomorrow's shocks resemble the standardised residuals of the "
        "training window. A shock unlike anything in the sample is outside it."
    )
    a(
        "- Expected shortfall is estimated from simulated paths, so its tail accuracy is "
        "bounded by the number of paths and by the empirical residual sample behind them."
    )
    a(
        "- Coverage is measured over the test origins as a whole. Calibration over a long "
        "sample does not guarantee calibration in any particular month."
    )
    return "\n".join(lines)


def write(run_dir: Path, cfg: RunConfig, gate: Gate | None, gate_why: str) -> Path:
    text = build(run_dir, cfg, gate, gate_why)
    out = run_dir / "risk" / "risk.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    return out
