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
Phase 1 result: [docs/gate_decision.md](docs/gate_decision.md) (NO-GO on both series under
the pre-registered rules; the report is in docs/phase1_report/).

## Quick start

Needs [uv](https://docs.astral.sh/uv/). uv installs Python 3.11 if you lack it.

```bash
uv sync                                        # create .venv and install
uv run forecast validate examples/config.yaml  # validate the example series
uv run forecast run examples/config.yaml       # backtest every model, write store + report
uv run forecast report examples/runs/<run_id>  # rebuild the report from the store, no refits
uv run pytest                                  # all tests (CI runs these), about 10 min
uv run pytest -m "not slow"                    # quick loop, about 40 s
uv run pytest -m canary -s                     # L3 with every model: hours; runs nightly
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

## The report

`forecast run` ends by writing `runs/<run_id>/report/report.md` with three PNGs per
series in `report/figures/`: relative MAE by horizon, the skill curve with its CI and
h*, and interval coverage with binomial bands. `forecast report RUN_DIR` (or the
config) rebuilds it from the store and manifest; nothing is refitted.

For every series the report gives a verdict on each research question, with the
tables behind it, and applies the Phase 1 exit table (provisional until gate.yaml is
enforced in M8):

| Question | Answered by |
|---|---|
| RQ1 does any model beat the best baseline? | Relative MAE, DM-HLN (Holm over every candidate and test horizon), MCS, per h |
| RQ2 predictable horizon h* | Skill SS(h) with a moving-block bootstrap CI |
| RQ3 are intervals calibrated? | Coverage per bucket with binomial bands, Kupiec |
| RQ4 are residuals autocorrelated or heteroskedastic? | Share of test origins where Ljung-Box or ARCH-LM rejects |
| RQ5 stable, or does rolling beat expanding? | Rolling vs expanding MAE with DM-HLN; four sub-periods |
| RQ6 is a transform needed? | The transform chosen per test fold, with counts |

Only test origins with status ok and a present actual are scored. "sma" is the
dev-chosen window, the reference is the dev-chosen best baseline, and where a question
needs one model the report speaks for the dev-chosen best candidate. From Python:

```python
from forecasting.evaluation.scoring import score_run

s = score_run("examples/runs/<run_id>")
s.point  # per series, window, model, h: MAE, MASE, relative MAE, DM-HLN, Holm, MCS
s.skill, s.h_star  # SS(h) with its CI; predictable horizon per model
s.intervals, s.interval_buckets  # coverage with binomial band, Kupiec, Winkler
s.residuals, s.window_gap, s.subperiods, s.transforms  # RQ4 to RQ6
```

## The gate (P1)

`gate.yaml` next to the run config is the pre-registration: decision horizons and model
list per series, the primary window, alpha and the thresholds (relative MAE below 1 up
to h*, coverage tolerances per bucket, sub-periods, the leakage-audit gain). It must be
committed before the first run that scores test origins on real data. `forecast run`
refuses a gate that does not match the config, records the gate's sha256 and git commit
in the manifest, and the report issues the A10 decision (GO, GO restricted, NO-GO, AUDIT
FIRST) only when the file was committed before the run and is unchanged since. Without
that, every exit decision stays provisional. `configs/gate.yaml` is the Phase 1 gate.

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

## Phase 1 data

| File | What it is |
|---|---|
| `data/nifty50_nse_raw.csv` | every row niftyindices.com returns, 8,807 rows, 03 Jul 1990 to 22 Sep 2026 |
| `data/nifty50.csv` | the analysis file: the same rows minus 46 weekend sessions (Muhurat, budget Saturdays, live test sessions) |
| `configs/data/fred/IPG2211A2N.csv` | the FRED cache for the electricity control, committed so a run reproduces off this machine |

`configs/phase1.yaml` reads the Nifty file from `1996-01-01` (the index base date is
03 Nov 1995; earlier values are back-computed and the early 1990s have multi-week gaps).

To refresh the Nifty history:

```bash
uv run python scripts/fetch_nifty50.py
```

The site's form refuses ranges longer than a year, so the script walks the history one
calendar year at a time and stitches the years together. The endpoint is IP restricted:
run it from an ordinary connection, not a data centre or a VPN, or it returns HTML
instead of JSON and the script stops with that message. Refreshing the data changes the
run_id, so re-register the gate if a run has already been scored.

## Leakage tests

`tests/test_leakage.py` runs on every push:

| Test | What it proves |
|---|---|
| L1 future poisoning | Replacing every value after an origin (1e9, NaN, a permutation) leaves all forecasts, intervals and fold facts unchanged |
| L2 planted leaks | A model that peeks at the next value, and a transform fitted on the whole series, are both caught by L1, and only they are |
| L4 alignment | Origins, targets and periods line up exactly, including trading-day closures and returns across them |
| L5 test quarantine | SMA-window and best-baseline choices are identical when every test row is poisoned; every score table is identical when every dev and warm-up row is poisoned (tests/test_scoring.py) |

The harness in `tests/leakage.py` is reused for every new model.

`tests/test_canary.py` is L3, the random-walk canary: 200 simulated random walks, each
through a full backtest; the share of series where any model "beats naive" (Holm-adjusted
one-sided DM-HLN at the decision horizons) must not exceed the 5% false-positive rate,
and naive must stay in the Model Confidence Set as often as a best model should. The
cheap model set runs with the slow tests; the full set (ETS, SARIMA, combination) runs
nightly (`.github/workflows/nightly.yml`) or by hand with `-m canary`.

## Acceptance checks

`tests/test_acceptance.py` runs three processes with known answers (research 9.2)
through every level model: on the seasonal AR(1) only seasonal models beat seasonal
naive; on the local linear trend ETS beats naive from h = m / 3 on; on a random walk
nothing shows skill (A3). The same runs check that every (origin, model, h) cell has a
row or a typed failure with under 1% failures (A4), that the report answers RQ1 to RQ6
(A5 to A8) and that a 12-year monthly backtest with every model stays well under ten
minutes (A9). `tests/test_simulation_conformance.py` checks the statsmodels path
primitive Phase 2 will build on: 10,000 simulated ETS and SARIMA paths match the
analytic point forecast and 80% bounds within 3 Monte Carlo standard errors.

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
  backtest/selection.py  SMA window, best baseline and best candidate, dev origins only
  evaluation/metrics.py  MAE, RMSE, MASE, relative MAE, WAPE, sMAPE, MAPE, bias,
                      coverage and band, Winkler, directional accuracy, OOS R^2, buckets
  evaluation/tests.py DM-HLN, Holm, Kupiec, Pesaran-Timmermann, MCS, skill bootstrap, h*
  evaluation/scoring.py  score / score_run: tidy per-horizon tables from a run
  evaluation/diagnostics.py  Ljung-Box, ARCH-LM, window gap, sub-periods, transforms
  evaluation/report.py  RQ1-RQ6 verdicts, exit decision, tables, 3 plots, markdown
  gate.py             gate.yaml: load, match the config, committed status (P1)
  pipeline.py         validate all, run, manifest
  cli.py              forecast validate | run | report
tests/
  synthetic.py        seeded DGPs: random walk, local linear trend, seasonal AR(1),
                      trend reversal with variance jump
  acceptance.py       in-memory synthetic backtests and the "beats naive" statistic (A3, L3)
  fixtures/           one bad CSV per rule
configs/phase1.yaml   the real Phase 1 series
configs/gate.yaml     the Phase 1 pre-registration (P1)
data/                 the Nifty 50 history (raw and analysis files)
scripts/fetch_nifty50.py  re-download the Nifty 50 history from niftyindices.com
docs/                 spec and research (snapshots of the Claude Docs pages)
examples/             synthetic stand-ins so the CLI runs out of the box
```
