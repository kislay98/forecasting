# Threshold-exceedance probabilities (OP-4)

Written 26 Sep 2026. Code: [forecasting/evaluation/thresholds.py](../forecasting/evaluation/thresholds.py).
Measurement: [scripts/threshold_interpolation_error.py](../scripts/threshold_interpolation_error.py).
Decisions: OP-7 to OP-11 in [DECISIONS.md](../DECISIONS.md).

## Outcome

**The grid supports the table, and the risk report now prints it.** In the tails, where a
threshold question almost always sits, the interpolation error is about the size of the
simulation noise the paths already carry. In the centre it is up to 1.9 percentage points,
and beyond the outermost stored quantile the report prints a bound instead of a number.

## What was asked

Section 8 of the original research prompt: the risk output should include the probability
of exceeding and of falling below a threshold. The report published quantiles, VaR and
expected shortfall, and no threshold probabilities, because the simulated paths are
summarised at each origin and discarded.

## Why not a new column

`ForecastStore.read` checks schema equality and `content_hash` hashes the column names, so
a column added now would change the recorded hash of every run and break the hashes cited
in six committed decision documents. P4-8 is the precedent: that identity is not moved
mid-study. The answer has to come from the ten quantiles already stored.

## What the grid can and cannot answer

| | |
|---|---|
| Stored probabilities | 0.005, 0.025, 0.05, 0.10, 0.25, 0.75, 0.90, 0.95, 0.975, 0.995 (levels 50 to 99%) |
| Answered | Any threshold between the 0.5% and 99.5% quantiles of that origin's forecast |
| Refused | Anything further out. State `outside_grid`, probability NaN, bracket [0, 0.005] on the rare side or [0.995, 1] on the near-certain side. No extrapolation |
| Crossed grids | Refused by default (`crossed_grid`). The conformal layer can cross them, see below |
| Weakest region | The centre: nothing is stored between the 25% and 75% quantiles, not even the median |

Refusal is not a corner case. On the Phase 3 run (gjr_garch, expanding window, 200 test
origins), a 5% one-day loss is beyond the grid at 184 origins and a 10% one-week loss at
181. At a month every default threshold is answerable at 197 or more origins.

## Method

Between two stored quantiles the probit of the probability is taken as linear in the
value. It is monotone, exact for any Normal distribution, and in the tails it assumes a
Normal-shaped density between grid points instead of a flat one. Plain linear
interpolation of the probability is kept as `method="linear"` for comparison. A loss
threshold L maps to the log return log(1 - L), so P(loss > L) = F(log(1 - L)).

## Measured interpolation error

For 20 Phase 4 test origins (every tenth), gjr_garch was fitted on the engine's slice and
10,000 paths simulated. The grid was built from those paths the way the engine builds it,
and at 400 thresholds spread across the grid it was compared with a direct count on the
**same** paths. Because the paths are the same, the difference is interpolation error
only. The binomial standard error of the count at 10,000 paths is given for scale.

| Region | Probit, mean abs | Probit, 95th pct | Probit, max | Linear, mean abs | Simulation noise (mean SE) |
|---|---|---|---|---|---|
| Lower tail, p < 0.05 | 0.0008 | 0.0027 | 0.0067 | 0.0020 | 0.0016 |
| Upper tail, p > 0.95 | 0.0006 | 0.0018 | 0.0041 | 0.0020 | 0.0016 |
| Shoulders, 0.05 to 0.25 and 0.75 to 0.95 | 0.0019 | 0.0056 | 0.0129 | 0.0071 | 0.0035 |
| Centre, 0.25 to 0.75 | 0.0051 | 0.0119 | 0.0192 | 0.0073 | 0.0048 |

What this says:

- **In the tails the interpolation is not the bottleneck.** Its mean error is half the
  simulation noise already in the count. Relative to the probability itself it is 4.6% on
  average and 20% at the 95th percentile, and the worst case, a 1.6% answer where the count
  said 1.0%, is at h = 1, where the filtered historical simulation draws from a finite set
  of residuals and the tail is lumpy.
- **The centre is where the grid is thin.** Errors up to 1.9 percentage points, because
  there is no stored point between the 25% and 75% quantiles. A question like "what is the
  chance of any loss at all over a month" is answered to about two points.
- **Probit beats linear everywhere**, by a factor of 2.4 to 3 in the tails. On a Normal
  grid probit is exact; on a Student t(4) grid its worst error is 0.0046 (the tests pin
  both).

At the report's default thresholds the probit error averaged 0.0008 to 0.0029 across
horizons, never above 0.0080.

## The conformal layer crosses quantiles

P4 corrects each level separately, and the 99% correction is the largest of the last 60
scores. After the correction, 3,864 of 20,000 test rows (19%) have a crossed grid, 181 of
600 for gjr_garch at the decision horizons. Almost all are the 99% band sitting inside the
95% band on the upper side, the same noise P4-7 recorded for the 95% corrections. The
report sorts a crossed grid before interpolating, which is the standard repair and never
moves a quantile further from the truth, and it says how many rows that touched. The
published intervals are not changed and P4 is not reopened: its registered checks read the
80% and 95% bands one level at a time.

## What a future store change would buy

| Change | Buys | Costs |
|---|---|---|
| A 10% and a 99.8% level added to `levels` in config | The 45% and 55% quantiles split the empty centre in three, where the worst error sits (the gain is not measured); refusals move out from the 0.5% to the 0.1% tail. No code change, since columns follow the levels | A new run_id and a new hash for every run, so only at a phase boundary |
| Named thresholds counted from the paths in the engine, one column per threshold | Exact frequencies with simulation noise only, and answers beyond the grid down to about 1 in 10,000 paths | A schema and `content_hash` change, and thresholds become part of the run's identity |

Recommendation: neither now. If a later phase registers new runs, add the levels at that
boundary together with the P4-8 fix, so the hash breaks once rather than twice. The count
columns are only worth it if someone needs probabilities rarer than 0.5%, and at 10,000
paths those would be too noisy to quote per origin anyway (OP-1).
