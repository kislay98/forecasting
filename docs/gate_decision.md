# Phase 1 gate decision (A10)

Written 23 Sep 2026 from run `fe04aa2da050ff5f` (git `e267a112f594`, config
`configs/phase1.yaml`, gate `configs/gate.yaml` sha256 `22e458d1d926`, committed as
`e267a112f594` before the run). The full report is [phase1_report/report.md](phase1_report/report.md);
its manifest is [phase1_report/manifest.json](phase1_report/manifest.json). The pre-registered
rules were applied as written; nothing in them was changed after seeing test origins.

| Series | Decision | Registered expectation | Held? |
|---|---|---|---|
| nifty50 | **NO-GO** | Nothing beats the zero-return forecast on the mean (U5) | Yes |
| electricity (control) | **NO-GO** under the registered significance rule | Seasonal models beat seasonal naive at every horizon | Yes in relative MAE at every h; not significant after Holm |

## Nifty 50

Daily log returns, 1996 to 22 Sep 2026, 250 test origins at step 5, decision horizons 1, 5
and 20 trading days, expanding window.

- **RQ1 no.** The dev-chosen reference is SMA(250) (a 250-day mean return). AR(p) ties it:
  relative MAE 0.99 to 1.00 at every h, one-sided DM-HLN p 0.15 and above at every decision
  horizon, so no candidate beats the baselines. Against the zero-return forecast the picture
  is the same: nothing is more than sampling noise better.
- **RQ2 h* = 0.** The skill CI of AR against the reference includes zero at h = 1
  (0.009 [-0.004, 0.02]) and at every longer horizon. No horizon is forecastable on the mean.
- **RQ3 over-covers.** AR's analytic 80% intervals cover 94 to 97% of actuals and the 95%
  intervals 98 to 100%: Normal intervals from the unconditional variance are too wide in
  calm periods, the mirror image of volatility clustering.
- **RQ4.** Ljung-Box rejects at 100% of test origins and ARCH-LM at 100%. The ARCH result
  confirms U6: volatility clusters. Autocorrelation in AR's one-step residuals says the daily
  mean is not white noise either, but RQ1 says whatever structure is there is not
  exploitable on the mean.
- **RQ5.** No significant gap between rolling and expanding windows; AR beats the reference
  in 3 of 4, 2 of 4 and 1 of 4 sub-periods at h = 1, 5, 20: no stable advantage.
- **Exit table row:** nothing beats naive significantly. Decision: stop modelling the mean.

## Electricity (FRED IPG2211A2N, positive control)

Monthly output from 1990, 120 test origins at step 1, decision horizons 1, 12 and 24 months,
expanding window. Every decision horizon was testable (n / h = 120, 10 and 5).

- **The control did its job.** Every seasonal model beats seasonal naive in relative MAE at
  every one of the 24 horizons (ETS 0.82 to 0.99, the combination 0.81 to 0.97, Theta 0.83 to
  1.02 with two horizons above 1), and all four sit in the Model Confidence Set at every h,
  while naive, drift, SES and SMA are excluded except where they coincide with seasonal
  naive at h = 12 and 24. The harness recognises seasonal structure on real data.
- **RQ1 no under the registered rule.** At h = 1 the raw one-sided p-values are 0.004
  (combination), 0.008 (ETS), 0.011 (SARIMA), 0.012 (Theta). Holm over the registered
  family of 15 tests (5 candidates x 3 horizons) puts the smallest at 0.066, above alpha
  0.05. At h = 12 ETS has raw p 0.006 and Holm p 0.087; at h = 24 nothing is close. An 18%
  MAE gain at h = 1 with 120 overlapping monthly origins is not enough to survive a
  15-way Holm correction. This is a statement about power, not about the models.
- **RQ2 h* = 1** for ETS: skill 0.18 [0.04, 0.30] at h = 1, and the block-bootstrap CI
  includes zero from h = 2 on.
- **RQ3 under-covers** at h = 1: ETS's 80% interval covers 63% and its 95% interval 88%
  (Kupiec p < 0.001). Longer buckets are inside their (wide) bands.
- **RQ4.** ETS residuals are autocorrelated at 100% of origins; SARIMA's at 73% (lag 12)
  and 97% (lag 24); ARCH is mixed (12% for ETS). **RQ5** no significant window gap; ETS
  beats seasonal naive in 3, 4 and 3 of 4 sub-periods. **RQ6** log in 120 of 120 folds.
- **Exit table row:** nothing beats the baseline significantly at the decision horizons.
  Decision under the registered rule: NO-GO.

## What this means for Phase 2

1. **Nifty.** Phase 2 lite as the exit table prescribes: the zero-return (random walk) point
   forecast plus empirical error quantiles, and, because ARCH fires at every origin (U6),
   a GARCH-family error model for the intervals. The calibration finding (over-coverage of
   Normal intervals from the unconditional variance) is the case for conditional variance.
   No further work on the conditional mean without covariates.
2. **Electricity.** The registered decision is NO-GO and stands. The evidence also says the
   models are better than seasonal naive by 10 to 19% at short horizons but that the
   pre-registered design (15-way Holm at three horizons, 120 origins) cannot certify it at
   5%. If the control is used again, register fewer tests (one decision horizon, or one
   pre-named candidate such as the combination) or more origins; do not re-run this design
   hoping for a different p-value.
3. **Harness.** L1 to L5 pass, A1 reproduces the 569,176-row store byte for byte across two
   runs, and the control behaves as a positive control should in relative accuracy. The
   Phase 1 objective, a trustworthy answer, is met; the answer for the primary series is
   the expected "no".

## Limitations recorded with the decision

- The Nifty file is the price index (dividends excluded) from niftyindices.com, weekend
  sessions dropped (DECISIONS M8-5, M8-6); no data vintages for either series.
- Every decision horizon of the control was testable, but the effective sample at h = 24 is
  5 origins: the long-horizon rows are shown, not trusted.
- Runtime: the run took 28 CPU minutes but 94 minutes of wall time with `n_jobs: 4`, and the
  reproduction run 2h49m; the parallel dispatch is inefficient on this design (M8-12) and
  is a Phase 4 item.
