# Holding-period risk: nifty50

Run `4a5899ec5e37c363`, git `09fb68aeda35`, expanding window, 10,000 simulated paths per origin.

Acceptance check A4: 0.75% of the run's rows are typed failures against a 1% limit, and 0.00% on the expanding window this decision is read from. A failure on the other window cannot reach the numbers below, but it is stated here rather than left in the manifest.

The quantity forecast is the cumulative move over h trading days, log(p_t+h / p_t), converted to a loss as 1 - exp(r). Phase 1 established that the mean of this series is not forecastable, so the point forecast is fixed at zero and everything below is about the spread.

## Conformal calibration (P4), window 60

Interval widths are corrected from the most recent 60 eligible past origins, eligible meaning origins whose h-step outcome had already landed by the origin being corrected. The correction is applied to the expanding window only, which is the window the decision is read from. Rows from the other window are dropped below; they were never scored either way, so the row count falls by about half without any evidence being discarded.

All 20000 scored test rows had the full calibration set available, so none were excluded.

## Gate decision: GO

Primary model `gjr_garch`, registered as 2026-09-26 (gate.yaml 14a5a0afd8bd, commit 584e3336445f).

| check | h | level | result | detail |
|---|---|---|---|---|
| coverage | 1 | 80% | pass | 0.805 against [0.75, 0.85] |
| kupiec | 1 | 80% | pass | p = 0.8592 against alpha 0.0043 |
| independence | 1 | 80% | pass | p = 0.1446 against alpha 0.0043 |
| coverage | 1 | 95% | pass | 0.970 against [0.91, 0.98] |
| kupiec | 1 | 95% | pass | p = 0.1622 against alpha 0.0043 |
| independence | 1 | 95% | pass | p = 0.5413 against alpha 0.0043 |
| crps | 1 | - | pass | 0.9567 of zero_return_fhs, must be below 1.0 |
| coverage | 5 | 80% | pass | 0.820 against [0.75, 0.85] |
| kupiec | 5 | 80% | pass | p = 0.4737 against alpha 0.0043 |
| independence | 5 | 80% | pass | p = 0.4575 against alpha 0.0043 |
| coverage | 5 | 95% | pass | 0.965 against [0.91, 0.98] |
| kupiec | 5 | 95% | pass | p = 0.3047 against alpha 0.0043 |
| independence | 5 | 95% | pass | p = 0.4749 against alpha 0.0043 |
| crps | 5 | - | pass | 0.9638 of zero_return_fhs, must be below 1.0 |
| coverage | 20 | 80% | pass | 0.830 against [0.75, 0.85] |
| kupiec | 20 | 80% | pass | p = 0.2792 against alpha 0.0043 |
| independence | 20 | 80% | pass | p = 0.7435 against alpha 0.0043 |
| coverage | 20 | 95% | pass | 0.975 against [0.91, 0.98] |
| kupiec | 20 | 95% | pass | p = 0.0737 against alpha 0.0043 |
| independence | 20 | 95% | pass | p = 0.6117 against alpha 0.0043 |
| crps | 20 | - | pass | 0.9720 of zero_return_fhs, must be below 1.0 |

## Calibration of the loss distribution

