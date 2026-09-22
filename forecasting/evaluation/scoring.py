"""Scoring: tidy per-horizon tables from a forecast store and its manifest (M5).

Reads only what a run already wrote (D5): the store frame and the manifest's config
labels, seed and dev-origin selections. Nothing here fits a model.

Rules (spec: Metrics and statistics; scope update, Metrics row):
- Rows scored: origin_role test, status ok, y_true present. Dev and warm-up rows are
  dropped before anything is computed (test_scoring's L5-style tests).
- "sma" is the dev-chosen sma_k; the other sma_k rows are dropped. The reference for
  relative MAE, DM and skill is the dev-chosen best baseline (manifest selections).
- Point metrics use each model's own rows. Anything that compares two models (relative
  MAE, DM, skill, out-of-sample R^2) uses the origins both have; the MCS uses the
  origins every model has.
- Level series get WAPE, sMAPE and MAPE (MAPE only if every test actual of the series
  is > 0). Return series get MAE and RMSE relative to the zero forecast, directional
  accuracy with Pesaran-Timmermann, and out-of-sample R^2 against mean_return.
- DM-HLN runs at every h; Holm adjusts the one-sided "beats the reference" p-values
  over every (candidate model, test horizon) of a (series, window). Candidates are the
  models that are not baselines. Test horizons are the decision horizons, else the
  last h of each bucket.
- Interval metrics skip the combination and any model without bounds. The effective
  sample at h is n / h: coverage bands and Kupiec use it.
- Fold facts (residual diagnostics, transforms) come from test rows with status ok,
  one row per (series, window, model, origin), whether or not y_true is present.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from forecasting.backtest.selection import is_baseline
from forecasting.backtest.store import ForecastStore, level_tag, levels_from_columns
from forecasting.evaluation import diagnostics as dg
from forecasting.evaluation import metrics as mt
from forecasting.evaluation import tests as st

MCS_ALPHA = 0.10
MCS_REPS = 1000
SKILL_REPS = 2000
SKILL_CONF = 0.95
COVERAGE_CONF = 0.95
NO_INTERVALS = frozenset({"combination"})


@dataclass(frozen=True)
class SeriesLabels:
    uid: str
    m: int
    H: int
    target: str
    decision_horizons: tuple[int, ...]

    @property
    def buckets(self) -> dict[str, tuple[int, ...]]:
        return mt.horizon_buckets(self.m, self.H)

    @property
    def test_horizons(self) -> tuple[int, ...]:
        return mt.test_horizons(self.m, self.H, self.decision_horizons)


@dataclass
class Scores:
    """Tidy tables, one row per (series, window, model, h) unless noted."""

    selections: pd.DataFrame  # per (series, window): sma_k, reference
    point: pd.DataFrame  # point metrics, relative MAE, DM-HLN, Holm, MCS per h
    skill: pd.DataFrame  # SS(h) with bootstrap CI
    h_star: pd.DataFrame  # per (series, window, model)
    mcs_buckets: pd.DataFrame  # MCS per (series, window, bucket, model)
    intervals: pd.DataFrame  # per (series, window, model, h, level)
    interval_buckets: pd.DataFrame  # per (series, window, model, bucket, level)
    residuals: pd.DataFrame  # RQ4: per (series, window, model), share of origins rejecting
    transforms: pd.DataFrame  # RQ6: per (series, window, transform), fold counts
    window_gap: pd.DataFrame  # RQ5: per (series, model, test h), rolling vs expanding
    subperiods: pd.DataFrame  # RQ5: per (series, window, model, test h, block)


def series_labels(manifest: dict[str, Any]) -> dict[str, SeriesLabels]:
    out = {}
    for s in manifest["config"]["series"]:
        out[s["id"]] = SeriesLabels(
            uid=s["id"],
            m=int(s["m"]),
            H=int(s["H"]),
            target=s["target"],
            decision_horizons=tuple(int(h) for h in s.get("decision_horizons") or ()),
        )
    return out


def scorable(frame: pd.DataFrame) -> pd.DataFrame:
    """Test rows with status ok and a present y_true and y_pred. Nothing else is scored."""
    keep = (
        (frame["origin_role"] == "test")
        & (frame["status"] == "ok")
        & ~frame["y_true_missing"].astype(bool)
        & frame["y_true"].notna()
        & frame["y_pred"].notna()
    )
    return frame[keep]


def test_folds(frame: pd.DataFrame) -> pd.DataFrame:
    """One row per (series, window, model, test origin) with status ok: fold facts."""
    keep = (frame["origin_role"] == "test") & (frame["status"] == "ok")
    keys = ["unique_id", "window", "model", "origin_t"]
    return frame[keep].drop_duplicates(keys)


test_folds.__test__ = False  # not a pytest test, despite the name


def _selected(
    rows: pd.DataFrame, sel: dict, uid: str, window: str
) -> tuple[pd.DataFrame, str | None, str | None]:
    """Collapse SMA to the dev-chosen k (renamed 'sma'); return rows, sma model, reference."""
    sma = sel.get("sma", {}).get(uid, {}).get(window, {}).get("model")
    ref = sel.get("best_baseline", {}).get(uid, {}).get(window, {}).get("model")
    is_sma = rows["model"].str.startswith("sma_")
    rows = rows[~is_sma | (rows["model"] == sma)].copy()
    rows.loc[rows["model"] == sma, "model"] = "sma"
    if ref is not None and ref == sma:
        ref = "sma"
    return rows, sma, ref


def score(
    frame: pd.DataFrame,
    manifest: dict[str, Any],
    mcs_reps: int = MCS_REPS,
    skill_reps: int = SKILL_REPS,
    alpha: float = MCS_ALPHA,
) -> Scores:
    labels = series_labels(manifest)
    seed = int(manifest["config"]["seed"])
    sel = manifest.get("selections", {})
    levels = levels_from_columns(frame.columns)
    rows_all = scorable(frame)
    folds_all = test_folds(frame)

    parts: dict[str, list] = {k: [] for k in Scores.__dataclass_fields__}
    collapsed: dict[str, list[pd.DataFrame]] = {}
    for (uid, window), folds in folds_all.groupby(["unique_id", "window"], sort=True):
        folds, _, _ = _selected(folds, sel, uid, window)
        parts["residuals"].append(dg.residual_summary(folds))
        parts["transforms"].append(dg.transform_counts(folds[~folds["model"].map(is_baseline)]))
    for (uid, window), rows in rows_all.groupby(["unique_id", "window"], sort=True):
        lab = labels[uid]
        rows, sma, ref = _selected(rows, sel, uid, window)
        collapsed.setdefault(uid, []).append(rows)
        k = int(sma.split("_", 1)[1]) if sma else None
        parts["selections"].append(
            {"unique_id": uid, "window": window, "sma_model": sma, "sma_k": k, "reference": ref}
        )
        ctx = _Ctx(lab, uid, window, ref, seed, levels, mcs_reps, skill_reps, alpha)
        point, skill = ctx.per_horizon(rows)
        parts["point"].append(point)
        parts["skill"].append(skill)
        parts["h_star"].append(ctx.h_star(skill))
        parts["mcs_buckets"].append(ctx.mcs_buckets(rows))
        ivals, ibuckets = ctx.intervals(rows)
        parts["intervals"].append(ivals)
        parts["interval_buckets"].append(ibuckets)
        sub = dg.subperiods(rows, ref, lab.test_horizons)
        if len(sub):
            parts["subperiods"].append(sub.assign(**ctx.key)[[*ctx.key, *sub.columns]])
    for uid, frames in collapsed.items():
        parts["window_gap"].append(dg.window_gap(pd.concat(frames), labels[uid].test_horizons))

    out = {}
    for name, dfs in parts.items():
        if name == "selections":
            out[name] = pd.DataFrame(dfs)
        else:
            dfs = [d for d in dfs if len(d)]
            out[name] = pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()
    return Scores(**out)


def score_run(run_dir: str | Path, **kw) -> Scores:
    """Score a run directory written by `forecast run` (forecasts.parquet, manifest.json)."""
    run_dir = Path(run_dir)
    frame = ForecastStore.read(run_dir / "forecasts.parquet").frame()
    manifest = json.loads((run_dir / "manifest.json").read_text())
    return score(frame, manifest, **kw)


def _nan_if_error(fn, *args) -> float:
    try:
        return fn(*args)
    except ValueError:
        return float("nan")


@dataclass
class _Ctx:
    lab: SeriesLabels
    uid: str
    window: str
    ref: str | None
    seed: int
    levels: tuple[float, ...]
    mcs_reps: int
    skill_reps: int
    alpha: float

    @property
    def key(self) -> dict[str, str]:
        return {"unique_id": self.uid, "window": self.window}

    # ------------------------------------------------------------ point, DM, MCS, skill
    def per_horizon(self, rows: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
        lab = self.lab
        returns = lab.target == "returns"
        bucket = mt.bucket_of(lab.m, lab.H)
        test_h = set(lab.test_horizons)
        mape_ok = not returns and bool((rows["y_true"] > 0).all())
        recs, skills = [], []
        for h, g in rows.groupby("h", sort=True):
            h = int(h)
            # One (origin x model) array per column: pairing is a mask, not a join.
            wide = g.pivot(
                index="origin_t", columns="model", values=["y_true", "y_pred", "mase_scale"]
            )
            Y, P, S = (wide[c].to_numpy() for c in ("y_true", "y_pred", "mase_scale"))
            names = list(wide["y_pred"].columns)
            col = {name: i for i, name in enumerate(names)}
            have = ~np.isnan(P)
            E = np.abs(Y - P)
            mcs = self._mcs(names, E, h)
            ref = col.get(self.ref) if self.ref else None
            mean = col.get("mean_return")
            for name, i in col.items():
                m = have[:, i]
                y, yp = Y[m, i], P[m, i]
                n = len(y)
                rec = {
                    **self.key,
                    "model": name,
                    "h": h,
                    "bucket": bucket[h],
                    "test_h": h in test_h,
                    "reference": self.ref,
                    "n": n,
                    "n_eff": mt.n_effective(n, h),
                    "mae": mt.mae(y, yp),
                    "rmse": mt.rmse(y, yp),
                    "mase": _nan_if_error(mt.mase, y, yp, S[m, i]),
                    "bias": mt.bias(y, yp),
                    "wape": np.nan if returns else _nan_if_error(mt.wape, y, yp),
                    "smape": np.nan if returns else mt.smape(y, yp),
                    "mape": mt.mape(y, yp) if mape_ok else np.nan,
                }
                if returns:
                    zero = np.zeros_like(y)
                    pt = st.pesaran_timmermann(y, yp)
                    rec |= {
                        "mae_vs_zero": _nan_if_error(mt.relative_mae, y, yp, zero),
                        "rmse_vs_zero": _nan_if_error(mt.relative_rmse, y, yp, zero),
                        "dir_acc": pt.hit_rate,
                        "pt_stat": pt.stat,
                        "pt_p": pt.p_value,
                        "r2_oos": np.nan,
                    }
                    if mean is not None:
                        both = m & have[:, mean]
                        if both.any():
                            rec["r2_oos"] = _nan_if_error(
                                mt.r2_oos, Y[both, i], P[both, i], P[both, mean]
                            )
                rec |= self._versus_reference(name, Y, P, have, i, ref, h, skills)
                rec |= mcs.get(name, {"mcs_p": np.nan, "mcs_in": pd.NA})
                recs.append(rec)
        point = pd.DataFrame(recs)
        if len(point):
            point["dm_p_better_holm"] = self._holm(point)
        return point, pd.DataFrame(skills)

    def _versus_reference(self, name, Y, P, have, i, ref, h, skills) -> dict[str, Any]:
        out = {
            "n_paired": 0,
            "rel_mae": np.nan,
            "dm_stat": np.nan,
            "dm_p": np.nan,
            "dm_p_better": np.nan,
        }
        if ref is None:
            return out
        both = have[:, i] & have[:, ref]
        n = int(both.sum())
        out["n_paired"] = n
        if n == 0:
            return out
        y, yp, yr = Y[both, i], P[both, i], P[both, ref]
        ea, er = np.abs(y - yp), np.abs(y - yr)
        out["rel_mae"] = _nan_if_error(mt.relative_mae, y, yp, yr)
        if name == self.ref:
            return out
        if n > h:
            dm = st.dm_hln(ea, er, h)
            out |= {"dm_stat": dm.stat, "dm_p": dm.p_value, "dm_p_better": dm.p_better}
        if er.sum() > 0:
            rng = st.child_rng(self.seed, "skill", self.uid, self.window, name, h)
            s = st.skill_bootstrap(ea, er, h, rng, reps=self.skill_reps, conf=SKILL_CONF)
            skills.append(
                {
                    **self.key,
                    "model": name,
                    "h": h,
                    "reference": self.ref,
                    "n_paired": n,
                    "block": s.block,
                    "skill": s.skill,
                    "lo": s.lo,
                    "hi": s.hi,
                }
            )
        return out

    def _holm(self, point: pd.DataFrame) -> pd.Series:
        """Holm over every (candidate, test horizon): RQ1's family."""
        candidate = ~point["model"].map(is_baseline)
        fam = point["test_h"] & point["dm_p_better"].notna() & candidate
        adj = pd.Series(np.nan, index=point.index)
        if fam.any():
            adj[fam] = st.holm(point.loc[fam, "dm_p_better"].to_numpy())
        return adj

    def _mcs(self, names: list[str], E: np.ndarray, h: int, tag: object = None) -> dict:
        """MCS on absolute errors E (origin x model) over the origins every model has;
        block length h."""
        if len(names) < 2:
            return {}
        E = E[~np.isnan(E).any(axis=1)]
        if len(E) < 2:
            return {}
        rng = st.child_rng(self.seed, "mcs", self.uid, self.window, tag if tag is not None else h)
        res = st.model_confidence_set(E, alpha=self.alpha, reps=self.mcs_reps, block=h, rng=rng)
        return {
            name: {"mcs_n": len(E), "mcs_p": float(p), "mcs_in": bool(inc)}
            for name, p, inc in zip(names, res.p_values, res.included, strict=True)
        }

    def mcs_buckets(self, rows: pd.DataFrame) -> pd.DataFrame:
        """MCS per bucket (spec): loss per origin = mean absolute error over the bucket's h."""
        recs = []
        rows = rows.assign(ae=(rows["y_true"] - rows["y_pred"]).abs())
        for bname, hs in self.lab.buckets.items():
            b = rows[rows["h"].isin(hs)]
            per = b.pivot_table(
                index="origin_t", columns=["model", "h"], values="ae", aggfunc="first"
            )
            per = per.dropna()  # origins with every h of the bucket for every model
            if per.empty:
                continue
            losses = per.T.groupby(level="model").mean().T
            if losses.shape[1] < 2:
                continue
            res = self._mcs(list(losses.columns), losses.to_numpy(), hs[-1], tag=f"bucket:{bname}")
            for name, r in res.items():
                recs.append(
                    {
                        **self.key,
                        "bucket": bname,
                        "h_first": hs[0],
                        "h_last": hs[-1],
                        "model": name,
                        **r,
                    }
                )
        return pd.DataFrame(recs)

    def h_star(self, skill: pd.DataFrame) -> pd.DataFrame:
        recs = []
        if skill.empty:
            return pd.DataFrame()
        hs_all = np.arange(1, self.lab.H + 1)
        for name, g in skill.groupby("model", sort=True):
            lo = g.set_index("h")["lo"].reindex(hs_all)  # missing h counts as no skill
            recs.append(
                {
                    **self.key,
                    "model": name,
                    "reference": self.ref,
                    "H": self.lab.H,
                    "h_star": st.h_star(hs_all, lo.to_numpy()),
                }
            )
        return pd.DataFrame(recs)

    # ------------------------------------------------------------ intervals
    def intervals(self, rows: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
        bucket = mt.bucket_of(self.lab.m, self.lab.H)
        rows = rows[~rows["model"].isin(NO_INTERVALS)]
        per_h, per_b = [], []
        for lv in self.levels:
            lo_c, hi_c = f"lo_{level_tag(lv)}", f"hi_{level_tag(lv)}"
            r = rows[rows[lo_c].notna() & rows[hi_c].notna()]
            for (name, h), g in r.groupby(["model", "h"], sort=True):
                h = int(h)
                per_h.append(
                    {
                        **self.key,
                        "model": name,
                        "h": h,
                        "bucket": bucket[h],
                        "level": lv,
                        **_interval_stats(g, lo_c, hi_c, lv, n_eff=mt.n_effective(len(g), h)),
                    }
                )
            for name, g in r.groupby("model", sort=True):
                for bname, hs in self.lab.buckets.items():
                    b = g[g["h"].isin(hs)]
                    if b.empty:
                        continue
                    n_eff = mt.n_effective(b["origin_t"].nunique(), hs[-1])
                    per_b.append(
                        {
                            **self.key,
                            "model": name,
                            "bucket": bname,
                            "h_first": hs[0],
                            "h_last": hs[-1],
                            "level": lv,
                            **_interval_stats(b, lo_c, hi_c, lv, n_eff=n_eff),
                        }
                    )
        return pd.DataFrame(per_h), pd.DataFrame(per_b)


def _interval_stats(g: pd.DataFrame, lo_c: str, hi_c: str, level: float, n_eff: float) -> dict:
    """Coverage with its binomial band, Kupiec on the effective sample, Winkler, width.

    Kupiec gets the effective counts: n_eff trials and misses scaled by n_eff / n.
    """
    y, lo, hi = g["y_true"].to_numpy(), g[lo_c].to_numpy(), g[hi_c].to_numpy()
    n = len(y)
    cov = mt.coverage(y, lo, hi)
    band_lo, band_hi = mt.coverage_band(level, n_eff, COVERAGE_CONF)
    misses = n * (1 - cov)
    kp = st.kupiec(misses * n_eff / n, n_eff, 1 - level)
    return {
        "n": n,
        "n_eff": n_eff,
        "coverage": cov,
        "band_lo": band_lo,
        "band_hi": band_hi,
        "in_band": band_lo <= cov <= band_hi,
        "kupiec_lr": kp.lr,
        "kupiec_p": kp.p_value,
        "winkler": mt.winkler(y, lo, hi, level),
        "width": mt.mean_width(lo, hi),
    }


def pairwise(
    frame: pd.DataFrame, manifest: dict[str, Any], uid: str, window: str, model: str, other: str
) -> pd.DataFrame:
    """One named comparison at every h: model vs other on the test origins both have.

    Same rows and SMA collapse as score(). Returns h, n, n_eff, rel_mae (MAE of model /
    MAE of other), and DM-HLN on |e_model| - |e_other| (dm_p two-sided, dm_p_better
    one-sided: model is more accurate). Used for the SMA finding (spec: Model set).
    """
    rows = scorable(frame)
    rows = rows[(rows["unique_id"] == uid) & (rows["window"] == window)]
    rows, _, _ = _selected(rows, manifest.get("selections", {}), uid, window)
    recs = []
    for h, g in rows.groupby("h", sort=True):
        w = {name: m.set_index("origin_t") for name, m in g.groupby("model")}
        if model not in w or other not in w:
            continue
        j = w[model][["y_true", "y_pred"]].join(w[other][["y_pred"]], rsuffix="_o", how="inner")
        n, h = len(j), int(h)
        if n == 0:
            continue
        y = j["y_true"].to_numpy()
        ea, eb = np.abs(y - j["y_pred"].to_numpy()), np.abs(y - j["y_pred_o"].to_numpy())
        rel = float(ea.mean() / eb.mean()) if eb.sum() > 0 else np.nan
        rec = {"h": h, "n": n, "n_eff": n / h, "rel_mae": rel}
        rec |= {"dm_stat": np.nan, "dm_p": np.nan, "dm_p_better": np.nan}
        if n > h:
            dm = st.dm_hln(ea, eb, h)
            rec |= {"dm_stat": dm.stat, "dm_p": dm.p_value, "dm_p_better": dm.p_better}
        recs.append(rec)
    return pd.DataFrame(recs)
