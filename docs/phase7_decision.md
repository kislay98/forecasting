# Phase 7 decision (parameter uncertainty in the GARCH simulation, OP-3)

Written 26 Sep 2026 from run `0b77275f8e2b5f53` (git `aedb84903990`, config
`configs/phase7/phase7.yaml`, gate `configs/phase7/gate.yaml` sha256 `237e8be9eaaa`,
committed as `aedb84903990` before the run) and, for the S&P 500, run `912cedb84dabf730`
(config `configs/phase7b/phase7b.yaml`, gate sha256 `b3df56cd2577`, same commit). The
registered rules were applied as written; nothing in them changed after a test origin was
scored.

| Series | Primary model | Gate decision | Adoption rule | Width the published intervals were missing at h = 20 |
|---|---|---|---|---|
| nifty50 | gjr_garch_pu | **GO**, 21 of 21 | **Do not adopt** | **none measurable**: -0.2% at 80%, -0.2% at 95%, -0.1% at 99% |
| sp500 (P7b) | gjr_garch_pu | **GO**, 21 of 21 | **Do not adopt** | +0.0% at 80%, +0.5% at 95%, +1.5% at 99%, mostly truncation at a bound |

Widths are the paired measurement (same innovations, parameters drawn or not), mean over
the 200 test origins of the expanding window.

The answer to OP-3: propagating parameter uncertainty does not make the Nifty intervals
wider. On average it changes them by less than a quarter of a percent in either direction,
an order of magnitude below the simulation noise already in every published interval
(2.4% of band width per origin at 10,000 paths, OP-1). No registered check changes on
either market. The point models stay as published, and OP-3 closes as measured rather than
as fixed.

OP-3 said the published intervals were too narrow "by an unmeasured amount", and that
the sign of that error was known. The size is now measured, and the sign was
not known: on Nifty it is slightly negative at the central levels. The reason is the most
useful thing this phase found, and it is set out below.

## What was measured, and how

`gjr_garch_pu` fits exactly as `gjr_garch` does. At simulation it draws one parameter
vector per path from a multivariate Normal centred on the estimate with arch's
asymptotic covariance (`param_cov`, the robust sandwich), rejects draws that break
positivity or stationarity, refilters the in-sample variance with each draw so that the
starting variance sigma_t+1|t is the one that draw implies, and runs the same recursion.
`garch_pu` is the same for GARCH(1,1). With a zero covariance both reproduce their point
model bit for bit, which a test pins.

The five existing models were not touched, and that is checked rather than asserted, on
one machine (P7-2). The Phase 2 store before and after the code change is identical in
all 39 value columns over 242,040 rows, with the same content hash. The point models'
rows in the P7 and P7b stores equal P4 and P5b runs made from the pre-change code: 38,200
and 38,440 rows, every value column identical.

Two measurements, both on the expanding window.

- **Paired.** At each test origin the point model is refitted and simulated twice with
  the same 10,000 innovation paths, once with the fitted parameters and once with draws.
  The seeds are the engine's, so the point paths are exactly the ones in the store. The
  width ratio at an origin is then the parameter effect with no innovation noise in it.
- **Store.** The registered run, where each model has its own innovation stream, re-scored
  with every registered check applied to each model in turn, with and without the
  conformal layer.

`scripts/phase7_widening.py` produces both; the full tables are in
[phase7_report/widening.md](phase7_report/widening.md) and
[phase7b_report/widening.md](phase7b_report/widening.md).

## The widening, Nifty 50

Mean ratio of interval width with draws over without, 200 test origins.

| Horizon | 80% | 95% | 99% | Paired or store |
|---|---|---|---|---|
| 1 | 0.9991 | 1.0013 | 0.9994 | paired |
| 5 | 0.9993 | 1.0001 | 1.0013 | paired |
| 20 | 0.9982 | 0.9985 | 0.9990 | paired |
| 20 | 0.9996 | 0.9984 | 0.9970 | store |

`garch_pu` over `garch` is the same picture: 0.9985, 0.9999, 0.9998 at h = 20.

