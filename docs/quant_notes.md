# OhCamel — the math, in one place

The [README](../README.md) argues; this states. Every formula below is the one
the code actually evaluates, cross-referenced to the file and function that
evaluates it, so a claim here can be checked against the implementation without
reading OCaml and an implementation can be checked against a standard reference
without reading the README.

Every result is standard. Where this codebase makes a choice that a reference
would leave open — a tail convention, a mean treatment, a sign — the choice is
stated and the alternative named, because those are the places where two
correct-looking implementations disagree.

**Notation.** Returns are simple and fractional: $r_t = P_t/P_{t-1} - 1$. VaR and
ES are reported as **positive loss magnitudes**, the desk convention, so
"VaR = 0.05" means a loss of 5%. $c$ is the confidence level (0.95 throughout),
$\Phi$ and $\varphi$ the standard normal CDF and density, $z_p = \Phi^{-1}(p)$.
Weights $w$ are signed and normalised by gross exposure, so $\sum_i |w_i| = 1$.

**References.** Jorion, *Value at Risk*, 3rd ed. McNeil, Frey & Embrechts,
*Quantitative Risk Management*. Hull, *Options, Futures and Other Derivatives*.
Christoffersen, *Elements of Financial Risk Management*.

---

## 1. Historical VaR and Expected Shortfall

`lib/risk_metrics.ml` — `tail_count`, `loss_tail`, `historical_var`,
`expected_shortfall`.

Let $r_{(1)} \le r_{(2)} \le \dots \le r_{(n)}$ be the order statistics of the
return window. The tail size is the **nearest-rank** count

$$k \;=\; \max\Big(1,\ \min\big(n,\ \lceil (1-c)\,n - \varepsilon \rceil\big)\Big),
\qquad \varepsilon = 10^{-9}$$

and then

$$\widehat{\mathrm{VaR}}_c = -\,r_{(k)},
\qquad
\widehat{\mathrm{ES}}_c = -\frac{1}{k}\sum_{i=1}^{k} r_{(i)} .$$

At $c = 0.95$ over $n = 100$ the tail is the worst 5 observations and VaR is the
5th worst. The clamp at 1 is not cosmetic: a confidence high enough that the tail
rounds to zero observations should return the single worst loss, not divide by
zero.

**$\mathrm{ES} \ge \mathrm{VaR}$, always.** Each $r_{(i)} \le r_{(k)}$ for
$i \le k$, so the mean of the $k$ smallest is at most $r_{(k)}$, and negating
reverses the inequality. Equality holds exactly when $k = 1$. Asserted in
`test_risk_metrics.ml`.

> **Footnote on $\varepsilon$, which is a real bug that was found and fixed.**
> In IEEE 754, `(1.0 - 0.70) * 10.0` evaluates to `3.0000000000000004`, not `3`.
> A bare $\lceil\cdot\rceil$ turns that into 4, silently widening the tail by one
> observation and biasing every VaR computed at that confidence. Subtracting a
> tolerance before rounding up removes the artefact without touching genuinely
> fractional ranks. See the comment on `Risk_metrics.tail_count`.

**What it cannot do.** A historical estimator can only report losses it has
already seen: it is blind to any tail the window does not contain, and it is a
step function of $c$ — between adjacent ranks the estimate does not move at all.
That is why it is read beside the parametric number rather than instead of it.

---

## 2. Parametric (variance–covariance) VaR

`lib/risk_metrics.ml` — `parametric_var`, `portfolio_stddev`,
`portfolio_parametric_var`, `covariance_matrix`.

For a single series with mean $\mu$ and standard deviation $\sigma$, under
$r \sim \mathcal{N}(\mu, \sigma^2)$:

$$\widehat{\mathrm{VaR}}_c = -\big(\mu + z_{1-c}\,\sigma\big),
\qquad z_{0.05} = -1.6449 .$$

For a book of $n$ instruments with covariance matrix $\Sigma$:

$$\sigma_p = \sqrt{w^{\top}\Sigma\,w},
\qquad
\widehat{\mathrm{VaR}}^{\,p}_c = -z_{1-c}\,\sigma_p .$$

> **A deliberate asymmetry worth noticing.** The portfolio form drops the mean
> term; the single-series form keeps it. That is not an oversight. A drift
> estimate over a 60-day window is mostly sampling noise, and at the portfolio
> level it would be a noise term multiplied through weights that themselves move
> on every tick. `Risk_metrics.parametric_var` keeps $\mu$ because the
> backtester needs an estimator it can score point-in-time;
> `portfolio_parametric_var` assumes zero because the live number is compared
> against a limit. Both are defensible; only one can be silent about it.

