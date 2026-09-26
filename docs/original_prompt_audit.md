# Audit against the original research prompt

The project began with a prompt that asked for a deep research investigation in 17
sections, ending in a justified architecture, and it said in plain terms that the proposed
methodology, metrics and targets were not to be assumed correct. This file checks the
finished work against that prompt rather than against the spec, because the spec
([docs/spec.md](spec.md)) and the research review ([docs/research.md](research.md)) are
themselves outputs of sections 2 and 17 and cannot audit themselves.

Written 26 Sep 2026, at the end of Phase 5.

## The short version

The prompt asked for a design. What exists is the design plus the experiments it called
for, actually run on real data, with the decision rules committed before the data were
scored. On the prompt's central question, whether the proposed system was the right
system, the answer came out negative and is reported as negative: moving averages are one
baseline among several rather than the primary model, the short-term accuracy target was
unreachable in principle on this data because the mean is not forecastable at all, and the
entire value of the work sits in the uncertainty half that the prompt treated as
secondary.

Four things the prompt asked for are genuinely not done, listed at the end with what each
would cost.

## Section by section

| # | The prompt asked | State | Evidence |
|---|---|---|---|
| 1 | Challenge the idea before building | Done, and tested rather than argued | [docs/research.md](research.md) sections on what the idea got right and wrong; the answer to "can moving averages forecast" is the Phase 1 result, where `sma` is a baseline and nothing beats naive |
| 2 | Literature and methodology review, classical to modern | Done | research.md, with a rejected / recommended / deferred table and a trigger for each deferral |
| 3 | Architecture separating 19 named components | 18 of 19 built | Ingestion, validation, missing values, outlier flagging, transforms, trend, seasonality, stationarity, fitting, short and long horizon, residual modelling, probabilistic uncertainty, Monte Carlo, risk metrics, backtesting, evaluation, visualization. Monitoring and recalibration is the exception: nothing is in production to monitor |
| 4 | Multi-horizon framework, horizons derived not chosen | Done | H = 20 with decision horizons 1, 5, 20, derived from data frequency and from what is statistically testable: the origin step has to be at least h for the independence test to mean anything (P2-4, P3-2). h\*, the predictable horizon, is computed from the skill curve's bootstrap interval |
| 5 | Treat MAE 3% and MAPE 35% as hypotheses | Done, both rejected | Replaced by MASE, relative MAE against the best baseline, skill with bootstrap intervals, DM-HLN with the Harvey-Leybourne-Newbold correction, Holm, the Model Confidence Set, and for the distribution: pinball, CRPS, coverage bands, Kupiec, Christoffersen, Winkler. MAPE is not used anywhere: it is undefined on returns, which cross zero |
| 6 | Rigorous backtesting, no random splits | Done, the strongest part | Rolling origin, expanding and rolling windows, per-horizon, forecast-origin evaluation, leakage tests L1 to L5 with planted cheats, a nightly canary on 200 random walks, and the DM test declining itself when n/h < 5 because its false-positive rate there was measured at 9 to 15% against a nominal 5% |
| 7 | Probabilistic model: A residual bootstrap, B parametric, C block bootstrap, D conditional volatility, E Bayesian | A, B, D done. **C investigated and not implemented.** E deferred | Filtered historical simulation from standardised residuals (A), a Normal-tails variant (B), EWMA, GARCH(1,1) and GJR-GARCH(1,1,1) (D). C is the real gap: Ljung-Box rejects on every model's residuals, so the independence that iid resampling assumes is measurably false, and the sampler still resamples iid |
| 8 | Monte Carlo framework, and determine whether 1,000 trials is enough | Done, and the trial count is now measured rather than asserted | 10,000 paths, part of the run_id so it cannot drift. Full trajectory propagation with the variance recursion running along each path, verified against sigma root h for constant variance. The trial-count answer is the table below. Threshold-exceedance probabilities are not produced: see the gaps |
| 9 | Uncertainty growth with horizon | Mostly done | Coverage, interval width, CRPS and pinball all by horizon, across four decisions. PIT is reported as unavailable rather than as a pass, because its smallest nominal bin needs about 1,000 observations and 200 origins cannot fill it. Bias by horizon is not tabulated separately, since the point forecast is fixed at zero |
| 10 | Regime changes, and whether a moving-average system reacts too slowly | Answered empirically, remedies deferred | Yes, and it is the finding that dominates Phases 3 to 5: a model selected in a volatile window over-covers when it meets a calm one, on two independent markets. Rolling versus expanding windows and EWMA are the adaptive mechanisms present. Change-point detection and regime switching are deferred with a trigger; online recalibration is not built |
| 11 | Implementable architecture, no unnecessary dependencies | Done, with a different module layout and the reason recorded | uv, ruff, pytest, GitHub Actions. Dependencies: numpy, pandas, pyarrow, scipy, statsmodels, matplotlib, pyyaml, joblib, arch, threadpoolctl. No PyMC, no scikit-learn, no seaborn, because nothing needed them |
| 12 | Pseudocode before implementation | Done | spec.md, "Algorithms" |
| 13 | Mathematical formulation, and four kinds of uncertainty distinguished | Formulae done. **Three of the four kinds are handled** | Every estimator is written out in spec.md and the README. Irreducible and forecast uncertainty are modelled, model uncertainty is addressed by the Model Confidence Set and by running a family rather than one model, and parameter uncertainty is not propagated: the GARCH parameters enter each simulation as point estimates |
| 14 | Experiments 1 to 8, progressively more sophisticated | 7 of 8 run | Naive, moving average, exponential smoothing, trend plus seasonality, SARIMA, probabilistic, probabilistic plus Monte Carlo. Experiment 8, a modern model, is deliberately not run: its trigger in the research review is covariates or a panel, and a tree or a transformer on a univariate index with no covariates has nothing to learn from that a GARCH does not already give it |
| 15 | A defensible definition of success | Done, and it held against its author | Pre-registered, hashed, committed before scoring, six times. It produced one NO-GO that could easily have been argued away (P3, where the significance test passed on the cell the band failed), and it recorded two of my own registered predictions as wrong (P3's sharpness prediction, P5b's whole expectation) |
| 16 | Failure modes, detection and response | Done | Leakage (tested with planted cheats), non-stationarity, seasonality, structural change (found), heavy tails (empirical residuals), correlated residuals (detected, not yet fixed: gap C), missing and sparse data (validation rules with fixtures), outliers (flagged, never replaced), insufficient history (reduced mode with stated interval widths), horizon beyond the predictable range (h\*) |
| 17 | Final recommended design, 23 sections | Done | spec.md and research.md, snapshotted in the repo at spec revision 28 and research revision 17 |

