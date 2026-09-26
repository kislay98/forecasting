# Session prompt: OP-4, threshold-exceedance probabilities

Paste everything below the line into a fresh session. It is self-contained and short: this
is the smallest of the three open items, about an hour, and it changes no conclusion.

---

Work in ~/Documents/GitHub/forecasting (request folder access). Read DECISIONS.md first,
then docs/original_prompt_audit.md, then the OP-4 row in its "## Against the original
prompt" section.

Branch: `git checkout -b op4-threshold-probabilities`. Do not commit to main, do not push.

## The task

The original research prompt's section 8 asked the risk output to include the probability of
exceeding and of falling below a threshold. The report publishes quantiles, VaR and expected
shortfall, and no threshold probabilities.

## The constraint that decides the design

**Do not add a column to the forecast store.** `ForecastStore` validates schema equality on
read and `content_hash` hashes the column names, so a new column changes the recorded hash
of every run and invalidates the hashes cited in six committed decision documents. P4-8 in
DECISIONS.md is the precedent and explains why that identity is not to be moved mid-study.

So compute the threshold probabilities in the report from the quantile grid already stored,
by monotone interpolation across the stored levels. That has a real limit and the limit is
the interesting part of this task:

- The grid reaches the 0.5% and 99.5% quantiles and no further. A threshold outside the
  outermost stored quantile cannot be answered, and the honest output is a refusal, not an
  extrapolation.
- Between grid points the answer is an interpolation, not a simulated frequency. Say so
  where the number is printed, and quantify the interpolation error once against a direct
  path count so the caveat has a size.

If you conclude the grid is too coarse to be worth publishing, that is an acceptable
outcome: write it up, recommend what the store would need, and stop. Do not add the column.

## Deliverables

- `forecasting/evaluation/thresholds.py` (new file): a function taking the frame, a level
  set and one or more thresholds on the loss scale, returning P(loss beyond threshold) per
  model and horizon, plus an explicit "outside the grid" state rather than a number.
- `tests/test_thresholds.py`, known-answer style: on a Normal quantile grid the interpolated
  probability must match the analytic one within a stated tolerance, a threshold beyond the
  outermost quantile must return the refusal state, and monotonicity must hold.
- A short section in the risk report (`forecasting/evaluation/phase3_report.py`) printing the
  table for a small set of thresholds on the loss scale, with the interpolation caveat in one
  line. Thresholds are a report argument with a stated default, not a magic number.
- `docs/threshold_probabilities.md`, one page: what was asked, what the grid can and cannot
  answer, the measured interpolation error, and what a future store change would buy.
- DECISIONS.md: no new section. Append your rows to the existing
  `## Against the original prompt` table as OP-7 onwards, and update the OP-4 row in
  docs/original_prompt_audit.md in place.

## Hard constraints

- No store schema change, no `content_hash` change, no change to any existing number.
- Do not reopen P2 through P5.
- No em dashes in any file.
- `uv` is not on the shell PATH; use `.venv/bin/...` or `export PATH="$HOME/.local/bin:$PATH"`.
- Pushing needs my credentials. Leave the branch local and tell me the command.

## Why this is safe to run in parallel

Two sibling sessions may be working on the same repo, on branches
`p6-block-bootstrap` and `p7-parameter-uncertainty`. They touch
`forecasting/models/*`, `forecasting/config.py` and `forecasting/models/registry.py`. You
touch `forecasting/evaluation/*` and add a new file, so the only file you share with them is
DECISIONS.md, and only in a different table. Do not edit `forecasting/models/` at all.

## Done when

Tests pass, the report prints the table or says in writing why the grid cannot support it,
and the one-page document exists. End by stating which of the two it was.
