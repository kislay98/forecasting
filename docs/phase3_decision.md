# Phase 3 lite gate decision (Nifty 50 holding-period risk)

Written 26 Sep 2026 from run `4259d18205518ed9` (git `31a987f80192`, config
`configs/phase3/phase3.yaml`, gate `configs/phase3/gate.yaml` sha256 `6bb36f41d45d`,
committed as `e14c51e0bc21` before the run). The registered rules were applied as
written; nothing in them was changed after seeing a test origin.

| Series | Primary model | Decision |
|---|---|---|
| nifty50 | gjr_garch | **NO-GO**, 20 of 21 checks pass |

The single failure is coverage at h = 20 and the 80% level: 0.865 against a registered
band of [0.75, 0.85]. The interval is too wide by about one and a half points of
coverage at the horizon the whole phase was built for.

## The decision

200 test origins, expanding window, 2010-07-12 to 2026-08-24, step 20, at the
Sidak-corrected per-test level of 0.0043.

| Check | h = 1 | h = 5 | h = 20 |
|---|---|---|---|
| 80% coverage, band [0.75, 0.85] | 0.825 | 0.840 | **0.865 FAIL** |
| Kupiec at 80% | p 0.369 | p 0.146 | p 0.016 |
| Independence at 80% | p 0.181 | p 0.659 | p 0.078 |
| 95% coverage, band [0.91, 0.98] | 0.950 | 0.980 | 0.975 |
| Kupiec at 95% | p 1.000 | p 0.028 | p 0.074 |
| Independence at 95% | p 0.509 | p 0.685 | p 0.612 |
| CRPS against the flat comparator | 0.938 | 0.907 | 0.911 |

Note what the statistical tests do here and what they do not. Kupiec at h = 20 and 80%
returns 0.016, which clears the corrected 0.0043 and therefore passes, while the
coverage band fails on the same cell. That is the registered design working as intended:
with 200 origins the p-values are underpowered, and the bands were put there to carry
the decision. Had the gate rested on significance alone, this run would have been a GO.

## One registered prediction was right and one was wrong

The gate recorded two predictions before the run.

**Wrong.** It predicted that condition 4, CRPS against the flat comparator, would fail at
h = 20, on the basis that dev showed conditioning losing its edge by a month
(gjr_garch 1.012 of flat). On test it did the opposite: 0.911 at h = 20, and it beats
the flat model at every horizon. Conditioning on current volatility does pay over a
month after all. The dev reading was misleading because the dev window (2002-2010) is
the most volatile stretch in the data at 27.9% annualised, where a flat unconditional
variance is closest to right.

**Right.** It predicted that the failure to watch for was over-coverage, from a model
selected in a violent regime facing a calm one. That is exactly the failure: 0.865
where 0.80 was wanted, intervals carrying more of the old regime than the new one needs.

Both are recorded because a pre-registration that only gets quoted when it was right is
decoration.

## The flat comparator collapses

The reason this is a near miss rather than a failure of the whole idea is visible in
the comparator. `zero_return_fhs`, flat variance with empirical tails, covers 0.950 at
h = 20 against a nominal 0.80, with Kupiec p below 0.0001, and 0.9950 at the 95% level.
It is not close. Its 20-day 95% VaR quotes a 10.0% loss where the conditional model
quotes 7.5%, and it breaches 4 times where 10 were expected.

So the finding is not "conditioning does not help at a month". It is "conditioning
helps a great deal, and the particular model chosen is still slightly too wide".

## What the risk numbers look like

Averages over the test origins, as fractions of the position, for the primary model.

| Horizon | 95% VaR | Expected shortfall given a breach | Breaches | Expected |
|---|---|---|---|---|
| 1 day | 1.67% | 2.32% | 9 | 10 |
| 5 days | 3.73% | 5.27% | 9 | 10 |
| 20 days | 7.50% | 11.03% | 9 | 10 |

Expected shortfall is roughly 1.4 times VaR at every horizon, which is the number that
matters for sizing and which a VaR alone never tells you.

The expected-shortfall backtest is mostly unusable at this sample size and the report
says so rather than implying a pass: at 200 origins a 97.5% VaR produces about five
breaches, and the test declines below ten. Only two rows in the whole table have enough
breaches to judge, and both cover zero. The honest position is that VaR calibration has
been tested and ES calibration has not.

## A reproducibility defect found and fixed

The same config produced different forecasts at different `n_jobs`. It was diagnosed
rather than guessed: `ewma` and `zero_return_fhs` were byte identical across the two
settings, so the seeding, the fold dispatch and the path simulation are all
deterministic. Only the three optimiser-fitted GARCH models drifted, by up to 9e-4 in
the point forecast, and the failure pattern moved too, because a fit near the
stationarity boundary flips on an arithmetic difference that small.

The cause is threaded linear algebra: the reduction order inside BLAS depends on the
thread count, and joblib gives its workers a different count than an in-process run.
Pinning the fit to one thread removes it, and n_jobs 1 and 4 now produce the same
content hash.

This matters beyond Phase 3. Until this commit, "reproducible" was conditional on
running with the same `n_jobs`, and nothing in Phase 1 or 2 would have caught it,
because their A1 check re-runs a config with the settings it came with and so never
varied the one that mattered.

## Limitations recorded with the decision

- 480 rows failed, all `garch`, `garch_normal` and `gjr_garch` on the **rolling**
  window at the stationarity boundary. The decision uses the expanding window, so it is
  unaffected, but a GARCH layer that cannot fit on a rolling window in a crisis is not
  something to build on without noticing.
- 200 test origins is thin. At the 95% level ten misses are expected, and at 99.5% one.
  Anything read from the far tail of this table is illustrative, not tested.
- The PIT test cannot run at this sample size: its smallest nominal bin is 0.5% wide
  and needs about 1000 observations. It is reported as unavailable rather than as a pass.
- One series, one market, and a test period whose calm dominates its shocks.
