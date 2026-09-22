"""Run config: one YAML file, strict keys, frozen dataclasses (D6).

Every default is resolved at load time and stored in the dataclass, so the run_id
covers the values actually used. Unknown keys are always rejected so a typo can
never silently fall back to a default.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

from forecasting.errors import ConfigError

Freq = Literal["monthly", "quarterly", "weekly", "trading_days"]
Target = Literal["level", "returns"]
Duplicates = Literal["error", "sum", "mean"]
TransformMode = Literal["none", "log", "boxcox", "auto"]
WindowMode = Literal["expanding", "rolling", "both"]

FREQS: tuple[str, ...] = ("monthly", "quarterly", "weekly", "trading_days")
TARGETS: tuple[str, ...] = ("level", "returns")
DUPLICATES: tuple[str, ...] = ("error", "sum", "mean")
TRANSFORMS: tuple[str, ...] = ("none", "log", "boxcox", "auto")
WINDOWS: tuple[str, ...] = ("expanding", "rolling", "both")

# Default seasonal period per frequency. trading_days has no default season (U1).
DEFAULT_SEASON: dict[str, int] = {"monthly": 12, "quarterly": 4, "weekly": 52, "trading_days": 1}

# Model names per target. models/registry.py builds them; a test keeps the two in sync.
LEVEL_BASELINES: tuple[str, ...] = ("naive", "seasonal_naive", "drift", "sma")
RETURN_BASELINES: tuple[str, ...] = ("zero_return", "mean_return", "last_return", "sma")
# Scope update: ETS, SARIMA and Theta only for level series; AR(p) for returns.
LEVEL_STATISTICAL: tuple[str, ...] = ("ses", "ets", "sarima", "theta", "combination")
RETURN_STATISTICAL: tuple[str, ...] = ("ar",)
LEVEL_MODELS: tuple[str, ...] = LEVEL_BASELINES + LEVEL_STATISTICAL
RETURN_MODELS: tuple[str, ...] = RETURN_BASELINES + RETURN_STATISTICAL
COMBINATION_MEMBERS: tuple[str, ...] = ("ets", "sarima", "theta")  # fixed before any run
WARMUP_MODES: tuple[str, ...] = ("baselines", "all")
SARIMA_SEARCHES: tuple[str, ...] = ("stepwise", "grid")

# Backtest design defaults (spec: Volume; scope update for trading days).
TRADING_INITIAL = 500
TRADING_STEP = 5
TRADING_TEST_ORIGINS = 250
TRADING_SMA_WINDOWS = (5, 20, 60, 250)  # week, month, quarter, year of trading days
N_DEV_ORIGINS = 20
N_TEST_ORIGINS = 30

DEFAULT_SEED = 20260922
DEFAULT_LEVELS = (0.80, 0.95)

SERIES_KEYS = {
    "id",
    "source",
    "columns",
    "date_format",
    "start",
    "freq",
    "season",
    "target",
    "positive",
    "duplicates",
    "H",
    "decision_horizons",
    # backtest (M2)
    "transform",
    "window",
    "initial_window",
    "rolling_length",
    "origin_step",
    "n_dev_origins",
    "n_test_origins",
    "models",
    "sma_windows",
    "warmup_models",
    "sarima_search",
}
REQUIRED_SERIES_KEYS = ("id", "source", "freq", "H")
TOP_KEYS = {"series", "seed", "levels", "output_dir", "n_jobs"}
COLUMN_KEYS = {"date", "value", "id"}
# Keys that never change results: excluded from run_id.
NON_RESULT_KEYS = ("base_dir", "output_dir", "n_jobs")


@dataclass(frozen=True)
class Columns:
    """Maps source column names onto the canonical table (unique_id, ds, y)."""

    date: str = "ds"
    value: str = "y"
    id: str | None = None


@dataclass(frozen=True)
class SeriesConfig:
    """The label for one series: how to load it, how to treat it, how to backtest it."""

    id: str
    source: str
    freq: Freq
    H: int
    m: int
    columns: Columns = field(default_factory=Columns)
    date_format: str | None = None
    start: str | None = None
    target: Target = "level"
    positive: bool = False
    duplicates: Duplicates = "error"
    decision_horizons: tuple[int, ...] = ()
    transform: TransformMode = "auto"
    window: WindowMode = "both"
    initial_window: int = 24
    rolling_length: int = 24
    origin_step: int = 1
    n_dev_origins: int = N_DEV_ORIGINS
    n_test_origins: int = N_TEST_ORIGINS
    models: tuple[str, ...] = ()
    sma_windows: tuple[int, ...] = ()
    warmup_models: Literal["baselines", "all"] = "baselines"
    sarima_search: Literal["stepwise", "grid"] = "stepwise"
    warnings: tuple[str, ...] = ()

    @property
    def source_kind(self) -> str:
        return self.source.split(":", 1)[0]

    @property
    def source_ref(self) -> str:
        return self.source.split(":", 1)[1]

    @property
    def windows(self) -> tuple[str, ...]:
        return ("expanding", "rolling") if self.window == "both" else (self.window,)

    @property
    def floor_initial(self) -> int:
        """Shortest first training window allowed in reduced mode."""
        default = 250 if self.freq == "trading_days" else max(2 * self.m, 12)
        return min(default, self.initial_window)


@dataclass(frozen=True)
class RunConfig:
    series: tuple[SeriesConfig, ...]
    seed: int = DEFAULT_SEED
    levels: tuple[float, ...] = DEFAULT_LEVELS
    output_dir: str = "runs"
    n_jobs: int = 1
    base_dir: Path = Path(".")

    def to_canonical_json(self) -> str:
        """Stable JSON of everything that changes results (paths and n_jobs excluded)."""
        d = asdict(self)
        for k in NON_RESULT_KEYS:
            d.pop(k)
        for s in d["series"]:
            s.pop("warnings")
        return json.dumps(d, sort_keys=True, separators=(",", ":"), default=str)

    @property
    def output_path(self) -> Path:
        p = Path(self.output_dir)
        return p if p.is_absolute() else self.base_dir / p


def _is_int(x: Any) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def _pos_int(raw: dict, key: str, p: str, default: int) -> int:
    v = raw.get(key, default)
    if not _is_int(v) or v < 1:
        raise ConfigError(f"{p}.{key}", f"must be an integer >= 1, got {v!r}")
    return int(v)


def _parse_columns(raw: Any, key: str) -> Columns:
    if raw is None:
        return Columns()
    if not isinstance(raw, dict):
        raise ConfigError(key, "must be a mapping like {date: Date, value: Close}")
    unknown = set(raw) - COLUMN_KEYS
    if unknown:
        raise ConfigError(
            f"{key}.{sorted(unknown)[0]}", f"unknown key; allowed: {sorted(COLUMN_KEYS)}"
        )
    for k, v in raw.items():
        if not isinstance(v, str) or not v:
            raise ConfigError(f"{key}.{k}", "must be a non-empty column name")
    return Columns(**raw)


def _parse_season(raw: Any, freq: str, key: str) -> int:
    if raw is None:
        return DEFAULT_SEASON[freq]
    if raw == "none" or raw is False:
        return 1
    if _is_int(raw) and raw >= 1:
        return int(raw)
    raise ConfigError(key, "must be a positive integer or 'none'")


def default_sma_windows(freq: str, m: int) -> tuple[int, ...]:
    """Spec: k in {3, 6, m, 2m}; SMA(1) is naive, so k >= 2. Trading days: week to year."""
    if freq == "trading_days":
        return TRADING_SMA_WINDOWS
    return tuple(sorted({k for k in (3, 6, m, 2 * m) if k >= 2}))


def _parse_series(raw: Any, i: int) -> SeriesConfig:
    p = f"series[{i}]"
    if not isinstance(raw, dict):
        raise ConfigError(p, "each series must be a mapping")
    unknown = set(raw) - SERIES_KEYS
    if unknown:
        k = sorted(unknown)[0]
        raise ConfigError(f"{p}.{k}", f"unknown key; allowed: {sorted(SERIES_KEYS)}")
    for k in REQUIRED_SERIES_KEYS:
        if k not in raw or raw[k] is None:
            if k == "H":
                raise ConfigError(
                    f"{p}.H",
                    "required and has no default. Choose it from the decision the forecast "
                    "feeds; if unsure, H = 2m and treat the run as exploratory",
                )
            raise ConfigError(f"{p}.{k}", "required")

    sid = raw["id"]
    if not isinstance(sid, str) or not sid:
        raise ConfigError(f"{p}.id", "must be a non-empty string")

    source = raw["source"]
    if not isinstance(source, str) or ":" not in source:
        raise ConfigError(
            f"{p}.source", "must look like 'csv:path/to/file.csv' or 'fred:SERIES_ID'"
        )
    kind, ref = source.split(":", 1)
    if kind not in ("csv", "fred") or not ref:
        raise ConfigError(f"{p}.source", f"unknown source kind '{kind}'; use csv: or fred:")

    freq = raw["freq"]
    if freq not in FREQS:
        raise ConfigError(f"{p}.freq", f"must be one of {list(FREQS)}, got {freq!r}")
    trading = freq == "trading_days"

    target = raw.get("target", "level")
    if target not in TARGETS:
        raise ConfigError(f"{p}.target", f"must be one of {list(TARGETS)}, got {target!r}")

    positive = raw.get("positive", False)
    if not isinstance(positive, bool):
        raise ConfigError(f"{p}.positive", "must be true or false")

    duplicates = raw.get("duplicates", "error")
    if duplicates not in DUPLICATES:
        raise ConfigError(f"{p}.duplicates", f"must be one of {list(DUPLICATES)}")

    m = _parse_season(raw.get("season"), freq, f"{p}.season")

    H = raw["H"]
    if not _is_int(H) or H < 1:
        raise ConfigError(f"{p}.H", f"must be an integer >= 1, got {H!r}")
    warnings: list[str] = []
    if m > 1:
        if H > 3 * m:
            raise ConfigError(
                f"{p}.H",
                f"H = {H} exceeds 3m = {3 * m}; too few test origins and overlapping errors",
            )
        if H < m:
            warnings.append(
                f"H = {H} < m = {m}: seasonal behaviour across a full cycle is not evaluated"
            )

    dh_raw = raw.get("decision_horizons", [])
    if dh_raw is None:
        dh_raw = []
    if not isinstance(dh_raw, list) or not all(_is_int(h) for h in dh_raw):
        raise ConfigError(f"{p}.decision_horizons", "must be a list of integers")
    for h in dh_raw:
        if not 1 <= h <= H:
            raise ConfigError(f"{p}.decision_horizons", f"{h} is outside 1..H = 1..{H}")
    if len(set(dh_raw)) != len(dh_raw):
        raise ConfigError(f"{p}.decision_horizons", "contains duplicates")

    start = raw.get("start")
    if start is not None:
        if isinstance(start, dt.date):
            start = start.isoformat()
        try:
            dt.date.fromisoformat(str(start))
        except ValueError as e:
            raise ConfigError(f"{p}.start", "must be an ISO date, e.g. 2005-01-01") from e
        start = str(start)

    date_format = raw.get("date_format")
    if date_format is not None and not isinstance(date_format, str):
        raise ConfigError(f"{p}.date_format", "must be a strftime string, e.g. '%d-%b-%Y'")

    # ---- backtest keys (M2). Defaults depend on freq and target.
    transform = raw.get("transform", "none" if target == "returns" else "auto")
    if transform not in TRANSFORMS:
        raise ConfigError(f"{p}.transform", f"must be one of {list(TRANSFORMS)}")
    if target == "returns" and transform != "none":
        raise ConfigError(
            f"{p}.transform",
            "must be none for target: returns (the log return is the transform; "
            "returns cross zero, so log or Box-Cox on them is undefined)",
        )

    window = raw.get("window", "both")
    if window not in WINDOWS:
        raise ConfigError(f"{p}.window", f"must be one of {list(WINDOWS)}")

    base_initial = TRADING_INITIAL if trading else max(3 * m, 24)
    initial_window = _pos_int(raw, "initial_window", p, base_initial)
    rolling_length = _pos_int(raw, "rolling_length", p, base_initial)
    origin_step = _pos_int(raw, "origin_step", p, TRADING_STEP if trading else 1)
    n_dev = _pos_int(raw, "n_dev_origins", p, N_DEV_ORIGINS)
    n_test = _pos_int(raw, "n_test_origins", p, TRADING_TEST_ORIGINS if trading else N_TEST_ORIGINS)
    if n_test < 30:
        warnings.append(
            f"n_test_origins = {n_test} < 30: coverage and DM tests will be underpowered"
        )
    if initial_window < 2:
        raise ConfigError(f"{p}.initial_window", "must be at least 2")

    allowed = RETURN_MODELS if target == "returns" else LEVEL_MODELS
    models_raw = raw.get("models")
    if models_raw is None:
        models = tuple(x for x in allowed if not (x == "seasonal_naive" and m == 1))
    else:
        if not isinstance(models_raw, list) or not models_raw:
            raise ConfigError(f"{p}.models", "must be a non-empty list of model names")
        for name in models_raw:
            if name not in allowed:
                raise ConfigError(
                    f"{p}.models",
                    f"'{name}' is not available for target: {target}; choose from {list(allowed)}",
                )
        if "seasonal_naive" in models_raw and m == 1:
            raise ConfigError(f"{p}.models", "seasonal_naive needs season > 1")
        if len(set(models_raw)) != len(models_raw):
            raise ConfigError(f"{p}.models", "contains duplicates")
        models = tuple(models_raw)
    if "combination" in models:
        absent = [x for x in COMBINATION_MEMBERS if x not in models]
        if absent:
            raise ConfigError(f"{p}.models", f"combination needs its fixed members; add {absent}")

    warmup_models = raw.get("warmup_models", "baselines")
    if warmup_models not in WARMUP_MODES:
        raise ConfigError(f"{p}.warmup_models", f"must be one of {list(WARMUP_MODES)}")
    sarima_search = raw.get("sarima_search", "stepwise")
    if sarima_search not in SARIMA_SEARCHES:
        raise ConfigError(f"{p}.sarima_search", f"must be one of {list(SARIMA_SEARCHES)}")

    sma_raw = raw.get("sma_windows")
    if sma_raw is None:
        sma_windows = default_sma_windows(freq, m)
    else:
        if (
            not isinstance(sma_raw, list)
            or not sma_raw
            or not all(_is_int(k) and k >= 2 for k in sma_raw)
        ):
            raise ConfigError(f"{p}.sma_windows", "must be a non-empty list of integers >= 2")
        sma_windows = tuple(sorted(set(sma_raw)))
    too_long = [k for k in sma_windows if k > initial_window - (1 if target == "returns" else 0)]
    if too_long:
        sma_windows = tuple(k for k in sma_windows if k not in too_long)
        warnings.append(f"sma windows {too_long} exceed the initial window and are dropped")
        if not sma_windows and "sma" in models:
            raise ConfigError(f"{p}.sma_windows", "no window fits inside initial_window")

    return SeriesConfig(
        id=sid,
        source=source,
        freq=freq,
        H=H,
        m=m,
        columns=_parse_columns(raw.get("columns"), f"{p}.columns"),
        date_format=date_format,
        start=start,
        target=target,
        positive=positive,
        duplicates=duplicates,
        decision_horizons=tuple(sorted(dh_raw)),
        transform=transform,
        window=window,
        initial_window=initial_window,
        rolling_length=rolling_length,
        origin_step=origin_step,
        n_dev_origins=n_dev,
        n_test_origins=n_test,
        models=models,
        sma_windows=sma_windows,
        warmup_models=warmup_models,
        sarima_search=sarima_search,
        warnings=tuple(warnings),
    )


def _parse_levels(raw: Any) -> tuple[float, ...]:
    if raw is None:
        return DEFAULT_LEVELS
    if (
        not isinstance(raw, list)
        or not raw
        or not all(isinstance(x, float) and 0 < x < 1 for x in raw)
    ):
        raise ConfigError(
            "levels", "must be a non-empty list of floats in (0, 1), e.g. [0.8, 0.95]"
        )
    if len({round(x * 100) for x in raw}) != len(raw):
        raise ConfigError("levels", "levels must differ in whole percent (columns are lo_80, ...)")
    return tuple(sorted(raw))


def parse_config(raw: Any, base_dir: Path = Path(".")) -> RunConfig:
    if not isinstance(raw, dict):
        raise ConfigError("<root>", "config must be a mapping with a 'series' list")
    unknown = set(raw) - TOP_KEYS
    if unknown:
        k = sorted(unknown)[0]
        raise ConfigError(k, f"unknown key; allowed: {sorted(TOP_KEYS)}")
    series_raw = raw.get("series")
    if not isinstance(series_raw, list) or not series_raw:
        raise ConfigError("series", "required: a non-empty list of series")
    series = tuple(_parse_series(s, i) for i, s in enumerate(series_raw))
    ids = [s.id for s in series]
    dupes = {x for x in ids if ids.count(x) > 1}
    if dupes:
        raise ConfigError("series", f"duplicate series ids: {sorted(dupes)}")
    seed = raw.get("seed", DEFAULT_SEED)
    if not _is_int(seed) or seed < 0:
        raise ConfigError("seed", "must be a non-negative integer")
    output_dir = raw.get("output_dir", "runs")
    if not isinstance(output_dir, str) or not output_dir:
        raise ConfigError("output_dir", "must be a path string")
    n_jobs = raw.get("n_jobs", 1)
    if not _is_int(n_jobs) or n_jobs == 0 or n_jobs < -1:
        raise ConfigError("n_jobs", "must be a positive integer or -1 (all cores)")
    return RunConfig(
        series=series,
        seed=seed,
        levels=_parse_levels(raw.get("levels")),
        output_dir=output_dir,
        n_jobs=n_jobs,
        base_dir=base_dir,
    )


def load_config(path: str | Path) -> RunConfig:
    """Load and validate a YAML run config. Raises ConfigError naming the offending key."""
    path = Path(path)
    try:
        text = path.read_text()
    except OSError as e:
        raise ConfigError("<file>", f"cannot read {path}: {e}") from e
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ConfigError("<file>", f"invalid YAML: {e}") from e
    return parse_config(raw, base_dir=path.resolve().parent)


def _git(args: list[str], cwd: str | None) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout


def git_sha(repo_dir: Path | None = None) -> str:
    """HEAD commit. With uncommitted changes: '<sha>-dirty-<hash of the changes>'.

    Hashing the diff means two different dirty trees never share a run_id.
    """
    cwd = str(repo_dir) if repo_dir else None
    try:
        sha = _git(["rev-parse", "HEAD"], cwd).strip()
        status = _git(["status", "--porcelain"], cwd)
    except (OSError, subprocess.CalledProcessError):
        return "nogit"
    if not status.strip():
        return sha
    diff = _git(["diff", "HEAD"], cwd)
    digest = hashlib.sha256((status + diff).encode()).hexdigest()[:8]
    return f"{sha}-dirty-{digest}"


def run_id(cfg: RunConfig, data_hash: str, git: str) -> str:
    """run_id = sha256(config, data hash, git sha), shortened to 16 hex chars (D6)."""
    h = hashlib.sha256()
    for part in (cfg.to_canonical_json(), data_hash, git):
        h.update(part.encode())
        h.update(b"\x00")
    return h.hexdigest()[:16]
