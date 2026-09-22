# Decisions

The source of truth is the spec ("Forecasting System: Roadmap and Phase 1
Implementation Specification") and the research review. Where they conflict, the
spec's "Scope update: Nifty 50" section wins. This file records the decisions so
each weekend session can start from the repo alone. Read it before any milestone.

## Status

| Milestone | Scope | State |
|---|---|---|
| M1 | Repo, config, adapters, validation, fixtures, synthetic DGPs, `forecast validate` | Done (22 Sep 2026) |
| M2 | Transforms (incl. LogReturn), return and level baselines, origins, engine, store, `forecast run` | Done (22 Sep 2026) |
| M3 | Leakage tests L1, L2, L4, L5 | Done (22 Sep 2026) |
| M4 | Statistical models (AR(p) on returns; SES, ETS, SARIMA, Theta, STL wrapper for the control), combination | Done (22 Sep 2026) |
| M5 | Metrics and tests (DM-HLN, Holm, Kupiec, MCS, Pesaran-Timmermann, skill bootstrap) | Next |
| M6 | Diagnostics, report, CLI `run` and `report` | |
| M7 | Known-answer acceptance, L3 canary, simulation conformance | |
| M8 | Real data run, gate.yaml (P1), go/no-go | |

## Scope update (22 Sep 2026)

| # | Decision | Why |
|---|---|---|
| U1 | Primary series: Nifty 50, daily trading days | Home market; an index avoids survivorship bias |
| U2 | Positive control: US electric and gas utilities output (FRED IPG2211A2N), monthly | Real seasonal structure; if clever models do not win here, the harness is broken |
| U3 | Replication target (later): S&P 500 | Checks whether Nifty findings hold in a second market |
| U4 | Forecast returns for stocks, not price levels; price forecasts are derived from returns | Prices are close to a random walk; returns are the scale where models and tests are valid |
| U5 | Expected Phase 1 result for Nifty: nothing beats the zero-return forecast on the mean. A valid finding, not a failure | Consistent with the research and the random-walk canary |
| U6 | GARCH is a core Phase 2 component for return series | Volatility clustering is the most reliably forecastable feature of returns |
| U7 | Not a trading system: no position sizing, signals or trading costs | Keeps the project a forecasting and risk study; not financial advice |

## Technical decisions fixed before coding

| # | Decision | Why | Reopen if |
|---|---|---|---|
| D1 | Long-format data everywhere: unique_id, ds, y | A single series is a panel of one; avoids a rewrite | Never |
| D2 | statsmodels as the model backend (ETSModel, SARIMAX, ThetaModel, STL) | Phase 2 needs simulate() from the fitted state-space model | More than about 100 series or backtests over 1 hour: switch fitting to statsforecast |
| D3 | Own model selection: ETS by AICc; SARIMA over a bounded grid (p, q <= 2; P, Q <= 1; d, D from KPSS and seasonal tests) | Transparent, testable, no auto-ARIMA dependency | Grid misses clearly better orders |
| D4 | Leakage prevented by the API: every data-dependent step is fit(train) / transform(x); only the engine slices data | Makes the most common forecasting bug structurally hard | Never |
| D5 | One forecast record store (Parquet), a row per series, origin, model, horizon | Re-scoring never requires refitting | Store exceeds memory: partition by model |
| D6 | Config is one YAML file loaded into a frozen dataclass; run_id = hash of config + data hash + git commit | Every result is reproducible and traceable | Never |
| D7 | Randomness: one numpy SeedSequence per run, spawned per (origin, model) | Identical results in any order or when parallelised | Never |
| D8 | Model on the transformed scale; back-transform paths and quantiles, never the mean | Quantiles survive monotone transforms; means do not | Zeros or negatives: no transform, or log1p with review |
| D9 | Horizon buckets relative to m; h* computed from results, never hard-coded | Horizons come from data and decisions | A mandatory horizon is added |
| D10 | Python 3.11+, uv, pytest, ruff, GitHub Actions; joblib for parallelism; notebooks never imported | Standard, light, fast CI | Never |

## M1 implementation decisions

Choices the spec left open or where two parts of it disagree. Each says where it lives.

| # | Decision | Why |
|---|---|---|
| M1-1 | Config shape: top-level `seed` and a `series` list; each series carries its own labels (id, source, columns, date_format, start, freq, season, target, positive, duplicates, H, decision_horizons). Backtest keys (window, origins, levels, models) are added in M2. Unknown keys are always rejected | Per-series labels are the scope update's schema; strict keys mean a typo never falls back to a default (config.py) |
| M1-2 | H is required with no default. H <= 3m is enforced only when m > 1; H < m warns | The scope update gives Nifty m = 1 and H = 20, which "H <= 3m" would forbid. The spec's volume table also uses H = 12 with m = 1 |
| M1-3 | Hard floor uses the formula max(2m, 12) + H + 20. For quarterly H = 8 that is 40, not the 36 in the spec's table | The table and formula disagree; the formula matches the other three rows, so it wins |
| M1-4 | Trading-day calendar: no holiday list. A weekday with no row is flagged in the report, never inserted or imputed, and does not count as missing. Weekend rows are an error. More than 5 consecutive weekdays without a value is a long gap; more than 15% of weekdays without a row means the data is not daily | Scope update: closures are not missing data. NSE closures are single days or short runs; 15% leaves room for about 12 to 16 holidays a year (validate.py constants) |
| M1-5 | Trading-day volume: minimum = 500 + 5 x (20 dev + 250 test - 1) + H = 1,865 for H = 20; floor = 250 + 5 x 20 + H = 370 | Minimum follows the scope update (500 initial, 250 test origins, step 5). The floor mirrors the periodic floor: a reduced initial window, 20 origins, H |
| M1-6 | target: returns validates the price level. Levels must be > 0; T counts returns (rows - 1). Log returns are computed inside each fold (LogReturn, M2) | Returns computed before slicing would be a transform fitted outside the fold |
| M1-7 | Frequency rule: after normalising to periods, two distinct dates in one period is "finer than declared"; a modal step other than 1 is "coarser than declared". pd.infer_freq is not used | infer_freq returns None whenever there is any gap, and short gaps are allowed |
| M1-8 | Duplicate aggregation (sum or mean, opt-in) is the only change to values validation ever makes | Spec: error unless the config says sum or mean |
| M1-9 | Only empty cells (and FRED's '.') are missing. Text such as "NA" or "nan" is a non-numeric error | Keeps "never changes values" literal: nothing is guessed |
| M1-10 | Adapters never parse or clean; they rename mapped columns and pass extras through. validate is the single place that parses and decides | One place owns every rule |
| M1-11 | Timezone-aware timestamps and trading-day timestamps with a time of day are errors, not converted | Close-time misalignment is a leakage source in the scope update |
| M1-12 | `start` drops rows before a date. It is the fix the long-gap error asks for | Spec: "error, asking for a start date after the gap" |
| M1-13 | run_id = first 16 hex of sha256(canonical config JSON without base_dir, sorted per-series data hashes, git sha with "-dirty" when the tree has changes). Issued only when every series passes | D6; a dirty tree must not look reproducible |
| M1-14 | FRED: fetched once into `<config dir>/data/fred/<ID>.csv` (gitignored), then always read from the cache. The fetcher is injectable; tests run offline | Data hash changes only on a deliberate refresh. FRED is not reachable from the dev VM. Vintages are not handled (leakage source 16) |
| M1-15 | Runtime dependencies in M1: numpy, pandas, pyyaml. scipy, statsmodels, pyarrow, matplotlib and joblib arrive with the milestone that uses them | Nothing unused in the lock file |
| M1-16 | Synthetic "seasonal AR(1)": fixed sine profile (amplitude 10, period m) plus AR(1) noise (phi 0.5) around 100. Trend reversal: slope +0.5 to -0.5 at 70% of the series, noise SD x 2.5 | Research 9.1 and 9.2; the seasonal profile makes "only seasonal models beat seasonal naive" a sharp known answer |
| M1-17 | The em dash is banned in every file; tests/test_style.py fails on one | Author's style rule |

## M2 implementation decisions

| # | Decision | Why |
|---|---|---|
| M2-1 | Backtest keys live on each series, with defaults by freq and target. Periodic: initial and rolling window max(3m, 24), step 1, 20 dev, 30 test, transform auto. Trading days: 500, 500, step 5, 20 dev, 250 test, transform none. Top level: levels (default 0.8, 0.95), output_dir, n_jobs. output_dir and n_jobs are left out of run_id | Scope update sets different designs for daily returns and monthly levels; paths and parallelism never change results (D7) |
| M2-2 | Baselines run on the untransformed target scale. The fold's variance transform (auto, log, Box-Cox) is fitted per fold only for models that set uses_transform, which M4's statistical models will | Baselines are the reference floor as the literature defines them. Naive and seasonal naive are unchanged by a log anyway; drift and SMA would silently become geometric |
| M2-3 | Return series: the model sees log returns r_1..r_t computed inside the fold from the slice's prices (after interpolation). y_true and y_pred are the per-period log return at t + h. level_true and level_pred carry prices, with level_pred = p_t exp(cumsum r_hat); the cumulative h-day return is ln(level / p_t) and can be derived from the store | U4: model returns, derive prices. Keeping both scales means M5 can score day, week and month returns without a refit |
| M2-4 | The price random walk is the zero-return forecast in log space (p_hat = p_t means r_hat = 0). It is stored once, as zero_return; its level_pred column is the random-walk price | Two models with identical forecasts would distort the Model Confidence Set and Holm corrections |
| M2-5 | Historical mean return is the mean over the training slice: expanding in the expanding window, rolling in the rolling one | Same slice rule as every other model |
| M2-6 | Auto transform: the spread in each window is the SD of first differences inside the window, not the SD of raw values | With raw values a trend inside each window swamps the SD and the rule misses multiplicative noise (found by test_auto_picks_log_when_spread_grows_with_level) |
| M2-7 | SMA candidates: {3, 6, m, 2m} with k >= 2 (SMA(1) is naive), so {2, 3, 6} when m = 1; trading days {5, 20, 60, 250}. Windows longer than the initial window are dropped. Every candidate runs at every origin as sma_k; k is chosen per (series, window) by mean MASE over all h on dev origins, ties to the smaller k, recorded in manifest.json. Later milestones read "sma" as that sma_k | Spec: k chosen on dev only, then frozen. Selecting from the store makes the choice testable against poisoned test rows (L5) |
| M2-8 | Origins are anchored at the end (last t = n - 1 - H) and step back by origin_step. Reduced mode first shortens the initial window (down to the floor) to keep full dev and test counts, and only then shrinks dev and test proportionally | Keeps as many scored origins as possible; the counts match validate's thresholds exactly (test_splits) |
| M2-9 | Outlier flags use a trailing window of max(2m, 20) | 2m is 2 for m = 1, which makes MAD meaningless |
| M2-10 | Store columns beyond the spec: level_true, level_pred, mase_scale (from each origin's own slice), n_train, n_outliers. Periods are ISO strings; missing floats are Parquet nulls; no pandas metadata. status is ok, failed or skipped (skipped: the origin's last value is missing) | Everything the report needs without refitting (D5); A4 needs a row or a typed failure for every cell |
| M2-11 | Reproducibility (A1) is checked on a content hash that leaves out fit_seconds, stored in manifest.json | Timing differs run to run, so Parquet bytes cannot match; spec already excludes timing columns |
| M2-12 | Runs go to <config dir>/<output_dir>/<run_id>/ (forecasts.parquet, manifest.json). An existing run_id is reused unless --force. A dirty git tree now adds a hash of the diff to the git string | Same inputs and code give the same directory; two different dirty trees must not share a run_id |
| M2-13 | gate.yaml is looked for next to the config. The manifest records whether it exists and its sha256; `forecast run` says the run is exploratory without it. Enforcing P1 is M8 | Real data can be backtested before P1 without pretending the results are pre-registered |
| M2-14 | Intervals are Normal with the fpp3 residual SD (divide by n - K). Return baselines have a constant SD across h, because each h is a one-period return | Spec baseline formulas; calibration is judged in M5 and Phase 2 |
| M2-15 | Parallelism: joblib (loky) over folds when n_jobs is not 1; a test checks serial and parallel stores match. M2 models use no random numbers, so SeedSequence wiring (D7) arrives with the first stochastic component | Order-independent results without code that nothing uses yet |
| M2-16 | Only FitError, TransformError and ForecastContractError become failed rows. NotFittedError and any other exception propagate | Spec: model failures are data; programming errors abort |

## M3 implementation decisions

| # | Decision | Why |
|---|---|---|
| M3-1 | The L1 harness lives in tests/leakage.py (poison, l1_check, the planted leaks) and is reused by later milestones. It compares every column except truth (y_true, y_true_missing, level_true) and timing: predictions, bounds, level_pred, mase_scale, n_train, n_outliers, transform, variant, status, error | A leak can surface in any of them, including a status flipping from ok to failed |
| M3-2 | L1 samples 10 origins with a fixed seed across all roles, always including the last, and runs both windows and all three poisons at each. Series covered: a seasonal level series with interior gaps under boxcox, auto and log (with test-only models that use the transform), and a trading-day return series with closures | Covers interpolation, per-fold transform fitting, the rolling slice and the return path |
| M3-3 | Transform modes are a registry (transforms.TRANSFORM_BUILDERS). Config still accepts only none, log, boxcox and auto; the leaky GlobalZScore is registered only inside its test | Spec L2: planted components exist only in the test suite |
| M3-4 | L2's transform leak is observed through a model that forecasts 0 on the transformed scale. Naive and drift are unchanged by an affine transform, so they would hide a leaky z-score and L2 would pass for the wrong reason. Controls: the same model is clean with a slice-fitted transform, and each L2 run flags only the planted component | L2 must prove L1 can fail, not merely that it ran |
| M3-5 | Best-baseline selection exists now (L5 needs it): per (series, window), the lowest mean dev MASE over all h among the baselines, with SMA entered once as its dev-chosen sma_k; ties go alphabetically. Recorded in manifest.json. M5 may refine it to per-horizon if the report needs that | Leakage source 11: the reference for relative MAE is chosen on dev only |
| M3-6 | L5 poisons every numeric column of the test rows (NaN, 1e9, -1e9, 0) and requires identical SMA and best-baseline choices. A positive control shows that poisoning dev rows does change the choice | Without the control, L5 could pass vacuously |
| M3-7 | A return forecast whose implied price path overflows is a ForecastContractError | Found while building L2: an "ok" row carried an infinite level_pred |
| M3-8 | Manual mutation check (not in CI), run 22 Sep 2026. Four leaks planted in the real engine all fail the leakage suite: slice includes t + 1 (10 tests fail), MASE scale from the full series (8), Box-Cox lambda seeing the next value (1), selection reading every role (4). The unmodified code passes all 17 | Evidence that the suite guards the engine itself, not only the planted test models. Worth repeating after engine changes |

L3 (random-walk canary) is M7; P1 (gate.yaml pre-registration) is M8.

## M4 implementation decisions

| # | Decision | Why |
|---|---|---|
| M4-1 | Level series get SES, ETS-auto, SARIMA, Theta and the combination; return series get AR(p). For m > 24 (weekly), ets and sarima keep their names but run on an STL-adjusted series (STLAdjusted). ETS, SARIMA and Theta are not offered for returns | Scope update: they are pointless on returns. One name per model keeps the store and the combination simple |
| M4-2 | SARIMA searches the spec's bounded space (p, q <= 2; P, Q <= 1) stepwise, Hyndman-Khandakar style, by default; `sarima_search: grid` fits all 36. Measured on 120-month synthetic series: grid 1.6 to 7.2 s per fold, stepwise 0.7 to 1.7 s with 10 to 14 fits; same order on 3 of 4 processes, 2.7 AICc worse on the trend reversal | The full grid alone would take about 11 minutes for a 10-year monthly backtest, past A9's 10. D3's reopen trigger applies if stepwise misses clearly better orders |
| M4-3 | Expensive models (every statistical model and the combination) skip warm-up origins by default (`warmup_models: baselines`); baselines still run everywhere. The design for A4 is therefore baselines at all origins, statistical models at dev and test origins | Warm-up rows are never scored. On the real FRED series (from 1939) they would be about 90% of the statistical fitting cost |
| M4-4 | The combination is formed by the engine from the members already fitted at the fold, on the fold's transformed scale, then back-transformed. A member failure leaves the mean of the rest (the variant says which failed); all failing is a failed row. Config refuses `combination` without all three members. EqualWeight is the same rule as a standalone Forecaster; a test checks the two agree to 1e-10 | No refits; membership is fixed in code before any run (leakage source 11) |
| M4-5 | The variance transform is fitted once per fold and shared by every model that uses one | Same result as before, computed once, and it guarantees combination members share one scale |
| M4-6 | D7 is wired: each (series, window, origin, model) gets a seed from SeedSequence(seed, spawn_key = sha256 of those four). The only random step in Phase 1 is ETS's simulated intervals for multiplicative models | Order-independent, reproducible intervals; poisoned L1 runs get the same seeds as clean ones |
| M4-7 | Both statsmodels pitfalls from the research are handled: models are always fitted on a pandas Series (array input breaks ETS prediction), and ETS simulation is seeded through `rng` (it has no `random_state`). Warnings are silenced inside fits; numerical exceptions become FitError rows | Found again in the M4 probe; the spec predicted both |
| M4-8 | SARIMA: D = 1 when the slice's robust-STL seasonal strength exceeds 0.64 (fpp3 nsdiffs); d by repeated KPSS at 5%, up to 2, on the seasonally differenced slice; trend 'c' only if d + D <= 1 (in statsmodels 'c' with d = 1 is the drift, verified); seasonal part only when 1 < m <= 24 and there are at least 2m + 1 points | Spec model table and D3; all tests run inside the fold (leakage source 8) |
| M4-9 | ETS: error {A, M}, trend {N, A, Ad}, season {N, A, M}; additive error with multiplicative season is excluded (unstable, as in fpp3 and forecast::ets); multiplicative components only if the (transformed) slice is positive; season only when m <= 24 and n >= 2m. Up to 15 variants per fold | Spec model table |
| M4-10 | Theta: statsmodels ThetaModel with theta = 2; deseasonalises when m > 1, n >= 2m and its own 90% ACF test passes; multiplicative if positive, else additive | Spec model table |
| M4-11 | AR(p): constant always, p in 0..5 by AICc on a common sample (first 5 points held back for every p), then refitted on the whole slice. AR(0) is the mean-return model | Scope update; a common sample makes AICc comparable across p |
| M4-12 | STL wrapper: robust STL on the slice; the seasonal component is continued with seasonal naive; bounds are shifted by it and seasonal uncertainty is not added (the STLForecast rule) | Spec: seasonal ETS and SARIMA are impractical at m = 52 |
| M4-13 | Tests: 6 statsmodels-heavy tests carry a `slow` marker. CI runs everything; `uv run pytest -m "not slow"` is the quick local loop (about 30 s). L1 now covers every model: monthly level models with the auto transform and a gap (10 origins, both windows), STL-wrapped weekly models, and AR on returns. The L1 harness can run in parallel (n_jobs) | The spec's 30 s budget is for unit tests; L1 on every model takes about a minute on 4 cores |
| M4-14 | A9 measured 22 Sep 2026: a 120-month synthetic series with every level model, both windows, 73 origins (23 warm-up), one thread: 6.5 minutes, 18,264 rows, 0 failures. SARIMA is 70% of the time, ETS 28%. The example config (both series, every model) runs in 7 minutes | Passes A9 (under 10 minutes) and A4 (failures under 1%). Rerun on real data: fits on longer series are slower |

## Notes for M5

- Score only test rows (dev for selection), status ok, y_true present. Warm-up rows exist only for baselines (M4-3).
- "sma" means the dev-chosen sma_k and the reference for relative MAE is `select_best_baseline` (both in manifest.json). M5 may move to a per-horizon best baseline if the report needs it (M3-5).
- MAPE and sMAPE are off for returns; returns also get directional accuracy with the Pesaran-Timmermann test and out-of-sample R^2 against the historical mean (scope update). Cumulative h-day returns come from level_true / level_pred (M2-3).
- The combination has no intervals, so coverage, Winkler and Kupiec skip it.
- The effective sample at horizon h is about n / h; print both next to every statistic (spec: Horizons).

## Open items for later milestones

- NSE holiday calendar: optional, would turn flagged weekdays into known closures. Not needed while flags are reported.
- Nifty source: price index vs. TRI. Phase 1 uses the price index and the report must say so (scope update).
- gate.yaml (P1) must be committed before the first run on real data that includes test origins (M8). `forecast run` reports whether it exists.
- Real electricity data starts in 1939. Statistical models only fit at dev and test origins (M4-3), but fits on ~1,000 points are slower; `start:` can shorten the history if a run is too slow.
