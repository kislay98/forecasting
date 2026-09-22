# forecasting

A leak-free, multi-horizon forecasting and uncertainty study. The primary series
is the Nifty 50 (daily returns); the positive control is US utilities output
(FRED IPG2211A2N, monthly). The engine is generic: any single number over time
goes through the same three-column table (unique_id, ds, y).

Phase 1 asks one question: does any model beat naive baselines on real data, in a
backtest proven free of look-ahead? "No" is a valid answer. Not a trading system
and not financial advice.

Spec: [docs/spec.md](docs/spec.md). Research: [docs/research.md](docs/research.md).
Status and every decision made while building: [DECISIONS.md](DECISIONS.md).

## Quick start

Needs [uv](https://docs.astral.sh/uv/). uv installs Python 3.11 if you lack it.

```bash
uv sync                                        # create .venv and install
uv run forecast validate examples/config.yaml  # validate the example series
uv run forecast run examples/config.yaml       # backtest the baselines, write the store
uv run pytest                                  # all tests (CI runs these)
uv run pytest -m "not slow"                    # quick loop, about 30 s
uv run ruff check . && uv run ruff format --check .
```

`forecast validate` loads every series in the config, applies every data rule and
prints a ValidationReport per series. Exit code 0 means all passed and a run_id is
printed; 1 means a series broke a rule (the error names it); 2 means the config or a
source could not be read.

`forecast run` validates first, then backtests every series and writes
`<config dir>/runs/<run_id>/forecasts.parquet` (one row per series, window, origin,
model and horizon) plus `manifest.json` (config, data hashes, git, origin plans, the
SMA window chosen on dev origins, row counts, content hash). The same config, data and
code give the same run_id; pass `--force` to recompute.

To validate the real Phase 1 series, put the NSE download at `data/nifty50.csv`
and run `uv run forecast validate configs/phase1.yaml`. The FRED series is
downloaded once into `configs/data/fred/` and read from there afterwards.

## Config

One YAML file, strict keys (unknown keys are errors). Paths are relative to the
config file.

```yaml
seed: 20260922
series:
  - id: nifty50
    source: csv:../data/nifty50.csv   # or fred:SERIES_ID
    columns: {date: Date, value: Close}  # optional; id: <col> for a panel file
    date_format: "%d-%b-%Y"           # optional strftime; inferred if absent
    start: 2000-01-01                 # optional; drop rows before this date
    freq: trading_days                # monthly | quarterly | weekly | trading_days
    season: none                      # int or none; default 12 / 4 / 52 / none
    target: returns                   # level | returns
    positive: false                   # true: any negative value is an error
    duplicates: error                 # error | sum | mean
    H: 20                             # required, no default
    decision_horizons: [1, 5, 20]     # optional, each within 1..H
    # backtest (all optional; defaults depend on freq and target, see DECISIONS.md M2-1)
    transform: none                   # none | log | boxcox | auto (returns: none only)
    window: both                      # expanding | rolling | both
    initial_window: 500
    rolling_length: 500
    origin_step: 5
    n_dev_origins: 20
    n_test_origins: 250
    models: [zero_return, mean_return, last_return, sma]
    sma_windows: [5, 20, 60, 250]
    warmup_models: baselines          # baselines | all
    sarima_search: stepwise           # stepwise | grid
levels: [0.8, 0.95]                   # interval levels stored as lo_80, hi_80, ...
output_dir: runs
n_jobs: 1                             # -1 for all cores; results are identical
```

| Series | Baselines | Statistical models |
|---|---|---|
| Level (`target: level`) | naive, seasonal_naive, drift, sma | ses, ets, sarima, theta, combination (equal-weight ETS + SARIMA + Theta) |
| Returns (`target: returns`) | zero_return (also the price random walk), mean_return, last_return, sma | ar (AR(p), p <= 5 by AICc) |

`sma` runs one model per window and picks the window on dev origins only. For weekly
series (m > 24) ets and sarima run on an STL-adjusted series. Statistical models skip
warm-up origins unless `warmup_models: all`; `sarima_search: grid` fits the full SARIMA
grid instead of the stepwise search.

## Data rules

Validation never changes a value. It refuses data it cannot evaluate honestly.

| Rule | Fails when | Error |
|---|---|---|
| Schema | unique_id, ds or y missing (or a mapped column absent) | MissingColumnError |
| Timestamps | unparseable, timezone-aware, or a trading day with a time of day | TimestampError |
| Target | non-numeric text, or infinite | NonNumericTargetError, NonFiniteTargetError |
| Duplicates | two rows with one timestamp (unless duplicates: sum or mean) | DuplicateTimestampError |
| Frequency | data finer or coarser than the declared freq | IrregularFrequencyError |
| Trading calendar | a row on a weekend; weekdays without a row are flagged, not imputed | TradingCalendarError |
| Gaps | more than 2 consecutive missing values (5 weekdays for trading days) | LongGapError |
| Missing share | more than 5% of values missing | TooManyMissingError |
| Zeros | more than 30% zeros | TooManyZerosError |
| Negatives | any negative value with positive: true | NegativeValueError |
| Returns | a level <= 0 with target: returns | NonPositivePriceError |
| Volume | T below the hard floor; between floor and minimum runs in reduced mode | TooShortError |

Each rule has a bad fixture in `tests/fixtures/` (regenerate with
`uv run python tests/fixtures/make_fixtures.py`).

## Leakage tests

`tests/test_leakage.py` runs on every push:

| Test | What it proves |
|---|---|
| L1 future poisoning | Replacing every value after an origin (1e9, NaN, a permutation) leaves all forecasts, intervals and fold facts unchanged |
| L2 planted leaks | A model that peeks at the next value, and a transform fitted on the whole series, are both caught by L1, and only they are |
| L4 alignment | Origins, targets and periods line up exactly, including trading-day closures and returns across them |
| L5 test quarantine | SMA-window and best-baseline choices are identical when every test row is poisoned |

The harness in `tests/leakage.py` is reused for every new model.

## Layout

```
forecasting/
  config.py           RunConfig, SeriesConfig, load_config, run_id
  errors.py           ConfigError and one DataValidationError subclass per rule
  data/adapters.py    CSV (column mapping) and FRED -> canonical table
  data/validate.py    validate -> [(Series, ValidationReport)]
  transforms.py       Interpolator, OutlierFlagger, Log, BoxCox, auto rule, LogReturn
  models/base.py      Forecaster contract, ForecastResult
  models/baselines.py naive, seasonal naive, drift, SMA; zero, mean and last return
  models/statistical.py  SES, ETS-auto, SARIMA, Theta, AR(p), STL wrapper
  models/combination.py  equal-weight combination
  backtest/splits.py  origins and dev / test roles
  backtest/engine.py  run_backtest: the only code that slices data
  backtest/store.py   ForecastStore (Parquet) and content hash
  backtest/selection.py  SMA window chosen on dev origins
  pipeline.py         validate all, run, manifest
  cli.py              forecast validate | run
tests/
  synthetic.py        seeded DGPs: random walk, local linear trend, seasonal AR(1),
                      trend reversal with variance jump
  fixtures/           one bad CSV per rule
configs/phase1.yaml   the real Phase 1 series
docs/                 spec and research (snapshots of the Claude Docs pages)
examples/             synthetic stand-ins so the CLI runs out of the box
```
