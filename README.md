# forecasting

Can any forecasting model beat "tomorrow will be like today" on real data, once every
way of cheating with future information has been ruled out?

This project answers that question for two series:

- **Nifty 50** (India's stock index, daily): the series we care about.
- **US electricity and gas output** (monthly, from FRED): a control with real seasonal
  structure, included to prove the machinery can see a pattern when there is one.

It is a research study, not a trading system, and not financial advice.

## The answer (Phase 1, September 2026)

**Nifty 50: no.** Nothing beats predicting a zero return for tomorrow, at 1, 5 or 20
trading days ahead. What the data do show is volatility clustering: calm and turbulent
periods come in runs. That is the one thing worth modelling next.

**Electricity control: the models see the season.** Every seasonal model beats the
"same month last year" baseline by 10 to 19% at short horizons. Under the strict
statistical rule we fixed in advance, that gain is just short of significant (the rule
was applied as written; we did not move it).

The written decision is in [docs/gate_decision.md](docs/gate_decision.md), with the full
report and figures in [docs/phase1_report/](docs/phase1_report/).

## Why you can trust the "no"

Most "we beat the market" results come from information leaking from the future into
the model. This harness is built so that cannot happen quietly:

- The engine hands each model only the past. Tests replace every future value with
  garbage (huge numbers, blanks, shuffles) and check that no forecast changes.
- Planted cheats are caught by those same tests, so the tests are known to work.
- 200 simulated random walks (which nothing can forecast) are backtested every night;
  the share of "wins" stays at the false-positive rate.
- The rules for declaring success were written down and committed **before** the real
  test data were scored, and the report refuses to issue a decision if they changed.
- Two independent runs produced the same 569,176 forecasts byte for byte.

## Try it in five minutes

Needs [uv](https://docs.astral.sh/uv/) (it installs Python for you).

```bash
uv sync
uv run forecast run examples/config.yaml   # backtest two synthetic series, write a report
```

Open `examples/runs/<run_id>/report/report.md`: one verdict per research question, the
tables behind each, and three plots per series. To reproduce the real result:

```bash
uv run forecast run configs/phase1.yaml    # about 30 CPU minutes; data are in the repo
```

## How it works, in one paragraph

Each series is replayed through time. At every origin the models see only the data up
to that point, refit from scratch, and forecast 1 to H steps ahead. Every forecast is
stored, then scored against simple baselines with tests that account for overlapping
horizons and for the number of comparisons made. Choices such as which baseline to
compare against are made on a separate "development" slice of origins, never on the
test slice.

## Read more

| Want to | Read |
|---|---|
| Run your own series, understand every option and test | [docs/reference.md](docs/reference.md) |
| See every decision made while building, in order | [DECISIONS.md](DECISIONS.md) |
| Read the original plan and the research behind it | [docs/spec.md](docs/spec.md), [docs/research.md](docs/research.md) |