$\Sigma$ uses **population** moments ($1/n$, not $1/(n-1)$) throughout. The
convention is fixed once and used everywhere, which matters most for $\beta$
below, where the choice cancels only if numerator and denominator agree.
`covariance_matrix` computes the upper triangle and mirrors it, so the result is
*exactly* symmetric — computing both halves independently can leave them
differing in the last bit, which is enough to fail a positive-definiteness check
downstream.

**Rounding guard.** `portfolio_stddev` clamps $w^{\top}\Sigma w$ at zero before
the square root. A mathematically-zero variance can come out very slightly
negative; a genuinely negative one would mean a non-PSD $\Sigma$, which is a
caller bug, but the two are indistinguishable at that magnitude and `nan` is a
far worse answer than `0.0` for a quantity a limit is compared against.

**What it cannot do.** The Gaussian assumption has thin tails, so this
understates risk on a book with real tail exposure. The engine computes both
estimators on purpose: **the gap between the historical and the parametric
number is a read on how non-normal the book's tail currently is**, and it is
only visible if both exist.

---

## 3. EWMA volatility and covariance

`lib/vol_estimators.ml` — `Ewma.weights`, `mean`, `covariance`,
`covariance_matrix`.

With decay factor $\lambda \in (0,1)$ over a window of $n$ observations, indexed
so that $k = 0$ is the **most recent**:

$$w_k \;=\; \frac{\lambda^{k}}{\sum_{j=0}^{n-1}\lambda^{j}},
\qquad
\hat{\mu}_i = \sum_k w_k\, r_{i,k},
\qquad
\hat{\Sigma}_{ij} = \sum_k w_k\,(r_{i,k}-\hat{\mu}_i)(r_{j,k}-\hat{\mu}_j).$$

**Two departures from RiskMetrics, both deliberate.**

*The mean.* RiskMetrics assumes $\mu = 0$ and skips the demeaning. This module
estimates a **weighted** mean instead, and the reason is comparability rather
than statistics: with a weighted mean, $\hat{\Sigma} \to$
`Risk_metrics.covariance_matrix` exactly as $\lambda \to 1$, so the difference
between the engine's two covariance nodes is a difference in *weighting* and
nothing else. Under the zero-mean convention they would differ in mean treatment
as well, and the diagnostic the dashboard offers — "these two disagreeing tells
you the regime is moving" — would be measuring two effects and attributing both
to one. Asserted in `test_vol_estimators.ml`.

*The normaliser.* Written as the explicit sum $\sum_j \lambda^j$ rather than the
closed form $(1-\lambda)/(1-\lambda^n)$. They are equal in real arithmetic; the
closed form is $0/0$ in precisely the limit the reduction property is stated
about.

**Constants at the documented default $\lambda = 0.94$** (RiskMetrics' fitted
daily value, from roughly 480 series in the 1996 Technical Document):

| quantity | formula | value |
|---|---|---|
| half-life | $\ln(1/2)/\ln\lambda$ | 11.2 observations |
| effective sample size, $n=60$ | $1/\sum_k w_k^2$ | 30.8 observations |

That second row is the entire trade the parameter makes: responsiveness is
bought with estimator variance, and at $\lambda = 0.94$ a 60-day window is
carrying about half the information a flat one would. `make backtest` prices
exactly this — EWMA rescues the `vol-regime` series and is rejected on
`iid-normal`, where there is no regime to track and the extra variance buys
nothing.

**Not GARCH.** $\lambda$ is hand-set, not fitted, and there is no
mean-reversion term. An EWMA tracks a regime change; it does not forecast the
return to normal after one. A GARCH(1,1),
$\sigma_t^2 = \omega + \alpha r_{t-1}^2 + \beta\sigma_{t-1}^2$, would, and is
not implemented.

---

## 4. Euler decomposition of portfolio risk

`lib/attribution.ml` — `compute`, `component_var`, `euler_residual`,
`diversification_ratio`.

$\sigma_p(w) = \sqrt{w^{\top}\Sigma w}$ is **homogeneous of degree one**:
$\sigma_p(tw) = t\,\sigma_p(w)$ for $t > 0$. Euler's theorem for homogeneous
functions then gives an exact additive split with no approximation and no
residual term:

$$\sigma_p \;=\; \sum_i w_i \frac{\partial \sigma_p}{\partial w_i}.$$

Differentiating,

$$\underbrace{\frac{\partial\sigma_p}{\partial w_i} = \frac{(\Sigma w)_i}{\sigma_p}}_{\text{marginal}},
\qquad
\underbrace{C_i = w_i\frac{(\Sigma w)_i}{\sigma_p}}_{\text{component}},
\qquad
\underbrace{S_i = |w_i|\,\sigma_i}_{\text{standalone}},\quad \sigma_i=\sqrt{\Sigma_{ii}} .$$

**Verification of the identity:**

$$\sum_i C_i \;=\; \frac{1}{\sigma_p}\sum_i w_i (\Sigma w)_i
\;=\; \frac{w^{\top}\Sigma w}{\sigma_p}
\;=\; \frac{\sigma_p^{2}}{\sigma_p} \;=\; \sigma_p . \qquad\blacksquare$$

`euler_residual` returns $\sum_i C_i - \sigma_p$, which is zero in exact
arithmetic. It is exposed rather than confined to the tests because anything
beyond float accumulation error means the weights and the covariance matrix have
gone out of alignment — the one failure mode of this module that produces
confident, plausible, entirely wrong answers.

**Into VaR units.** Parametric VaR is $-z_{1-c}\sigma_p$, a constant multiple of
$\sigma_p$ that does not depend on $w$, so the same constant scales every
component and the identity survives:

$$\mathrm{CVaR}_i = -z_{1-c}\,C_i,
\qquad \sum_i \mathrm{CVaR}_i = \mathrm{VaR}^{\,p}_c .$$

This is what makes an instrument-scoped `Component_var` limit well posed while
an instrument-scoped `Value_at_risk` limit is not: **a quantile of a sum is not
the sum of quantiles**, but an Euler share is additive by construction. Two names
each carrying \$10,000 of standalone VaR do not carry \$20,000 together unless
they are perfectly correlated. See `Limits.scope_is_valid`.

**Signs are real.** $C_i < 0$ means position $i$ moves against the book and
*reduces* portfolio volatility. Taking $|C_i|$ — easy to do by reflex, since VaR
itself is reported as a positive magnitude — would report a hedge as a risk
contributor and would break additivity. `test_properties.ml` asserts this over
randomly generated books, not just the hand-built ones.

**Diversification ratio.**

$$D \;=\; \frac{\sum_i S_i}{\sigma_p} \;=\; \frac{\sum_i |w_i|\sigma_i}{\sigma_p} \;\ge\; 1 .$$

*Proof.* Write $\sigma_p = \lVert \Sigma^{1/2} w \rVert_2 = \lVert \sum_i w_i \Sigma^{1/2} e_i \rVert_2$.
By the triangle inequality this is at most
$\sum_i |w_i| \lVert \Sigma^{1/2} e_i \rVert_2 = \sum_i |w_i| \sigma_i$. $\blacksquare$

Equality holds when all positions are perfectly correlated — one bet — so
$D$ collapses toward 1.0 in a crisis, because correlations converging is what a
crisis mechanically *is*.

**Why the Gaussian path and not the historical one.** The decomposition needs a
differentiable closed form for portfolio risk. Historical VaR is an order
statistic of the sample: its derivative with respect to a weight is zero almost
everywhere. Kernel-smoothed component-historical-VaR estimators exist and are
too noisy at these sample sizes to trade against. So this says how risk is
*shared out* — a question about correlation structure, and fairly robust —
rather than how large the tail *is*, which is the question normality gets wrong.

---

## 5. Factor beta

`lib/risk_metrics.ml` — `beta`, `is_effectively_constant`.

$$\hat{\beta} = \frac{\widehat{\mathrm{Cov}}(r_p, f)}{\widehat{\mathrm{Var}}(f)}$$

over a common window, right-aligned (most recent observations) because the two
series fill at different rates and left-aligning would regress this week's book
against last month's rates.