Per origin the effect is not zero, it is small and two-sided. The 5th to 95th percentile
of the 95% ratio at h = 20 runs from 0.988 to 1.011, the extremes are 0.966 (2020-03-31)
and 1.026 (2018-01-17), and the draws do move the volatility forecast: across draws the
20-day volatility has a coefficient of variation of 4.9% at a typical origin.

### The split the prompt asked for

At h = 20, the share of total interval width that parameter uncertainty accounts for,
measured as 1 - (width without draws) / (width with draws):

| Level | Share from parameter uncertainty | As a variance share | Share from innovations |
|---|---|---|---|
| 80% | -0.2% | -0.4% | effectively all |
| 95% | -0.2% | -0.3% | effectively all |

The negative share is not a rounding artefact: with parameter draws the central
intervals are very slightly narrower on average. Innovation uncertainty is the whole of
the published width, to within the precision this can be measured at.

### h = 1

The session prompt expected almost nothing at h = 1 because there is no recursion for
parameter error to compound through. That is half right. There is a channel at h = 1, the
starting variance, which each draw refilters from the data, and the next-day volatility
does vary across draws (coefficient of variation 3.2%). But the effect on width is the
same size as at h = 20, which is to say nil: 0.999 to 1.001. h = 1 is not special; no
horizon is.

## Why it is nothing, and why the sign was never known

The mean is fixed at zero and is not estimated. Parameter error therefore cannot move the
centre of the distribution, which is the mechanism behind the textbook result that
parameter uncertainty widens intervals. All it can do is change the scale path by path,
and the predictive distribution becomes a mixture of scales. A mixture of scales with
roughly the same average variance is not wider; it is more peaked with fatter tails. Its
central quantiles move in, its extreme ones move out, and both by an amount that is
second order in the scale uncertainty: a 5% coefficient of variation on volatility is a
change of order 0.25% in width. That is what was measured.

Rejection adds a second, directional effect. Draws that break stationarity are the
high-persistence ones, so the accepted draws have lower persistence than the estimate on
average (by 0.002 on test) and revert faster to a lower long-run variance. That pushes
widths down, and it is strongest exactly when the fit is closest to the boundary.

The crash shows both. At the origins from 2020-03-02 to 2020-05-05, with the conditional
variance far above its long-run level and 27% to 32% of draws rejected, the twin is 2.8% to
3.4% narrower than the point model at 95% and h = 20. The paths that would have kept
volatility high for the whole month are disproportionately the ones refused for
non-stationarity. That is P2-18's finding from a different direction: in a crisis the fit
sits at the stationarity boundary, and a method that respects the boundary has to decide
what to do with the mass beyond it. Rejection sampling decides by throwing it away.

So "the published intervals are too narrow" was an intuition carried over from models
whose mean is estimated. For a zero-mean GARCH simulated by filtered historical
simulation the sign depends on the level and the regime, and the size is negligible.

## The S&P 500 (P7b): a parameter on its bound

On the S&P 500 the GJR fit puts `alpha[1]` exactly at zero, its lower bound, at every dev
and test origin; `gamma` carries all of the response to shocks. The asymptotic covariance
treats that estimate as interior, so about half of all draws have a negative alpha and are
rejected. The registration said so in advance.

| h = 20, mean over 200 test origins | 80% | 95% | 99% | Rejected draws per origin |
|---|---|---|---|---|
| gjr_garch_pu / gjr_garch, paired | 1.0003 | 1.0052 | 1.0145 | mean 47.1%, range 30.7% to 51.0% |
| gjr_garch_pu / gjr_garch, store | 1.0014 | 1.0077 | 1.0218 | |
| garch_pu / garch, paired | 0.9984 | 1.0001 | 1.0009 | mean 1.1%, max 7.3% |

