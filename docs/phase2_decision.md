# Phase 2 lite gate decision (Nifty 50)

Written 26 Sep 2026 from run `9260b33a46f88d81` (git `d62deac04562`, config
`configs/phase2/phase2.yaml`, gate `configs/phase2/gate.yaml` sha256 `323ddc09c0ad`,
committed as `82f388a51dde` before the run). The registered rules were applied as
written; nothing in them was changed after seeing a test origin.

| Series | Primary model | Decision |
|---|---|---|
| nifty50 | ewma | **GO**, all 14 registered checks pass |

Phase 1 established that the conditional mean of Nifty returns is not forecastable
(h* = 0) while ARCH-LM rejected at 100% of test origins. Phase 2 lite therefore fixes
the point forecast at zero and asks one question instead: can the interval be made
honest. It can.

## The decision

400 test origins, expanding window, 2018-07-19 to 2026-08-24, at the Sidak-corrected
per-test level of 0.0064.

| Check | h = 1 | h = 5 |
|---|---|---|
| 80% coverage, band [0.75, 0.85] | 0.787 | 0.815 |
| Kupiec at 80% | p 0.535 | p 0.449 |
| Christoffersen independence at 80% | p 0.974 | p 0.288 |
| 95% coverage, band [0.91, 0.98] | 0.940 | 0.938 |
| Kupiec at 95% | p 0.373 | p 0.269 |
| Christoffersen independence at 95% | p 0.080 | p 0.608 |
| CRPS against Phase 1's zero_return | 0.874 | 0.866 |

The closest call is independence at h = 1 and the 95% level, p = 0.080. It clears
0.0064 by a wide margin but is an order of magnitude nearer the line than anything
else, and it is the cell to watch if this is ever re-run.

h = 20 is outside the decision because independence needs `origin_step >= h` (P2-4).
Reported anyway: ewma covers 0.798 at the 80% level with Kupiec p 0.950, the best of
the six models, while every other model over-covers.

## The registered risk did not materialise

The gate recorded, before the run, that ewma was selected on the calmest four years in
the data (13.4% annualised volatility against 22.5% over the full sample) and that the
test window contains 2020. The predicted failure was under-coverage concentrated in
the spike, arriving as clustered misses.

That did not happen. Independence does not reject at any registered cell, and ewma's
80% coverage across four equal blocks of the test period is 0.750, 0.790, 0.820, 0.790.
Saying so is the point of having written the prediction down: it was a real risk, and
it is now a real piece of evidence rather than a story assembled afterwards.

## Why it worked, checked rather than assumed

Mean half-width of the 80% interval at h = 1:

| Period | ewma | garch | zero_return (Phase 1) |
|---|---|---|---|
| Jun-Dec 2019, calm | 0.0116 | 0.0117 | 0.0192 |
| Mar-May 2020, the crash | 0.0418 | 0.0401 | 0.0194 |
| Jun-Dec 2021, calm | 0.0093 | 0.0102 | 0.0192 |

The conditional models widen by a factor of about 3.6 and then come back. Phase 1's
interval moves by 1% and is wrong in both directions: far too wide in calm periods and
far too narrow in the crash.

Inside the crash window (Feb to Jun 2020, 19 origins) that costs Phase 1 the answer
outright: `zero_return` covers 0.579 with 8 misses out of 19 at a nominal 80%, and
`zero_return_fhs` covers 0.526. `ewma` covers 0.895.

## What the ablation says

The family was built so the two effects could be separated, and they separate cleanly.

- **Conditional variance is the whole effect.** Empirical tails on a flat variance
  (`zero_return_fhs`) improve coverage from 0.920 to 0.883 at h = 1 against a nominal
  0.80, and still fail Kupiec at p < 0.0001. Both flat models also show significantly
  clustered misses, which is exactly the failure Christoffersen was registered to catch.
- **The tails matter on top of it, but less.** At h = 1, `garch` covers 0.813 and
  `garch_normal` 0.830. At h = 5 the gap widens: 0.838 against 0.873, where
  `garch_normal` fails Kupiec.
- **The model with no fitted parameters wins.** EWMA at the fixed RiskMetrics decay
  beats all three fitted GARCH variants on coverage at every horizon, and ties them on
  CRPS. Whatever the GARCH likelihood buys on these data, it is not calibration.

## Limitations recorded with the decision

- Two fits failed, both `garch` and `garch_normal` on the **rolling** window at the
  origin of 2020-04-09, hitting the non-stationarity boundary at persistence 1.0000.
  The decision uses the expanding window, so it is unaffected, but a GARCH layer that
  cannot fit at the peak of a crash is worth knowing about before anyone relies on one.
- `y_true` at horizon h is the single-day return on day t+h, not the h-day cumulative
  return. A risk product usually wants the latter, and that would be a different study
  with a different target definition (P2-1).
- The test period is one series over eight years containing one major shock. That ewma
  survived 2020 is evidence, not a guarantee about the next regime.
- The 99% level is registered in the config and reported but is not part of the
  decision: 400 origins give 4 expected misses there, which no test can read.
