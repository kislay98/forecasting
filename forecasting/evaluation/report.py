"""The Phase 1 report: RQ1 to RQ6 verdicts, tables and three plots per series, markdown.

build_report turns a store frame and its manifest into a Report; write_report puts
report.md and its PNGs in a directory. Nothing here fits a model: every number comes
from scoring.score, which reads the store (spec: evaluation.report must not refit).

Choices the report makes (DECISIONS.md, M6):
- Verdicts use the primary window: expanding when it was run, else the only window.
  The other window is summarised and answers RQ5.
- When a question needs one model (h*, calibration, residuals), the report speaks for
  the dev-chosen best candidate (selection.select_best_candidate), never a model picked
  on test results. Calibration uses the best candidate with intervals.
- The Phase 1 exit decision is provisional unless gate.yaml (P1) was committed before
  the run and is unchanged since; then the report issues the A10 gate decision with the
  gate's thresholds (M8).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from matplotlib.figure import Figure
from matplotlib.ticker import MaxNLocator

from forecasting.backtest.selection import is_baseline, select_best_candidate
from forecasting.backtest.store import ForecastStore
from forecasting.evaluation.metrics import BUCKET_NAMES
from forecasting.evaluation.scoring import Scores, SeriesLabels, pairwise, score, series_labels
from forecasting.evaluation.tests import MIN_N_EFF_DM
from forecasting.gate import Gate, current_sha, load_gate

ALPHA = 0.05  # significance for RQ1, RQ3 (Kupiec) and RQ5
LEAKAGE_GAIN = 0.30  # exit table: gains above 30% over ETS call for a leakage audit
INFORMATIVE_N = 10  # a coverage band needs floor(n / h) >= 10 trials to say anything
SHARE_YES = 0.5  # RQ4: most test origins reject
SHARE_NO = 0.10  # RQ4: about what 5% tests on overlapping slices give under the null
BLOCK_SHARE = 1 / 3  # skill CIs with block length above n / 3 are flagged unreliable
SUBPERIOD_MIN = 3  # research 8.5 criterion 5: holds in at least 3 of 4 sub-periods

# Chart tokens: the dataviz reference palette, light surface (validated, 5 slots).
SURFACE, INK, INK_2, MUTED, GRID, AXIS = (
    "#fcfcfb",
    "#0b0b0b",
    "#52514e",
    "#898781",
    "#e1e0d9",
    "#c3c2b7",
)
SLOTS = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948")
# Colour follows the model, never its rank: a fixed map, unknown names take later slots.
MODEL_SLOT = {"combination": 0, "ar": 0, "ets": 1, "sarima": 2, "theta": 3, "ses": 4}
BASELINE_MARKER = {
    "naive": "o",
    "seasonal_naive": "s",
    "drift": "^",
    "sma": "D",
    "zero_return": "o",
    "mean_return": "s",
    "last_return": "^",
}
DPI = 144  # 1 pt = 2 px: lines 1 pt (2 px), markers 4 pt (8 px)


@dataclass(frozen=True)
class Verdict:
    rq: str
    answer: str  # short: yes, no, partly, mixed, not answerable, ...
    text: str


@dataclass
class SeriesReport:
    uid: str
    window: str
    reference: str | None
    candidate: str | None
    verdicts: list[Verdict]
    exit: Verdict
    markdown: str
    figures: dict[str, Figure] = field(default_factory=dict)
    h_star: int | None = None
    interval_model: str | None = None
    gate: Verdict | None = None  # A10, only with a valid pre-registration


@dataclass
class Report:
    run_id: str
    markdown: str
    series: list[SeriesReport]

    @property
    def figures(self) -> dict[str, Figure]:
        return {k: v for s in self.series for k, v in s.figures.items()}

    def verdict(self, uid: str, rq: str) -> Verdict:
        s = next(s for s in self.series if s.uid == uid)
        return s.exit if rq == "exit" else next(v for v in s.verdicts if v.rq == rq)


# ---------------------------------------------------------------- formatting


def fmt(x: Any, d: int = 3) -> str:
    if x is None or (isinstance(x, float) and not math.isfinite(x)) or x is pd.NA:
        return "n/a"
    if isinstance(x, bool | np.bool_):
        return "yes" if x else "no"
    if isinstance(x, int | np.integer):
        return str(int(x))
    if isinstance(x, float | np.floating):
        if d == 0:
            return f"{x:.0f}"
        if x == 0 or abs(x) >= 10 ** (-d):
            return f"{x:.{d}f}"
        return np.format_float_positional(float(x), precision=2, fractional=False, trim="-")
    return str(x)


def pval(p: Any) -> str:
    if p is None or p is pd.NA or not math.isfinite(float(p)):
        return "n/a"
    return "< 0.001" if p < 0.001 else f"{p:.3f}"


def md_table(df: pd.DataFrame, formats: dict[str, Any] | None = None) -> str:
    """GitHub markdown table; formats maps a column to a digit count or a callable."""
    if df is None or df.empty:
        return "_no rows_"
    formats = formats or {}
    cols = []
    for c in df.columns:
        f = formats.get(c, 3)
        cols.append([f(v) if callable(f) else fmt(v, f) for v in df[c].tolist()])
    head = "| " + " | ".join(map(str, df.columns)) + " |"
    rule = "|" + "|".join("---" for _ in df.columns) + "|"
    rows = ["| " + " | ".join(cells) + " |" for cells in zip(*cols, strict=True)]
    return "\n".join([head, rule, *rows])


def hs_text(hs) -> str:
    return ", ".join(str(int(h)) for h in hs) if len(hs) else "none"


# ---------------------------------------------------------------- selections


def primary_window(windows) -> str:
    return "expanding" if "expanding" in windows else next(iter(windows))


def candidate_choice(manifest: dict, frame: pd.DataFrame) -> dict:
    sel = manifest.get("selections", {}).get("best_candidate")
    return sel if sel is not None else select_best_candidate(frame)


# ---------------------------------------------------------------- verdicts


def rq1(point: pd.DataFrame, lab: SeriesLabels, ref: str | None) -> tuple[Verdict, dict]:
    """Does any candidate beat the best baseline, significantly, at the test horizons?

    A candidate passes at h when its relative MAE is below 1 and its Holm-adjusted
    one-sided DM-HLN p-value is below 0.05. Returns the verdict and, per candidate, the
    test horizons where it passes.
    """
    th = list(lab.test_horizons)
    cand = point[~point["model"].map(is_baseline)]
    if ref is None or cand.empty:
        why = "no reference baseline" if ref is None else "no candidate models in this run"
        return Verdict("RQ1", "not tested", f"Not tested: {why}."), {}
    t = cand[cand["h"].isin(th)]
    ok = (t["rel_mae"] < 1) & (t["dm_p_better_holm"] < ALPHA)
    passes = {m: sorted(int(h) for h in g.loc[ok[g.index], "h"]) for m, g in t.groupby("model")}
    no_test = t.groupby("h")["dm_p_better"].apply(lambda x: x.isna().all())
    untestable = sorted(int(h) for h in no_test[no_test].index)
    testable = [h for h in th if h not in untestable]
    note = (
        f" Untestable at h = {hs_text(untestable)}: fewer than {MIN_N_EFF_DM} effective "
        "origins (n / h), where DM-HLN is oversized."
        if untestable
        else ""
    )
    full = [m for m, hs in passes.items() if hs == testable and testable]
    some = {m: hs for m, hs in passes.items() if hs}
    if full:
        best = min(full, key=lambda m: t.loc[t["model"] == m, "rel_mae"].mean())
        rel = t[t["model"] == best].set_index("h")["rel_mae"]
        text = (
            f"Yes. {', '.join(sorted(full))} beat {ref} at every testable horizon "
            f"({hs_text(testable)}), Holm-adjusted DM-HLN p < {ALPHA}. Relative MAE of {best}: "
            + ", ".join(f"{fmt(rel[h], 2)} at h = {h}" for h in th)
            + "."
            + note
        )
        return Verdict("RQ1", "yes", text), passes
    if some:
        parts = "; ".join(f"{m} at h = {hs_text(hs)}" for m, hs in sorted(some.items()))
        return (
            Verdict(
                "RQ1",
                "partly",
                f"Partly. Significant wins over {ref} only at some test horizons: {parts}." + note,
            ),
            passes,
        )
    if not testable:
        return Verdict("RQ1", "not testable", f"Not testable: {note.strip()}"), passes
    return (
        Verdict(
            "RQ1",
            "no",
            f"No. No candidate beats {ref} significantly at any testable horizon "
            f"({hs_text(testable)}) after Holm correction." + note,
        ),
        passes,
    )


def gate_state(manifest: dict[str, Any]) -> tuple[Gate | None, str]:
    """(gate, why) for a manifest: the loaded gate when the run can carry a gate decision,
    else None and the reason. P1: gate.yaml must have been committed before the run and
    must be unchanged since (its sha256 is recorded in the manifest)."""
    g = manifest.get("gate", {})
    if not g.get("present"):
        return None, "no gate.yaml: exploratory run, not a gate decision"
    if not g.get("committed"):
        return None, "gate.yaml had uncommitted changes at run time (P1): no gate decision"
    now = current_sha(g["path"])
    if now is None:
        return None, "gate.yaml is missing now: no gate decision"
    if now != g.get("sha256"):
        return None, "gate.yaml changed after this run (P1): no gate decision; rerun"
    try:
        return load_gate(
            g["path"]
        ), f"gate.yaml {g['sha256'][:12]}, commit {g.get('commit', '')[:12]}"
    except Exception as e:
        return None, f"gate.yaml does not load: {e}"


def exit_decision(
    point: pd.DataFrame,
    lab: SeriesLabels,
    v1: Verdict,
    h_star: int | None,
    gate: dict,
    leak_gain: float = LEAKAGE_GAIN,
    status: str | None = None,
    label: str = "Provisional",
) -> Verdict:
    """The Phase 1 exit table (spec), applied to the primary window's test horizons."""
    if status is None:
        status = (
            f"gate.yaml present (sha256 {gate.get('sha256', '')[:12]})"
            if gate.get("present")
            else "no gate.yaml: exploratory run, not a gate decision"
        )
    th = list(lab.test_horizons)
    t = point[point["h"].isin(th)]
    cand = t[~t["model"].map(is_baseline)]
    if "ets" in set(t["model"]):
        base, base_name = t[t["model"] == "ets"].set_index("h")["mae"], "ETS"
        others = cand[cand["model"] != "ets"]
    elif lab.target == "returns":
        base_name = "the reference"
        ref = t[t["reference"] == t["model"]]
        base = ref.set_index("h")["mae"]
        others = cand
    else:
        base, others, base_name = None, cand.iloc[0:0], ""
    if base is not None and len(others):
        ratio = others["mae"].to_numpy() / base.reindex(others["h"]).to_numpy()
        if np.nanmin(ratio) < 1 - leak_gain:
            i = int(np.nanargmin(ratio))
            row = others.iloc[i]
            return Verdict(
                "exit",
                "leakage audit",
                f"Leakage audit before anything else: {row['model']} is "
                f"{fmt(100 * (1 - ratio[i]), 0)}% better than {base_name} at h = {int(row['h'])} "
                f"(threshold {fmt(100 * leak_gain, 0)}%). {label} ({status}).",
            )
    if v1.answer == "yes":
        return Verdict("exit", "proceed", f"Proceed to Phase 2. {label} ({status}).")
    if v1.answer == "partly":
        hs = f"h <= h* = {h_star} (RQ2)" if h_star is not None else "the horizons where it wins"
        return Verdict(
            "exit",
            "proceed, restricted",
            f"Proceed, but restrict Phase 2 to {hs}; longer horizons become scenario "
            f"outputs. RQ1 finds significant wins only at some test horizons, so treat the "
            f"others with caution. {label} ({status}).",
        )
    if v1.answer in ("no", "not testable"):
        return Verdict(
            "exit",
            "stop modelling",
            "Stop modelling: ship naive plus empirical error quantiles (Phase 2 lite) and look "
            f"for covariates or a panel before further work. {label} ({status}).",
        )
    return Verdict("exit", "no decision", f"No decision: RQ1 was not tested ({status}).")


