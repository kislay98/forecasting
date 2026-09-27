# Prompt: post 4, "The second market"

Paste everything below the rule into a fresh session. Self-contained.

---

Write one blog post for kislay.pages.dev. Do not publish it. Hand back the post file, its
figure components, and a short note on anything you could not verify.

Source material is the repository at ~/Documents/GitHub/forecasting (request folder
access). The blog lives at ~/Documents/Goals/site, an Astro site with MDX posts in
`src/content/posts/` and figure components in `src/components/figures/`. Read one existing
post and two existing figure components before writing a line, and match what they do.

## The claim this post makes

One market is an anecdote. Taking the same registered designs to the S&P 500, with nothing
re-chosen, the model that had been selected on Indian data failed, and the flaw that a
correction had been built to repair turned up on the new market unprompted. One of those
two results is worth more than the other, and it is not the one that passed.

## Why a reader should finish it

This is what replication actually does to conclusions, written by someone whose own
registered prediction about it was wrong for a reason he can name.

## What to read, in this order

1. `docs/phase5a_decision.md` and `docs/phase5b_decision.md`, including the dated
   correction note at the end of the second.
2. `configs/phase5a/gate.yaml` and `configs/phase5b/gate.yaml`, the `expectation` blocks.
   These were committed before the data file existed in the repository, which is the
   strongest form the registration takes and is worth saying out loud.
3. `docs/phase4_decision.md` for what was being replicated.
4. `forecasting/evaluation/conformal.py`, for the correction and for the bug at the end.
5. `DECISIONS.md`, the Phase 5 section and rows OP-12 to OP-14.

Do not open `Claude outputs/the-gate-said-no.md`.

## The spine

1. Open with the discipline, briefly: both rules were written and committed before the
   second market's data was even downloaded. Nothing in them could have been informed by
   what they would judge.
2. What "replication" meant here in mechanical terms. Same start date, same origin spacing,
   same counts, same thresholds, same corrected significance level, same primary model
   carried over by name. Only the series changed. Say why re-picking the model would have
   answered a much weaker question.
3. Result one, the daily-interval design: it failed, on a single check, by 0.0008. Do not
   soften that margin. Explain why it was recorded as a failure anyway: it is the one check
   with measured power against this kind of error, and the average miss rate was almost
   exactly right while the misses arrived in runs.
4. The detail that makes it more than a near miss: every model in the family sits on the
   same cell. No member of the registered family removes the clustering at a week on that
   market. So the finding is not "the wrong model was picked", it is "this family does not
   fit this market at this horizon".
5. What did replicate from that phase, stated fairly: conditioning on volatility pays on
   both markets, and the flat interval is badly wrong on both.
6. Result two, the monthly design, and the real story of the post. The uncorrected run on
   the new market failed exactly one check, at the same horizon, with the same test passing
   on the same cell and the coverage band carrying the decision. Different level, same
   shape. Put the two failures side by side in a table.
7. The correction itself: watch how far outside its own interval the truth has been landing
   lately, and move the interval by that much. The conformity score deserves a figure and
   one line of algebra. Then the result: it moved in both directions on the new market,
   widening where the model was too narrow and shrinking where it was too wide, which the
   first market had never tested.
8. The cost, because it replicated too. The correction makes the good model slightly worse
   and the bad comparator markedly better, so the measured edge of conditioning halves.
9. The paragraph where the author was wrong. The registered prediction said this would fail
   or pass by a hair, and the reason given was a statement about which years fell in which
   window. The statement was checkable and false. Read correctly, the calendar predicts the
   result that actually happened. Say it plainly and do not soften it.
10. End on the defect that closed the study, because it is a good ending. Reading the whole
    quantile grid rather than the two levels the gates use turned up intervals that crossed:
    a 99% band sitting inside the 95% one, in a fifth of the corrected rows on one market
    and more than a quarter on the other. The gates never looked there. Fixing it moved one
    published number and no decision.

## Numbers you may use, all verifiable in the repository

