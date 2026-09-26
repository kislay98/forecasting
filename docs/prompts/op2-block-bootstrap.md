# Session prompt: OP-2, block bootstrap for the residual sampler

Paste everything below the line into a fresh session. It is self-contained.

---

Work in ~/Documents/GitHub/forecasting (request folder access). Read DECISIONS.md first,
then docs/original_prompt_audit.md, then the "## Against the original prompt" section's
OP-2 row. Those three tell you the state of the study and why this task exists.

Branch: `git checkout -b p6-block-bootstrap`. Do not commit to main and do not push it.

## The task

The original research prompt's section 7C asked whether temporal dependence makes
independent residual resampling inappropriate. It was investigated and the answer is yes
on this data: Ljung-Box rejects on every model's residuals. The sampler in
`forecasting/models/variance.py` still resamples standardised residuals independently
(`VarianceModel._draw` calls `rng.choice(self._z, ...)`). Every interval and every
expected shortfall in the study is built on that sampler.

Fix it as a registered phase, P6, not as a patch.

## Hard constraints

1. **Do not change the behaviour of the five existing variance models.** `zero_return_fhs`,
   `ewma`, `garch_normal`, `garch` and `gjr_garch` must produce byte-identical forecasts
   after your change. Six committed decision documents cite their numbers. Add new model
   names instead: `ewma_block`, `garch_block`, `gjr_garch_block`, or a cleaner set if you
   argue for one. Prove it: run `uv run forecast run configs/phase2/phase2.yaml` before and
   after, and compare every value column of the two stores, not just the content hash
   (P4-8 explains why the hash is a weaker check than it looks).
2. **Register before scoring.** Write `configs/phase6/phase6.yaml` and
   `configs/phase6/gate.yaml`, commit them, and only then run anything that scores test
   origins. The block length is part of the registration and is chosen by a stated a priori
   rule, not tuned. The gate's `expectation` names the predicted outcome and the registered
   risk in advance, as configs/phase4/gate.yaml does.
3. **Do not reopen P2 through P5.** Those decisions stand whatever P6 finds. If P6 changes
   what we would now believe, say so in P6's own decision document and leave theirs alone.
4. No em dashes in any file.

## The method, and the trap in it

Filtered historical simulation assumes the standardised residuals are iid. They are not, so
resample blocks of consecutive residuals rather than single values. Either the moving-block
bootstrap or the stationary bootstrap of Politis and Romano is defensible; pick one, say
why, and record the block length rule.

The trap, which belongs in the registered expectation: the variance recursion already has
memory, and a GARCH path already produces clustering because a large drawn innovation feeds
the next step's variance. Feeding it blocks of dependent innovations can double-count
persistence and make the cumulative distribution too wide. So the registered prediction is
not "coverage improves". It is a direction you argue for in advance, with the opposite named
as the risk. Measure it: the h = 20 cumulative standard deviation and kurtosis of the
simulated distribution, block against iid, on the same fitted model.

## What to run

Four configs, since the question is whether this changes a conclusion, not whether it runs:

| Config | Shape | Market |
|---|---|---|
| configs/phase6/ | cumulative, step 20 (the P4 shape) | Nifty |
| a second series entry or sibling config | cumulative, step 20 | S&P 500 |
| optionally the P2 shape (single-day, step 5) on both | | |

Prioritise the cumulative shape: it is where path dependence compounds and where the block
sampler should matter most. The single-day shape at h = 1 barely uses the path.

## Deliverables

- `forecasting/models/blocks.py` (new file, so the diff does not collide with other work)
  plus the new names in `forecasting/models/registry.py` REGISTRY and in
  `forecasting/config.py` RETURN_VARIANCE. Those two are one line each and are the only
  shared files you touch in `forecasting/`.
- `tests/test_blocks.py`, known-answer style like tests/test_calibration.py: a sampler that
  reproduces a known autocorrelation in its output, a degenerate block length of 1 that
  matches the iid sampler exactly, and a test that the existing models are untouched.
- `docs/phase6_decision.md`, in the style of docs/phase4_decision.md: lead with the
  decision, then the margin, then what the registered prediction got right and wrong.
- `docs/phase6_report/` with the risk report and manifest copied out of the run directory.
- DECISIONS.md: one row appended to the Status table immediately after the `| Phase 5b |`
  row, and a new `## Phase 6 decisions` section placed immediately before
  `## Against the original prompt`. Keep all your decision rows in that section.
- Update the OP-2 row's status in docs/original_prompt_audit.md, in place, one edit.

## Practical notes

- Background jobs do not survive between shell calls on the Mac. A `forecast run` that takes
  more than about two minutes has to run inside a single call or in the session's cloud
  container. The cumulative-shape runs take 5 to 10 minutes on two cores.
- `uv` is not on the shell PATH; use `.venv/bin/...` or `export PATH="$HOME/.local/bin:$PATH"`.
- Pushing needs my credentials. Leave the branch local and tell me the command.
- Expect a one-line merge conflict in `registry.py` and `config.py` with the OP-3 session.
  Whoever merges second keeps both lines.

## Done when

All tests pass including the slow tier, the existing five models are proven unchanged, the
gate was committed before the first scored origin, and docs/phase6_decision.md states a
decision and its margin. End by summarising what changed and whether any earlier conclusion
now looks different.