Undefined, not zero, when the factor does not move — a constant regressor
explains nothing, and reporting $0.0$ would read as "no exposure". The test is
**relative**: $\hat{\sigma}_f \le 10^{-12}\max_t|f_t|$. An exact test against
zero is insufficient, and the reason is instructive. Ten copies of `0.0425` have
true variance zero, but the mean is not exactly representable, so each deviation
is a rounding residue near $10^{-17}$ and the computed variance is about
$10^{-33}$ — small, non-zero, and passed straight through. Dividing one such
residue by another yields a number like $-0.3$: finite, plausible, and entirely
fabricated. On a dashboard that reads as "the book is inversely exposed to
rates", a claim about the world derived from float error.

---

## 6. Drawdown

`lib/risk_metrics.ml` — `max_drawdown`, `current_drawdown`.

For an equity curve $E_1,\dots,E_T$:

$$\mathrm{DD}_t = \frac{\max_{s \le t} E_s - E_t}{\max_{s\le t} E_s},
\qquad
\mathrm{MaxDD} = \max_t \mathrm{DD}_t,
\qquad
\mathrm{CurrentDD} = \mathrm{DD}_T .$$

The divisor is guarded: an equity curve at or through zero would otherwise
produce $\infty$ or `nan` and propagate it into a limit check.

`Limits` compares against **current**, not max. A breaker keyed to the maximum
would latch on forever after one bad morning, which is a different instrument
from a risk limit.

---

## 7. Backtesting: is the number any good?

`lib/var_backtest.ml`.

### 7.1 Rolling origin

`rolling` is the only forecast generator in the module. At step $t$ the estimator
receives $r_{t-w},\dots,r_{t-1}$ and is scored against $r_t$. The day being
forecast is **not in the array the estimator is handed**, so it cannot be
reached — the discipline is structural rather than a matter of care. A VaR
estimated from a window containing the day it predicts looks superb and means
nothing, and the output gives no sign. `test_properties.ml` generalises the
check across arbitrary window sizes, series lengths and estimators.

The exceedance indicator is

$$I_t = \mathbf{1}\{\,r_t < -\widehat{\mathrm{VaR}}_t\,\},
\qquad x = \sum_t I_t,\quad p = 1-c .$$

### 7.2 Kupiec, unconditional coverage

`kupiec_pof`. $H_0: \Pr(I_t = 1) = p$. With $\hat{\pi} = x/n$:

$$LR_{uc} = -2\ln\frac{(1-p)^{\,n-x}p^{\,x}}{(1-\hat\pi)^{\,n-x}\hat\pi^{\,x}}
= -2\Big[(n-x)\ln(1-p) + x\ln p - (n-x)\ln(1-\hat\pi) - x\ln\hat\pi\Big]
\;\xrightarrow{d}\;\chi^2_1 .$$

**Two-sided in effect.** $LR_{uc}$ is large when $\hat\pi$ departs from $p$ in
*either* direction. Too few exceedances is a rejection too: a VaR that is never
breached is not measuring the quantile it claims to, and every limit written
against it is slack by an unknown amount.

`xlogx` implements the $0\ln 0 = 0$ convention. Not decoration — with zero
exceedances, `0.0 *. log 0.0` is `0 × -∞ = nan`, which propagates to a p-value of
`nan`.

### 7.3 Christoffersen, independence

`christoffersen_independence`. Let $n_{ij}$ count transitions $i \to j$ in
$\{I_t\}$, and

$$\pi_{01} = \frac{n_{01}}{n_{00}+n_{01}},\quad
\pi_{11} = \frac{n_{11}}{n_{10}+n_{11}},\quad
\pi = \frac{n_{01}+n_{11}}{n} .$$

$H_0: \pi_{01} = \pi_{11}$ — the probability of an exceedance does not depend on
whether yesterday was one.

$$LR_{ind} = -2\Big[(n_{00}+n_{10})\ln(1-\pi) + (n_{01}+n_{11})\ln\pi
- n_{00}\ln(1-\pi_{01}) - n_{01}\ln\pi_{01}
- n_{10}\ln(1-\pi_{11}) - n_{11}\ln\pi_{11}\Big]
\;\xrightarrow{d}\;\chi^2_1 .$$

> **A limitation this project found empirically.** $LR_{ind}$ is a **first-order
> Markov** test: it compares $\Pr(I_t{=}1 \mid I_{t-1}{=}1)$ against
> $\Pr(I_t{=}1 \mid I_{t-1}{=}0)$. It therefore detects exceedances arriving *on
> consecutive days* and is blind to a burst whose members are not adjacent. In
> `make backtest-crisis`, the standard book takes **five exceedances between 15
> September and 7 October 2008** — seventeen sessions spanning Lehman, against
> 0.85 expected — and $LR_{ind}$ returns $p = 0.92$, because exactly one pair in
> that 570-day series falls on adjacent days. The test is not wrong; it is
> answering a narrower question than a reader assumes. §7.4 is the response.