def rq2(
    skill: pd.DataFrame, hstar: pd.DataFrame, model: str | None, H: int
) -> tuple[Verdict, int | None]:
    if model is None or skill.empty or model not in set(hstar["model"]):
        return Verdict("RQ2", "not answerable", "No candidate model with a skill curve."), None
    hs = int(hstar.set_index("model").loc[model, "h_star"])
    sk = skill[skill["model"] == model].set_index("h")
    first = sk.iloc[0]
    tail = (
        f" Horizons {hs + 1} to {H} are not forecastable by these models."
        if hs < H
        else " Every horizon up to H is forecastable."
    )
    text = (
        f"h* = {hs} of {H} for {model} (dev-chosen). Skill at h = 1: {fmt(first['skill'], 2)} "
        f"[{fmt(first['lo'], 2)}, {fmt(first['hi'], 2)}]." + tail
    )
    return Verdict("RQ2", f"h* = {hs}", text), hs


def rq3(ib: pd.DataFrame, model: str | None) -> Verdict:
    if model is None or ib.empty or model not in set(ib["model"]):
        return Verdict("RQ3", "not answerable", "No model with analytic intervals.")
    b = ib[ib["model"] == model]
    info = b[np.floor(b["n_eff"]) >= INFORMATIVE_N]
    if info.empty:
        return Verdict(
            "RQ3",
            "not answerable",
            f"Not answerable: every bucket has fewer than {INFORMATIVE_N} effective trials, "
            f"so the binomial bands cannot reject anything ({model}).",
        )
    under = info[
        (info["coverage"] < info["band_lo"])
        | ((info["kupiec_p"] < ALPHA) & (info["coverage"] < info["level"]))
    ]
    over = info[
        (info["coverage"] > info["band_hi"])
        | ((info["kupiec_p"] < ALPHA) & (info["coverage"] > info["level"]))
    ]
    skipped = [k for k in BUCKET_NAMES if k in set(b["bucket"]) - set(info["bucket"])]
    note = (
        f" Too few effective trials to judge the {' and '.join(skipped)} "
        f"bucket{'s' if len(skipped) > 1 else ''}."
        if skipped
        else ""
    )

    def where(df):
        return "; ".join(
            f"{r.bucket} {fmt(100 * r.level, 0)}% ({fmt(100 * r.coverage, 0)}%)"
            for r in df.itertuples()
        )

    if under.empty and over.empty:
        return Verdict(
            "RQ3",
            "calibrated",
            f"Calibrated where testable: the 80% and 95% intervals of {model} are inside the "
            f"binomial band and Kupiec does not reject at 5%.{note}",
        )
    parts = []
    if len(under):
        parts.append(f"under-covers in {where(under)}")
    if len(over):
        parts.append(f"over-covers in {where(over)}")
    answer = (
        "under-covers"
        if len(under) and not len(over)
        else "over-covers"
        if len(over) and not len(under)
        else "miscalibrated"
    )
    return Verdict("RQ3", answer, f"Not calibrated: {model} {', '.join(parts)}.{note}")


