"""Source adapters. Their only job is to produce the canonical table.

Canonical table: columns unique_id (str), ds, y, in that order, plus any extra
source columns passed through untouched so validate can warn about them. Adapters
never parse, clean, sort or drop values: ds and y are returned exactly as read.
Parsing and every rule live in validate.py, so there is one place that decides.
"""

from __future__ import annotations

import urllib.request
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pandas as pd

from forecasting.config import Columns, RunConfig, SeriesConfig
from forecasting.errors import AdapterError, MissingColumnError

CANONICAL = ["unique_id", "ds", "y"]
FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={id}"

Fetcher = Callable[[str], str]


def _to_canonical(raw: pd.DataFrame, scfg: SeriesConfig, origin: str) -> pd.DataFrame:
    cols = scfg.columns
    for role, name in (("date", cols.date), ("value", cols.value), ("id", cols.id)):
        if name is not None and name not in raw.columns:
            raise MissingColumnError(
                f"{origin}: mapped {role} column '{name}' not found; columns are {list(raw.columns)}",
                scfg.id,
            )
    out = raw.rename(columns={cols.date: "ds", cols.value: "y"})
    if cols.id is not None:
        out = out.rename(columns={cols.id: "unique_id"})
        out["unique_id"] = out["unique_id"].astype(str)
    else:
        if "unique_id" in out.columns:
            out = out.rename(columns={"unique_id": "unique_id_source"})
        out.insert(0, "unique_id", scfg.id)
    extras = [c for c in out.columns if c not in CANONICAL]
    return out[CANONICAL + extras]


def read_csv(path: str | Path, scfg: SeriesConfig) -> pd.DataFrame:
    """CSV adapter with column mapping. Everything is read as text; validate parses."""
    path = Path(path)
    if not path.exists():
        raise AdapterError(f"[{scfg.id}] CSV not found: {path}")
    raw = pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[""])
    return _to_canonical(raw, scfg, str(path))


def _fetch_url(series_id: str) -> str:
    url = FRED_URL.format(id=series_id)
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return r.read().decode()
    except OSError as e:
        raise AdapterError(
            f"FRED download failed for {series_id} ({e}). Download {url} by hand and save it "
            f"as the cache file, or run where FRED is reachable"
        ) from e


def read_fred(
    series_id: str,
    scfg: SeriesConfig,
    cache_dir: str | Path,
    fetch: Fetcher = _fetch_url,
    refresh: bool = False,
) -> pd.DataFrame:
    """FRED adapter. Downloads fredgraph.csv once into cache_dir, then reads the cache.

    The cache makes runs reproducible: the data hash only changes when the cache is
    refreshed on purpose. FRED marks missing values with '.', which become NaN here
    (a missing value, not a changed one). Revisions (vintages) are not handled; see
    leakage source 16 in the spec.
    """
    cache = Path(cache_dir) / f"{series_id}.csv"
    if refresh or not cache.exists():
        text = fetch(series_id)
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(text)
    raw = pd.read_csv(cache, dtype=str, keep_default_na=False, na_values=["", "."])
    date_col = next((c for c in ("observation_date", "DATE") if c in raw.columns), None)
    if date_col is None or series_id not in raw.columns:
        raise AdapterError(
            f"[{scfg.id}] {cache} is not a FRED csv for {series_id}: columns {list(raw.columns)}"
        )
    raw = raw[[date_col, series_id]]
    mapped = replace(scfg, columns=Columns(date=date_col, value=series_id))
    return _to_canonical(raw, mapped, f"fred:{series_id}")


def load_series(scfg: SeriesConfig, base_dir: Path, fetch: Fetcher | None = None) -> pd.DataFrame:
    """Dispatch on the series source. Relative paths resolve against the config's folder."""
    if scfg.source_kind == "csv":
        p = Path(scfg.source_ref)
        return read_csv(p if p.is_absolute() else base_dir / p, scfg)
    if scfg.source_kind == "fred":
        kwargs = {"fetch": fetch} if fetch else {}
        return read_fred(scfg.source_ref, scfg, base_dir / "data" / "fred", **kwargs)
    raise AdapterError(f"[{scfg.id}] unknown source kind {scfg.source_kind}")


def load_all(cfg: RunConfig, fetch: Fetcher | None = None) -> dict[str, pd.DataFrame]:
    return {s.id: load_series(s, cfg.base_dir, fetch) for s in cfg.series}
