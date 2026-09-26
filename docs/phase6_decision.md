# Phase 6 decision (block-bootstrap innovations, OP-2)

Written 26 Sep 2026. Four registered runs, all from git `4d98769c4595`, the commit that
added the four gates and nothing else. No Phase 6 config was run before it. The runs
were made in the session's cloud container, not on the machine that wrote the P3 to P5
stores; the last section says why that does not move any number quoted here.

| Arm | Run | Gate sha256 | Rules |
|---|---|---|---|
| Nifty, uncorrected | `8302694e90783526` | `c52cfd12304a` | P3's |
| Nifty, conformal 60 | `c366b142179548fc` | `7cd30c8a021e` | P4's |
| S&P 500, uncorrected | `d887e82f18d7501b` | `4eb094fd614a` | P3's (P5b's diagnostic) |
| S&P 500, conformal 60 | `d887e82f18d7501b` | `709e2ad5bb62` | P5b's |

The two S&P arms share a run_id because their configs are identical once the directory
is set aside (M1-13); each manifest records its own gate.

## The decision

**Keep the iid sampler. The block sampler is not adopted, and no registered conclusion
changes.**

| Arm | iid gjr_garch, same run (diagnostic) | gjr_garch_block (registered) | Conclusion changed? |
|---|---|---|---|
| Nifty, uncorrected | NO-GO, 20 of 21 | **NO-GO, 18 of 21** | No. Same verdict, deeper failure |
| Nifty, conformal 60 | GO, 21 of 21 | **GO, 21 of 21** | No |
| S&P, uncorrected | NO-GO, 20 of 21 | **NO-GO, 19 of 21** | No. Same verdict, **different failure** |
| S&P, conformal 60 | GO, 21 of 21 | **GO, 21 of 21** | No |

The adoption rule was registered with the gates: the block sampler replaces the iid one
only if it turns no iid GO into a NO-GO across the four arms, and its CRPS at h = 20 is
below gjr_garch's on both uncorrected arms. The first condition holds. The second fails
on Nifty.

## The margin

The adoption decision turned on one number, and its interval only just excludes the
tie:

| CRPS, block over iid, uncorrected | h = 1 | h = 5 | h = 20 |
|---|---|---|---|
| Nifty | 1.0006 | 1.0129 [1.0019, 1.0249] | **1.0100 [1.0004, 1.0216]** |
| S&P | 0.9976 | 0.9993 | 0.9933 [0.9841, 1.0014] |

Intervals are a paired bootstrap over the 200 test origins (5,000 draws). On Nifty the
block model is worse at a month by 1.0%, and has the lower CRPS at only 87 of 200
origins at h = 20 and 80 of 200 at h = 5. On the S&P it is better by 0.7%, with an
interval that includes no difference. Had the rule asked only for "not worse", it would
still have failed on Nifty, whose lower bound is above 1.

The verdicts themselves moved by a few origins. Coverage over 200 origins moves in steps
of one origin, so the cells that changed are shown that way; "room" is how many more
covered (or uncovered) origins the cell could take before leaving its band, negative when
it is already outside.

| Cell | iid covered | block covered | Band in origins | iid room | block room |
|---|---|---|---|---|---|
| Nifty, 80%, h = 20 | 173 | 180 | 150 to 170 | -3 | **-10** |
| Nifty, 80%, h = 5 | 168 | 175 | 150 to 170 | +2 | **-5** |
| S&P, 95%, h = 20 | 197 | 196 | 182 to 196 | -1 | **0** |
| S&P, 95%, h = 5 | 187 | 181 | 182 to 196 | +5 | **-1** |

On the S&P the block sampler repaired the iid model's one failure by exactly one origin,
and opened a new one at h = 5 by exactly one origin, where independence also rejects
(p = 0.0028 against 0.0043). The second failure is the one that should not be discounted
for its margin: A12 measured that independence is the check with power against a
misspecified variance model.

## What the block sampler did

Measured with `scripts/block_vs_iid.py`, which fits each model once per origin and
simulates both samplers from that one fit with the same seed. sd ratio is the spread of
the 20-day cumulative return, block over iid; "linear" is what the standardised
residuals' own autocorrelations predict with no recursion at all.