def _level(share: float) -> str:
    if not math.isfinite(share):
        return "n/a"
    return "yes" if share > SHARE_YES else "no" if share <= SHARE_NO else "mixed"


def rq4(res: pd.DataFrame, model: str | None, target: str) -> Verdict:
    if model is None or res.empty or model not in set(res["model"]):
        return Verdict("RQ4", "not answerable", "No residual diagnostics in this run.")
    r = res.set_index("model").loc[model]
    ac_share = np.nanmax([r["share_lb"], r.get("share_lb_2m", np.nan)])
    ac, ar = _level(ac_share), _level(r["share_arch"])
    lines = [
        f"{model}: Ljung-Box rejects at {fmt(100 * ac_share, 0)}% of {int(r['n_origins'])} test "
        f"origins, ARCH-LM at {fmt(100 * r['share_arch'], 0)}%."
    ]
    if ac == "yes":
        lines.append("Residuals are autocorrelated: Phase 2 needs a block bootstrap.")
    if ar == "yes":
        lines.append(
            "Volatility clusters: this confirms U6 (GARCH in Phase 2)."
            if target == "returns"
            else "Residuals are heteroskedastic: Phase 2 needs a variance model or scaling."
        )
    answer = f"autocorrelation {ac}, ARCH {ar}"
    return Verdict("RQ4", answer, " ".join(lines))