| model | h | n | cov_80 | kupiec_p_80 | ind_p_80 | cov_95 | kupiec_p_95 | crps |
|---|---|---|---|---|---|---|---|---|
| ewma | 1 | 200 | 0.7850 | 0.5992 | 0.1315 | 0.9600 | 0.5020 | 0.0050 |
| garch | 1 | 200 | 0.8150 | 0.5923 | 0.3336 | 0.9650 | 0.3047 | 0.0049 |
| garch_normal | 1 | 200 | 0.8000 | 1.0000 | 0.3966 | 0.9700 | 0.1622 | 0.0049 |
| gjr_garch | 1 | 200 | 0.8050 | 0.8592 | 0.1446 | 0.9700 | 0.1622 | 0.0049 |
| zero_return_fhs | 1 | 200 | 0.8600 | 0.0268 | 0.0940 | 0.9650 | 0.3047 | 0.0051 |
| ewma | 5 | 200 | 0.8100 | 0.7220 | 0.7353 | 0.9600 | 0.5020 | 0.0108 |
| garch | 5 | 200 | 0.8050 | 0.8592 | 0.4494 | 0.9750 | 0.0737 | 0.0107 |
| garch_normal | 5 | 200 | 0.8050 | 0.8592 | 0.7704 | 0.9750 | 0.0737 | 0.0107 |
| gjr_garch | 5 | 200 | 0.8200 | 0.4737 | 0.4575 | 0.9650 | 0.3047 | 0.0106 |
| zero_return_fhs | 5 | 200 | 0.8300 | 0.2792 | 0.1274 | 0.9800 | 0.0275 | 0.0110 |
| ewma | 20 | 200 | 0.8350 | 0.2051 | 0.4849 | 0.9650 | 0.3047 | 0.0234 |
| garch | 20 | 200 | 0.8250 | 0.3689 | 0.6196 | 0.9700 | 0.1622 | 0.0232 |
| garch_normal | 20 | 200 | 0.8250 | 0.3689 | 0.6196 | 0.9700 | 0.1622 | 0.0232 |
| gjr_garch | 20 | 200 | 0.8300 | 0.2792 | 0.7435 | 0.9750 | 0.0737 | 0.0230 |
| zero_return_fhs | 20 | 200 | 0.8500 | 0.0671 | 0.0012 | 0.9750 | 0.0737 | 0.0237 |

## Value at risk and expected shortfall

Losses are fractions of the position, so 0.05 is a 5% loss. `var_mean_loss` is the average VaR the model quoted; `es_mean_loss` is the average loss it expected given a breach. `es_ok` is whether a bootstrap interval on realised minus predicted covers zero: NO, with a negative bias, means breaches were worse than the model said. `not tested` means fewer than ten breaches, which is not a pass: at these sample sizes most tail rows say nothing either way.