| gjr_garch, median over origins | Nifty dev | Nifty test | S&P dev | S&P test |
|---|---|---|---|---|
| sd ratio at h = 20 | 1.105 | 1.110 | 0.900 | 0.901 |
| Origins moved that way | 100 of 100 wider | 200 of 200 wider | 100 of 100 narrower | 200 of 200 narrower |
| Linear prediction | 1.136 | 1.108 | 0.949 | 0.937 |
| Excess kurtosis, iid to block | 3.53 to 2.52 | 3.14 to 3.68 | 3.92 to 1.86 | 5.06 to 3.82 |

The sampler does one thing on each market and does it at every origin. Nifty's
standardised residuals carry positive autocorrelation (lag 1 about 0.09 to 0.10), so
keeping it widens the month; the S&P's carry mostly negative autocorrelation at lags 2
to 7, so keeping it narrows the month. Same method, same block length, opposite signs,
about 10% each way.

The effect on the risk numbers is the size of that spread, not the size of the
simulation noise. Mean over test origins, h = 20, uncorrected:

| gjr_garch, 97.5% | VaR iid | VaR block | ES iid | ES block |
|---|---|---|---|---|
| Nifty | 0.0976 | 0.1112 (+14%) | 0.1347 | 0.1553 (+15%) |
| S&P | 0.0952 | 0.0860 (-10%) | 0.1338 | 0.1187 (-11%) |

OP-1 measured the simulation noise on a 20-day ES at 10,000 paths as 4.7% of its value.
The sampler choice moves it by three times that.

## The registered predictions, right and wrong

**Direction. Right on both markets.** The gate predicted that the block sampler widens
Nifty's month and narrows the S&P's, both by about 10%, from dev origins only. On test:
1.110 and 0.901, every origin moving the predicted way. This was the prediction worth the
most, because the sign differs by market and was stated before any test origin was
scored.

**Nifty, uncorrected: right on the verdict and the cell, wrong on where else it broke.**
Predicted: NO-GO on the P3 cell, by more, with 80% coverage at h = 20 "about 0.88 to
0.90". It was 0.900. Predicted also that 95% at h = 20 was at risk; it did not move
(0.975). Not predicted: 80% at h = 5 left its band as well (0.875). The prediction that
block CRPS would be worse at h = 20 was right, and it is what decided adoption. The
registered risk, that the widening would be earned, did not materialise: the positive
lag-1 autocorrelation of raw Nifty returns is 0.064 before the test window and 0.004
inside it, so the block sampler carries a dependence from the 1990s and 2000s into a
market that no longer has it.

**S&P, uncorrected: the prediction was wrong and the registered risk is what happened.**
Predicted: the narrowing repairs 95% at h = 20 and the arm returns GO where iid is NO-GO.
The repair happened, by one origin. The registered risk, "overshoot: if 95% coverage at
h = 5 drops below 0.91 the sampler would have moved the failure rather than removed it",
happened too, also by one origin, and independence rejected on the same cell. The gate
named, before the run, the failure that occurred. It also predicted block CRPS below
iid at h = 20, which was right in sign (0.9933) and not established in size.

**Conformal arms: right on both verdicts, right on the S&P detail, slightly wrong on
Nifty's.** Both predicted GO, which they were, and coverage within about 0.005 of the
iid model's at every cell. On the S&P every cell is within 0.005. On Nifty two are not:
80% at h = 1 (0.805 iid, 0.790 block) and h = 5 (0.820, 0.810). The layer absorbed a
steady 10% width change, as predicted, to within one to three origins.

**The trap, double counting: right for ewma, half right for gjr_garch.** Predicted: the
recursion would count persistence twice in ewma_block and not in gjr_garch_block. On
test, ewma_block's sd ratio on Nifty is 1.125 against 1.103 from autocorrelation alone,
and its median kurtosis goes from 1.47 to 4.06: double counting, as registered.
gjr_garch_block's sd ratio is 1.110 against 1.108, so no double counting in the spread,
as registered. But its median kurtosis rose on test (3.14 to 3.68) where it had fallen on
dev (3.53 to 2.52), so the kurtosis half of that prediction was wrong on Nifty. On the
S&P the recursion amplifies the narrowing rather than the width (0.901 against 0.937),
on dev and test alike.

## What this changes in what we would now believe

P2 through P5 are not reopened and their documents are not edited (P6-5). This is what
P6 says about them.