def rq5(gap: pd.DataFrame, sub: pd.DataFrame, model: str | None, ref: str | None) -> Verdict:
    if gap.empty:
        return Verdict("RQ5", "not answerable", "Only one window was run.")
    g = gap if model is None else gap[gap["model"] == model]
    if g.empty:
        g = gap
    who = model or "any model"
    roll = g[(g["ratio"] < 1) & (g["p_rolling_better_holm"] < ALPHA)]
    expd = g[(g["ratio"] > 1) & (g["p_expanding_better_holm"] < ALPHA)]
    if len(roll):
        answer, text = (
            "rolling wins",
            (
                f"Rolling beats expanding for {who} at h = {hs_text(roll['h'])} (Holm p < {ALPHA}): "
                "evidence of instability, so break handling (Phase 4) is worth considering."
            ),
        )
    elif len(expd):
        answer, text = (
            "stable",
            (
                f"Expanding beats rolling for {who} at h = {hs_text(expd['h'])} (Holm p < {ALPHA}): "
                "long history helps; no sign of a break."
            ),
        )
    else:
        answer, text = (
            "no significant gap",
            (
                f"No significant gap between rolling and expanding windows for {who} at the test "
                "horizons."
            ),
        )
    if model is not None and ref is not None and not sub.empty:
        s = sub[sub["model"] == model]
        if len(s):
            wins = s.assign(win=s["rel_mae"] < 1).groupby("h")["win"].agg(["sum", "count"])
            held = [f"{int(r['sum'])} of {int(r['count'])} at h = {h}" for h, r in wins.iterrows()]
            text += f" Sub-periods where {model} beats {ref}: {'; '.join(held)}."
            if (wins["sum"] >= SUBPERIOD_MIN).all():
                text += " The advantage holds across the test period."
    return Verdict("RQ5", answer, text)


def rq6(tr: pd.DataFrame, mode: str) -> Verdict:
    if tr.empty:
        return Verdict(
            "RQ6",
            "not applicable",
            f"No model in this run uses the variance transform (configured: {mode}).",
        )
    if mode != "auto":
        return Verdict(
            "RQ6",
            f"{mode} (configured)",
            f"Not tested: the config fixes the transform to {mode}; only transform: auto "
            "lets each fold choose.",
        )
    top = tr.sort_values(["n_folds", "transform"], ascending=[False, True]).iloc[0]
    counts = ", ".join(f"{r.transform} {r.n_folds}" for r in tr.itertuples())
    n = int(tr["n_folds"].sum())
    if top["share"] >= 0.9:
        needed = (
            "no transform is needed"
            if top["transform"] == "none"
            else f"{top['transform']} is needed"
        )
        return Verdict(
            "RQ6",
            str(top["transform"]),
            f"{top['transform']} in {top['n_folds']} of {n} test folds (configured: {mode}): "
            f"{needed}. Counts: {counts}.",
        )
    return Verdict(
        "RQ6", "mixed", f"The choice varies across folds (configured: {mode}). Counts: {counts}."
    )


A10_ANSWER = {
    "proceed": "GO",
    "proceed, restricted": "GO, restricted",
    "stop modelling": "NO-GO",
    "leakage audit": "AUDIT FIRST",
    "no decision": "NO DECISION",
}


def gate_criteria(
    gate: Gate,
    ex: Verdict,
    v1: Verdict,
    point: pd.DataFrame,
    ib: pd.DataFrame,
    sub: pd.DataFrame,
    lab: SeriesLabels,
    candidate: str | None,
    interval_model: str | None,
    h_star: int | None,
) -> tuple[Verdict, list[dict]]:
    """A10: the exit table's row, with the research 8.5 criteria the gate registers
    (1 accuracy up to h* and significance at the decision horizons, 3 calibration within
    the registered tolerances, 5 stability across sub-periods) checked for the dev-chosen
    candidate. The decision follows the exit table; the criteria say why."""
    th = gate.thresholds
    rows: list[dict] = []
    # 1 accuracy
    if candidate is None or h_star is None:
        rows.append({"criterion": "1 accuracy", "result": "n/a", "detail": "no candidate"})
    else:
        c = point[(point["model"] == candidate) & (point["h"] <= max(h_star, 1))]
        below = bool((c["rel_mae"] < th["relative_mae_below"]).all()) and h_star >= 1
        rows.append(
            {
                "criterion": "1 accuracy",
                "result": "pass"
                if below and v1.answer == "yes"
                else "partial"
                if below or v1.answer in ("yes", "partly")
                else "fail",
                "detail": f"{candidate}: relative MAE below {th['relative_mae_below']} at every h <= h* = {h_star}: "
                f"{'yes' if below else 'no'}; RQ1 {v1.answer}",
            }
        )
    # 3 calibration
    if interval_model is None or ib.empty or interval_model not in set(ib["model"]):
        rows.append({"criterion": "3 calibration", "result": "n/a", "detail": "no intervals"})
    else:
        b = ib[(ib["model"] == interval_model) & (np.floor(ib["n_eff"]) >= INFORMATIVE_N)]
        if b.empty:
            rows.append(
                {
                    "criterion": "3 calibration",
                    "result": "not judged",
                    "detail": f"no bucket with {INFORMATIVE_N} effective trials",
                }
            )
        else:
            tol = {0.8: th["coverage_80"], 0.95: th["coverage_95"]}
            misses = [
                f"{r.bucket} {fmt(100 * r.level, 0)}%: {fmt(100 * r.coverage, 0)}%"
                for r in b.itertuples()
                if round(r.level, 2) in tol
                and not (tol[round(r.level, 2)][0] <= r.coverage <= tol[round(r.level, 2)][1])
            ]
            rows.append(
                {
                    "criterion": "3 calibration",
                    "result": "pass" if not misses else "fail",
                    "detail": f"{interval_model}: judged buckets inside "
                    f"{th['coverage_80']} (80%) and {th['coverage_95']} (95%)"
                    + (f"; outside: {'; '.join(misses)}" if misses else ""),
                }
            )
    # 5 stability
    if candidate is None or sub.empty or candidate not in set(sub["model"]):
        rows.append({"criterion": "5 stability", "result": "n/a", "detail": "no sub-periods"})
    else:
        w = sub[sub["model"] == candidate].assign(win=lambda d: d["rel_mae"] < 1)
        wins = w.groupby("h")["win"].sum()
        ok = bool((wins >= th["subperiods_min"]).all())
        rows.append(
            {
                "criterion": "5 stability",
                "result": "pass" if ok else "fail",
                "detail": f"{candidate} beats the reference in "
                + "; ".join(
                    f"{int(v)} of {int(w[w['h'] == h].shape[0])} blocks at h = {h}"
                    for h, v in wins.items()
                )
                + f" (needs {th['subperiods_min']})",
            }
        )
    answer = A10_ANSWER.get(ex.answer, ex.answer)
    text = (
        f"{answer}: {ex.text} Criteria: "
        + "; ".join(f"{r['criterion']} {r['result']}" for r in rows)
        + "."
    )
    return Verdict("A10", answer, text), rows


