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

## The method

Each step below is one idea, the equation the code implements, and where the spec or
research defines it (see [docs/spec.md](docs/spec.md), "Metrics and statistics").

**Returns, not prices.** Prices are close to a random walk, so the Nifty is modelled on
log returns and price forecasts are derived from them:

```math
r_t = \ln\frac{p_t}{p_{t-1}}, \qquad \hat p_{t+h} = p_t \exp\Big(\sum_{j=1}^{h} \hat r_{t+j}\Big)
```

**Rolling-origin backtest.** At each origin $t$ every model is refitted on $y_1 \dots y_t$
(expanding) or $y_{t-L+1} \dots y_t$ (rolling) and forecasts $h = 1 \dots H$ ahead. The
last 250 (Nifty) or 120 (electricity) origins are the test set; the 20 before them are the
development set, used for every choice (baseline, SMA window, headline model) so that no
choice ever sees a test result.

**Scale-free accuracy.** MASE divides each error by the in-sample naive error of its own
training slice, so the number reads the same across series; relative MAE compares a model
with the best baseline on the same origins, and skill is its complement:

```math
\text{MASE}_h = \frac{1}{n}\sum_{t}\frac{|y_{t+h}-\hat y_{t+h}|}{\frac{1}{T-m}\sum_{s=m+1}^{T}|y_s-y_{s-m}|},
\qquad
\text{SS}(h) = 1 - \frac{\text{MAE}_{\text{model}}(h)}{\text{MAE}_{\text{baseline}}(h)}
```

The predictable horizon $h^*$ is the last $h$ before the first one whose bootstrap
confidence interval for $\text{SS}(h)$ touches zero (moving-block bootstrap over origins,
block length $h$, 2,000 replicates).

**Is a difference real?** The Diebold-Mariano test on the loss differential
$d_t = |e^{A}_{t}| - |e^{B}_{t}|$ with the Harvey-Leybourne-Newbold small-sample
correction; multi-step errors overlap, so the variance uses autocovariances up to lag
$h-1$ and the effective sample is about $n/h$:

```math
\text{DM} = \frac{\bar d}{\sqrt{\left(\hat\gamma_0 + 2\sum_{k=1}^{h-1}\hat\gamma_k\right)/n}},
\qquad
\text{DM}^{*} = \text{DM}\sqrt{\frac{n+1-2h+h(h-1)/n}{n}} \sim t_{n-1}
```

The test is not run when $n/h < 5$: on 200 simulated random walks with $n/h = 2.5$ its
false-positive rate was 9 to 15%, against 5% nominal.

**Many comparisons.** With $k$ models and horizons tested at once, p-values are Holm
adjusted (the smallest is multiplied by $k$, the next by $k-1$, and so on, keeping the
running maximum), and the Model Confidence Set (Hansen, Lunde and Nason) keeps every model
whose bootstrap $T_{\max}$ p-value is at least 0.10:

```math
t_i = \frac{\bar d_{i\cdot}}{\widehat{\text{sd}}(\bar d_{i\cdot})},
\qquad \bar d_{i\cdot} = \bar L_i - \frac{1}{|\mathcal M|}\sum_{j\in\mathcal M}\bar L_j,
\qquad T_{\max} = \max_{i\in\mathcal M} t_i
```

**Are the intervals honest?** Coverage is the share of actuals inside an interval,
judged against the binomial band a calibrated interval would show in $n/h$ trials, and
the Kupiec likelihood ratio for $x$ misses in $n$ at nominal miss rate $p$; the Winkler
score charges width plus a penalty for each miss:

```math
\text{LR}_{uc} = -2\ln\frac{(1-p)^{n-x}p^{x}}{(1-\hat p)^{n-x}\hat p^{x}} \sim \chi^2_1,
\qquad
W_\alpha = (u-l) + \tfrac{2}{\alpha}(l-y)^{+} + \tfrac{2}{\alpha}(y-u)^{+}
```

**What is left in the residuals?** Ljung-Box for autocorrelation at lags $m$ and $2m$
(10 when $m=1$) and Engle's ARCH-LM for volatility clustering, on each test origin's
one-step residuals; the report gives the share of origins that reject:

```math
Q = n(n+2)\sum_{k=1}^{L}\frac{\hat\rho_k^2}{n-k} \sim \chi^2_L,
\qquad
\text{LM} = (n-q)\,R^2 \text{ of } e_t^2 \text{ on } e_{t-1}^2 \dots e_{t-q}^2 \sim \chi^2_q
```

For the Nifty, ARCH-LM rejects at every one of the 250 test origins: the returns have no
forecastable mean, but their variance clusters, which is what Phase 2 models.

**The decision rule.** Committed before the test origins were scored: a model wins only
if its relative MAE is below 1 and its Holm-adjusted one-sided DM p-value is below 0.05
at the registered decision horizons. That, and nothing chosen afterwards, produced the
NO-GO.

## Read more

| Want to | Read |
|---|---|
| Run your own series, understand every option and test | [docs/reference.md](docs/reference.md) |
| See every decision made while building, in order | [DECISIONS.md](DECISIONS.md) |
| Read the original plan and the research behind it | [docs/spec.md](docs/spec.md), [docs/research.md](docs/research.md) |
