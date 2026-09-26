"""Validation: enforce every rule in 'Dataset requirements' and canonicalise.

Validation decides whether the data can be evaluated honestly. It never changes a
value. It may: drop rows before a configured `start`, drop extra columns (with a
warning), put timestamps in canonical form, reindex to the full calendar (gaps
become NaN), and aggregate duplicates only when the config asks for sum or mean.
Imputation and transforms are fold-level concerns (M2), never done here.

Rule order (first failure wins, so each bad fixture fails on its own rule):
  1 schema       unique_id, ds, y present                  MissingColumnError
  2 timestamps   parseable, tz-naive                       TimestampError
  3 target       numeric and finite                        NonNumeric / NonFiniteTargetError
  4 duplicates   one row per timestamp (or aggregate)      DuplicateTimestampError
  5 frequency    spacing equals declared freq              IrregularFrequencyError
                 trading_days: no weekend rows             TradingCalendarError
  6 gaps         run <= 2 missing, share <= 5%             LongGapError / TooManyMissingError
  7 sign, zeros  zeros <= 30%; no negatives if positive    TooManyZerosError / NegativeValueError
                 returns need levels > 0                   NonPositivePriceError
  8 volume       T >= floor, else error; < minimum: reduced mode
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Literal

import numpy as np
import pandas as pd

from forecasting.config import SeriesConfig, is_return_target
from forecasting.errors import (
    DuplicateTimestampError,
    IrregularFrequencyError,
    LongGapError,
    MissingColumnError,
    NegativeValueError,
    NonFiniteTargetError,
    NonNumericTargetError,
    NonPositivePriceError,
    TimestampError,
    TooManyMissingError,
    TooManyZerosError,
    TooShortError,
    TradingCalendarError,
)

PERIOD_ALIAS = {"monthly": "M", "quarterly": "Q", "weekly": "W"}

MAX_GAP = 2  # longest run of missing values, in periods (or trading-day rows)
MAX_MISSING_SHARE = 0.05
MAX_ZERO_SHARE = 0.30

# Fewest origins allowed at the hard floor (spec: floor = max(2m, 12) + H + 20).
MIN_ORIGINS = 20

# Trading-day calendar (no holiday list in M1): weekdays without a row are flagged.
MAX_TRADING_CLOSURE = 5  # longest run of weekdays with no value; longer is a gap, not a closure
MAX_FLAGGED_WEEKDAY_SHARE = 0.15  # above this the data is not daily trading data

REQUIRED = ("unique_id", "ds", "y")


@dataclass(frozen=True)
class Series:
    """One validated series. y has the full calendar, NaN where a value is missing.

    Periodic freqs: PeriodIndex (M, Q or W). trading_days: DatetimeIndex of the dates
    that have rows; weekdays without a row are listed in the report, not inserted.
    """

    unique_id: str
    y: pd.Series
    m: int
    freq: str
    target: str
    data_hash: str


@dataclass(frozen=True)
class ValidationReport:
    unique_id: str
    freq: str
    m: int
    target: str
    H: int
    first: str
    last: str
    n_rows: int
    n_obs: int
    n_missing: int
    max_gap: int
    missing_share: float
    n_zero: int
    n_negative: int
    T: int
    t_floor: int
    t_min: int
    mode: Literal["full", "reduced"]
    n_dropped_before_start: int = 0
    n_aggregated: int = 0
    flagged_weekdays: tuple[str, ...] = ()
    ignored_columns: tuple[str, ...] = ()
    warnings: tuple[str, ...] = field(default=())

    def format(self) -> str:
        rows = [
            ("series", self.unique_id),
            ("freq / m / target", f"{self.freq} / {self.m} / {self.target}"),
            ("range", f"{self.first} .. {self.last}"),
            ("rows / observed", f"{self.n_rows} / {self.n_obs}"),
            (
                "missing (share, longest run)",
                f"{self.n_missing} ({self.missing_share:.1%}, {self.max_gap})",
            ),
            ("zeros / negatives", f"{self.n_zero} / {self.n_negative}"),
            ("T vs floor / minimum", f"{self.T} vs {self.t_floor} / {self.t_min} (H = {self.H})"),
            ("mode", self.mode),
        ]
        if self.freq == "trading_days":
            rows.append(("weekdays without a row", str(len(self.flagged_weekdays))))
        if self.n_dropped_before_start:
            rows.append(("rows dropped before start", str(self.n_dropped_before_start)))
        if self.n_aggregated:
            rows.append(("duplicate rows aggregated", str(self.n_aggregated)))
        w = max(len(k) for k, _ in rows)
        lines = [f"  {k.ljust(w)}  {v}" for k, v in rows]
        lines += [f"  warning: {x}" for x in self.warnings]
        return "\n".join(lines)


# ---------------------------------------------------------------- helpers


def _examples(values, k: int = 3) -> str:
    vals = [str(v) for v in list(values)[:k]]
    return ", ".join(vals)


def _longest_run(mask: np.ndarray) -> tuple[int, int]:
    """Longest run of True: (length, start index). (0, -1) if none."""
    best, best_start, cur, start = 0, -1, 0, 0
    for i, v in enumerate(mask):
        if v:
            if cur == 0:
                start = i
            cur += 1
            if cur > best:
                best, best_start = cur, start
        else:
            cur = 0
    return best, best_start


def volume_thresholds(scfg: SeriesConfig) -> tuple[int, int]:
    """(hard floor, minimum) for T, from the series' backtest design.

    minimum = initial_window + step x (n_dev + n_test - 1) + H: exactly n_dev + n_test
    origins. With the defaults this is the spec's max(3m, 24) + 20 + 30 + H - 1, and for
    trading days 500 + 5 x 269 + H (scope update).
    floor = floor_initial + step x 20 + H: max(2m, 12) + H + 20 for periodic series.
    backtest/splits.make_origins uses the same numbers, so the two cannot drift.
    """
    step, H = scfg.origin_step, scfg.H
    t_min = scfg.initial_window + step * (scfg.n_dev_origins + scfg.n_test_origins - 1) + H
    t_floor = scfg.floor_initial + step * MIN_ORIGINS + H
    return min(t_floor, t_min), t_min


def data_hash(y: pd.Series) -> str:
    h = hashlib.sha256()
    h.update("\n".join(map(str, y.index)).encode())
    h.update(np.ascontiguousarray(y.to_numpy(dtype="float64")).tobytes())
    return h.hexdigest()


def _parse_ds(ds: pd.Series, scfg: SeriesConfig) -> pd.Series:
    if ds.isna().any():
        raise TimestampError(f"{int(ds.isna().sum())} rows have an empty timestamp", scfg.id)
    if pd.api.types.is_datetime64_any_dtype(ds):
        parsed = ds
    else:
        try:
            parsed = pd.to_datetime(ds, format=scfg.date_format)
        except (ValueError, TypeError) as e:
            raise TimestampError(f"cannot parse timestamps ({e})", scfg.id) from e
    if getattr(parsed.dt, "tz", None) is not None:
        raise TimestampError(
            "timestamps carry a timezone; supply tz-naive dates in the exchange's local "
            "calendar (a silent conversion could shift closes across days)",
            scfg.id,
        )
    return parsed


def _parse_y(y: pd.Series, scfg: SeriesConfig) -> pd.Series:
    if pd.api.types.is_bool_dtype(y):
        raise NonNumericTargetError("y is boolean", scfg.id)
    if pd.api.types.is_numeric_dtype(y):
        parsed = y.astype("float64")
    else:
        raw = y.astype("string").str.strip()
        parsed = pd.to_numeric(raw, errors="coerce").astype("float64")
        bad = raw.notna() & (raw != "") & parsed.isna()
        if bad.any():
            raise NonNumericTargetError(
                f"{int(bad.sum())} non-numeric values in y, e.g. {_examples(raw[bad].unique())}",
                scfg.id,
            )
    inf = np.isinf(parsed.to_numpy())
    if inf.any():
        raise NonFiniteTargetError(f"{int(inf.sum())} infinite values in y", scfg.id)
    return parsed


# ---------------------------------------------------------------- per series


def _validate_one(
    ds: pd.Series,
    y: pd.Series,
    scfg: SeriesConfig,
    uid: str,
    warnings: list[str],
    ignored: tuple[str, ...],
    n_dropped: int,
) -> tuple[Series, ValidationReport]:
    trading = scfg.freq == "trading_days"
    frame = pd.DataFrame({"ds": ds.to_numpy(), "y": y.to_numpy()})

    # ds canonical form: trading days are dates; periodic freqs become periods.
    if trading:
        if (frame["ds"] != frame["ds"].dt.normalize()).any():
            raise TimestampError(
                "trading_days expects dates without a time of day; intraday or shifted close "
                "times suggest a timezone misalignment",
                uid,
            )
        frame["key"] = frame["ds"]
    else:
        frame["key"] = frame["ds"].dt.to_period(PERIOD_ALIAS[scfg.freq])

    # 4 duplicates: identical timestamps.
    n_aggregated = 0
    dup = frame["ds"].duplicated(keep=False)
    if dup.any():
        if scfg.duplicates == "error":
            raise DuplicateTimestampError(
                f"{int(dup.sum())} rows share a timestamp, e.g. "
                f"{_examples(frame.loc[dup, 'ds'].dt.date.unique())}; set duplicates: sum or mean "
                "if repeated rows are parts of one value",
                uid,
            )
        agg = frame.groupby("ds", sort=False).agg(
            y=("y", lambda s: s.sum(min_count=1) if scfg.duplicates == "sum" else s.mean()),
            key=("key", "first"),
        )
        n_aggregated = int(dup.sum())
        frame = agg.reset_index()
        warnings.append(f"{n_aggregated} duplicate rows aggregated by {scfg.duplicates}")

    frame = frame.sort_values("key", kind="stable").reset_index(drop=True)

    # 5 frequency.
    flagged: tuple[str, ...] = ()
    if trading:
        weekend = frame["ds"].dt.dayofweek >= 5
        if weekend.any():
            raise TradingCalendarError(
                f"{int(weekend.sum())} rows fall on a weekend, e.g. "
                f"{_examples(frame.loc[weekend, 'ds'].dt.date)}",
                uid,
            )
        calendar = pd.bdate_range(frame["ds"].iloc[0], frame["ds"].iloc[-1])
        present = pd.DatetimeIndex(frame["ds"])
        no_row = ~calendar.isin(present)
        flagged = tuple(str(d.date()) for d in calendar[no_row])
        share = no_row.mean() if len(calendar) else 0.0
        if share > MAX_FLAGGED_WEEKDAY_SHARE:
            raise IrregularFrequencyError(
                f"{share:.0%} of weekdays have no row (limit {MAX_FLAGGED_WEEKDAY_SHARE:.0%}); "
                "the data looks coarser than daily trading data",
                uid,
            )
        values = pd.Series(frame["y"].to_numpy(), index=present, name=uid)
        # A closure run counts weekdays with no row or no value.
        absent_row = pd.Series(True, index=calendar)
        absent_row[present] = values.isna().to_numpy()
        run, at = _longest_run(absent_row.to_numpy())
        if run > MAX_TRADING_CLOSURE:
            raise LongGapError(
                f"{run} consecutive weekdays without a value from {calendar[at].date()} "
                f"(limit {MAX_TRADING_CLOSURE}); set start: to a date after the gap",
                uid,
            )
        y_full = values
    else:
        finer = frame["key"].duplicated(keep=False)
        if finer.any():
            raise IrregularFrequencyError(
                f"{int(finer.sum())} distinct timestamps fall in the same {scfg.freq} period, "
                f"e.g. {_examples(frame.loc[finer, 'ds'].dt.date)}; the data is finer than declared",
                uid,
            )
        if len(frame) >= 3:
            steps = np.diff(frame["key"].map(lambda p: p.ordinal).to_numpy())
            modal = int(pd.Series(steps).mode().iloc[0])
            if modal != 1:
                raise IrregularFrequencyError(
                    f"typical spacing is {modal} {scfg.freq} periods, not 1; "
                    "the data is coarser than declared",
                    uid,
                )
        full = pd.period_range(
            frame["key"].iloc[0], frame["key"].iloc[-1], freq=frame["key"].iloc[0].freq
        )
        y_full = pd.Series(frame["y"].to_numpy(), index=pd.PeriodIndex(frame["key"]), name=uid)
        y_full = y_full.reindex(full)

    # 6 gaps and missing values.
    missing = y_full.isna().to_numpy()
    n_missing = int(missing.sum())
    run, at = _longest_run(missing)
    missing_share = n_missing / len(y_full) if len(y_full) else 0.0
    if run > MAX_GAP:
        raise LongGapError(
            f"{run} consecutive missing values from {y_full.index[at]} (limit {MAX_GAP}); "
            "long gaps usually mean a definition or regime change: set start: to a date after it",
            uid,
        )
    if missing_share > MAX_MISSING_SHARE:
        raise TooManyMissingError(
            f"{n_missing} of {len(y_full)} values missing ({missing_share:.1%}, "
            f"limit {MAX_MISSING_SHARE:.0%})",
            uid,
        )

    # 7 sign and zeros (observed values only).
    obs = y_full.dropna()
    n_obs = len(obs)
    n_zero = int((obs == 0).sum())
    n_negative = int((obs < 0).sum())
    if n_obs and n_zero / n_obs > MAX_ZERO_SHARE:
        raise TooManyZerosError(
            f"{n_zero} of {n_obs} values are zero ({n_zero / n_obs:.0%}, limit "
            f"{MAX_ZERO_SHARE:.0%}); intermittent series are out of scope",
            uid,
        )
    if scfg.positive and n_negative:
        raise NegativeValueError(
            f"{n_negative} negative values with positive: true, e.g. {_examples(obs[obs < 0].index)}",
            uid,
        )
    if is_return_target(scfg.target) and (obs <= 0).any():
        raise NonPositivePriceError(
            f"{int((obs <= 0).sum())} values <= 0; log returns need a strictly positive level",
            uid,
        )

    # 8 volume. Returns lose one observation to differencing.
    T = len(y_full) - (1 if is_return_target(scfg.target) else 0)
    t_floor, t_min = volume_thresholds(scfg)
    if t_floor > T:
        raise TooShortError(
            f"T = {T} is below the hard floor {t_floor} for {scfg.freq}, m = {scfg.m}, "
            f"H = {scfg.H} (minimum for full mode: {t_min})",
            uid,
        )
    mode: Literal["full", "reduced"] = "full"
    if T < t_min:
        mode = "reduced"
        warnings.append(
            f"reduced mode: T = {T} < minimum {t_min}; fewer origins, tests underpowered"
        )

    y_full = y_full.astype("float64")
    series = Series(
        unique_id=uid,
        y=y_full,
        m=scfg.m,
        freq=scfg.freq,
        target=scfg.target,
        data_hash=data_hash(y_full),
    )
    report = ValidationReport(
        unique_id=uid,
        freq=scfg.freq,
        m=scfg.m,
        target=scfg.target,
        H=scfg.H,
        first=str(y_full.index[0].date() if trading else y_full.index[0]),
        last=str(y_full.index[-1].date() if trading else y_full.index[-1]),
        n_rows=len(y_full),
        n_obs=n_obs,
        n_missing=n_missing,
        max_gap=run,
        missing_share=missing_share,
        n_zero=n_zero,
        n_negative=n_negative,
        T=T,
        t_floor=t_floor,
        t_min=t_min,
        mode=mode,
        n_dropped_before_start=n_dropped,
        n_aggregated=n_aggregated,
        flagged_weekdays=flagged,
        ignored_columns=ignored,
        warnings=tuple(warnings),
    )
    return series, report


def validate(df: pd.DataFrame, scfg: SeriesConfig) -> list[tuple[Series, ValidationReport]]:
    """Validate a canonical table for one config entry; one result per unique_id.

    Raises a DataValidationError subclass on the first rule a series breaks.
    """
    missing_cols = [c for c in REQUIRED if c not in df.columns]
    if missing_cols:
        raise MissingColumnError(f"missing required columns {missing_cols}", scfg.id)
    base_warnings = list(scfg.warnings)
    ignored = tuple(c for c in df.columns if c not in REQUIRED)
    if ignored:
        base_warnings.append(
            f"ignored columns {list(ignored)}: Phase 1 is univariate, extra columns never reach models"
        )
    if df.empty:
        raise TooShortError("no rows", scfg.id)

    ds = _parse_ds(df["ds"], scfg)
    y = _parse_y(df["y"], scfg)
    uid_col = df["unique_id"].astype(str)

    n_dropped_all = pd.Series(0, index=uid_col.unique())
    if scfg.start is not None:
        keep = ds >= pd.Timestamp(scfg.start)
        n_dropped_all = (~keep).groupby(uid_col).sum()
        ds, y, uid_col = ds[keep], y[keep], uid_col[keep]

    results = []
    for uid in sorted(uid_col.unique()):
        sel = uid_col == uid
        results.append(
            _validate_one(
                ds[sel],
                y[sel],
                scfg,
                uid,
                list(base_warnings),
                ignored,
                int(n_dropped_all.get(uid, 0)),
            )
        )
    if not results:
        raise TooShortError(f"no rows on or after start {scfg.start}", scfg.id)
    return results
