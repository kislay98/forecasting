# Phase 5a gate decision (the Phase 2 decision, on the S&P 500)

Written 26 Sep 2026 from run `93af45770cfd562b` (git `b76eba38e27f`, config
`configs/phase5a/phase5a.yaml`, gate `configs/phase5a/gate.yaml` sha256 `9e05b0027254`,
committed as `b76eba38e27f` before the run). The gate was written before the S&P 500 data
file existed in the repo. Nothing in it was chosen from this market, and `ewma` was
carried over from Phase 2 by name.

| Series | Primary model | Decision |
|---|---|---|
| sp500 | ewma, carried over from P2 | **NO-GO**, 13 of 14 checks pass |

The single failure is Christoffersen independence at h = 5 and the 80% level:
p = 0.0056 against a corrected 0.0064. It fails by 0.0008.

## The decision

400 test origins, 2018-09-18 to 2026-08-27, expanding window, step 5, at the
Sidak-corrected per-test level of 0.0064. Nifty's Phase 2 numbers are beside them.

Each cell is Nifty (P2) / S&P (P5a).

| Check | h = 1 | h = 5 |
|---|---|---|
| 80% coverage, band [0.75, 0.85] | 0.787 / 0.805 | 0.815 / 0.795 |
| Kupiec at 80% | pass / p 0.802 | pass / p 0.803 |
| Independence at 80% | pass / p 0.076 | pass / **p 0.0056 FAIL** |
| 95% coverage, band [0.91, 0.98] | 0.940 / 0.965 | 0.938 / 0.925 |
| Kupiec at 95% | pass / p 0.147 | pass / p 0.032 |
| Independence at 95% | pass / p 0.505 | pass / p 0.607 |
| CRPS against zero_return | 0.874 / 0.937 | 0.866 / 0.951 |

Coverage is not the problem. At h = 5 the 80% interval missed 82 times where 80 were
expected, which is why Kupiec returns 0.803 on the same cell that independence rejects.
The average rate is close to perfect and the ordering is wrong.

| h = 5, 80% level, ewma | Value |
|---|---|
| Misses | 82 of 400, expected 80 |
| P(miss given a miss at the previous origin) | 0.321 |
| P(miss given no miss at the previous origin) | 0.176 |
| Longest run of consecutive missing origins | 5, which is 25 trading days |
| Misses by year | 2024: 15, 2020: 12, 2023: 10, 2022: 9, 2021: 8, 2025: 8 |

Not one crisis. Recurring bursts across the whole test window.

## This is the check the control said to watch

A12, built earlier the same day, measured which of the registered checks can actually
detect a variance model that is wrong. On a synthetic GARCH DGP the answer was
independence, in three seeds out of three, while the coverage bands caught the wrong model
once and Kupiec once. This failure is that check firing on real data, with the coverage
band and Kupiec both passing on the same cell, which is exactly the pattern the control
described.

Two consequences. The failure is not a rounding accident despite the 0.0008 margin, since
it is the one check with measured power against this kind of error. And the whole family is
in the same state on the same cell:

| h = 5, 80%, independence p | Model |
|---|---|
| 0.0001 | zero_return (Phase 1's: constant variance, Normal tails) |
| 0.0045 | zero_return_fhs |
| 0.0056 | **ewma**, the primary |
| 0.0093 | gjr_garch |
| 0.0300 | garch |
| 0.115 | garch_normal |

No model in the registered family removes the clustering at a week on this market. The
one that comes closest is `garch_normal`, which is the only member whose intervals come
from a fitted variance with Normal tails rather than resampled residuals.

## The registered prediction was wrong, and its named risk was half right

**Wrong.** The gate predicted ewma passes, on the grounds that the mechanism Phase 2
verified (interval width tracking the current volatility level) is generic rather than
Indian. The mechanism is generic; it is not sufficient here.

**Half right.** The named risk was that the S&P's stronger leverage effect is the one
thing ewma cannot represent, and that if ewma failed, the asymmetric models should visibly
beat it on CRPS in the same run. They do: gjr_garch scores 0.0049 against ewma's 0.0051 at
h = 1 and 0.0056 against 0.0058 at h = 5, the best in the family at both horizons. The
rest of the prediction missed: the failure was supposed to be under-coverage at the 95%
level concentrated in falling markets, and what happened was clustering at the 80% level
with 95% coverage at 0.925, low but inside the band.

## What replicates and what does not

**Replicates.** Conditional variance pays. Every conditional model beats Phase 1's
zero_return on CRPS at both horizons, ewma at 0.937 and 0.951, and zero_return itself
covers 0.880 at h = 1 against a nominal 0.80 with Kupiec below 0.0001. The Phase 2 finding
that a flat unconditional interval is badly wrong on an equity index is not market
specific.

**Does not replicate.** The specific claim that Phase 2 registered and passed, that `ewma`
is calibrated at the decision horizons, fails on the second market. The dev rule that
picked ewma over gjr_garch on a 0.4% CRPS margin was picking noise, which the Phase 2
write-up said in advance was a risk.

## No reselection

P5-4 was registered in advance: a failure is not followed by re-tuning. The thresholds do
not move, the primary model is not reselected on S&P dev origins, and Phase 2's Nifty
decision stands as what it always was, a decision about Nifty. That gjr_garch would have
passed this gate is visible in the table above and is not a decision.

## Limitations recorded with the decision

- The test window is 2018-09 to 2026-08. It contains 2020 and 2022 and it does not
  contain 2008, so nothing here says how the family behaves in a credit crisis.
- Dev is 2014-09 to 2018-09, the calmest stretch of the sample, which is the same
  selection pathology Phase 2 recorded for Nifty and not an accident of that market.
- 1,920 rows failed, all GARCH-family fits at the stationarity boundary, on both windows.
  The decision uses the expanding window. Same limitation as P2-18.
- Both decisions rest on one shape of one object. A12 measured that this shape (single-day
  returns, step 5) is the one where the harness has the most power, which is the reason to
  trust this NO-GO more than a NO-GO in the cumulative shape.