| model | h | var_p | var_mean_loss | es_mean_loss | breaches | expected | kupiec_p | es_breaches | es_bias | es_ok |
|---|---|---|---|---|---|---|---|---|---|---|
| ewma | 1 | 0.9500 | 0.0188 | 0.0220 | 8 | 10.0000 | 0.5020 | 8 | not tested | not tested |
| garch | 1 | 0.9500 | 0.0183 | 0.0230 | 7 | 10.0000 | 0.3047 | 7 | not tested | not tested |
| garch_normal | 1 | 0.9500 | 0.0182 | 0.0231 | 7 | 10.0000 | 0.3047 | 7 | not tested | not tested |
| gjr_garch | 1 | 0.9500 | 0.0183 | 0.0232 | 7 | 10.0000 | 0.3047 | 7 | not tested | not tested |
| zero_return_fhs | 1 | 0.9500 | 0.0199 | 0.0358 | 7 | 10.0000 | 0.3047 | 7 | not tested | not tested |
| ewma | 1 | 0.9750 | 0.0252 | 0.0267 | 4 | 5.0000 | 0.6391 | 4 | not tested | not tested |
| garch | 1 | 0.9750 | 0.0256 | 0.0277 | 5 | 5.0000 | 1.0000 | 5 | not tested | not tested |
| garch_normal | 1 | 0.9750 | 0.0257 | 0.0279 | 4 | 5.0000 | 0.6391 | 4 | not tested | not tested |
| gjr_garch | 1 | 0.9750 | 0.0248 | 0.0277 | 4 | 5.0000 | 0.6391 | 4 | not tested | not tested |
| zero_return_fhs | 1 | 0.9750 | 0.0292 | 0.0448 | 5 | 5.0000 | 1.0000 | 5 | not tested | not tested |
| ewma | 1 | 0.9950 | 0.0309 | 0.0400 | 2 | 1.0000 | 0.3779 | 2 | not tested | not tested |
| garch | 1 | 0.9950 | 0.0305 | 0.0410 | 3 | 1.0000 | 0.1061 | 3 | not tested | not tested |
| garch_normal | 1 | 0.9950 | 0.0307 | 0.0412 | 2 | 1.0000 | 0.3779 | 2 | not tested | not tested |
| gjr_garch | 1 | 0.9950 | 0.0315 | 0.0411 | 2 | 1.0000 | 0.3779 | 2 | not tested | not tested |
| zero_return_fhs | 1 | 0.9950 | 0.0392 | 0.0686 | 1 | 1.0000 | 1.0000 | 1 | not tested | not tested |
| ewma | 5 | 0.9500 | 0.0374 | 0.0464 | 9 | 10.0000 | 0.7416 | 9 | not tested | not tested |
| garch | 5 | 0.9500 | 0.0354 | 0.0500 | 10 | 10.0000 | 1.0000 | 10 | 0.0072 | NO |
| garch_normal | 5 | 0.9500 | 0.0354 | 0.0501 | 10 | 10.0000 | 1.0000 | 10 | 0.0075 | NO |
| gjr_garch | 5 | 0.9500 | 0.0361 | 0.0527 | 10 | 10.0000 | 1.0000 | 10 | 0.0098 | NO |
| zero_return_fhs | 5 | 0.9500 | 0.0387 | 0.0723 | 5 | 10.0000 | 0.0737 | 5 | not tested | not tested |
| ewma | 5 | 0.9750 | 0.0466 | 0.0550 | 4 | 5.0000 | 0.6391 | 4 | not tested | not tested |
| garch | 5 | 0.9750 | 0.0460 | 0.0597 | 4 | 5.0000 | 0.6391 | 4 | not tested | not tested |
| garch_normal | 5 | 0.9750 | 0.0454 | 0.0597 | 3 | 5.0000 | 0.3283 | 3 | not tested | not tested |
| gjr_garch | 5 | 0.9750 | 0.0461 | 0.0634 | 3 | 5.0000 | 0.3283 | 3 | not tested | not tested |
| zero_return_fhs | 5 | 0.9750 | 0.0561 | 0.0856 | 1 | 5.0000 | 0.0274 | 1 | not tested | not tested |
| ewma | 5 | 0.9950 | 0.0576 | 0.0755 | 3 | 1.0000 | 0.1061 | 3 | not tested | not tested |
| garch | 5 | 0.9950 | 0.0549 | 0.0836 | 1 | 1.0000 | 1.0000 | 1 | not tested | not tested |
| garch_normal | 5 | 0.9950 | 0.0541 | 0.0832 | 1 | 1.0000 | 1.0000 | 1 | not tested | not tested |
| gjr_garch | 5 | 0.9950 | 0.0611 | 0.0907 | 1 | 1.0000 | 1.0000 | 1 | not tested | not tested |
| zero_return_fhs | 5 | 0.9950 | 0.0769 | 0.1177 | 1 | 1.0000 | 1.0000 | 1 | not tested | not tested |
| ewma | 20 | 0.9500 | 0.0765 | 0.0885 | 8 | 10.0000 | 0.5020 | 8 | not tested | not tested |
| garch | 20 | 0.9500 | 0.0756 | 0.1002 | 8 | 10.0000 | 0.5020 | 8 | not tested | not tested |
| garch_normal | 20 | 0.9500 | 0.0756 | 0.1001 | 8 | 10.0000 | 0.5020 | 8 | not tested | not tested |
| gjr_garch | 20 | 0.9500 | 0.0786 | 0.1103 | 7 | 10.0000 | 0.3047 | 7 | not tested | not tested |
| zero_return_fhs | 20 | 0.9500 | 0.0784 | 0.1300 | 7 | 10.0000 | 0.3047 | 7 | not tested | not tested |
| ewma | 20 | 0.9750 | 0.1035 | 0.1050 | 5 | 5.0000 | 1.0000 | 5 | not tested | not tested |
| garch | 20 | 0.9750 | 0.1022 | 0.1199 | 5 | 5.0000 | 1.0000 | 5 | not tested | not tested |
| garch_normal | 20 | 0.9750 | 0.1017 | 0.1198 | 5 | 5.0000 | 1.0000 | 5 | not tested | not tested |
| gjr_garch | 20 | 0.9750 | 0.1102 | 0.1347 | 4 | 5.0000 | 0.6391 | 4 | not tested | not tested |
| zero_return_fhs | 20 | 0.9750 | 0.1077 | 0.1493 | 5 | 5.0000 | 1.0000 | 5 | not tested | not tested |
| ewma | 20 | 0.9950 | 0.1388 | 0.1444 | 4 | 1.0000 | 0.0234 | 4 | not tested | not tested |
| garch | 20 | 0.9950 | 0.1391 | 0.1688 | 3 | 1.0000 | 0.1061 | 3 | not tested | not tested |
| garch_normal | 20 | 0.9950 | 0.1385 | 0.1684 | 3 | 1.0000 | 0.1061 | 3 | not tested | not tested |
| gjr_garch | 20 | 0.9950 | 0.1410 | 0.1970 | 2 | 1.0000 | 0.3779 | 2 | not tested | not tested |
| zero_return_fhs | 20 | 0.9950 | 0.1674 | 0.1892 | 3 | 1.0000 | 0.1061 | 3 | not tested | not tested |

