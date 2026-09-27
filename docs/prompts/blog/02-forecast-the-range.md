# Prompt: post 2, "Forecast the range"

Paste everything below the rule into a fresh session. Self-contained.

---

Write one blog post for kislay.pages.dev. Do not publish it. Hand back the post file, its
figure components, and a short note on anything you could not verify.

Source material is the repository at ~/Documents/GitHub/forecasting (request folder
access). The blog lives at ~/Documents/Goals/site, an Astro site with MDX posts in
`src/content/posts/` and figure components in `src/components/figures/`. Read one existing
post and two existing figure components before writing a line, and match what they do.

## The claim this post makes

When the number is unforecastable the spread still is not. And at a one-month horizon you
cannot get the spread by scaling a daily number by the square root of time, because
volatility arrives in runs. You have to simulate the path.

This post should work for a reader who has never read post 1. One sentence of setup, then
go.

## Why a reader should finish it

It is the practical half. It ends with a value-at-risk and an expected shortfall that
survived a pre-registered test, and with an honest statement of which of those two was
actually tested and which was not.

## What to read, in this order

1. `docs/phase2_decision.md` and `docs/phase3_decision.md`.
2. `forecasting/models/variance.py`, the constant, EWMA and GARCH models and the
   `simulate` methods. The recursion inside `simulate` is the centre of this post.
3. `forecasting/evaluation/risk.py` and `docs/phase3_report/risk.md`.
4. `DECISIONS.md`, the Phase 2 and Phase 3 sections, especially P3-1 and P3-5.
5. `tests/test_simulation_conformance.py` for what the simulator was checked against.

Do not open `Claude outputs/the-gate-said-no.md`.

## The spine

1. Open on the failure, concretely. A flat interval built from all of history, meeting
   March 2020. It kept the same width while the market moved, and its coverage inside the
   crash collapsed.
2. What a conditional variance model does instead, in plain words before any algebra: it
   remembers that yesterday was violent.
3. The recursion, as a picture and then as one line of algebra. EWMA first because it has
   no fitted parameters and the reader can hold it in their head. GARCH and the asymmetric
   version in two sentences after.
4. Standardised residuals and why the tails come from the data rather than from a normal
   distribution. This is the step most write-ups skip.
5. The jump to a month. State the intuition and then break it: scaling by the square root
   of the horizon assumes each day is an independent draw from the same distribution, and
   the whole point of the variance model is that it is not. Simulate instead: draw a shock,
   feed it back into the variance, take the next step, accumulate.
6. What the simulator was checked against, because a simulator that nobody validated is a
   random number generator with ambition. Constant variance must reproduce the square root
   rule exactly; the conditional models must come out wider with fatter tails, and by how
   much.
7. What comes out: value at risk as a quantile you can read off, expected shortfall as the
   average loss given a breach, which cannot be recovered from the quantiles afterwards and
   has to be computed while the paths still exist.
8. The honest ending. At 200 origins the expected-shortfall backtest mostly declines to
   judge, and the report says "not tested" rather than printing a pass. Say plainly that
   VaR calibration was tested and ES calibration was not.

## Numbers you may use, all verifiable in the repository

| Fact | Value |
|---|---|
| EWMA half-width around the 2020 crash, Nifty | 0.0116 calm, 0.0418 in the crash, 0.0093 after |
| The flat Phase 1 interval over the same stretch | moved about 1%, and covered 0.579 inside the crash against a nominal 0.80 |
| Phase 2 decision, primary model ewma | GO. 80% coverage 0.787 at h = 1 and 0.815 at h = 5; 95% coverage 0.940 and 0.938; CRPS 0.874 and 0.866 of the flat comparator |
| Simulator conformance | constant variance reproduces sigma root h exactly; the conditional models come out 5% to 7% wider at 20 days, with positive excess kurtosis |
| Phase 3 risk table, primary model, average over test origins | 95% VaR 1.67% at 1 day, 3.73% at 5, 7.50% at 20. Expected shortfall given a breach 2.32%, 5.27%, 11.03%. Breaches 9 where 10 were expected |
| Expected shortfall against VaR | roughly 1.4 times at every horizon |
| The flat comparator on the same 20-day object | covered 0.950 against a nominal 0.80, Kupiec below 0.0001, quoted a 10.0% VaR where the conditional model quoted 7.5%, and breached 4 times where 10 were expected |

That last row is the argument for the whole post in one line: conditioning on current
volatility is not a refinement, the unconditional interval is simply wrong.

## Figures

Four. Follow the reference discipline below.

1. **The recursion as a loop.** Variance today feeds tomorrow's variance, with the shock
   entering from the side. Put the equation inside the figure, not in a separate block, so
   the picture and the algebra are the same object.
2. **Width tracking volatility.** A strip across 2019 to 2021 with the interval width
   drawn against realised moves, marking the three widths in the table above. The flat
   interval as a straight line through it.
3. **One step of the simulation, numbered.** Draw a standardised residual, scale it by the
   current volatility, add it to the running total, update the variance, repeat. Five
   numbered callouts with a legend column, in the style of the Hevo workflow diagrams.
4. **Square root of time against the simulated distribution.** Two overlaid densities of
   the 20-day cumulative return, one scaled, one simulated, with the tail difference
   shaded. This is the figure that carries the central claim.

## Figure discipline

Reference: the Hevo engineering posts on Medium, "Building a lossless Kafka Producer" and
"Where's that event?". Plain boxes and arrows, near monochrome, numbered callouts with a
legend column, grey caption beneath in sentence case. Copy that discipline, not the subject.

- Inline SVG in an Astro component, one per figure, in the site's figures folder.
- Readable in light and dark. Use the site's figure accent token for dark mode rather than
  a hard-coded near-white, which broke a chart once.
- Legible at 320px wide, no horizontal scroll.
- If a figure needs real data, take it from the repository. Do not draw a curve from
  memory; either compute it or choose a different figure.

## Voice

One engineer telling another what happened. Past tense, first person singular, specific
numbers. The reader knows what a standard deviation is and has never heard of filtered
historical simulation.

Banned, because they read as machine-written: "delve", "moreover", "furthermore", "it is
worth noting", "at its core", "game-changer", "leverage" as a verb, "robust" as filler,
"seamless", "let's dive in", "in this post we will". No roadmap paragraph after the intro.
No conclusion that restates the post. No em dashes anywhere, rewrite the sentence instead.
Vary sentence length hard. Paragraphs of two to four sentences.

Keep the ES admission. A post about risk numbers that admits one of its two headline
numbers was not testable at this sample size is worth more than one that does not.

## Length

1,200 to 1,800 words. Hard cap 2,000. Short plain title.

## Done when

The post file and figure components exist, unpublished, and the handback note lists every
number you could not verify in the repository.
