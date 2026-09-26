# Session prompt: OP-3, propagate parameter uncertainty

Paste everything below the line into a fresh session. It is self-contained.

---

Work in ~/Documents/GitHub/forecasting (request folder access). Read DECISIONS.md first,
then docs/original_prompt_audit.md, then the OP-3 row in its "## Against the original
prompt" section. Those three tell you the state of the study and why this task exists.

Branch: `git checkout -b p7-parameter-uncertainty`. Do not commit to main and do not push it.

## The task

The original research prompt's section 13 asked for four kinds of uncertainty to be
distinguished: forecast, model, parameter and irreducible. Three are handled. Parameter
uncertainty is not: in `forecasting/models/variance.py` the fitted GARCH parameters enter
every simulated path as point estimates, so each path is conditional on the fit being
exactly right. The published intervals are therefore too narrow, by an amount nobody has
measured. The sign of the error is known; the size is the deliverable.

Do it as a registered phase, P7.

## Hard constraints

1. **Do not change the behaviour of the five existing variance models.**
   `zero_return_fhs`, `ewma`, `garch_normal`, `garch` and `gjr_garch` must produce
   byte-identical forecasts after your change. Six committed decision documents cite their
   numbers. Add new model names instead: `garch_pu`, `gjr_garch_pu`, or a better set if you
   argue for one. Prove it: run `uv run forecast run configs/phase2/phase2.yaml` before and
   after and compare every value column of the two stores, not just the content hash (P4-8
   explains why the hash is the weaker check).
2. **Register before scoring.** Write `configs/phase7/phase7.yaml` and
   `configs/phase7/gate.yaml`, commit them, then run. The gate's `expectation` names the
   predicted direction and size of the change and the registered risk, in advance.
3. **Do not reopen P2 through P5.** Those decisions stand. If P7 implies we would now
   believe something different, say so in P7's own decision document and leave theirs alone.
4. No em dashes in any file.

## The method, and what is honest about it

Do not refit. Use the asymptotic parameter covariance the fit already produces: `arch`
returns it on the fit result as `param_cov`. Draw one parameter vector per simulated path
from a multivariate Normal centred on the point estimate, then run the variance recursion
for that path with its own parameters.

Four things to handle and to write down:

| Issue | What to do |
|---|---|
| Draws that violate stationarity or positivity | Reject and redraw, and report the rejection rate per origin. A high rate is itself a finding: it means the fit sits near the boundary, which P2-18 already showed happens in crises |
| The covariance is asymptotic | So this is an approximation, not a posterior. Say so plainly in the decision document. A bootstrap over refits would be better and costs a refit per replication, which is why it is not the choice here |
| Cost | Drawing parameters per path means the recursion can no longer be vectorised the same way. Measure the runtime multiple against the point-estimate model and record it. If it is more than about 5x, reduce `n_paths` for P7 only and say so, and note the Monte Carlo noise that buys you (OP-1 in DECISIONS.md has the table) |
| The expected size of the effect | Predict it before the run. Parameter uncertainty on a well-identified GARCH fitted to 3,000 observations should widen the h = 20 interval by single-digit percent, not double. If you measure 30%, suspect the draw, not the finance |

## What to measure

The number this phase exists to produce: how much wider the intervals get, per horizon and
per level, and whether that changes any registered check. Also report the split, which is the
prompt's actual question: at h = 20, what share of total interval width comes from
innovation uncertainty and what share from parameter uncertainty.

Run the cumulative shape (the P4 design) on the Nifty, and on the S&P 500 if the runtime
allows. The single-day shape is secondary: at h = 1 there is almost no recursion for
parameter error to compound through, which is itself worth one line of measurement.

## Deliverables

- `forecasting/models/parameter_uncertainty.py` (new file, so the diff does not collide with
  other work) plus the new names in `forecasting/models/registry.py` REGISTRY and in
  `forecasting/config.py` RETURN_VARIANCE. Those two are one line each and are the only
  shared files you touch in `forecasting/`.
- `tests/test_parameter_uncertainty.py`, known-answer style: a zero covariance matrix must
  reproduce the point-estimate model exactly, a known covariance must widen the simulated
  distribution by a computable amount, the stationarity rejection must trigger on a draw
  built to violate it, and the existing models must be untouched.
- `docs/phase7_decision.md` in the style of docs/phase4_decision.md: decision first, then
  the measured widening, then what the registered prediction got right and wrong.
- `docs/phase7_report/` with the risk report and manifest copied out of the run directory.
- DECISIONS.md: one row appended to the Status table immediately after the
  `| Control (A12) |` row, and a new `## Phase 7 decisions` section placed immediately
  before `## Notes for Phase 2`. Keep all your decision rows in that section.
- Update the OP-3 row's status in docs/original_prompt_audit.md, in place, one edit.

## Practical notes

- Background jobs do not survive between shell calls on the Mac. A run over about two
  minutes has to finish inside a single call or run in the session's cloud container. Expect
  this phase to be the slowest in the study.
- `uv` is not on the shell PATH; use `.venv/bin/...` or `export PATH="$HOME/.local/bin:$PATH"`.
- Pushing needs my credentials. Leave the branch local and tell me the command.
- Expect a one-line merge conflict in `registry.py` and `config.py` with the OP-2 session.
  Whoever merges second keeps both lines.

## Done when

All tests pass including the slow tier, the existing five models are proven unchanged, the
gate was committed before the first scored origin, and docs/phase7_decision.md states how
much of the published interval width was missing and whether it changes any registered
check. End by summarising that, and whether any earlier conclusion now looks different.
