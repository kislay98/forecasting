# Phase 5b gate decision (the Phase 4 decision, on the S&P 500)

Written 26 Sep 2026 from run `bae058ac35a5322d` (git `b76eba38e27f`, config
`configs/phase5b/phase5b.yaml`, gate `configs/phase5b/gate.yaml` sha256 `223acc758950`,
committed as `e9022578d708` before the run). The gate was written before the S&P 500 data
file existed in the repo. `gjr_garch` and the conformal window of 60 were carried over
from Phase 4 by name.

| Series | Primary model | Layer | Decision |
|---|---|---|---|
| sp500 | gjr_garch, carried over from P4 | split conformal, window 60 | **GO**, 21 of 21 checks pass |

The result replicates Phase 4, and it replicates the failure Phase 4 was built to fix.
Both halves matter, and the second one is the evidence.

## The failure appeared again, on its own

Before the conformal layer, the S&P run is the P3 design exactly. Applying P3's rules to
it, as a diagnostic rather than a registered decision, gives:

| Uncorrected S&P, P3 shape | Result |
|---|---|
| Decision | NO-GO, 20 of 21 checks pass |
| The one failure | 95% coverage at h = 20: 0.985 against [0.91, 0.98] |
| Kupiec on the same cell | p = 0.0080, clears the corrected 0.0043, passes |

Set that beside Nifty's Phase 3, which failed on 80% coverage at h = 20 with 0.865 against
[0.75, 0.85] and Kupiec passing at 0.016 on the same cell. Two independent markets, the
same design, and the same failure: over-coverage at the longest horizon, one check, the
coverage band carrying the decision while Kupiec lacks the power to see it. The level
differs, 95% here against 80% there, and nothing else does.

That is the part of this run that is worth more than the GO. The error P4 was registered
to repair was not a Nifty artefact.

## What the correction did

Per-horizon, primary model, before and after the layer.

| Horizon | Level | Coverage before | Coverage after | Mean width |
|---|---|---|---|---|
| 1 | 80% | 0.795 | 0.830 | 1.044x |
| 1 | 95% | 0.955 | 0.975 | 1.132x |
| 5 | 80% | 0.785 | 0.800 | 1.056x |
| 5 | 95% | 0.935 | 0.965 | 1.238x |
| 20 | 80% | 0.810 | 0.815 | 0.992x |
| 20 | 95% | **0.985** | **0.960** | 0.886x |

The registered expectation asked for exactly this comparison in advance, because Nifty had
only ever tested one direction: "If the S&P model under-covers instead, the correction will
widen it, and a GO here would be the layer fixing the opposite error. That would be
stronger evidence for the method than a second shrink."

Both directions appeared in one run. At h = 1 and h = 5 the uncorrected model under-covers
and the layer widens, by up to 24% at h = 5 and 95%. At h = 20 and 95%, where the failure
was, it shrinks by 11% and brings 0.985 to 0.960. So the layer is not a one-sided
adjustment that happened to point the right way on Nifty; it reads the recent errors and
moves either way. It is also not converging on nominal: at h = 1 it overshoots from 0.795
to 0.830, which the wide registered band tolerates and a tighter one would not.

## The registered prediction was wrong

The gate predicted, in writing, before the run: "this fails, or passes with a margin too
thin to mean anything. P4 passed by four origins out of 200, about one standard error of a
coverage estimate at that sample size, so an independent market has roughly even odds of
landing on the other side of the band."

It passed, and not by a hair.

| Check that carried P4 | Nifty (P4) | S&P (P5b) |
|---|---|---|
| 80% coverage at h = 20 | 0.830, four origins inside the band | 0.815, seven origins inside |
| 95% coverage at h = 20 | 0.975 | 0.960, four origins inside a 0.98 ceiling |

The reasoning behind the prediction was sound and the arithmetic in it was wrong in a way
that was checkable before the run. The gate said "the S&P dev window contains 2000 to 2002,
and the test window contains 2008 and 2020", and it does not. The actual windows:

| Run | Warm-up | Dev | Test |
|---|---|---|---|
| P5b | 1998-01 to 2002-10 | 2002-11 to 2010-09 | 2010-10 to 2026-08 |

Dev contains 2008, the most violent stretch in the sample, and test is the calmer period
after it with one spike in 2020. That is the same ordering as Nifty, a model selected in a
volatile regime meeting a calmer one, which is the precise mechanism P4 named for the
over-coverage. Had the calendar been read correctly when the gate was written, the
prediction would have been that the same failure recurs, which is what happened. The
lesson is not about conformal prediction; it is that a registered expectation has to get
its facts right to be worth anything, and this one did not.

## The cost replicates too

Absolute CRPS on the test origins, before and after the layer.

| Horizon | gjr_garch before | gjr_garch after | flat before | flat after | Ratio before | Ratio after |
|---|---|---|---|---|---|---|
| 1 | 0.004799 | 0.004839 | 0.005265 | 0.005329 | 0.911 | 0.908 |
| 5 | 0.010932 | 0.011051 | 0.011559 | 0.011536 | 0.946 | 0.958 |
| 20 | 0.019439 | 0.019581 | 0.021489 | 0.020588 | 0.905 | 0.951 |

The same pattern as Nifty, on a second market. The layer makes the primary model slightly
worse at every horizon (0.7% at h = 20) and the flat comparator markedly better (4.2% at
h = 20), so the registered ratio erodes from 0.905 to 0.951. On Nifty it went from 0.911 to
0.972. A width correction has more to fix in a model that was badly calibrated, so it
transfers most of its benefit there, and the measured edge of conditioning over not
conditioning shrinks by roughly half as a result. That is now a replicated finding rather
than an observation from one run.

## What a 21 of 21 is worth here

Less than it looks, and the gate said so before the run. A12 measured that in this shape
(cumulative object, origin step 20) the coverage bands and Kupiec have no power against a
model with no conditional variance at all, and that Christoffersen caught it in only two of
three synthetic seeds. A table where every check passes is therefore weak evidence about
conditioning.

The column that carries information is the CRPS ratio, and it says conditioning pays on
this market: 0.908, 0.958, 0.951 against the flat comparator at h = 1, 5 and 20. The
uncorrected numbers, 0.911, 0.946, 0.905, say it more strongly.

## Limitations recorded with the decision

- Everything in the P4 limitations list still applies: exchangeability does not hold for a
  clustered return series, the window was not tuned, and at the 95% level the correction is
  a third-largest-of-60 order statistic and should carry no weight on its own. The 1.132x
  and 1.238x widenings at h = 1 and h = 5 are at that level and are probably mostly noise.
- 840 rows failed, all GARCH-family fits at the stationarity boundary. The decision uses
  the expanding window. Same limitation as P2-18.
- Two markets is two, and they are both large-cap equity indices with a common global
  factor. Their 2008 and 2020 are the same 2008 and 2020, so these are not independent
  samples in the way two seeds of a simulation are.
- 200 test origins, so anything read from the far tail of the risk table is illustrative.