| Fact | Value |
|---|---|
| S&P history used | 7,734 trading days from 1996-01-02, 19.0% annualised volatility |
| Daily-interval replication | NO-GO, 13 of 14 checks pass. Independence at h = 5 and the 80% level: p = 0.0056 against a corrected threshold of 0.0064 |
| That cell in detail | 82 misses where 80 were expected. P(miss after a miss) 0.321 against P(miss otherwise) 0.176. Longest run 5 consecutive origins, which is 25 trading days |
| The whole family on that cell | flat with normal tails 0.0001, flat with empirical tails 0.0045, the chosen model 0.0056, the asymmetric GARCH 0.0093, plain GARCH 0.0300 |
| Which model would have won there | the asymmetric one, on CRPS at both horizons, 0.0049 against 0.0051 at h = 1 |
| The monthly design, uncorrected, new market | NO-GO on one check: 95% coverage at h = 20 was 0.985 against a band of [0.91, 0.98], with the significance test passing at 0.0080 |
| The same phase on the first market | NO-GO on one check: 80% coverage at h = 20 was 0.865 against [0.75, 0.85], with the significance test passing at 0.016 |
| After the correction, new market | GO, 21 of 21 |
| The correction's direction, new market, before and after | h = 1 at 80%: 0.795 to 0.830. h = 5 at 95%: 0.935 to 0.965, widening by 1.24x. h = 20 at 95%: 0.985 to 0.960, shrinking by 0.89x |
| The cost | primary model CRPS 0.019439 to 0.019581 at h = 20, flat comparator 0.021489 to 0.020588, so the ratio moved from 0.905 to 0.951. On the first market it moved from 0.911 to 0.972 |
| The calendar error in the registered prediction | it claimed the test window contained 2008. Development was 2002-11 to 2010-09 and held 2008; test was 2010-10 to 2026-08 |
| The crossing defect | 19.3% of corrected rows on the first market, 28.9% on the second, mostly the 99% band inside the 95%. Fixing it moved 95% coverage at h = 20 on the second market from 0.960 to 0.970, inside the same band, and changed no decision |

## Figures

Four. Follow the reference discipline below.

1. **The conformity score.** A number line with the interval marked, one outcome inside and
   one outside, and the score as the signed distance to the nearer bound. Then the same
   line with the interval moved by the correction. Two panels, the algebra inside the
   picture.
2. **The same failure, twice.** Two small coverage plots side by side, one per market, with
   the registered band shaded and the failing point marked. The point of the figure is that
   the shape is identical and the level is not.
3. **Both directions.** Before and after coverage for all six cells on the new market, as a
   dot plot with arrows, nominal marked. Widening at short horizons, shrinking at the long
   one, all in one picture.
4. **The crossed grid.** Nested intervals drawn as bars, showing the 99% band inside the
   95%, then the same row after the repair. Short, and it lands.

## Figure discipline

Reference: the Hevo engineering posts on Medium, "Building a lossless Kafka Producer" and
"Where's that event?". Plain boxes and arrows, near monochrome, numbered callouts with a
legend column, grey caption beneath in sentence case. Copy that discipline, not the subject.

- Inline SVG in an Astro component, one per figure, in the site's figures folder.
- Readable in light and dark. Use the site's figure accent token rather than a hard-coded
  near-white, which broke a chart once.
- Legible at 320px wide, no horizontal scroll.
- Figures 2 and 3 use real numbers from the decision documents. Do not approximate them.

## Voice

One engineer telling another what happened. Past tense, first person singular. The reader
knows what a confidence interval is and has never heard of conformal prediction, so
introduce it by what it does rather than by its name.

Banned, because they read as machine-written: "delve", "moreover", "furthermore", "it is
worth noting", "at its core", "game-changer", "leverage" as a verb, "robust" as filler,
"seamless", "let's dive in", "in this post we will". No roadmap paragraph after the intro.
No conclusion that restates the post. No em dashes anywhere. Vary sentence length hard.

The wrong-prediction paragraph is not an aside, it is a load-bearing part of the post.
Write it without self-flagellation and without excuses: the reasoning was sound, the fact
under it went unchecked, and checking it was a two-line query.

Close on what is still not established rather than on a summary. Two large-cap equity
indices share their crises, so they are not the independent samples the word "replication"
suggests, and a defensible change to one sampling choice moves a 20-day expected shortfall
by 10% to 15%. That is the honest ceiling of what the work demonstrates.

## Length

1,200 to 1,800 words. Hard cap 2,000. Short plain title.

## Done when

The post file and figure components exist, unpublished, and the handback note lists every
number you could not verify in the repository.
