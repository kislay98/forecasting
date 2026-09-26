# Holding-period risk: nifty50

Run `4a5899ec5e37c363`, git `09fb68aeda35`, expanding window, 10,000 simulated paths per origin.

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
| crps | 1 | - | pass | 0.9568 of zero_return_fhs, must be below 1.0 |
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
| crps | 20 | - | pass | 0.9721 of zero_return_fhs, must be below 1.0 |

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
| ewma | 1 | 0.9950 | 0.0308 | 0.0400 | 2 | 1.0000 | 0.3779 | 2 | not tested | not tested |
| garch | 1 | 0.9950 | 0.0303 | 0.0410 | 3 | 1.0000 | 0.1061 | 3 | not tested | not tested |
| garch_normal | 1 | 0.9950 | 0.0307 | 0.0412 | 2 | 1.0000 | 0.3779 | 2 | not tested | not tested |
| gjr_garch | 1 | 0.9950 | 0.0314 | 0.0411 | 2 | 1.0000 | 0.3779 | 2 | not tested | not tested |
| zero_return_fhs | 1 | 0.9950 | 0.0391 | 0.0686 | 1 | 1.0000 | 1.0000 | 1 | not tested | not tested |
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
| ewma | 5 | 0.9950 | 0.0571 | 0.0755 | 3 | 1.0000 | 0.1061 | 3 | not tested | not tested |
| garch | 5 | 0.9950 | 0.0547 | 0.0836 | 3 | 1.0000 | 0.1061 | 3 | not tested | not tested |
| garch_normal | 5 | 0.9950 | 0.0539 | 0.0832 | 2 | 1.0000 | 0.3779 | 2 | not tested | not tested |
| gjr_garch | 5 | 0.9950 | 0.0611 | 0.0907 | 1 | 1.0000 | 1.0000 | 1 | not tested | not tested |
| zero_return_fhs | 5 | 0.9950 | 0.0767 | 0.1177 | 1 | 1.0000 | 1.0000 | 1 | not tested | not tested |
| ewma | 20 | 0.9500 | 0.0765 | 0.0885 | 8 | 10.0000 | 0.5020 | 8 | not tested | not tested |
| garch | 20 | 0.9500 | 0.0756 | 0.1002 | 8 | 10.0000 | 0.5020 | 8 | not tested | not tested |
| garch_normal | 20 | 0.9500 | 0.0756 | 0.1001 | 8 | 10.0000 | 0.5020 | 8 | not tested | not tested |
| gjr_garch | 20 | 0.9500 | 0.0786 | 0.1103 | 7 | 10.0000 | 0.3047 | 7 | not tested | not tested |
| zero_return_fhs | 20 | 0.9500 | 0.0784 | 0.1300 | 7 | 10.0000 | 0.3047 | 7 | not tested | not tested |
| ewma | 20 | 0.9750 | 0.1034 | 0.1050 | 5 | 5.0000 | 1.0000 | 5 | not tested | not tested |
| garch | 20 | 0.9750 | 0.1022 | 0.1199 | 5 | 5.0000 | 1.0000 | 5 | not tested | not tested |
| garch_normal | 20 | 0.9750 | 0.1017 | 0.1198 | 5 | 5.0000 | 1.0000 | 5 | not tested | not tested |
| gjr_garch | 20 | 0.9750 | 0.1102 | 0.1347 | 4 | 5.0000 | 0.6391 | 4 | not tested | not tested |
| zero_return_fhs | 20 | 0.9750 | 0.1077 | 0.1493 | 5 | 5.0000 | 1.0000 | 5 | not tested | not tested |
| ewma | 20 | 0.9950 | 0.1380 | 0.1444 | 4 | 1.0000 | 0.0234 | 4 | not tested | not tested |
| garch | 20 | 0.9950 | 0.1388 | 0.1688 | 3 | 1.0000 | 0.1061 | 3 | not tested | not tested |
| garch_normal | 20 | 0.9950 | 0.1378 | 0.1684 | 3 | 1.0000 | 0.1061 | 3 | not tested | not tested |
| gjr_garch | 20 | 0.9950 | 0.1410 | 0.1970 | 2 | 1.0000 | 0.3779 | 2 | not tested | not tested |
| zero_return_fhs | 20 | 0.9950 | 0.1673 | 0.1892 | 3 | 1.0000 | 0.1061 | 3 | not tested | not tested |

## The distribution from the latest origin

![cumulative loss fan chart](fan.png)

## What this does not tell you

- The distribution is conditional on the volatility state at the origin and on the assumption that tomorrow's shocks resemble the standardised residuals of the training window. A shock unlike anything in the sample is outside it.
- Expected shortfall is estimated from simulated paths, so its tail accuracy is bounded by the number of paths and by the empirical residual sample behind them.
- Coverage is measured over the test origins as a whole. Calibration over a long sample does not guarantee calibration in any particular month.