### 7.4 Christoffersen–Pelletier, duration-based independence

`duration_independence`, `exceedance_durations`,
`weibull_profile_log_likelihood`.

Under correct conditional coverage the exceedance process is Bernoulli($p$), so
the **durations** between exceedances are geometric — memoryless, and
exponential in the continuous limit. Memorylessness *is* independence: how long
you have waited says nothing about how much longer you will.

Embed the exponential in a family that can express memory. The Weibull,

$$f(d) = a^{b}\,b\,d^{\,b-1}\exp\!\big(-(ad)^{b}\big),
\qquad
S(d) = \exp\!\big(-(ad)^{b}\big),$$

is exponential exactly at $b = 1$, and the shape reads directly:

| $b$ | hazard | meaning |
|---|---|---|
| $< 1$ | decreasing | a breach makes the next arrive **sooner** than chance — clustering |
| $= 1$ | flat | memoryless — independence |
| $> 1$ | increasing | breaches **more regular** than chance |

$H_0: b = 1$, by likelihood ratio against $\chi^2_1$. Because it consumes
durations rather than adjacencies, a burst landing every third session is as
visible as one landing on consecutive days.

**Censoring.** With exceedances at $t_1 < \dots < t_N$ in a sample of length
$T$, the durations are $d_0 = t_1$ (start to first hit), $d_i = t_{i+1}-t_i$ for
$i = 1..N-1$, and $d_N = T - t_N$ (last hit to end). The two ends are **censored**
— neither is a complete waiting time — so each contributes $\ln S(d)$ rather than
$\ln f(d)$. Treating them as complete would bias the shape downward on any series
that happens to begin or end quietly, which is most of them.

$$\ln L \;=\; \ln S(d_0) \;+\; \sum_{i=1}^{N-1}\ln f(d_i) \;+\; \ln S(d_N).$$

**Profiling out the scale.** Let $U$ be the number of uncensored durations and
$T(b) = \sum_{\text{all }i} d_i^{\,b}$. A censored term contributes $-(ad)^b$ and
no $\ln a$, so the first-order condition in $a$ is

$$\frac{Ub}{a} \;=\; b\,a^{\,b-1}T(b)
\qquad\Longrightarrow\qquad
a^{b} = \frac{U}{T(b)} .$$

Substituting back, $a^{b}T(b) = U$ cancels the last term and leaves a function of
$b$ alone:

$$\ell(b) \;=\; U\ln\!\frac{U}{T(b)} \;+\; U\ln b
\;+\; (b-1)\!\!\sum_{\text{uncensored}}\!\!\ln d_i \;-\; U .$$

One smooth, unimodal dimension — maximised by golden-section search, which needs
no derivative and no initial guess. Then

$$LR_{dur} = -2\big[\ell(1) - \ell(\hat b)\big] \;\xrightarrow{d}\; \chi^2_1 .$$

**Two boundary cases, both real.**

*Fewer than two exceedances* returns `None`, not $p = 1$. With none or one there
is no complete duration to fit, and "the test does not apply" is a different
statement from "no evidence of clustering".

*Zero duration variance* — exceedances at perfectly regular intervals — makes
$\ell(b)$ increasing in $b$ without limit: the MLE diverges. The search is bounded
to $b \in [0.05, 20]$, so the reported shape is a **ceiling artefact** in that
case rather than a fitted value. `make backtest`'s `jumps` series is exactly
this: a −8% loss every twentieth day, which passes Kupiec at $p = 1.0000$, passes
the Markov test, passes the joint verdict at $p = 0.089$, is Basel green — and is
rejected here at $p < 10^{-4}$ with $\hat b$ pinned at 20. A tail that arrives on
a schedule is a metronome, not a market, and nothing else in the battery can see
it.

**What it still cannot do.** It is a **global** fit over the whole duration
distribution. A single localised burst inside a long otherwise-calm series moves
$\hat b$ very little: on the GFC window it returns $\hat b = 0.95$, $p = 0.75$
despite the five exceedances around Lehman, because twenty-five durations spread
over 570 days still look roughly exponential in aggregate. `backtest-crisis`
therefore also prints a plain worst-burst count, labelled a descriptive statistic
and not a test, because it is the only one of the three that sees a local
cluster. Three instruments, three blind spots — adjacency, aggregation, and no
distribution theory at all.

