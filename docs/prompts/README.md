# Session prompts for the open items

Three sessions, runnable at the same time, each on its own branch. Paste the part of each
file below its horizontal rule into a fresh session.

| File | Item | Branch | Cost | Can it change a conclusion? |
|---|---|---|---|---|
| [op2-block-bootstrap.md](op2-block-bootstrap.md) | OP-2, the residual sampler assumes an independence the data reject | `p6-block-bootstrap` | a session | Yes |
| [op3-parameter-uncertainty.md](op3-parameter-uncertainty.md) | OP-3, parameter uncertainty is not propagated | `p7-parameter-uncertainty` | a session, the slowest to run | Direction yes, size unknown |
| [op4-threshold-probabilities.md](op4-threshold-probabilities.md) | OP-4, no threshold-exceedance probabilities | `op4-threshold-probabilities` | about an hour | No |

OP-5 (a modern model, and monitoring) is deliberately not being done. Both have triggers
written before the fact: covariates or a panel for the first, a deployment for the second.

## How they stay out of each other's way

| Area | OP-2 | OP-3 | OP-4 |
|---|---|---|---|
| New code | `forecasting/models/blocks.py` | `forecasting/models/parameter_uncertainty.py` | `forecasting/evaluation/thresholds.py` |
| New tests | `tests/test_blocks.py` | `tests/test_parameter_uncertainty.py` | `tests/test_thresholds.py` |
| Registration | `configs/phase6/` | `configs/phase7/` | none |
| Decision doc | `docs/phase6_decision.md` | `docs/phase7_decision.md` | `docs/threshold_probabilities.md` |
| DECISIONS.md | new `## Phase 6 decisions` section, status row after Phase 5b | new `## Phase 7 decisions` section, status row after Control (A12) | rows appended to the existing prompt-audit table, no new section |
| `forecasting/models/registry.py` | one line | one line | never touched |
| `forecasting/config.py` | one line | one line | never touched |

Two one-line collisions are expected, in `registry.py` and `config.py`, between OP-2 and
OP-3. Whoever merges second keeps both lines.

## Rules all three inherit

- Existing model behaviour is frozen. The five variance models must produce byte-identical
  forecasts, because six committed decision documents cite their numbers. New behaviour
  arrives as new model names.
- The gate is committed before the first scored test origin, and its `expectation` names the
  predicted outcome and the registered risk in advance.
- P2 through P5 are not reopened, whatever these phases find.
- No store schema change and no `content_hash` change. P4-8 says why.
- No em dashes in any file.
