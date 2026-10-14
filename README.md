# forecasting

A rolling-origin backtesting harness for univariate time series, and a study run on it.

The study started with one question: does any forecasting model beat "tomorrow will be
like today" on the Nifty 50, once every way of leaking future information into the model
has been ruled out? None did. So the rest of the study fixed the point forecast at zero
and asked a narrower question: can a volatility model produce prediction intervals that
are honest, meaning the actual move lands inside them about as often as the interval
claims? At one day and one week ahead it can. At one month ahead it needs a calibration
step, and with that step the result held on the S&P 500 too.

Every decision rule was written down and committed before the test data it judged were
scored. This is research code, not a trading system, and not financial advice.

## Data

| Series | Frequency | Used from | Role |
|---|---|---|---|
| Nifty 50 price index (`data/nifty50.csv`) | daily | 1996 | the subject |
| S&P 500 (`data/sp500.csv`) | daily | 1996 | replication on a second market |
| US electricity and gas output, FRED series IPG2211A2N | monthly | 1990 | control with a known seasonal pattern |

## What happened, phase by phase

Each phase has a decision document that states the registered rule, the run it was
applied to, and the result cell by cell. The table gives the short version.

| Phase | Question | Setup | Result |
|---|---|---|---|
| [1](docs/gate_decision.md) | Does any model beat a naive forecast of Nifty returns? | 250 test origins, horizons 1, 5 and 20 trading days. Baselines: naive, drift, moving average, zero return. Candidates: AR, exponential smoothing, ETS, SARIMA, Theta, and their equal-weight combination. | **No.** Nothing beats a zero-return forecast at any horizon, so the predictable horizon is zero. The residuals do show volatility clustering (ARCH-LM rejects at all 250 origins), and that is what the later phases model. |
| [1, control](docs/gate_decision.md) | Can the harness see a pattern when one exists? | The same design on the electricity series: 120 test origins, horizons 1, 12 and 24 months. | **Yes, but not significant under the registered rule.** Seasonal models beat seasonal naive by 16 to 19% one month ahead. After the multiple-comparison correction the gain misses the significance threshold, so the recorded decision is NO-GO. The rule was applied as written. |
| [2](docs/phase2_decision.md) | With the point forecast fixed at zero, are one-day and one-week intervals from a volatility model honest? | 400 test origins. EWMA, GARCH(1,1) and GJR-GARCH, with Normal, Student-t or bootstrapped tails. Baseline: empirical quantiles of past returns. EWMA was named as the primary model before the run. | **Yes.** 80% intervals cover 78.7% of actuals at one day and 81.5% at five days; 95% intervals cover 94.0% and 93.8%. Misses are independent of each other. CRPS is 13% better than the flat baseline. 14 of 14 checks pass. |
| [3](docs/phase3_decision.md) | And a whole month held at once? | The 20-day cumulative return, from 10,000 simulated paths per origin. GJR-GARCH, 200 test origins. | **No.** The 80% interval at 20 days covers 86.5%, outside the registered band of 75 to 85%. 20 of 21 checks pass. The interval is too wide. |
| [4](docs/phase4_decision.md) | Does a conformal width correction fix that? | Phase 3's forecasts, unchanged, plus a per-level width factor learned from the errors of the previous 60 origins. | **Yes.** Coverage at 20 days goes from 86.5% to 83.0%. 21 of 21 checks pass. The margin is four origins out of 200. |
| [5a](docs/phase5a_decision.md) | Does Phase 2 hold on the S&P 500? | Same design and model. The rule was committed before the S&P data were in the repo. | **Not quite.** 13 of 14 checks pass. Coverage is right, but misses cluster at the five-day horizon: the independence test gives p = 0.0056 against a corrected threshold of 0.0064. |
| [5b](docs/phase5b_decision.md) | Does Phase 4 hold on the S&P 500? | Same design and model. | **Yes.** 21 of 21 checks pass. Without the correction, the S&P shows the same over-coverage at 20 days that Nifty showed in Phase 3, so that fault is a property of the method, not of one market. |
| [6](docs/phase6_decision.md) | The simulation resamples past residuals independently, and they are not independent. Does resampling them in blocks change anything? | Blocks of 10 days. Four arms: two markets, with and without the conformal correction. | **No decision changes.** The block sampler moves the 20-day distribution by about 10%, wider on Nifty and narrower on the S&P, and its CRPS on Nifty at 20 days is 1% worse. Not adopted. |
| [7](docs/phase7_decision.md) | GARCH parameters enter the simulation as point estimates. Does drawing them per path widen the intervals? | Draws from the fit's asymptotic covariance, same random innovations, both markets. | **No.** On Nifty the 20-day interval width changes by under 0.2%, a tenth of the simulation noise already in it. Not adopted. |

