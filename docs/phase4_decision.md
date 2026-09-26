# Phase 4 gate decision (conformal calibration of the Nifty 50 holding-period risk)

Written 26 Sep 2026 from run `4a5899ec5e37c363` (git `09fb68aeda35`, config
`configs/phase4/phase4.yaml`, gate `configs/phase4/gate.yaml` sha256 `14a5a0afd8bd`,
committed as `584e3336445f` before the run). The registered rules were applied as
written; nothing in them was changed after seeing a test origin.

| Series | Primary model | Layer | Decision |
|---|---|---|---|
| nifty50 | gjr_garch, carried over from P3 | split conformal, window 60 | **GO**, 21 of 21 checks pass |

P3 failed on exactly one check. The correction repaired it and broke nothing else, which
is what the gate predicted. The rest of this file is about how much that is worth.

## The forecasts are Phase 3's forecasts

This was the point of holding the design fixed, so it is checked rather than asserted.
Every quantile, point forecast and expected shortfall in the P4 store is bit identical
to Phase 3's. The two stores compare equal on all 39 value columns for all 64,400 rows.

The recorded content hashes still differ (`83d0aecb` for P3, `f167a6f4` for P4). The
whole difference is `arch_p`, a diagnostic p-value, at a maximum absolute deviation of
2.9e-13. See the limitation at the end: the hash covers columns that carry last-digit
float noise, so it is a weaker identity than it claims to be.

## The decision

200 test origins, expanding window, step 20, at the Sidak-corrected per-test level of
0.0043. P3's numbers are shown beside P4's because the only difference between them is
the conformal layer.

Each cell is P3 / P4.

| Check | h = 1 | h = 5 | h = 20 |
|---|---|---|---|
| 80% coverage, band [0.75, 0.85] | 0.825 / 0.805 | 0.840 / 0.820 | **0.865 / 0.830** |
| Kupiec at 80% | 0.369 / 0.859 | 0.146 / 0.474 | 0.016 / 0.279 |
| Independence at 80% | 0.181 / 0.145 | 0.659 / 0.458 | 0.078 / 0.744 |
| 95% coverage, band [0.91, 0.98] | 0.950 / 0.970 | 0.980 / 0.965 | 0.975 / 0.975 |
| Kupiec at 95% | 1.000 / 0.162 | 0.028 / 0.305 | 0.074 / 0.074 |
| Independence at 95% | 0.509 / 0.541 | 0.685 / 0.475 | 0.612 / 0.612 |
| CRPS against the flat comparator | 0.938 / 0.957 | 0.907 / 0.964 | 0.911 / 0.972 |

Every scored test origin had the full 60-origin calibration set, so nothing was dropped
for being uncalibratable. The row count in the report falls by half because the layer
corrects the expanding window only, which is the window the decision is read from; the
rolling arm was never scored either way.

## The margin is four origins

Coverage over 200 origins moves in steps of 0.005, so the size of the repair is worth
stating in origins rather than in decimals.

| h = 20, 80% level | Origins covered | Coverage |
|---|---|---|
| Registered band | 150 to 170 | 0.75 to 0.85 |
| P3 | 173 | 0.865 |
| P4 | 166 | 0.830 |

Ten origins moved out of the interval and three moved in, a net seven. Four more covered
origins would have put it back outside the band. A decision resting on seven
observations out of 200 is a decision to state with its margin attached.

## What the layer actually did to the widths

The correction is a single number per origin, level and horizon, added to both bounds.
Its direction should be a shrink wherever the model was over-covering.

| Horizon | 80% mean width | 95% mean width |
|---|---|---|
| 1 | 0.975x, shrank on 52% of origins | 1.218x, shrank on 6% |
| 5 | 1.014x, shrank on 55% | 0.960x, shrank on 69% |
| 20 | 0.965x, shrank on 74% | 1.123x, shrank on 46% |

At the 80% level the behaviour is coherent: a mild shrink, strongest at h = 20, which is
where the over-coverage was. At the 95% level it is not. It widens by 22% at h = 1,
shrinks at h = 5, widens by 12% at h = 20, with no story that fits all three.