**Not folded into conditional coverage.** $LR_{cc} = LR_{uc} + LR_{ind}$ has two
degrees of freedom because it is Christoffersen's decomposition of exactly those
two pieces. Adding $LR_{dur}$ to that sum would produce a statistic with no
derived distribution, and would silently change the meaning of every verdict the
module has already published. It is reported alongside, with its own verdict.

### 7.5 Conditional coverage

$$LR_{cc} = LR_{uc} + LR_{ind} \;\xrightarrow{d}\; \chi^2_2 ,$$

valid because the two statistics are asymptotically independent. Reported
*alongside* its components rather than instead of them: a joint rejection says
the model is wrong, and the parts say which half. `rejected` gates on this one,
because gating on whichever component looks worst without correcting for testing
twice is a multiple-comparison error.

$\chi^2$ tail probabilities (`chi2_p`) use the regularised upper incomplete
gamma function, $\Pr(\chi^2_k > x) = Q(k/2,\, x/2)$.

### 7.6 Basel traffic light

`traffic_light`. Not a hypothesis test — a supervisor's decision rule. Under
$H_0$ the exceedance count is $X \sim \mathrm{Binomial}(n, p)$, and the zone is
read off the cumulative probability:

| zone | condition |
|---|---|
| green | $\Pr(X \le x) < 0.95$ |
| yellow | $0.95 \le \Pr(X \le x) < 0.9999$ |
| red | $\Pr(X \le x) \ge 0.9999$ |

Computed from the binomial rather than looked up, and it reproduces the published
250-day table exactly — **green 0–4, yellow 5–9, red 10+** — which is asserted as
a test. The boundary is genuinely at 10: $\Pr(X\le 9) = 0.99975$ and
$\Pr(X\le 10) = 0.999946$.

**One-sided by design.** The zone asks only whether a bank is *understating*
risk, because that is the direction that threatens solvency. A coverage test is
two-sided. So a model can be comprehensively wrong and still be green — which is
exactly what `make backtest`'s `jumps`/historical row shows: **zero exceptions in
940 days, a Kupiec p-value that rounds to zero, and a green zone.** Not an
inconsistency; a supervisor's tolerance is not a verdict.

---

## 8. Options

`lib/options.ml` — `Black_scholes.compute`, `Position.delta_equivalent`.

European, no dividends, one flat continuously-compounded rate, one implied
volatility per contract. Time in **years**, volatility **annualised**.

$$d_1 = \frac{\ln(S/K) + \big(r + \tfrac{1}{2}\sigma^2\big)T}{\sigma\sqrt{T}},
\qquad d_2 = d_1 - \sigma\sqrt{T}$$

$$c = S\,\Phi(d_1) - K e^{-rT}\Phi(d_2),
\qquad
p = K e^{-rT}\Phi(-d_2) - S\,\Phi(-d_1)$$

**Greeks** (`Black_scholes.t`):

| | call | put |
|---|---|---|
| $\Delta$ | $\Phi(d_1)$ | $\Phi(d_1) - 1$ |
| $\Gamma$ | $\dfrac{\varphi(d_1)}{S\sigma\sqrt{T}}$ | same |
| $\nu$ | $S\,\varphi(d_1)\sqrt{T}$ | same |
| $\Theta$ | $-\dfrac{S\varphi(d_1)\sigma}{2\sqrt{T}} - rKe^{-rT}\Phi(d_2)$ | $-\dfrac{S\varphi(d_1)\sigma}{2\sqrt{T}} + rKe^{-rT}\Phi(-d_2)$ |

$\Gamma$ and $\nu$ are **identical for both rights**, and that follows from
parity rather than being a coincidence to check: $c - p = S - Ke^{-rT}$ is linear
in $S$ and free of $\sigma$, so the second derivative in $S$ and the first in
$\sigma$ both survive the subtraction. The code computes them once and shares
them.

**Put–call parity**, the sanity check that catches a discount applied to the
wrong leg:

$$c - p = S - Ke^{-rT} .$$

**Units.** $\nu$ is returned per $1.00$ of annualised volatility and $\Theta$ per
year — the mathematical units, matching Hull. The desk conventions (per vol
point, per day) are $\nu/100$ and $\Theta/365$ and are applied at the display
only. Getting this backwards is how a vega limit ends up a hundred times too
loose.

