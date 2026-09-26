"""Forecast record store: one row per series, window, origin, model, horizon (D5).

Append-only. Metrics are computed from this table, so new metrics or tests never need a
refit. Periods are stored as ISO strings ('2020-01', '2024-01-02'). Missing floats are
Parquet nulls. The schema is explicit so the file is typed and reproducible.

Columns beyond the spec: level_true / level_pred (return series only: the price the
return path implies), mase_scale (MASE denominator from the origin's own training
slice, leakage source 9), n_train and n_outliers (fold facts for the report), and the
residual diagnostics of test folds (RQ4): n_resid, lb_p, lb_p_2m, arch_p, from the
model's one-step in-sample residuals while it is fitted (0 and nulls elsewhere).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from forecasting.errors import SchemaError

BASE_COLUMNS: list[tuple[str, str]] = [
    ("run_id", "str"),
    ("unique_id", "str"),
    ("model", "str"),
    ("window", "str"),
    ("origin_t", "int"),
    ("origin_period", "str"),
    ("origin_role", "str"),
    ("h", "int"),
    ("target_period", "str"),
    ("y_true", "float"),
    ("y_true_missing", "bool"),
    ("y_pred", "float"),
]
TAIL_COLUMNS: list[tuple[str, str]] = [
    ("level_true", "float"),
    ("level_pred", "float"),
    ("mase_scale", "float"),
    ("n_train", "int"),
    ("n_outliers", "int"),
    ("n_resid", "int"),
    ("lb_p", "float"),
    ("lb_p_2m", "float"),
    ("arch_p", "float"),
    ("transform", "str"),
    ("variant", "str"),
    ("fit_seconds", "float"),
    ("status", "str"),
    ("error", "str"),
]
SORT_KEYS = ["unique_id", "window", "model", "origin_t", "h"]
TIMING_COLUMNS = ["fit_seconds"]
STATUSES = {"ok", "failed", "skipped"}

_ARROW = {"str": pa.string(), "int": pa.int64(), "float": pa.float64(), "bool": pa.bool_()}
_PANDAS = {"str": "str", "int": "int64", "float": "float64", "bool": "bool"}


def level_tag(level: float) -> str:
    return f"{round(level * 100):d}"


def columns_for(levels: tuple[float, ...]) -> list[tuple[str, str]]:
    ivals = []
    for lv in sorted(levels):
        tag = level_tag(lv)
        # es_ is the mean of the simulated outcomes below the lower bound: what the loss
        # looks like given that the interval was breached downwards. It is populated only
        # by a simulated (cumulative) target, because a two-sided analytic interval does
        # not carry the shape of its own tail.
        ivals += [(f"lo_{tag}", "float"), (f"hi_{tag}", "float"), (f"es_{tag}", "float")]
    return BASE_COLUMNS + ivals + TAIL_COLUMNS


def levels_from_columns(cols) -> tuple[float, ...]:
    return tuple(sorted(int(c[3:]) / 100 for c in cols if c.startswith("lo_")))


class ForecastStore:
    def __init__(self, levels: tuple[float, ...]) -> None:
        self.levels = tuple(sorted(levels))
        self.columns = columns_for(self.levels)
        self._chunks: list[pd.DataFrame] = []

    # ---- writing
    def append(self, rows: pd.DataFrame) -> None:
        names = [c for c, _ in self.columns]
        if list(rows.columns) != names:
            missing = set(names) - set(rows.columns)
            extra = set(rows.columns) - set(names)
            raise SchemaError(
                f"columns differ from the store schema (missing {sorted(missing)}, "
                f"extra {sorted(extra)}, or wrong order)"
            )
        bad = set(rows["status"].unique()) - STATUSES
        if bad:
            raise SchemaError(f"unknown status values {sorted(bad)}")
        try:
            self._chunks.append(self._cast(rows))
        except (ValueError, TypeError) as e:
            raise SchemaError(f"cannot cast rows to the store schema: {e}") from e

    def extend(self, other: ForecastStore) -> None:
        if other.levels != self.levels:
            raise SchemaError("stores with different interval levels cannot be combined")
        self._chunks.extend(other._chunks)

    def _cast(self, df: pd.DataFrame) -> pd.DataFrame:
        return df.astype({c: _PANDAS[k] for c, k in self.columns})

    # ---- reading
    def frame(self) -> pd.DataFrame:
        names = [c for c, _ in self.columns]
        if not self._chunks:
            return self._cast(pd.DataFrame({c: [] for c in names}))
        df = pd.concat(self._chunks, ignore_index=True)
        return df.sort_values(SORT_KEYS, kind="stable").reset_index(drop=True)

    def __len__(self) -> int:
        return sum(len(c) for c in self._chunks)

    def arrow_schema(self) -> pa.Schema:
        return pa.schema([pa.field(c, _ARROW[k]) for c, k in self.columns])

    def write(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        table = pa.Table.from_pandas(self.frame(), schema=self.arrow_schema(), preserve_index=False)
        table = table.replace_schema_metadata(None)  # no pandas metadata: plain, stable file
        pq.write_table(table, path)
        return path

    @classmethod
    def read(cls, path: str | Path) -> ForecastStore:
        table = pq.read_table(path)
        levels = levels_from_columns(table.column_names)
        store = cls(levels)
        expected = store.arrow_schema()
        if not table.schema.equals(expected):
            raise SchemaError(f"{path} does not match the forecast store schema")
        store.append(table.to_pandas())
        return store


def content_hash(frame: pd.DataFrame) -> str:
    """Hash of the store contents, timing columns and the run_id label excluded (A1).

    run_id names the run, it is not a forecast: two runs of the same config and data
    from a clean and a dirty tree differ only in it and must hash the same (M8-11)."""
    body = frame.drop(columns=[*TIMING_COLUMNS, "run_id"]).sort_values(SORT_KEYS, kind="stable")
    digest = pd.util.hash_pandas_object(body.reset_index(drop=True), index=True).to_numpy()
    h = hashlib.sha256(digest.tobytes())
    h.update(",".join(body.columns).encode())
    return h.hexdigest()