# ---------------------------------------------------------------- plots


def _color(model: str, others: list[str]) -> str:
    if model in MODEL_SLOT:
        return SLOTS[MODEL_SLOT[model]]
    unknown = sorted(m for m in others if m not in MODEL_SLOT)
    return SLOTS[(5 + unknown.index(model)) % len(SLOTS)]


def _style(ax, title: str, xlabel: str, ylabel: str = "") -> None:
    ax.set_facecolor(SURFACE)
    ax.set_title(title, loc="left", color=INK, fontsize=9)
    ax.set_xlabel(xlabel, color=INK_2, fontsize=8.5)
    ax.set_ylabel(ylabel, color=INK_2, fontsize=8.5)
    ax.tick_params(colors=MUTED, labelsize=8, labelcolor=INK_2)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(AXIS)
    ax.grid(True, color=GRID, linewidth=0.5)
    ax.set_axisbelow(True)
    ax.xaxis.set_major_locator(MaxNLocator(integer=True))


def _axes(title: str, xlabel: str, ylabel: str):
    fig = Figure(figsize=(7.5, 3.6), dpi=DPI, facecolor=SURFACE)
    ax = fig.add_subplot()
    _style(ax, title, xlabel, ylabel)
    ax.title.set_fontsize(10)
    return fig, ax


def _legend(ax) -> None:
    leg = ax.legend(fontsize=7.5, frameon=False, loc="upper left", bbox_to_anchor=(1.0, 1.0))
    for t in leg.get_texts():
        t.set_color(INK_2)


def plot_relative_mae(point: pd.DataFrame, uid: str, window: str, ref: str) -> Figure:
    fig, ax = _axes(
        f"{uid}: relative MAE vs {ref} by horizon ({window} window)",
        "horizon h",
        f"MAE / MAE({ref}), log scale",
    )
    p = point[point["model"] != ref]
    names = sorted(p["model"].unique(), key=lambda m: (is_baseline(m), m))
    cands = [m for m in names if not is_baseline(m)]
    for m in names:
        d = p[p["model"] == m].sort_values("h")
        if is_baseline(m):
            ax.plot(
                d["h"],
                d["rel_mae"],
                color=INK_2,
                lw=1.0,
                ls="--",
                zorder=2,
                marker=BASELINE_MARKER.get(m, "x"),
                ms=4,
                label=f"{m} (baseline)",
            )
        else:
            ax.plot(
                d["h"],
                d["rel_mae"],
                color=_color(m, cands),
                lw=1.0,
                marker="o",
                ms=4,
                zorder=3,
                label=m,
            )
    ax.axhline(1.0, color=MUTED, lw=0.8, zorder=1)
    ax.set_yscale("log")
    ax.yaxis.set_major_formatter("{x:g}")
    ax.yaxis.set_minor_formatter("{x:g}")
    ax.tick_params(axis="y", which="both", labelsize=8, labelcolor=INK_2, colors=MUTED)
    _legend(ax)
    fig.tight_layout()
    return fig


def plot_skill(
    skill: pd.DataFrame, uid: str, window: str, model: str, ref: str, hstar: int
) -> Figure:
    fig, ax = _axes(
        f"{uid}: skill of {model} vs {ref}, 95% block-bootstrap CI ({window} window)",
        "horizon h",
        "SS(h) = 1 - relative MAE",
    )
    d = skill[skill["model"] == model].sort_values("h")
    c = _color(model, [model])
    ax.fill_between(d["h"], d["lo"], d["hi"], color=c, alpha=0.18, linewidth=0)
    ax.plot(d["h"], d["skill"], color=c, lw=1.0, marker="o", ms=4)
    ax.axhline(0.0, color=MUTED, lw=0.8)
    if hstar > 0:
        ax.axvline(hstar, color=INK_2, lw=0.8, ls=":")
    ax.annotate(
        f"h* = {hstar}",
        xy=(max(hstar, 1), 1),
        xycoords=("data", "axes fraction"),
        xytext=(4, -12),
        textcoords="offset points",
        color=INK_2,
        fontsize=8,
    )
    fig.tight_layout()
    return fig


def plot_coverage(iv: pd.DataFrame, uid: str, window: str, model: str) -> Figure:
    """One panel per level (small multiples), so the bands never overlap."""
    d = iv[iv["model"] == model]
    levels = sorted(d["level"].unique())
    fig = Figure(figsize=(7.5, 3.4), dpi=DPI, facecolor=SURFACE)
    fig.suptitle(
        f"{uid}: coverage of {model} intervals with the binomial 95% band ({window} window)",
        x=0.01,
        ha="left",
        color=INK,
        fontsize=10,
    )
    axes = fig.subplots(1, max(1, len(levels)), sharey=True, squeeze=False)[0]
    for ax, lv in zip(axes, levels, strict=False):
        _style(ax, f"{fmt(100 * lv, 0)}% interval", "horizon h")
        g = d[d["level"] == lv].sort_values("h")
        ax.fill_between(
            g["h"],
            g["band_lo"],
            g["band_hi"],
            color=SLOTS[0],
            alpha=0.15,
            linewidth=0,
            label="band",
        )
        ax.axhline(lv, color=MUTED, lw=0.8, ls="--")
        ax.plot(g["h"], g["coverage"], color=SLOTS[0], lw=1.0, marker="o", ms=4)
        ax.set_ylim(0, 1.02)
    axes[0].set_ylabel("share of actuals inside", color=INK_2, fontsize=8.5)
    fig.tight_layout()
    return fig


