"""Diagnostics for RQ4 to RQ6 (spec: Metrics and statistics; A8).

| Function          | Answers                                                       |
| residual_tests    | RQ4, per fold: Ljung-Box at lags m and 2m (10 if m = 1) and    |
|                   | ARCH-LM with max(m, 4) lags on one fold's one-step residuals.  |
|                   | Called by the engine at test origins, while the model exists  |
| residual_summary  | RQ4: share of test origins with p < 0.05, per model            |
| window_gap        | RQ5: rolling vs expanding MAE per model and h, with DM-HLN     |
| subperiods        | RQ5: relative MAE in consecutive blocks of test origins        |
| transform_counts  | RQ6: the variance transform chosen per fold, with counts       |

residual_tests is pure and never raises on degenerate input: a test that cannot run
(too few residuals for its lag, constant residuals) gives NaN. The table functions take
rows that the scoring layer has already filtered (test origins, status ok).
"""

from __future__ import annotations

import math
import warnings

import numpy as np
import pandas as pd
from scipy import stats

from forecasting.evaluation import tests as st

LB_NONSEASONAL_LAG = 10  # fpp3's rule for m = 1
ARCH_MIN_LAGS = 4
REJECT_AT = 0.05
N_SUBPERIODS = 4
DIAG_COLUMNS = ("n_resid", "lb_p", "lb_p_2m", "arch_p")


def residual_lags(m: int) -> tuple[tuple[int, ...], int]:
    """(Ljung-Box lags, ARCH-LM lags) for seasonal period m."""
    if m < 1:
        raise ValueError("m must be >= 1")
    lb = (LB_NONSEASONAL_LAG,) if m == 1 else (m, 2 * m)
    return lb, max(m, ARCH_MIN_LAGS)


def _enough(n: int, lag: int) -> bool:
    """A test with `lag` lags runs only on at least 2 lag + 1 residuals."""
    return n >= 2 * lag + 1


def ljung_box(r: np.ndarray, lag: int) -> float:
    """Ljung-Box p-value: Q = n (n + 2) sum_{k=1}^{lag} rho_k^2 / (n - k) ~ chi2(lag).

    rho_k is the lag-k autocorrelation of the demeaned series (divisor: the full sum of
    squares), as in statsmodels' acorr_ljungbox with model_df = 0.
    """
    n = r.size
    x = r - r.mean()
    den = float(x @ x)
    rho = np.array([float(x[k:] @ x[:-k]) / den for k in range(1, lag + 1)])
    q = n * (n + 2) * float(np.sum(rho**2 / (n - np.arange(1, lag + 1))))
    return float(stats.chi2.sf(q, lag))