The reason is arithmetic, not mystery. With 60 calibration points the corrected rank is
ceil(61 x 0.95) / 60, which selects the 58th of 60 order statistics: the third largest
recent score. The 95% correction is therefore estimated from what amounts to three
observations, and it inherits their variance. Nothing failed because of this, but no
weight should be put on the 95% intervals being better calibrated after the correction
than before. They are differently calibrated, mostly by noise.

## The correction costs sharpness, and it helps the comparator more

CRPS in absolute terms, over the same test origins, before and after the layer.

| Horizon | gjr_garch before | gjr_garch after | flat before | flat after |
|---|---|---|---|---|
| 1 | 0.00484 | 0.00490 | 0.00516 | 0.00512 |
| 5 | 0.01052 | 0.01060 | 0.01160 | 0.01099 |
| 20 | 0.02276 | 0.02301 | 0.02498 | 0.02367 |

The conformal layer makes the primary model slightly worse at every horizon and makes
the flat comparator substantially better. That is the expected direction, since a
badly calibrated model has more for a width correction to fix, but it means the headline
ratio moved for the wrong reason. Phase 3's demonstrated edge for conditional variance
at a month was 0.911 of flat. After both models are corrected it is 0.972. The gate
condition still passes, and the margin behind it fell from 0.089 to 0.028.

Read together with the coverage result: the layer buys interval calibration and pays for
it in sharpness, and it transfers most of its benefit to the model that needed it most.

## The registered prediction held, and the registered risk left a mark

The gate registered a prediction and a named risk. Both are quoted.

**The prediction.** "The correction brings h = 20 coverage down inside the band and P4
returns GO." Correct, and it is the reason this file says GO.

**The risk.** "The layer adds noise at the horizons that were already passing, pushing
h = 1 or h = 5 outside their bands." It did not do that, so the registered failure mode
did not fire. It did move h = 1 at the 95% level from 0.950 to 0.970, which is 0.010
from the top of the band, in the direction of over-coverage, on a correction that widened
that cell by 22%. The risk was real and the sample was kind.

The gate also said, before the run, that dev could not validate any of this: on dev the
model covers 0.78 at h = 20, which is already right, and the layer moves it to roughly
0.85, which is worse. That statement stands unchanged after the run. `conformal_window`
was set by an a priori rule and the test result does not retrospectively justify the
rule; it only shows that this value of it worked once.

## Phase 3 is not reopened

P3 said NO-GO on a rule written and hashed in advance, and it stays said. P4 is a
separate registration answering a separate question, whether a correction registered
afterwards repairs the failure, and its answer does not travel backwards. The honest
one-line summary of the two together:

> The conditional variance model over-covers at a month. A conformal width correction,
> chosen without being able to validate it, brings it inside the registered band with
> four origins to spare, at a small cost in sharpness and a large cost in the margin by
> which conditioning beats not conditioning.

## Limitations recorded with the decision

- Exchangeability, which split conformal's finite-sample guarantee requires, does not
  hold for a volatility-clustered return series. The rolling window trades the guarantee
  for adaptivity. What is reported is an approximation with no coverage guarantee, and
  the 60-origin window is the whole of its regularisation.
- The window was not tuned, because there was nothing honest to tune it on. One value
  was registered and one was run. Whether 40 or 100 would have done better is unknown
  and cannot be checked without turning the test set into a dev set.
- Seven origins carry the decision and four are its margin. This is not a robust pass.
- The 95% correction is a third-largest-of-60 order statistic. Treat it as noisy.
- `content_hash` covers `arch_p` and `lb_p`, diagnostic p-values whose last digits move
  with BLAS reduction order. Two runs whose forecasts are bit identical can therefore
  record different hashes, which is the opposite of what M8-11 claims for the hash. The
  fix is to exclude diagnostics from the digest, and it is deliberately not being made
  mid-study, because changing the identity function would invalidate every hash already
  recorded in P1 through P4. Until then, the check that means something is a direct
  column comparison between stores, which is what was done above.
- One series, one market, 200 origins, one registered window value.
