# Four posts out of the forecasting study

Each file below its horizontal rule is a complete prompt for one post. Paste one into a
fresh session with a capable model. They are independent: four sessions can run at once,
and none of them needs the others' output.

## Why these four, and in this order

The study has one shape: a thing that failed, a thing that worked, a discovery that the
instruments themselves were suspect, and a second market that judged all of it. Each post
is one of those, and each stands alone for a reader who arrives cold from a search engine.

| # | Working title | The single claim | Why a reader cares |
|---|---|---|---|
| 1 | `01-nothing-beat-naive.md` | The next-day return of an index is not forecastable by any classical model, and the machinery is what makes that believable rather than just claimed | Everyone has seen a backtest that worked. This is about the five ways they lie |
| 2 | `02-forecast-the-range.md` | When the number is unforecastable the spread still is not, and at a month you have to simulate paths rather than scale by the square root of time | The practical half: VaR and expected shortfall that hold up |
| 3 | `03-tests-that-cannot-see.md` | Two of the three standard calibration tests could not detect a model with no conditional variance at all, measured on a world we wrote ourselves | The most portable idea in the series. Anyone running a statistical check should measure its power first |
| 4 | `04-the-second-market.md` | One market is an anecdote. On the S&P the chosen model failed and the flaw the correction was built for appeared unprompted | What replication actually does to your conclusions |

Post 3 is the one most likely to travel beyond finance. Post 1 is the best entry point for
a cold reader. If only two ever get written, write 1 and 3.

## Rules every prompt repeats

They are repeated inside each file on purpose, so each is self-contained:

- The voice rules, including the list of phrases that make writing read as machine-made.
- Figures carry the math. Every formula that matters gets a picture.
- Every number must come from the repository, and anything unverifiable gets flagged
  rather than smoothed over.
- Do not publish. Hand back the post file, the figure components and a note on what could
  not be checked.

## What not to reuse

There is an older draft at `Claude outputs/the-gate-said-no.md` in the forecasting repo.
Do not open it, do not mine it for structure and do not treat its framing as settled. The
series is being rebuilt from the evidence.