## Section 8's question, answered with numbers

The prompt proposed 1,000 trials and asked whether substantially more are necessary. The
same fitted GJR-GARCH was simulated 30 times at each path count, changing only the RNG
seed, so the spread is pure simulation noise with the model held fixed. Quantities are
20-day cumulative log returns.

| Paths | Noise on a 95% band edge (95% CI) | As a share of band width | Noise on 5% expected shortfall | As a share of ES |
|---|---|---|---|---|
| 1,000 | ±0.0105 | 7.60% | ±0.0124 | **14.86%** |
| 10,000 | ±0.0033 | 2.36% | ±0.0039 | 4.72% |
| 100,000 | ±0.0009 | 0.67% | ±0.0013 | 1.57% |

Read against what this project decided, the conclusion is specific:

- **1,000 is not enough for anything published per origin.** A 20-day expected shortfall
  quoted from 1,000 paths carries about 15% of its own value in simulation noise. The
  original proposal's trial count would have made the risk table's headline number mostly
  sampling error.
- **10,000, the registered count, is adequate for the calibration decisions** but only
  because they average over 200 test origins with an independent stream per origin, which
  divides the per-origin noise by roughly the square root of 200. Without that averaging it
  would not be: P4's width correction at h = 20 was 1.8%, which is smaller than the 2.4%
  per-origin noise on a band edge.
- **100,000 is what a single origin's expected shortfall needs** to be quotable to within
  about 2%, which is the regime anyone using the fan chart for one date is in.

`scripts/mc_standard_error.py` reproduces the table.

## What is not done

| Gap | Prompt section | Why it matters | Cost |
|---|---|---|---|
| **Block bootstrap for the residual sampler** | 7C | The largest hole. The prompt asked whether temporal dependence makes independent residual sampling inappropriate. It was investigated, the answer is yes on this data (Ljung-Box rejects on every model's residuals), and the simulator still resamples standardised residuals independently. The conditional variance model absorbs part of the dependence, which is why calibration passes, and part is not absorbed | A modelling change, so it needs its own pre-registration rather than a patch. Half a session |
| **Parameter uncertainty is not propagated** | 13 | The prompt asked for four kinds of uncertainty to be distinguished. Three are. GARCH parameters enter each simulated path as point estimates, so the published intervals are conditional on the fit being exactly right, and are therefore slightly too narrow by an unmeasured amount | A parameter bootstrap over refits, or the Bayesian route the research review deferred. A session, and expensive to run |
| **Threshold-exceedance probabilities** | 8 | The prompt asked for the probability of exceeding or falling below a threshold. The report publishes quantiles, VaR and ES but no threshold probabilities, because the paths are summarised and discarded at each origin | Small, but it needs an engine change to carry the extra statistic, or a named threshold to compute against |
| **Monitoring and recalibration** | 3, component 19 | Not built. Nothing is deployed, so there is nothing to monitor, and the nightly canary covers the only thing that degrades silently | Out of scope until something runs continuously |

Three further items are deferred rather than missing, each with a trigger written down
before the fact: a modern model (experiment 8) waits on covariates or a panel; Bayesian and
state-space methods wait on a short series where their advantage is real; change-point
detection and regime switching wait on repeated regimes rather than the single transitions
the samples contain.

## The honest summary

The prompt's goal was "a statistically defensible, empirically validated, robust
multi-horizon forecasting and uncertainty-analysis system", explicitly not the proposed
MAE and MAPE numbers. What it got:

- Defensible: every decision rule was written and hashed before the data it judged were
  scored, and the one that failed was not rewritten.
- Empirically validated: on two markets, with a synthetic positive control whose right
  answer is known in advance, and with an acceptance suite that includes planted cheats.
- Multi-horizon: 1, 5 and 20 days, chosen from testability rather than taste.
- Robust: not established. Two equity indices sharing the same crises is not a robustness
  claim, and the residual sampler still assumes an independence that the data reject.

The proposed system, as proposed, does not work, and the reason is the first thing the
prompt told us to check: moving averages describe the past and the mean of a stock index
is not forecastable from it. What survives is the second half of the idea, that the useful
output is a distribution rather than a number, and that half now has a working, tested,
calibrated implementation whose limits are written down.