Taken together: the mean of an index return is not forecastable from its own past, and
the harness could not be talked into saying otherwise. The variance is forecastable, and
GJR-GARCH with a conformal width correction gives month-ahead intervals that held on two
markets. The two refinements tried afterwards did not improve on it.

Two more things came out of closing the study:

- The risk report now prints the probability of a move beyond a named threshold, read
  from the stored forecast quantiles. It refuses to answer beyond the outermost stored
  quantile rather than extrapolate. See [docs/threshold_probabilities.md](docs/threshold_probabilities.md).
- Reading the whole quantile grid for that work exposed a defect: the conformal
  correction could make the 99% interval narrower than the 95% one, in about a fifth of
  corrected rows. It is fixed, one published cell moved, and no decision changed.

[docs/original_prompt_audit.md](docs/original_prompt_audit.md) checks the finished work
against the brief the study started from, and lists what was deliberately not done:
monitoring, a machine-learning model, Bayesian methods and regime switching, each with
the condition that would make it worth doing.

## How leakage is ruled out

Most "we beat the market" results come from information leaking from the future into the
model. The harness is built so that cannot happen quietly.

- The engine hands each model only the data up to the forecast origin. Tests replace
  every future value with garbage (huge numbers, blanks, shuffled order) and check that no
  forecast changes.
- Models with deliberately planted leaks are run through the same tests, to show the
  tests catch them.
- 200 simulated random walks, which nothing can forecast, are backtested every night.
  The share of "wins" stays at the false-positive rate.
- Every choice that could be tuned, such as which baseline to compare against, is made on
  a development slice of origins that comes before the test slice.
- The success rule for each phase is committed and hashed before the test origins are
  scored. The report refuses to issue a decision if the rule changed afterwards.
- Runs are reproducible on one machine: two independent runs of Phase 1 produced the
  same 569,176 forecasts, identical in every value once the run label and timing column
  are set aside.

## Run it

Needs [uv](https://docs.astral.sh/uv/), which installs Python for you.

```bash
uv sync
uv run forecast run examples/config.yaml      # two synthetic series, writes a report
```

Open `examples/runs/<run_id>/report/report.md` for one verdict per research question, the
tables behind it, and three plots per series.

To reproduce the study, run a phase config. The data are in the repo.

```bash
uv run forecast run configs/phase1.yaml       # about 30 CPU minutes
uv run forecast run configs/phase4/phase4.yaml
uv run forecast risk <run dir> --thresholds -0.05 0.02 0.05 0.10   # VaR, expected shortfall, fan chart, threshold probabilities
```

Later phases live under `configs/phase<N>/`, each with its config and the gate it was
scored against.

## How a run works

Each series is replayed through time. At every origin the models see only the data up
to that point, refit from scratch, and forecast 1 to H steps ahead. Every forecast is
stored, then scored against the baselines with tests that account for overlapping
horizons and for the number of comparisons made.

## The statistics

One paragraph per idea, with the equation the code implements. The definitions are in
[docs/spec.md](docs/spec.md) under "Metrics and statistics".

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
$`d_t = \lvert e^{A}_{t}\rvert - \lvert e^{B}_{t}\rvert`$ with the Harvey-Leybourne-Newbold small-sample
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
forecastable mean, but their variance clusters, which is what the later rounds model.

**The decision rule.** Committed before the test origins were scored: a model wins only
if its relative MAE is below 1 and its Holm-adjusted one-sided DM p-value is below 0.05
at the registered decision horizons. That, and nothing chosen afterwards, produced the
NO-GO.

## Read more

| Want to | Read |
|---|---|
| Run your own series, understand every option and test | [docs/reference.md](docs/reference.md) |
| See every decision made while building, in order | [DECISIONS.md](DECISIONS.md) |
| Check the finished work against the brief it started from | [docs/original_prompt_audit.md](docs/original_prompt_audit.md) |
| Read the original plan and the research behind it | [docs/spec.md](docs/spec.md), [docs/research.md](docs/research.md) |