The GJR twin widens at the outer levels because the accepted draws are a Normal truncated
at alpha = 0, whose mean alpha is about 0.009 rather than 0, so every accepted path
responds a little more to every shock than the fit does. That is truncation, not
uncertainty in any sense the asymptotic covariance describes, and the +1.5% at 99% should
be read as an upper bound on the parameter effect, not a measurement of it. The GARCH(1,1)
twin, whose parameters are interior, shows the Nifty picture again: nothing.

## Whether it changes any registered check

No, on either market, with or without the conformal layer.

| Rule | Model | Nifty | S&P 500 |
|---|---|---|---|
| P3 (uncorrected) | gjr_garch | NO-GO, 80% h = 20 at 0.865 | NO-GO, 95% h = 20 at 0.985 |
| P3 (uncorrected) | gjr_garch_pu | NO-GO, 80% h = 20 at 0.860 | NO-GO, 95% h = 20 at 0.985 |
| P4 (conformal, the P7 gate) | gjr_garch | GO, 21 of 21 | GO, 21 of 21 |
| P4 (conformal, the P7 gate) | gjr_garch_pu | **GO, 21 of 21** | **GO, 21 of 21** |

The P7 gate's own table for Nifty, cell by cell, is the P4 table to within one origin at
every coverage cell except h = 5 at 95% (0.965 to 0.955, two origins), and identical at
h = 20: 0.830 at 80%, 0.975 at 95%. The CRPS ratio at h = 20 is 0.9711 against P4's
0.972. The uncorrected over-coverage that P3 failed on moved by one origin, 173 to 172
covered out of 200, still two above the ceiling.

One cell moved more than the width change can explain. On the S&P 500 with the conformal
layer, 95% coverage at h = 20 is 0.960 for gjr_garch and 0.935 for its twin, five origins
apart, from widths that differ by under 1%. That is the 95% conformal correction being a
third-largest-of-60 order statistic (P4-7): a small change in the scores it is read from
moves it a long way. It is recorded as more evidence that the 95% correction is noise,
not as a parameter effect. The check passed either way.

## Cost

The twin costs 1.3 times the point model's fit-plus-simulate time in the registered runs
(1.26x to 1.31x on `fit_seconds`) and about 1.4x in the paired runs. The per-draw filter
runs back only as far as the point fit's variance still carries a weight above 1e-8 on
the last day, typically a few hundred days rather than the whole slice; with the whole
slice it was 3x to 6x, growing with the sample. Well under the 5x at which the session prompt said to cut
`n_paths`, so P7 kept 10,000 paths and its Monte Carlo noise is P4's.

## What the registered predictions got right and wrong

Quoted from the Nifty gate, scored against the test origins.

| # | Prediction | Result |
|---|---|---|
| 1 | Mean width ratio at h = 20 in [0.98, 1.03] at every level; less than the prompt's single-digit percent | **Right.** 0.997 to 1.000 at every level in both measurements |
| 2 | Any widening grows with the level; the 99% ratio exceeds the 80% ratio at h = 20 | **Wrong, or right only by noise.** Paired 0.9990 against 0.9982, store 0.9970 against 0.9996. Dev showed the pattern (1.003 against 0.996) and test does not. The mixture argument predicts it, the truncation effect runs against it, and at this size neither wins reliably |
| 3 | h = 1 is not special: same order as h = 20 | **Right.** 0.999 to 1.001 at h = 1 |
| 4 | Rejection rises from dev to test, mean between 8% and 35% | **Right.** 4.7% on dev, 15.0% on test (median 17.4%, peak 31.7% on 2020-03-31) |
| 5 | Nothing registered changes; GO 21 of 21 with the layer; the P3 failure recurs; no primary coverage cell moves by more than two origins | **Right** on every part |
| Adoption | The twin replaces the point model only if it widens h = 20 by more than 2.4% or changes a check | Neither happened: **do not adopt** |
| Risk | A mean rejection above 50% would mean a parameter on its bound | Did not fire on Nifty (15%). Fired on the S&P as the 7b gate said it would |

And the S&P gate:

| # | Prediction | Result |
|---|---|---|
| 1 | Rejection between 45% and 55% at every test origin | **Wrong** for 40 of 200 origins. Mean 47.1%, median 49.8%, and 49% to 50% at every origin from 2010 to 2019, but from 2020-09 to 2023-10 the rate falls as low as 30.7%: after the 2020 shock the fit moves alpha off its bound for about three years, then returns to it. The bound is a regime, not a permanent state |
| 2 | gjr_garch_pu at h = 20 in [1.00, 1.04] at 95% and 99%; garch_pu within [0.98, 1.03] | **Right.** 1.005 and 1.015 paired, 1.008 and 1.022 in the store; garch_pu 0.998 to 1.001 |
| 3 | GO 21 of 21 with the layer; uncorrected, the 95% h = 20 failure recurs at least as badly | **Right.** GO; 0.985, the same cell and the same count |
| 4 | Do not adopt | **Right** |

The prediction that failed on Nifty is the interesting one. It was the only one that
depended on the shape of the effect rather than its size, and its size turned out to be
too small for the shape to be read.

## Earlier conclusions

None of P2 to P5 is reopened, and none needs to be. Specifically:

- **P3's NO-GO** was about over-coverage at h = 20. Parameter uncertainty could only have
  made that worse, and it does not move it: 0.865 becomes 0.860.
- **P4's GO** rests on the same four-origin margin with or without the draws.
- **P5b's recurrence** of the h = 20 failure on the S&P 500 is unchanged.
- **OP-1** now has a comparison it lacked. At 10,000 paths, simulation noise on a single
  origin's 95% band edge is 2.4% of its width; parameter uncertainty moves the same band
  by a mean under 0.2% and by about 1% either way at the 5th and 95th percentiles. For anyone reading
  one origin's fan chart, the number of paths matters more than whether the parameters
  were drawn.
- **OP-3's own reasoning** was wrong in one respect, and it is corrected here rather than
  in its row: the sign of the error was not known. For this model it is not even
  consistently positive.

What does look different is the weight to put on the stationarity boundary. P2-18 found
fits hitting it in a crash; this phase finds that at the same dates a quarter to a third
of the parameter distribution the fit implies lies beyond it, and that what one does with
that mass decides the sign of the answer. A method that keeps those draws (a bootstrap
over refits, or a Bayesian posterior that puts prior mass near 1) would widen the crisis
intervals where rejection narrows them.

## Limitations recorded with the decision

- **The covariance is asymptotic, so this is an approximation, not a posterior.** The
  Normal is symmetric where the likelihood near a boundary is not, rejection truncates it,
  and the robust sandwich is itself an estimate. A bootstrap over refits would avoid most
  of this; it costs a refit per replication, 10,000 per origin here, which is why it was
  not the method. The measurement is "what the fit's own standard errors imply", and a
  bootstrap could find more, most plausibly at the crisis origins where the boundary
  binds.
- **At a bound the method measures truncation.** The S&P GJR result is an upper bound on
  the parameter effect for that reason, and should not be quoted as a measurement.
- **The standardised residuals are the point fit's.** Under each drawn parameter vector
  they would differ slightly; that second-order effect is ignored.
- **The rolling window has one new failure.** On the S&P 500 at the 2014-09-22 rolling
  origin, 99.93% of draws were inadmissible and the twin is a typed failure where the
  point model fitted. The run's A4 is 1.63% overall (rolling 3.27%) against P5b's 1.29%,
  and 0.000% on the expanding window every decision is read from. Nifty's run is 0.96%
  overall, also 0.000% on the expanding window.
- **The prediction was not blind to the order of magnitude.** During development one
  smoke fit on the full Nifty sample (and one on the S&P) showed widening of about 1% at
  late origins, before the gate was written, and the gate says so. No test coverage was
  seen before registration.
- **Reproducibility is per machine.** P7's runs were made on a different machine from
  the registered P2 to P5 runs, so the proof that the existing models are unchanged was
  made on one machine, before and after the code change (see DECISIONS.md, P7-2). Across
  machines the expanding-window forecasts agree to about 1e-6 relative, and fits at the
  stationarity boundary can flip between failing and fitting.