# ---------------------------------------------------------------- sections


def _of(df: pd.DataFrame, uid: str, window: str | None = None) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    m = df["unique_id"] == uid
    if window is not None and "window" in df.columns:
        m &= df["window"] == window
    return df[m]


def _series_report(
    scores: Scores,
    frame: pd.DataFrame,
    manifest: dict,
    lab: SeriesLabels,
    scfg: dict,
    cand_sel: dict,
    gate: Gate | None = None,
    gate_why: str = "",
) -> SeriesReport:
    uid = lab.uid
    plan = manifest.get("plans", {}).get(uid, {})
    windows = plan.get("windows") or sorted(set(_of(scores.point, uid)["window"]))
    w = gate.primary_window if gate and gate.primary_window in windows else primary_window(windows)
    other = [x for x in windows if x != w]
    point = _of(scores.point, uid, w)
    sel = _of(scores.selections, uid, w)
    ref = sel["reference"].iloc[0] if len(sel) else None
    sma_k = sel["sma_k"].iloc[0] if len(sel) else None
    csel = cand_sel.get(uid, {}).get(w, {})
    cand = csel.get("model")
    cand_iv = csel.get("with_intervals")
    res_w = _of(scores.residuals, uid, w)
    ranking = csel.get("ranking", [])
    cand_res = next((m for m in ranking if m in set(res_w.get("model", []))), None)
    res_model = cand_res or (ref if ref in set(res_w.get("model", [])) else None)
    th = list(lab.test_horizons)

    v1, _ = rq1(point, lab, ref)
    v2, hstar = rq2(_of(scores.skill, uid, w), _of(scores.h_star, uid, w), cand, lab.H)
    iv_model = cand_iv or ref
    v3 = rq3(_of(scores.interval_buckets, uid, w), iv_model)
    v4 = rq4(res_w, res_model, lab.target)
    v5 = rq5(_of(scores.window_gap, uid), _of(scores.subperiods, uid, w), cand, ref)
    v6 = rq6(_of(scores.transforms, uid, w), scfg.get("transform", "auto"))
    if gate is not None:
        ex = exit_decision(
            point,
            lab,
            v1,
            hstar,
            manifest.get("gate", {}),
            leak_gain=gate.thresholds["leakage_gain_over_ets"],
            status=gate_why,
            label="Registered",
        )
        a10, _ = gate_criteria(
            gate,
            ex,
            v1,
            point,
            _of(scores.interval_buckets, uid, w),
            _of(scores.subperiods, uid, w),
            lab,
            cand,
            iv_model,
            hstar,
        )
    else:
        ex = exit_decision(point, lab, v1, hstar, manifest.get("gate", {}), status=gate_why or None)
        a10 = None
    verdicts = [v1, v2, v3, v4, v5, v6]

    md: list[str] = [f"## {uid}", ""]
    reduced = plan.get("reduced")
    md.append(
        f"Source `{scfg.get('source', '')}`, {scfg.get('freq', '')}, target {lab.target}, "
        f"m = {lab.m}, H = {lab.H}, test horizons {hs_text(th)}. "
        f"{plan.get('n_test', '?')} test origins (step {plan.get('origin_step', '?')}), "
        f"{plan.get('n_dev', '?')} dev, {plan.get('n_warmup', '?')} warm-up; windows "
        f"{', '.join(windows)}; verdicts use the {w} window."
    )
    if reduced:
        md += [
            "",
            "**Reduced mode: the series is between the hard floor and the minimum, so "
            "every test below is underpowered.**",
        ]
    if lab.target == "returns":
        md += [
            "",
            "Treated as a price index: dividends are not included (Phase 1 uses the "
            "price index, not the total return index). y is the one-period log return at "
            "t + h.",
        ]
    md += [
        "",
        f"Chosen on dev origins only: SMA window k = {fmt(sma_k)}; reference (best baseline) "
        f"{ref}; best candidate {cand or 'none'}"
        + (f" (with intervals: {cand_iv})" if cand_iv and cand_iv != cand else "")
        + ".",
        "",
        "| Question | Answer | Detail |",
        "|---|---|---|",
    ]
    md += [f"| {v.rq} | {v.answer} | {v.text} |" for v in verdicts]
    md += [f"| Phase 1 exit | {ex.answer} | {ex.text} |", ""]

    figures: dict[str, Figure] = {}

    # RQ1
    md += [f"### RQ1: does any model beat {ref}?", "", v1.text, ""]
    t = point[point["h"].isin(th) & (point["model"] != ref)].sort_values(["h", "model"])
    md += [
        "At the test horizons (DM-HLN one-sided: the model is more accurate; Holm over every "
        "candidate and test horizon; MCS at alpha 0.10):",
        "",
        md_table(
            t[
                ["h", "model", "n", "n_eff", "rel_mae", "dm_p_better", "dm_p_better_holm", "mcs_in"]
            ].rename(
                columns={
                    "n_eff": "n/h",
                    "rel_mae": "rel. MAE",
                    "dm_p_better": "DM p",
                    "dm_p_better_holm": "Holm p",
                    "mcs_in": "in MCS",
                }
            ),
            {"n/h": 1, "rel. MAE": 3, "DM p": pval, "Holm p": pval},
        ),
        "",
    ]
    if len(point) and ref is not None:
        wide = point.pivot_table(index="h", columns="model", values="rel_mae")
        mcs = point.pivot_table(index="h", columns="model", values="mcs_in", aggfunc="first")
        n = point.groupby("h")["n"].max()
        tab = pd.DataFrame({"h": wide.index, "n": n.to_numpy(), "n/h": (n / n.index).to_numpy()})
        for m in wide.columns:
            if m == ref:
                continue
            tab[m] = [
                f"{fmt(v, 2)}{'*' if bool(mcs.loc[h, m]) else ''}" for h, v in wide[m].items()
            ]
        md += [
            f"Relative MAE vs {ref} at every h (* = in the MCS at that h):",
            "",
            md_table(tab, {"n/h": 1}),
            "",
        ]
        fname = f"{uid}_relative_mae.png"
        figures[fname] = plot_relative_mae(point, uid, w, ref)
        md += [f"![Relative MAE by horizon for {uid}](figures/{fname})", ""]
    naive = "zero_return" if lab.target == "returns" else "naive"
    if "sma" in set(point["model"]) and naive in set(point["model"]):
        pw = pairwise(frame, manifest, uid, w, "sma", naive)
        pt = pw[pw["h"].isin(th)]
        md += [
            f"Named finding, the original idea: SMA(k = {fmt(sma_k)}) vs {naive}.",
            "",
            md_table(
                pt[["h", "n", "n_eff", "rel_mae", "dm_p", "dm_p_better"]].rename(
                    columns={
                        "n_eff": "n/h",
                        "rel_mae": "rel. MAE",
                        "dm_p": "DM p (two-sided)",
                        "dm_p_better": "DM p (SMA better)",
                    }
                ),
                {"n/h": 1, "DM p (two-sided)": pval, "DM p (SMA better)": pval},
            ),
            "",
        ]
    for ow in other:
        v1o, _ = rq1(
            _of(scores.point, uid, ow), lab, _of(scores.selections, uid, ow)["reference"].iloc[0]
        )
        md += [f"{ow.capitalize()} window: {v1o.text}", ""]

    # RQ2
    sk = _of(scores.skill, uid, w)
    md += ["### RQ2: what is the predictable horizon h*?", "", v2.text, ""]
    if cand is not None and len(sk):
        s = sk[sk["model"] == cand].copy()
        s["reliable"] = s["block"] <= BLOCK_SHARE * s["n_paired"]
        md += [
            f"Skill of {cand} vs {ref} (95% moving-block bootstrap CI, block length h; "
            f"'reliable' is no when the block exceeds n / 3, where the CI can miss the "
            "estimate):",
            "",
            md_table(
                s[["h", "n_paired", "skill", "lo", "hi", "reliable"]].rename(
                    columns={"n_paired": "n", "lo": "CI low", "hi": "CI high"}
                )
            ),
            "",
            "h* per candidate:",
            "",
            md_table(
                _of(scores.h_star, uid, w)[["model", "h_star", "H"]].loc[
                    lambda d: ~d["model"].map(is_baseline)
                ]
            ),
            "",
        ]
        fname = f"{uid}_skill.png"
        figures[fname] = plot_skill(sk, uid, w, cand, ref, hstar)
        md += [f"![Skill curve for {uid}](figures/{fname})", ""]

    # RQ3
    md += ["### RQ3: are the analytic intervals calibrated?", "", v3.text, ""]
    ib = _of(scores.interval_buckets, uid, w)
    if len(ib):
        show = ib[ib["model"].isin({iv_model, ref})].sort_values(["model", "level", "h_first"])
        md += [
            "Coverage per horizon bucket (effective trials n / h for the bucket's last h; the "
            "band is the central 95% range for a calibrated interval):",
            "",
            md_table(
                show[
                    [
                        "model",
                        "bucket",
                        "level",
                        "n",
                        "n_eff",
                        "coverage",
                        "band_lo",
                        "band_hi",
                        "in_band",
                        "kupiec_p",
                        "winkler",
                    ]
                ].rename(
                    columns={
                        "n_eff": "n/h",
                        "band_lo": "band low",
                        "band_hi": "band high",
                        "in_band": "in band",
                        "kupiec_p": "Kupiec p",
                    }
                ),
                {"level": 2, "n/h": 1, "coverage": 3, "Kupiec p": pval, "winkler": 3},
            ),
            "",
        ]
        if iv_model is not None:
            fname = f"{uid}_coverage.png"
            figures[fname] = plot_coverage(_of(scores.intervals, uid, w), uid, w, iv_model)
            md += [f"![Interval coverage by horizon for {uid}](figures/{fname})", ""]

    # RQ4
    lb_lags = f"lags {lab.m} and {2 * lab.m}" if lab.m > 1 else "lag 10"
    md += [
        "### RQ4: are the residuals autocorrelated or heteroskedastic?",
        "",
        v4.text,
        "",
        f"Share of test origins where the test rejects at 5%: Ljung-Box at {lb_lags}, "
        f"ARCH-LM with {max(lab.m, 4)} lags, on each fold's one-step in-sample residuals. "
        "Theta and the combination expose no residuals.",
        "",
    ]
    if len(res_w):
        cols = ["model", "n_origins", "n_resid_median", "share_lb", "share_arch"]
        if lab.m > 1:
            cols.insert(4, "share_lb_2m")
        md += [
            md_table(
                res_w[cols].rename(
                    columns={
                        "n_origins": "origins",
                        "n_resid_median": "median n resid",
                        "share_lb": f"LB lag {lab.m if lab.m > 1 else 10}",
                        "share_lb_2m": f"LB lag {2 * lab.m}",
                        "share_arch": "ARCH-LM",
                    }
                ),
                {"median n resid": 0},
            ),
            "",
        ]

    # RQ5
    md += [
        "### RQ5: is the series stable, or does a rolling window beat expanding?",
        "",
        v5.text,
        "",
    ]
    gap = _of(scores.window_gap, uid)
    if len(gap):
        md += [
            "Rolling vs expanding on the same origins (ratio = MAE rolling / MAE expanding; "
            "Holm over every model and test horizon):",
            "",
            md_table(
                gap[
                    [
                        "model",
                        "h",
                        "n",
                        "n_eff",
                        "ratio",
                        "dm_p",
                        "p_rolling_better_holm",
                        "p_expanding_better_holm",
                    ]
                ].rename(
                    columns={
                        "n_eff": "n/h",
                        "dm_p": "DM p (two-sided)",
                        "p_rolling_better_holm": "Holm p rolling better",
                        "p_expanding_better_holm": "Holm p expanding better",
                    }
                ),
                {
                    "n/h": 1,
                    "DM p (two-sided)": pval,
                    "Holm p rolling better": pval,
                    "Holm p expanding better": pval,
                },
            ),
            "",
        ]
    sub = _of(scores.subperiods, uid, w)
    if cand is not None and len(sub):
        s = sub[sub["model"] == cand]
        md += [
            f"{cand} vs {ref} in four consecutive blocks of test origins (relative MAE):",
            "",
            md_table(s[["h", "block", "first_origin", "last_origin", "n", "rel_mae"]]),
            "",
        ]

    # RQ6
    md += ["### RQ6: is a transform needed, and which?", "", v6.text, ""]
    trs = _of(scores.transforms, uid)
    if len(trs):
        md += [
            md_table(
                trs[["window", "transform", "n_folds", "share", "lambda_min", "lambda_max"]].rename(
                    columns={"n_folds": "test folds"}
                )
            ),
            "",
        ]
    return SeriesReport(
        uid,
        w,
        ref,
        cand,
        verdicts,
        ex,
        "\n".join(md),
        figures,
        h_star=hstar,
        interval_model=iv_model,
        gate=a10,
    )