**Degenerate boundary.** When $\sigma\sqrt{T} < 10^{-8}$ the formulas divide by
zero. The correct answer is the intrinsic value with a step delta and
$\Gamma = \nu = \Theta = 0$: a contract with no remaining uncertainty has no
sensitivity to uncertainty. A contract expiring exactly at the money is
genuinely undefined ($\Delta$ is a step, $\Gamma$ unbounded) and is reported as
$\Delta = \Gamma = 0$, because a limit read off an infinity is worse than one
read off a slightly wrong zero.

**Delta-equivalent exposure**, the quantity that folds into `exposure[S]`:

$$E_o = \Delta \cdot m \cdot q \cdot S$$

with $m$ the contract multiplier (100 for US listed equity options) and $q$ the
signed contract count. This is a **first-order** statement — what the position
does for a small move — and $\Gamma$ is precisely the statement that it stops
being true for a large one, which is why $\Gamma$ and $\nu$ are reported
separately rather than folded into any linear sum.

**Additivity of the Greeks.** $\Gamma$ and $\nu$ are sums of derivatives, not
quantiles, so the book's value is the derivative of a sum, which *is* the sum of
the derivatives — exactly, by linearity, with no correlation term. This is why
`Greek_limit` is valid at every scope where `Value_at_risk` is not.

> **The caveat the arithmetic hides.** Summing $\nu$ across contracts at
> *different expiries* adds sensitivities to different volatilities: the 30-day
> and 180-day implieds move together but not identically. A single portfolio
> vega therefore treats the term structure as one number shifting in parallel.
> Every desk does this and calls it parallel-shift vega; it is a bucketed
> approximation, not an identity, and it understates a calendar spread — long
> one expiry against short another nets to near-zero vega while carrying real
> exposure to the term structure twisting. Bucketing $\nu$ by expiry is the fix
> and is not implemented.

**Not modelled:** American exercise, dividends, a term structure of rates, and
any implied-volatility solve. $\sigma$ is an input, because there is no options
chain here to invert a price from. `rho` is absent: with one flat rate there is
nothing to shock.

---

## 9. Time conventions

Two different year counts, on purpose, because they measure different things.

| quantity | basis | where |
|---|---|---|
| option time to expiry | **365** calendar days | `Options.years_to_expiry` |
| volatility annualisation | 252 trading days | (implied by the vol input) |

Black-Scholes discounts and decays in **calendar** time — an option held over a
weekend loses two days of theta and earns two days of carry, and the market
prices it that way. Trading-day counts belong to volatility estimation. Using 252
for expiry would misprice every contract by about 4% of its time value while
looking like a defensible choice.

---

## 10. Where each of these is asserted

| Result | Test |
|---|---|
| VaR/ES hand values, $\varepsilon$ artefact | `test_risk_metrics.ml` |
| $\mathrm{ES}\ge\mathrm{VaR}$ | `test_risk_metrics.ml` |
| VaR monotone in $c$ (both estimators) | `test_properties.ml` |
| EWMA hand-derived $6\times10^{-4}$; $\lambda\to1$ reduction; regime response | `test_vol_estimators.ml` |
| Euler additivity, arbitrary PSD $\Sigma$ and $w$ | `test_properties.ml` |
| $\sum \mathrm{CVaR}_i = \mathrm{VaR}^p_c$ | `test_properties.ml` |
| Negative component for a constructed hedge | `test_properties.ml`, `test_attribution.ml` |
| Rolling-origin lookahead isolation | `test_properties.ml`, `test_var_backtest.ml` |
| Basel zones reproduce the 250-day table | `test_var_backtest.ml` |
| Duration test rejects a non-adjacent burst the Markov test passes | `test_var_backtest.ml` |
| Duration test accepts memoryless durations, rejects perfect regularity | `test_var_backtest.ml` |
| Censored ends are treated as censored | `test_var_backtest.ml` |
| $LR_{dur}$ stays out of the conditional-coverage sum | `test_var_backtest.ml` |
| Hull's prices and Greeks | `test_options.ml` |
| Put–call parity across a grid | `test_options.ml` |
| Delta-hedged book: flat $\Delta$, live $\Gamma$ and $\nu$ | `test_options_graph.ml` |
| Fork isolation under random scenarios | `test_properties.ml`, `test_stress.ml` |