## Probability of a move beyond a threshold

`gjr_garch`, expanding window. `latest` is P(beyond the threshold) at the most recent origin, the one the fan chart is drawn from. `p_mean` is the average answered probability over test origins and `realised` the frequency on the same origins, so a calibrated model has the two close. A loss threshold is P(loss > L); a gain threshold is P(gain > G). The complement is the probability of staying inside it.

These are interpolations between the 10 stored quantiles, not simulated frequencies: against a direct count of the same paths the error averages 0.0007 in the tails and reaches 0.019 in the centre (docs/threshold_probabilities.md). Beyond the outermost stored quantile the grid gives only a bound, printed as one.

Latest origin 2026-08-24.

| h | threshold | latest | p_mean | realised | answered | outside | outside_hits |
|---|---|---|---|---|---|---|---|
| 1 | gain 5% | < 0.005 | 0.0366 | 0.0000 | 9 of 200 | 191 | 0 |
| 1 | loss 2% | 0.0211 | 0.0483 | 0.0305 | 164 of 200 | 36 | 1 |
| 1 | loss 5% | < 0.005 | 0.0272 | 0.0000 | 12 of 200 | 188 | 0 |
| 1 | loss 10% | < 0.005 | 0.0332 | 0.0000 | 1 of 200 | 199 | 0 |
| 5 | gain 5% | < 0.005 | 0.0534 | 0.0297 | 101 of 200 | 99 | 0 |
| 5 | loss 2% | 0.1105 | 0.1477 | 0.1550 | 200 of 200 | 0 | 0 |
| 5 | loss 5% | < 0.005 | 0.0378 | 0.0174 | 115 of 200 | 85 | 0 |
| 5 | loss 10% | < 0.005 | 0.0195 | 0.0000 | 18 of 200 | 182 | 0 |
| 20 | gain 5% | 0.0524 | 0.1760 | 0.1759 | 199 of 200 | 1 | 0 |
| 20 | loss 2% | 0.2209 | 0.2456 | 0.2500 | 200 of 200 | 0 | 0 |
| 20 | loss 5% | 0.0429 | 0.1011 | 0.0700 | 200 of 200 | 0 | 0 |
| 20 | loss 10% | < 0.005 | 0.0390 | 0.0187 | 160 of 200 | 40 | 1 |

`outside` counts test origins where the threshold lay beyond the grid; `outside_hits` is how many of those on the rare side, where the grid said less than 0.5%, crossed the threshold anyway. `p_mean` prints `not tested` when no origin could be answered.

## The distribution from the latest origin

![cumulative loss fan chart](fan.png)

## What this does not tell you

- The distribution is conditional on the volatility state at the origin and on the assumption that tomorrow's shocks resemble the standardised residuals of the training window. A shock unlike anything in the sample is outside it.
- Expected shortfall is estimated from simulated paths, so its tail accuracy is bounded by the number of paths and by the empirical residual sample behind them.
- Coverage is measured over the test origins as a whole. Calibration over a long sample does not guarantee calibration in any particular month.