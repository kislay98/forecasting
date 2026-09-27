# Prompt: post 1, "Nothing beat the naive forecast"

Paste everything below the rule into a fresh session. Self-contained.

---

Write one blog post for kislay.pages.dev. Do not publish it. Hand back the post file, its
figure components, and a short note on anything you could not verify.

Source material is the repository at ~/Documents/GitHub/forecasting (request folder
access). The blog lives at ~/Documents/Goals/site, an Astro site with MDX posts in
`src/content/posts/` and figure components in `src/components/figures/`. Read one existing
post and two existing figure components before writing a line, and match what they do.

## The claim this post makes

The next-day return of a stock index is not forecastable by any classical model, and the
interesting part is not the result but the machinery that makes it believable. A backtest
that has not been attacked is a rumour.

## Why a reader should finish it

Almost every engineer who has touched market data has produced a backtest that worked.
This post is about the five specific ways the future gets into one, how to plant a cheat so
your tests prove they can catch one, and why the decision rule has to be written and hashed
before anyone looks at the answer.

## What to read, in this order

1. `DECISIONS.md`, the Status table and the M1 to M8 sections.
2. `docs/gate_decision.md`, the Phase 1 written decision.
3. `docs/original_prompt_audit.md`, sections 1, 6 and 16 of the table.
4. `forecasting/backtest/engine.py` and `tests/leakage.py`, enough to describe the guards
   accurately.
5. `configs/gate.yaml` and `forecasting/gate.py`, for how a registration is enforced.

Do not open `Claude outputs/the-gate-said-no.md`. An earlier draft exists and is being
deliberately set aside.

## The spine

Roughly this order. Rework it if the material argues otherwise, but keep the shape: a
seductive result, the attack, the machinery, the answer, the one thing that did show up.

1. The setup in three sentences. A daily index since 1996, nine models, every horizon from
   1 to 20 days, and a question with a yes or no answer.
2. Why a working backtest is the default outcome and not a good sign. Name the mechanism:
   information from after the forecast origin reaching the model, by any of several routes.
3. The five leakage routes the harness closes, each in one or two sentences, with the guard
   named. This is the heart of the post and it wants the first figure.
4. Planted cheats. The tests deliberately introduce a leak and assert the suite fails.
   A test suite that has never caught a real bug is a hypothesis.
5. The random-walk canary: 200 simulated series that nothing can forecast, backtested every
   night, with the "win" rate expected to sit at the false-positive rate and not above it.
6. Pre-registration. The decision rule written in a YAML file, hashed, committed, and the
   report refusing to issue a decision if the file changed or was uncommitted at run time.
   Show the actual fragment. This is where the post earns its credibility.
7. The answer: no model beats a zero-return forecast at 1, 5 or 20 days. Say plainly that a
   negative result was a successful outcome of the design, not a disappointment.
8. The one thing that did show up, which sets up everything that followed: the mean is
   unforecastable and the variance is not. ARCH-LM rejects at every single test origin.

## Numbers you may use, all verifiable in the repository

Check each one rather than trusting this list.

| Fact | Value |
|---|---|
| Forecasts in the Phase 1 run, reproduced byte for byte across two runs | 569,176 |
| Random walks backtested nightly by the canary | 200 |
| Origins where ARCH-LM rejected, out of the test origins | every one of 250 |
| The Diebold-Mariano test's measured false-positive rate when n/h is 2.5, against 5% nominal | 9% to 15%, which is why the test declines itself below n/h = 5 |
| The positive control (US electricity and gas output) | seasonal models beat the seasonal-naive baseline by 10% to 19% at short horizons, and the gain fell just short of significance under the correction, which was applied as written |

The control matters to the argument: a harness that finds nothing is indistinguishable from
a broken harness until you show it finding something that is certainly there.

## Figures

Three or four. Follow the reference discipline below.

1. **Where the future gets in.** A timeline: training window, forecast origin, horizon,
   truth. Numbered callouts 1 to 5 marking each leakage route, with a legend column giving
   each one a single line. This is the figure the post is built around.
2. **Planting a cheat.** A small loop: plant a known leak, run the suite, expect red, remove
   it, expect green. Four boxes and two arrows. It should be obvious in three seconds.
3. **The sealed rule.** The gate file, its hash, the commit, and the report refusing a
   decision when they do not match. Show the refusal as an outcome, not as prose.
4. Optional: skill against horizon with the bootstrap interval crossing zero, marking the
   predictable horizon. Only if the numbers are in the repository; do not draw a shape from
   memory.

## Figure discipline

Reference: the Hevo engineering posts on Medium, "Building a lossless Kafka Producer" and
"Where's that event?". Plain boxes and arrows, near monochrome, numbered callouts with a
legend column, grey caption beneath in sentence case. Copy that discipline, not the subject.

- Inline SVG in an Astro component, one per figure, in the site's figures folder.
- Readable in light and dark. The site has a figure accent token for dark mode; use it
  rather than a hard-coded near-white, which broke a chart once.
- Legible at 320px wide, no horizontal scroll.
- Every formula that matters appears as a picture at least once.

## Voice

One engineer telling another what happened. Past tense, first person singular, specific
dates and numbers. The reader knows what a p-value is and has never heard of Kupiec.

Banned, because they read as machine-written: "delve", "moreover", "furthermore", "it is
worth noting", "at its core", "game-changer", "leverage" as a verb, "robust" as filler,
"seamless", "let's dive in", "in this post we will". No roadmap paragraph after the intro.
No conclusion that restates the post: end on the strongest concrete fact or on the question
the work left open. No em dashes anywhere, rewrite the sentence instead; en dashes in date
ranges are fine. Vary sentence length hard. Paragraphs of two to four sentences.

Include the part where the design could have embarrassed its author: the rule was written
first and it returned an answer nobody wants to publish.

## Length

1,200 to 1,800 words. Hard cap 2,000. Title should be short and plain, in the register of
"Where's that event?" rather than a colon-subtitle construction.

## Done when

The post file and figure components exist, unpublished, and the handback note lists every
number you could not verify in the repository.