- **P3's over-coverage at a month is not an artefact of iid sampling.** The sampler that
  respects Nifty's residual dependence makes it worse, 0.865 to 0.900. OP-2 had left
  open the possibility that the independence assumption was producing the failure; on
  Nifty it was masking some of it.
- **P5-12 is weaker than it reads on the S&P side.** It said the uncorrected failure
  recurred on a second market, "the same failure at the same horizon". Under the block
  sampler the S&P's uncorrected run still fails, but on a different cell (95% at h = 5,
  under-coverage with clustered misses), and the original cell passes by zero origins.
  The Nifty half of the replication survives the sampler change; the S&P half depended
  on it by one origin each way. The replication claim should be read as "the uncorrected
  model fails on both markets", not "fails the same way".
- **P4 and P5b stand, and are sturdier than their margins suggested.** The conformal GO
  survives a sampler change that moves raw 20-day spreads by 10% in either direction,
  because the layer re-reads the width from recent errors. The tightest cell has as much
  room or more under the block sampler: 2 origins against 1 on Nifty, 1 against 1 on
  the S&P.
- **The iid sampler is not a neutral default.** It is a modelling choice worth 10 to 15%
  of a 20-day VaR or ES, in a direction that depends on the market. The risk tables of
  P3 to P5 quote 20-day ES to three decimals; their model uncertainty from this one
  choice is larger than their simulation noise.
- **The single-day shape (P2, P5a) cannot have been affected.** A single-period target
  never simulates, so a block model's forecasts on that shape are its parent's, value for
  value (`tests/test_blocks.py` pins it). That is why the optional single-day runs were
  not made.

## Proof that the five registered models are unchanged

| Check | Rows | Value columns | Result |
|---|---|---|---|
| `configs/phase2/phase2.yaml` before (`12363e4`) and after (`4bb8e88`) | 242,040 | 39 | identical, and the same content hash `daa387a8` |
| `configs/phase4/phase4.yaml` before and after, the shape that uses the sampler | 64,400 | 39 | identical, and the same content hash `67ba868a` |
| The iid rows of every P6 run against the P4 and P5b verdicts | 21 checks x 4 | coverage, Kupiec, independence | P3, P4 and P5b's recorded cells reproduced exactly (0.865, 0.830, 0.985, 0.815 and the rest) |

All columns except `run_id` and `fit_seconds` were compared value by value, not through
the hash (P4-8). `tests/test_blocks.py` pins the same property in CI.

## Limitations recorded with the decision

- **Platform.** The runs were made in the cloud container (Linux x86_64, Python 3.11,
  numpy 2.4.6), not on the machine that wrote the P3 to P5 stores. A rerun of P4 there
  differs from the registered P4 store on every GARCH-family row: by at most 6e-8 on the
  expanding window every decision is read from, and by up to 0.0037, with 260 fits
  flipping status, on the rolling window. The iid arm of each P6 run reproduces every
  registered verdict and every quoted coverage exactly, which is the check that matters.
  Rerunning the four configs on the original machine would move last digits, not cells.
- One block length, 10, by an a priori rule, run once. Whether 5 or 20 would have
  changed an arm is unknown and cannot be learned from these test origins without
  turning them into dev.
- The moving-block scheme is not circular, so the last nine standardised residuals of a
  training window, the most recent days, are drawn less often than the rest.
- Block and iid rows use different path seeds (the seed includes the model name, D7), so
  the CRPS ratio carries a little simulation noise on top of the origin sampling the
  bootstrap covers. The same-seed comparison in `scripts/block_vs_iid.py` agrees in sign
  on both markets (Nifty 1.0075, S&P 0.9910 at h = 20).
- Parameter uncertainty (OP-3) is still not propagated, in either sampler.
- Two markets with common crises (P5-16), 200 test origins each.
- A4: the S&P run has 1.30% failed rows, all rolling-window GARCH fits, the same breach
  P5-17 recorded; the expanding window is 0.000% on both markets.

Evidence: `docs/phase6_report/` holds the four risk reports, fan charts and manifests,
the verdict comparison (`verdicts.txt`, from `scripts/phase6_compare.py`) and the
sampler measurements on dev and test (`block_vs_iid_dev.txt`, `block_vs_iid_test.txt`).