def build_report(
    frame: pd.DataFrame, manifest: dict[str, Any], scores: Scores | None = None, **score_kw
) -> Report:
    """Every verdict, table and figure for a run. Reads the store and manifest only."""
    scores = scores if scores is not None else score(frame, manifest, **score_kw)
    labels = series_labels(manifest)
    cfg = manifest["config"]
    scfgs = {s["id"]: s for s in cfg["series"]}
    cand_sel = candidate_choice(manifest, frame)
    present = set(scores.point["unique_id"]) if len(scores.point) else set()
    gate_obj, gate_why = gate_state(manifest)
    series = [
        _series_report(
            scores, frame, manifest, labels[uid], scfgs[uid], cand_sel, gate_obj, gate_why
        )
        for uid in labels
        if uid in present
    ]
    gate = manifest.get("gate", {})
    rid = manifest.get("run_id", "")
    head = [
        f"# Phase 1 report: run {rid}",
        "",
        f"Git {manifest.get('git', '?')}, created {manifest.get('created_utc', '?')}, seed "
        f"{cfg.get('seed')}, interval levels {', '.join(fmt(100 * lv, 0) + '%' for lv in cfg.get('levels', []))}.",
        "",
    ]
    if not gate.get("present"):
        head += [
            "**Exploratory run: there is no gate.yaml next to the config. Nothing below is a "
            "gate decision; on real data, commit gate.yaml before studying test origins (P1).**",
            "",
        ]
    head += [
        "Every number comes from the forecast store and the manifest; nothing was refitted. "
        "Only test origins are scored; the SMA window, the reference baseline and the best "
        "candidate were chosen on dev origins. Leakage tests L1, L2, L4 and L5 run in CI on "
        f"every push: check that CI passed for commit {manifest.get('git', '?')[:12]}. "
        "L3 (the random-walk canary) is tests/test_canary.py.",
        "",
        "Limitations: data revisions are not handled (vintages, leakage source 16). "
        "Multi-step errors from overlapping windows are correlated, so n / h is printed next "
        "to every statistic.",
        "",
        "| Series | RQ1 | RQ2 | RQ3 | RQ4 | RQ5 | RQ6 | Phase 1 exit |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for s in series:
        head.append(
            f"| {s.uid} | " + " | ".join(v.answer for v in s.verdicts) + f" | {s.exit.answer} |"
        )
    head += ["", "## Gate decision (A10)", ""]
    if gate_obj is None:
        head += [f"**No gate decision: {gate_why}.**", ""]
    else:
        th = gate_obj.thresholds
        head += [
            f"Pre-registered {gate_obj.registered} ({gate_why}); alpha {gate_obj.alpha}, primary "
            f"window {gate_obj.primary_window}; thresholds: relative MAE below "
            f"{th['relative_mae_below']} up to h*, 80% coverage in {th['coverage_80']}, 95% in "
            f"{th['coverage_95']}, at least {th['subperiods_min']} of 4 sub-periods, leakage "
            f"audit above {fmt(100 * th['leakage_gain_over_ets'], 0)}% over ETS.",
            "",
            "| Series | Decision | Registered expectation | Detail |",
            "|---|---|---|---|",
        ]
        for s in series:
            exp = gate_obj.series.get(s.uid).expectation if s.uid in gate_obj.series else ""
            head.append(f"| {s.uid} | {s.gate.answer} | {exp} | {s.gate.text} |")
        head.append("")
    body = "\n\n".join(s.markdown for s in series)
    return Report(run_id=rid, markdown="\n".join(head) + "\n" + body + "\n", series=series)


def write_report(report: Report, out_dir: str | Path) -> Path:
    """report.md plus figures/*.png in out_dir. Returns the markdown path."""
    out = Path(out_dir)
    (out / "figures").mkdir(parents=True, exist_ok=True)
    for name, fig in report.figures.items():
        fig.savefig(out / "figures" / name, dpi=DPI, facecolor=SURFACE, metadata={"Software": None})
    path = out / "report.md"
    path.write_text(report.markdown)
    return path


def report_run(run_dir: str | Path, **score_kw) -> Path:
    """Build and write the report for a run directory into <run_dir>/report/."""
    run_dir = Path(run_dir)
    frame = ForecastStore.read(run_dir / "forecasts.parquet").frame()
    manifest = json.loads((run_dir / "manifest.json").read_text())
    return write_report(build_report(frame, manifest, **score_kw), run_dir / "report")
