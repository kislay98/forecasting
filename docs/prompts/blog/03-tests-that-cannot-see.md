# Prompt: post 3, "The tests could not see it"

Paste everything below the rule into a fresh session. Self-contained.

---

Write one blog post for kislay.pages.dev. Do not publish it. Hand back the post file, its
figure components, and a short note on anything you could not verify.

Source material is the repository at ~/Documents/GitHub/forecasting (request folder
access). The blog lives at ~/Documents/Goals/site, an Astro site with MDX posts in
`src/content/posts/` and figure components in `src/components/figures/`. Read one existing
post and two existing figure components before writing a line, and match what they do.

## The claim this post makes

A statistical test you have not measured the power of is a decoration. On a synthetic world
whose answer was known in advance, two of the three standard interval-calibration checks
could not detect a model with no conditional variance at all. The third could. Most
write-ups lean on the two that cannot.

This is the most portable post in the series and it should read that way. The finance is
the setting, not the subject.

## Why a reader should finish it

Anyone who runs a statistical check to gate a decision has this problem and has probably
never measured it. The method generalises: build a world where you know the answer, make
your check pass the right model and catch the wrong one, and count how often it does.

## What to read, in this order

1. `tests/test_control.py`. The whole file, including the docstrings, which carry the
   measured power table.
2. `DECISIONS.md`, the section "Calibration positive control (A12)", rows A12-1 to A12-8.
3. `tests/synthetic.py`, the `garch11_returns` generator and its self-check in
   `tests/test_synthetic.py`.
4. `forecasting/evaluation/calibration.py` for what the three checks actually compute.
5. `DECISIONS.md` row OP-1 and `scripts/mc_standard_error.py` for the second half.

Do not open `Claude outputs/the-gate-said-no.md`.

## The spine

1. Open on the uncomfortable question. Your test says the model is calibrated. How do you
   know the test can tell? Keep this to four sentences, no preamble.
2. The idea: write the world yourself. A GARCH process with parameters you chose means the
   correctly specified model is known in advance and so is a model that is wrong in exactly
   one way.
3. The two arms, and why both are needed. A check that cannot pass a correct model raises
   false alarms. A check that cannot catch a wrong one is not measuring anything. Most
   people build the first arm and skip the second.
4. What the three checks are, in one line each and in plain language. Coverage band: did
   the truth land inside the interval about as often as promised. Kupiec: is that rate
   significantly off. Christoffersen independence: did the misses arrive scattered or in
   runs.
5. The result, which is the post. Over three seeds, the wrong model was caught by the
   independence test almost every time, by a coverage band once, by Kupiec once, and in the
   month-horizon setup by the bands and Kupiec never.
6. Why. A flat variance model with empirical tails is right on average and wrong in
   sequence. Its unconditional coverage lands inside the band at every seed. Only one of
   the three checks reads the sequence, so only one can see the error. This deserves its
   own figure and is the sentence readers will quote.
7. The trade-off that fell out. Spacing the forecast origins 20 days apart is what makes
   the independence test valid at a 20-day horizon, and it is also what costs the test most
   of its power at 1 and 5 days, where the spacing used to be 5. Validity and power pulled
   in opposite directions and the measurement says by how much.
8. Second half, shorter: how many Monte Carlo trials. The original plan said a thousand.
   Repeating the same simulation from the same fitted model at different path counts, with
   only the seed changing, turns that into a number instead of a preference.
9. End on the two numbers together: a thousand paths puts about 15% of its own value in
   noise on every expected shortfall you print, and the correction that one decision in this
   study turned on was smaller than the per-origin noise at the count actually used. It
   survived only because it averaged over 200 origins.

## Numbers you may use, all verifiable in the repository

The DGP: GARCH(1,1) with normal innovations, omega 1.6e-6, alpha 0.09, beta 0.90, so
persistence 0.99. Unconditional volatility about 18% annualised, excess kurtosis about 4.44
against a normal's 3.0, 8,000 days after a 2,000-day burn-in.

Measured over seeds 1, 2 and 3, before the assertions were written:

| Arm | Shape A: daily returns, origins 5 apart, 400 of them | Shape B: 20-day cumulative, origins 20 apart, 300 of them |
|---|---|---|
| Correct model passes every check | yes | yes |
| Wrong model caught at all | 3 of 3 | 2 of 3 |
| Caught by a coverage band | 1 of 3 | 0 of 3 |
| Caught by Kupiec | 1 of 3 | 0 of 3 |
| Caught by Christoffersen independence | 3 of 3 | 2 of 3 |

Monte Carlo standard error, 30 repetitions per count, 20-day cumulative returns from one
fitted GJR-GARCH, model held fixed:

| Paths | Noise on a 95% band edge | As a share of band width | Noise on 5% expected shortfall | As a share of ES |
|---|---|---|---|---|
| 1,000 | 0.0105 | 7.60% | 0.0124 | 14.86% |
| 10,000 | 0.0033 | 2.36% | 0.0039 | 4.72% |
| 100,000 | 0.0009 | 0.67% | 0.0013 | 1.57% |

The decision that turned on a 1.8% change in interval width was run at 10,000 paths, where
the per-origin noise on a band edge is 2.36%.

One more honest detail worth a sentence: the control's negative arm asserts less than was
measured, 2 of 3 where 3 of 3 was observed, because a test pinned to exactly the number you
saw fails on the next library upgrade and gets deleted.

## Figures

Four. Follow the reference discipline below.

1. **Same count, different story.** Two rows of 200 tick marks with 40 misses each. One
   scattered, one arriving in runs. Identical coverage, identical Kupiec, and only one of
   them is a calibrated model. This is the figure the post exists for.
2. **What each check reads.** Three small panels over the same miss sequence: the coverage
   band reads the total, Kupiec reads the rate, independence reads the transitions. Show the
   two-by-two transition count as part of the third panel so the formula becomes a picture.
3. **The power table as a grid.** The six cells above, shaded by hit rate, with the two
   shapes as columns. Small, no chart junk.
4. **Simulation noise against path count.** Log x axis, the three measured points marked,
   and a horizontal line at the size of the effect one decision turned on. The crossing is
   the point.

## Figure discipline

Reference: the Hevo engineering posts on Medium, "Building a lossless Kafka Producer" and
"Where's that event?". Plain boxes and arrows, near monochrome, numbered callouts with a
legend column, grey caption beneath in sentence case. Copy that discipline, not the subject.

- Inline SVG in an Astro component, one per figure, in the site's figures folder.
- Readable in light and dark. Use the site's figure accent token rather than a hard-coded
  near-white, which broke a chart once.
- Legible at 320px wide, no horizontal scroll.
- Figure 1 can be generated from a seeded simulation rather than drawn by hand. If you do
  that, commit the generator script alongside it.

## Voice

One engineer telling another what happened. Past tense, first person singular. The reader
knows what a p-value is and has never heard of Christoffersen, so introduce it by what it
reads rather than by its name.

Banned, because they read as machine-written: "delve", "moreover", "furthermore", "it is
worth noting", "at its core", "game-changer", "leverage" as a verb, "robust" as filler,
"seamless", "let's dive in", "in this post we will". No roadmap paragraph after the intro.
No conclusion that restates the post. No em dashes anywhere. Vary sentence length hard.

Resist the urge to moralise about rigour. State what was measured and let the reader draw
the lesson. One line of generalisation at the end is enough.

## Length

1,200 to 1,800 words. Hard cap 2,000. Short plain title, ideally the thing the control
found rather than the word "control".

## Done when

The post file and figure components exist, unpublished, and the handback note lists every
number you could not verify in the repository.