def arch_lm(r: np.ndarray, lags: int) -> float:
    """Engle's ARCH-LM p-value: regress r_t^2 on a constant and r_{t-1}^2 .. r_{t-lags}^2;
    LM = (n - lags) R^2 ~ chi2(lags). As statsmodels' het_arch (squares not demeaned)."""
    x = r**2
    n = x.size
    y = x[lags:]
    X = np.column_stack([np.ones(n - lags)] + [x[lags - k : n - k] for k in range(1, lags + 1)])
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    ss_res = float(np.sum((y - X @ beta) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    if ss_tot == 0:
        return math.nan
    return float(stats.chi2.sf((n - lags) * (1 - ss_res / ss_tot), lags))


def residual_tests(resid, m: int) -> dict[str, float]:
    """Ljung-Box and ARCH-LM p-values on one fold's one-step residuals.

    Returns n_resid (finite residuals used), lb_p (lag m, or 10 when m = 1), lb_p_2m
    (lag 2m; NaN when m = 1) and arch_p (ARCH-LM with max(m, 4) lags). Ljung-Box uses
    model_df = 0 for every model. NaN residuals are dropped first.
    """
    r = np.asarray(resid, dtype="float64").ravel()
    r = r[np.isfinite(r)]
    n = int(r.size)
    out = {"n_resid": n, "lb_p": math.nan, "lb_p_2m": math.nan, "arch_p": math.nan}
    if n < 3 or np.ptp(r) == 0:
        return out
    lb_lags, arch_lags = residual_lags(m)
    with warnings.catch_warnings(), np.errstate(all="ignore"):
        warnings.simplefilter("ignore")
        for key, lag in zip(("lb_p", "lb_p_2m"), lb_lags, strict=False):
            if _enough(n, lag):
                out[key] = ljung_box(r, lag)
        if _enough(n, arch_lags) and np.ptp(r**2) > 0:
            out["arch_p"] = arch_lm(r, arch_lags)
    for k in ("lb_p", "lb_p_2m", "arch_p"):
        if not np.isfinite(out[k]):
            out[k] = math.nan
    return out


def _share(p: pd.Series) -> float:
    p = p.dropna()
    return float((p < REJECT_AT).mean()) if len(p) else math.nan


def residual_summary(folds: pd.DataFrame) -> pd.DataFrame:
    """RQ4: per (series, window, model), the share of test origins whose residual test
    rejects at 5%. `folds` has one row per (series, window, model, origin) with the
    store's diagnostic columns; folds without residuals (n_resid 0) are left out."""
    cols = ["unique_id", "window", "model"]
    f = folds[folds["n_resid"] > 0]
    if f.empty:
        return pd.DataFrame(columns=[*cols, "n_origins"])
    g = f.groupby(cols, sort=True)
    return pd.DataFrame(
        {
            "n_origins": g.size(),
            "n_resid_median": g["n_resid"].median(),
            "n_lb": g["lb_p"].count(),
            "share_lb": g["lb_p"].agg(_share),
            "n_lb_2m": g["lb_p_2m"].count(),
            "share_lb_2m": g["lb_p_2m"].agg(_share),
            "n_arch": g["arch_p"].count(),
            "share_arch": g["arch_p"].agg(_share),
            "median_lb_p": g["lb_p"].median(),
            "median_arch_p": g["arch_p"].median(),
        }
    ).reset_index()


def _abs_errors(rows: pd.DataFrame) -> pd.Series:
    return (rows["y_true"] - rows["y_pred"]).abs()


def window_gap(rows: pd.DataFrame, horizons) -> pd.DataFrame:
    """RQ5: rolling vs expanding for each model at each h in `horizons`, on the origins
    both windows have. ratio = MAE(rolling) / MAE(expanding); DM-HLN on
    d = |e_rolling| - |e_expanding|, so p_rolling_better is the one-sided p that the
    rolling window is more accurate. Holm runs over every row of one series."""
    recs = []
    for (uid, model, h), g in rows[rows["h"].isin(list(horizons))].groupby(
        ["unique_id", "model", "h"], sort=True
    ):
        w = {k: v.set_index("origin_t") for k, v in g.groupby("window")}
        if set(w) != {"expanding", "rolling"}:
            continue
        j = pd.concat(
            {k: _abs_errors(w[k]) for k in ("expanding", "rolling")}, axis=1, join="inner"
        )
        n = len(j)
        if n == 0:
            continue
        mae_e, mae_r = float(j["expanding"].mean()), float(j["rolling"].mean())
        rec = {
            "unique_id": uid,
            "model": model,
            "h": int(h),
            "n": n,
            "n_eff": n / int(h),
            "mae_expanding": mae_e,
            "mae_rolling": mae_r,
            "ratio": mae_r / mae_e if mae_e > 0 else math.nan,
            "dm_stat": math.nan,
            "dm_p": math.nan,
            "p_rolling_better": math.nan,
        }
        if st.dm_testable(n, int(h)):
            dm = st.dm_hln(j["rolling"].to_numpy(), j["expanding"].to_numpy(), int(h))
            rec |= {"dm_stat": dm.stat, "dm_p": dm.p_value, "p_rolling_better": dm.p_better}
        recs.append(rec)
    out = pd.DataFrame(recs)
    if out.empty:
        return out
    out["p_rolling_better_holm"] = np.nan
    out["p_expanding_better_holm"] = np.nan
    for _uid, idx in out.groupby("unique_id").groups.items():
        sub = out.loc[idx]
        ok = sub["p_rolling_better"].notna()
        if ok.any():
            p = sub.loc[ok, "p_rolling_better"].to_numpy()
            out.loc[sub.index[ok], "p_rolling_better_holm"] = st.holm(p)
            out.loc[sub.index[ok], "p_expanding_better_holm"] = st.holm(1 - p)
    return out


def subperiods(rows: pd.DataFrame, reference: str, horizons, k: int = N_SUBPERIODS):
    """RQ5: relative MAE vs the reference in k consecutive, near-equal blocks of test
    origins (block 1 is the earliest), per model and h, on origins both models have.
    `rows` holds one (series, window)."""
    recs = []
    if reference is None:
        return pd.DataFrame()
    for h, g in rows[rows["h"].isin(list(horizons))].groupby("h", sort=True):
        by_model = {name: m.set_index("origin_t") for name, m in g.groupby("model", sort=True)}
        ref = by_model.get(reference)
        if ref is None:
            continue
        origins = np.sort(ref.index.unique().to_numpy())
        blocks = [b for b in np.array_split(origins, k) if len(b)]
        for name, w in by_model.items():
            if name == reference:
                continue
            j = pd.concat({"m": _abs_errors(w), "r": _abs_errors(ref)}, axis=1, join="inner")
            for i, b in enumerate(blocks, start=1):
                jb = j[j.index.isin(b)]
                mae_r = float(jb["r"].mean()) if len(jb) else math.nan
                recs.append(
                    {
                        "model": name,
                        "h": int(h),
                        "block": i,
                        "first_origin": int(b[0]),
                        "last_origin": int(b[-1]),
                        "n": len(jb),
                        "rel_mae": float(jb["m"].mean()) / mae_r if mae_r > 0 else math.nan,
                    }
                )
    return pd.DataFrame(recs)


def transform_family(label: str) -> str:
    """'boxcox(0.123)' -> 'boxcox'; 'none (fallback: ...)' -> 'none (fallback)'."""
    if label.startswith("none (fallback"):
        return "none (fallback)"
    return label.split("(", 1)[0].strip()


def transform_counts(folds: pd.DataFrame) -> pd.DataFrame:
    """RQ6: the variance transform chosen per test fold, with frequency counts.

    `folds` has one row per (series, window, model, origin) for models that use the
    fold transform. The transform is fitted once per fold and shared (M4-5), so each
    (series, window, origin) counts once. Box-Cox lambdas are summarised by their range.
    """
    cols = ["unique_id", "window", "origin_t"]
    if folds.empty:
        return pd.DataFrame(columns=["unique_id", "window", "transform", "n_folds", "share"])
    per = folds.drop_duplicates(cols).copy()
    per["family"] = per["transform"].map(transform_family)
    lam = per["transform"].str.extract(r"^boxcox\(([-0-9.eE]+)\)")[0].astype(float)
    per["lambda"] = lam
    recs = []
    for (uid, window), g in per.groupby(["unique_id", "window"], sort=True):
        n = len(g)
        for fam, gg in g.groupby("family", sort=True):
            rec = {
                "unique_id": uid,
                "window": window,
                "transform": fam,
                "n_folds": len(gg),
                "share": len(gg) / n,
                "lambda_min": np.nan,
                "lambda_max": np.nan,
            }
            if fam == "boxcox":
                rec["lambda_min"] = float(gg["lambda"].min())
                rec["lambda_max"] = float(gg["lambda"].max())
            recs.append(rec)
    return pd.DataFrame(recs)
