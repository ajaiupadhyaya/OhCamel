"""The NumPy reference for every kernel (contract II.4). Each function has the
dispatcher's signature and is the semantic definition the Rust kernel is
tested against. Random kernels draw from np.random.Generator(PCG64(seed)) and
ignore ``threads`` (their output does not depend on it). No I/O."""

from __future__ import annotations

import math

import numpy as np

#: Rows per chunk, as in Rust (compute plan A2: 16k).
CHUNK = 16_384


def tail_count(n: int, alpha: float) -> int:
    """risk.core.tail_count, verbatim: ``max(1, ceil(n (1 - alpha) - 1e-9))``."""
    return max(1, math.ceil(n * (1.0 - alpha) - 1e-9))


# ------------------------------------------------------------------ A2: risk
def var_es_from_pnl(pnl: np.ndarray, alpha: float) -> tuple[float, float]:
    """risk.core.empirical_var_es on the losses -pnl (non-finite dropped)."""
    from ..risk.core import empirical_var_es

    return empirical_var_es(-pnl, alpha)


def fhs_paths(std_resid: np.ndarray, sigma_path_params, weights: np.ndarray, horizon: int,
              n_paths: int, seed: int, threads: int) -> np.ndarray:
    """Filtered historical simulation (Barone-Adesi et al. 1999): see native/kernels/src/fhs.rs."""
    mu, om, al, ga, be, s2n = sigma_path_params.arrays()
    z = std_resid
    n = z.shape[0]
    rng = np.random.Generator(np.random.PCG64(seed))
    out = np.empty(n_paths)
    for lo in range(0, n_paths, CHUNK):
        m = min(CHUNK, n_paths - lo)
        s2 = np.broadcast_to(s2n, (m, s2n.size)).copy()
        logg = np.zeros(m)
        wiped = np.zeros(m, dtype=bool)
        for _ in range(horizon):
            e = np.sqrt(s2) * z[rng.integers(0, n, size=m)]
            rp = ((mu + e) / 100.0) @ weights
            bad = rp <= -1.0
            wiped |= bad
            logg += np.log1p(np.where(bad, 0.0, rp))
            s2 = om + (al + ga * (e < 0)) * e * e + be * s2
        out[lo:lo + m] = np.where(wiped, -1.0, np.expm1(logg))
    return out


def _kendall_corr(returns: np.ndarray, threads: int = 1) -> np.ndarray:
    """R_ij = sin(pi tau_b / 2) (Lindskog, McNeil & Schmock 2003); tau_b by scipy."""
    from scipy.stats import kendalltau

    n, k = returns.shape
    if n < 2:
        raise ValueError(f"returns must be n x k with n >= 2; got {returns.shape}")
    for i in range(k):
        if np.all(returns[:, i] == returns[0, i]):
            raise ValueError(f"column {i} is constant: Kendall's tau is undefined")
    r = np.eye(k)
    for i in range(k):
        for j in range(i + 1, k):
            tau = float(kendalltau(returns[:, i], returns[:, j]).statistic)
            r[i, j] = r[j, i] = math.sin(math.pi / 2.0 * tau)
    return r


def copula_t_paths(returns: np.ndarray, weights: np.ndarray, nu: float, horizon: int, n_paths: int,
                   seed: int, threads: int) -> np.ndarray:
    """Student-t copula with empirical marginals: see native/kernels/src/copula.rs."""
    from scipy.stats import t as student_t

    n, k = returns.shape
    try:
        chol = np.linalg.cholesky(_kendall_corr(returns))
    except np.linalg.LinAlgError:
        raise ValueError("the Kendall sin-transform correlation matrix is not positive definite") from None
    cols = np.sort(returns, axis=0)
    rng = np.random.Generator(np.random.PCG64(seed))
    out = np.empty(n_paths)
    ks = np.arange(k)
    for lo in range(0, n_paths, CHUNK):
        m = min(CHUNK, n_paths - lo)
        logg = np.zeros(m)
        wiped = np.zeros(m, dtype=bool)
        for _ in range(horizon):
            z = rng.standard_normal((m, k)) @ chol.T
            s = np.sqrt(rng.chisquare(nu, size=m) / nu)
            u = student_t.cdf(z / s[:, None], nu)
            idx = np.clip(np.ceil(u * n).astype(np.int64) - 1, 0, n - 1)
            rp = cols[idx, ks] @ weights
            bad = rp <= -1.0
            wiped |= bad
            logg += np.log1p(np.where(bad, 0.0, rp))
        out[lo:lo + m] = np.where(wiped, -1.0, np.expm1(logg))
    return out


# ------------------------------------------------------------------ A3: bootstrap
def _sb_indices(rng: np.random.Generator, n: int, p: float, b: int) -> np.ndarray:
    """(b, n) stationary-bootstrap index paths: a block starts at t = 0 and wherever
    u < p, at a uniform position; otherwise the index is the previous one + 1 mod n."""
    new = rng.random((b, n)) < p
    new[:, 0] = True
    starts = rng.integers(0, n, size=(b, n))
    t = np.arange(n)
    last = np.maximum.accumulate(np.where(new, t, 0), axis=1)
    return (np.take_along_axis(starts, last, axis=1) + (t - last)) % n


def stationary_bootstrap_means(x: np.ndarray, mean_block: float, reps: int, seed: int,
                               threads: int) -> np.ndarray:
    """(reps, k) column means of stationary-bootstrap resamples (Politis & Romano 1994).

    Each column is reduced on its own (m, n) C-contiguous gather, so column j's means are
    bit-identical whatever k is. ``x[idx].mean(axis=1)`` on (m, n, k) would not be: NumPy
    reduces the contiguous axis pairwise but a strided axis naively, so (m, n, 1) and (m, n, 2)
    can differ in the last bits (a review measured 3.7e-18 on SPY's last 300 sessions).
    The batch depends on n only, so the index stream is the same for every k too."""
    n, k = x.shape
    rng = np.random.Generator(np.random.PCG64(seed))
    p = 1.0 / mean_block
    out = np.empty((reps, k))
    batch = max(1, min(4096, 2_000_000 // n))          # <= 2M indices (and one 2M gather) in flight
    for lo in range(0, reps, batch):
        m = min(batch, reps - lo)
        idx = _sb_indices(rng, n, p, m)
        for j in range(k):
            out[lo:lo + m, j] = x[:, j][idx].mean(axis=1)
    return out


# ------------------------------------------------------------------ A4: cscv
MAX_PARTITIONS = 20
ZERO_VAR_REL = 1e-10      # s2 - n mean^2 <= ZERO_VAR_REL * s2 is zero variance (as cscv.rs)
CSCV_CHUNK_BYTES = 8 * 2**20  # each (splits x trials) float64 temporary of the reference (Lane B B5)


def cscv_pbo(perf: np.ndarray, n_partitions: int, threads: int) -> dict:
    """CSCV PBO (Bailey et al. 2017): backtest/validation.py's vectorised NumPy at 0b87dec,
    moved here and returning the per-combination selection with it. One change: zero variance
    is the relative rule s2 - n mean^2 <= ZERO_VAR_REL * s2 (shared with cscv.rs) instead of
    var > 0, whose verdict on a constant trial depended on the sign of a rounding residue and
    so on summation order. The combinations are scored in chunks whose (chunk x N) temporaries
    stay under CSCV_CHUNK_BYTES (Lane B, B5), so memory is O(C(S, S/2)) plus 8 MiB."""
    import itertools

    from scipy import stats

    m, s = perf, int(n_partitions)
    if m.shape[1] < 2:
        raise ValueError("CSCV needs a T x N matrix with N >= 2 trials")
    if s < 2 or s % 2:
        raise ValueError("n_partitions must be an even integer >= 2")
    if s > MAX_PARTITIONS:
        raise ValueError(f"n_partitions must be <= {MAX_PARTITIONS} (C(20, 10) = 184,756 combinations)")
    if np.isnan(m).any():
        raise ValueError("returns matrix contains NaN")
    if len(m) < 4 * s:
        raise ValueError(f"need at least {4 * s} rows for {s} partitions")
    t = (len(m) // s) * s
    blocks = m[len(m) - t:].reshape(s, t // s, m.shape[1])
    b1, b2, bn = blocks.sum(axis=1), (blocks ** 2).sum(axis=1), np.full(s, t // s, dtype=float)
    t1, t2, tn = b1.sum(0), b2.sum(0), bn.sum()
    combos = np.array(list(itertools.combinations(range(s), s // 2)))
    n, c_all = m.shape[1], len(combos)

    def sharpe(s1: np.ndarray, s2: np.ndarray, n: np.ndarray) -> np.ndarray:
        mean = s1 / n[:, None]
        dev = s2 - n[:, None] * mean ** 2                  # (n - 1) x variance
        var = dev / (n[:, None] - 1.0)
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.where(dev > ZERO_VAR_REL * s2, mean / np.sqrt(np.maximum(var, 0.0)), np.nan)

    best = np.empty(c_all, dtype=np.int64)
    w, x, y = np.empty(c_all), np.empty(c_all), np.empty(c_all)
    step = max(1, CSCV_CHUNK_BYTES // (8 * n))  # each (chunk x N) temporary stays under CSCV_CHUNK_BYTES
    for lo in range(0, c_all, step):
        cc = combos[lo:lo + step]
        k = np.arange(len(cc))
        mask = np.zeros((len(cc), s))
        mask[k[:, None], cc] = 1.0
        is1, is2, isn = mask @ b1, mask @ b2, mask @ bn
        sr_is, sr_oos = sharpe(is1, is2, isn), sharpe(t1 - is1, t2 - is2, tn - isn)
        del is1, is2
        bst = np.argmax(np.where(np.isfinite(sr_is), sr_is, -np.inf), axis=1)
        ranks = stats.rankdata(np.where(np.isfinite(sr_oos), sr_oos, -np.inf), axis=1, method="average")
        best[lo:lo + len(cc)] = bst
        w[lo:lo + len(cc)] = ranks[k, bst] / (n + 1.0)
        x[lo:lo + len(cc)] = sr_is[k, bst]
        y[lo:lo + len(cc)] = sr_oos[k, bst]
        del sr_is, sr_oos, ranks
    lam = np.log(w / (1.0 - w))
    return {"pbo": float(np.mean(lam <= 0)), "logits": lam, "n_combinations": int(c_all),
            "selected": best, "is_sharpe": x, "oos_sharpe": y}


# ------------------------------------------------------------------ A5: garch
SQRT2_OV_PI = math.sqrt(2.0 / math.pi)
LNSIGMA_MAX = math.log(np.finfo(float).max)


def _backcast(y: np.ndarray) -> float:
    e = y - y.mean()
    tau = min(75, e.size)
    w = 0.94 ** np.arange(tau)
    return float(np.sum(e[:tau] ** 2 * w / w.sum()))


def _variance_path(p: np.ndarray, y: np.ndarray, kind: str, bc: float) -> np.ndarray | None:
    from scipy.signal import lfilter

    mu, om = float(p[0]), float(p[1])
    n = y.size
    if kind == "egarch":
        al, ga, be = float(p[2]), float(p[3]), float(p[4])
        s2 = np.empty(n)
        lns = min(om + be * math.log(bc), LNSIGMA_MAX)
        s2[0] = math.exp(lns)
        yl = y.tolist()
        for t in range(1, n):
            if not s2[t - 1] > 0.0:                        # underflow to 0 (or NaN): infeasible, as garch.rs
                return None
            z = (yl[t - 1] - mu) / math.sqrt(s2[t - 1])
            lns = min(om + al * (abs(z) - SQRT2_OV_PI) + ga * z + be * lns, LNSIGMA_MAX)
            s2[t] = math.exp(lns)
    else:
        al = float(p[2])
        ga = float(p[3]) if kind == "gjr" else 0.0
        be = float(p[-2])
        e = y - mu
        s2 = np.empty(n)
        s2[0] = om + (al + 0.5 * ga + be) * bc
        u = om + (al + ga * (e[:-1] < 0)) * e[:-1] ** 2
        s2[1:], _ = lfilter([1.0], [1.0, -be], u, zi=[be * s2[0]])
    if not np.all(np.isfinite(s2)) or np.any(s2 <= 0):
        return None
    return s2


def garch_nll(params: np.ndarray, r: np.ndarray, kind: str) -> float:
    """Total Student-t NLL of (GJR-/E)GARCH(1,1): see native/kernels/src/garch.rs."""
    from scipy.special import gammaln

    p = np.asarray(params, dtype=float)
    nu = float(p[-1])
    if not nu > 2.0 or not np.all(np.isfinite(p)):
        return math.inf
    s2 = _variance_path(p, r, kind, _backcast(r))
    if s2 is None:
        return math.inf
    e = r - p[0]
    c = gammaln((nu + 1) / 2) - gammaln(nu / 2) - 0.5 * math.log(math.pi * (nu - 2))
    with np.errstate(over="ignore"):                       # an overflow is an infeasible point: inf below
        ll = r.size * c - 0.5 * np.sum(np.log(s2)) - 0.5 * (nu + 1) * np.sum(np.log1p(e * e / ((nu - 2) * s2)))
    return float(-ll) if math.isfinite(ll) else math.inf


def garch_fit(r: np.ndarray, kind: str, x0: np.ndarray | None):
    """garch/gjr: risk.garch.fit_garch_fast_pct (SLSQP, analytic gradient) -- compute plan A5's
    parity target. egarch: the Rust kernel's own algorithm -- scipy's bounded Nelder-Mead on the
    mean NLL inside the same box, restarted from the best point until a restart gains <= 1e-12
    -- because the EGARCH likelihood is multimodal: on SPY's last 1,000 fixture sessions SLSQP
    (and arch) stop at a local optimum (NLL 1292.137, alpha > 0) that Nelder-Mead walks past to
    a better one (NLL 1290.938, alpha < 0), so a different optimiser would not be a reference."""
    from scipy.optimize import minimize

    from .types import GarchFitResult

    if kind in ("garch", "gjr"):
        from ..risk.garch import fit_garch_fast_pct

        f = fit_garch_fast_pct(r, kind, x0)
        return GarchFitResult(kind, np.asarray(f.x, dtype=float), float(-f.loglik), bool(f.converged),
                              int(f.iterations), f.sigma2, float(f.next_variance_pct), f.std_resid)
    mean, var, amax = float(r.mean()), float(r.var()), float(np.abs(r).max())
    lnv, lc = math.log(var), math.log(10_000.0)
    bounds = [(-10 * amax, 10 * amax), (lnv - lc, lnv + lc), (-5.0, 5.0), (-5.0, 5.0), (0.0, 0.9999), (2.05, 500.0)]
    start = np.array([mean, 0.05 * lnv, 0.1, -0.05, 0.95, 8.0]) if x0 is None else np.asarray(x0, dtype=float)
    start = np.clip(start, [b[0] for b in bounds], [b[1] for b in bounds])
    n = r.size

    def obj(p: np.ndarray) -> float:
        v = garch_nll(p, r, "egarch") / n
        return v if math.isfinite(v) else 1e10

    opts = {"xatol": 1e-10, "fatol": 1e-14, "maxiter": 5_000, "maxfev": 10_000}
    x, best = start, obj(start)
    nit, converged = 0, False
    for _ in range(8):                                     # garch.rs's restart rule
        res = minimize(obj, x, method="Nelder-Mead", bounds=bounds, options=opts)
        nit += int(res.nit)
        gain = best - float(res.fun)
        if res.fun <= best:
            x, best = np.asarray(res.x, dtype=float), float(res.fun)
        if gain <= 1e-12 * max(abs(best), 1.0):
            converged = bool(res.success)
            break
    s2 = _variance_path(x, r, "egarch", _backcast(r))
    if s2 is None:   # garch.rs returns Err here; raise the same error rather than crash on None
        raise ValueError("the GARCH fit ended at a non-finite likelihood")
    e_last, s_last = float(r[-1] - x[0]), float(s2[-1])
    z = e_last / math.sqrt(s_last)
    nxt = math.exp(min(x[1] + x[2] * (abs(z) - SQRT2_OV_PI) + x[3] * z + x[4] * math.log(s_last), LNSIGMA_MAX))
    return GarchFitResult("egarch", x, float(garch_nll(x, r, "egarch")), converged, nit, s2, nxt,
                          (r - x[0]) / np.sqrt(s2))


# ------------------------------------------------------------------ A6: backtest
def backtest_weights(prices: np.ndarray, target_w: np.ndarray, decision_idx: np.ndarray, cost_bps: np.ndarray,
                     borrow_bps: float, rf: np.ndarray, financing_bps: float = 0.0):
    """backtest/engine.run_weights's accounting loop at 0b87dec, moved here unchanged; completion task 2.3 made the
    cost one half-spread per name (``cost_bps``, length n_a) and added the financing spread on gross above 1."""
    from .types import BacktestPath

    n_t, n_a = prices.shape
    rets = np.zeros_like(prices)
    rets[1:] = prices[1:] / prices[:-1] - 1.0
    exec_at = {int(s): target_w[j] for j, s in enumerate(decision_idx)}
    w = np.zeros(n_a)
    W = np.zeros((n_t, n_a))
    gross = np.zeros(n_t)
    net = np.zeros(n_t)
    turnover = np.zeros(n_t)
    trades = np.zeros((n_t, n_a))
    costs = np.zeros(n_t)
    borrow = np.zeros(n_t)
    financing = np.zeros(n_t)
    c = np.asarray(cost_bps, dtype=float) / 1e4
    b = borrow_bps / 1e4 / 252
    f = financing_bps / 1e4 / 252
    ruined = False
    first = min(exec_at) if exec_at else n_t
    gross[1:first] = rf[1:first]
    net[1:first] = rf[1:first]
    rf_l = rf.tolist()
    for s in range(first, n_t):
        if s > 0:
            r = rets[s]
            wr = w * r
            wsum = float(w.sum())
            wabs = float(np.abs(w).sum())
            gs = float(wr.sum()) + (1.0 - wsum) * rf_l[s]
            bs = b * 0.5 * (wabs - wsum) if b else 0.0
            fs = f * (wabs - 1.0) if f and wabs > 1.0 else 0.0
            gross[s] = gs
            borrow[s] = bs
            financing[s] = fs
            g = gs - bs - fs
            if 1.0 + g <= 0:
                net[s] = -1.0
                ruined = True
                W[s:] = 0.0
                break
            w = (w + wr) / (1.0 + g)
            net[s] = g
        tw = exec_at.get(s)
        if tw is not None:
            dw = tw - w
            trades[s] = dw
            to = float(np.abs(dw).sum())
            turnover[s] = to
            costs[s] = float((c * np.abs(dw)).sum())
            net[s] = (1.0 + net[s]) * (1.0 - costs[s]) - 1.0
            w = tw.copy()
        W[s] = w
    return BacktestPath(gross, net, W, turnover, trades, costs, borrow, ruined, financing)


# ------------------------------------------------------------------ A7: options
def svi_fit(k: np.ndarray, w: np.ndarray, weights: np.ndarray):
    """options.svi.calibrate, unchanged: wings within Lee's bound, the vertex inside the quotes,
    and no butterfly arbitrage on [-K_MAX, K_MAX]."""
    from ..options.svi import calibrate
    from .types import SviFitResult

    p, sse, constrained = calibrate(k, w, weights)
    return SviFitResult(float(p.a), float(p.b), float(p.rho), float(p.m), float(p.sigma), float(sse), bool(constrained))


# ------------------------------------------------------------------ American options
#: Volatility search interval of american_iv: a floor (raised per contract so the coarse tree's
#: CRR probability stays in (0, 1)), a ceiling of 500%, the start when no usable guess is given.
AM_SIG_FLOOR, AM_SIG_HI, AM_SIG_START = 1e-3, 5.0, 0.25
#: Brent's tolerances (scipy brentq's convention: |step| < (xtol + rtol |x|) / 2) and its cap.
AM_XTOL, AM_RTOL, AM_MAXITER = 1e-7, 4.0 * np.finfo(float).eps, 100
#: The screen: a guess the tree prices within AM_SCREEN x vega of the quote is kept after one Newton step.
AM_SCREEN = 3e-5
#: The identifiability guard's vol step (one vol point) and its skip factor (american.rs ``identified``).
AM_VOL_PT, AM_GUARD_SKIP = 0.01, 100.0
#: Brent's slope stop and the guard's skip use the bracket's secant only when it is this narrow.
AM_LOCAL_W = 1e-3
#: Brent stops once the pricing error is within AM_FTOL in vol at the bracket's secant slope (or AM_XTOL).
AM_FTOL = 3e-7
#: A dividend counts at tree level i when t_d / dt >= i - AM_DIV_EPS (exercise just before ex counts).
AM_DIV_EPS = 1e-9
#: A quote within AM_EX_TOL x K of immediate exercise identifies no vol (the tree is flat there).
AM_EX_TOL = 1e-10
#: The tree keeps offsets within AM_BAND sqrt(n) steps beyond the drift's whole move (weight beyond ~1e-14).
AM_BAND = 8.0
#: The last step's Black-Scholes value saturates beyond |phi d| > AM_SAT (N(9) = 1 - 1e-19).
AM_SAT = 9.0
#: American options per block: the tree's arrays stay cache-sized.
AM_BLOCK = 256


def _ndtr(x: np.ndarray) -> np.ndarray:
    from scipy.special import ndtr

    return ndtr(x)


def _escrowed(S, T, r, div_t, div_amt):
    """Spot less the present value of the dividends paid in (0, T), summed in schedule order."""
    pv = np.zeros_like(S)
    for td, a in zip(div_t.tolist(), div_amt.tolist(), strict=True):
        live = (td > 0.0) & (td < T)
        pv = pv + np.where(live, a * np.exp(-r * np.where(live, td, 0.0)), 0.0)
    return S - pv


def _bbs_tree(s0, K, T, r, q, sig, phi, div_t, div_amt, n):
    """Root value of an n-step CRR tree on the escrowed spot ``s0`` (one row per contract),
    the last step replaced by the Black-Scholes value (Broadie & Detemple 1996) and every node
    floored at exercise -- whose value adds back the dividends still to come before T."""
    dt = T / n
    sq = sig * np.sqrt(dt)
    u = np.exp(sq)
    d = 1.0 / u
    p = (np.exp((r - q) * dt) - d) / (u - d)
    disc = np.exp(-r * dt)
    pu, pd = disc * p, disc * (1.0 - p)
    divs = list(zip(div_t.tolist(), div_amt.tolist(), strict=True))

    def pv(i: int) -> np.ndarray:
        ti = i * dt
        out = np.zeros_like(s0)
        for td, a in divs:
            live = (td < T) & (td / dt >= i - AM_DIV_EPS)
            out = out + np.where(live, a * np.exp(-r * (td - ti)), 0.0)
        return out

    # Node (i, j) sits at log-offset o = 2j - i (units of sigma sqrt dt), grid index o + m on one
    # grid S* e^{o sigma sqrt dt}, o in -m..m, stored by index parity: rows idx // 2 of the arrays
    # for idx % 2 (level i lives on parity (m - i) % 2). Only offsets within each contract's band
    # are computed; a child outside reads its exercise value phi (g - K).
    m = n - 1
    # each contract's band covers AM_BAND sqrt(n) steps beyond its drift's whole move; the block
    # runs on its widest band and puts the slots outside a contract's own band back to exercise
    bands = np.minimum(np.ceil(AM_BAND * math.sqrt(n) + np.abs((r - q - 0.5 * sig * sig) * T / sq)), m)
    band = int(bands.max())
    offs = np.arange(2 * m + 1) - m
    g = s0[None, :] * np.exp(offs[:, None] * sq[None, :])
    gp = (g[0::2], g[1::2])
    xp = tuple(phi * (gg - K) for gg in gp)
    vp = (xp[0].copy(), xp[1].copy())
    out_band = np.abs(offs)[:, None] > bands[None, :]
    oob = (out_band[0::2], out_band[1::2])
    trim = bool(out_band.any())

    def rng(i: int) -> tuple[int, int]:
        h = min(i, band if (band + i) % 2 == 0 else band - 1)
        return (m - h) // 2, (m + h) // 2

    ta, tb = rng(m)
    s = gp[0][ta: tb + 1]
    with np.errstate(divide="ignore", invalid="ignore"):
        d1 = (np.log(s / K) + (r - q + 0.5 * sig * sig) * dt) / sq
    d2 = d1 - sq
    z1, z2 = phi * d1, phi * d2
    eq_, er_ = np.exp(-q * dt), np.exp(-r * dt)
    # N(z) is 1.0 in double above AM_SAT and below N(-AM_SAT) s < 1e-18 s: no erf there
    v = np.where(np.minimum(z1, z2) > AM_SAT, phi * (s * eq_ - K * er_),
                 np.where(np.maximum(z1, z2) < -AM_SAT, 0.0, phi * (s * eq_ * _ndtr(z1) - K * er_ * _ndtr(z2))))
    np.maximum(v, phi * (s + pv(m) - K) if divs else xp[0][ta: tb + 1], out=vp[0][ta: tb + 1])
    if trim:
        np.copyto(vp[0][ta: tb + 1], xp[0][ta: tb + 1], where=oob[0][ta: tb + 1])
    tmp = np.empty_like(vp[0])
    ex = np.empty_like(vp[0])
    for i in range(m - 1, -1, -1):
        par = (m - i) % 2
        ta, tb = rng(i)
        du = 0 if par == 0 else 1          # children of slot t: up t + du, down t + du - 1
        src = vp[1 - par]
        c, e = tmp[: tb - ta + 1], ex[: tb - ta + 1]
        np.multiply(src[ta + du: tb + du + 1], pu, out=c)
        np.multiply(src[ta + du - 1: tb + du], pd, out=e)
        np.add(c, e, out=c)
        if divs:
            np.add(gp[par][ta: tb + 1], pv(i), out=e)
            np.subtract(e, K, out=e)
            np.multiply(e, phi, out=e)
            np.maximum(c, e, out=vp[par][ta: tb + 1])
        else:
            np.maximum(c, xp[par][ta: tb + 1], out=vp[par][ta: tb + 1])
        if trim:
            np.copyto(vp[par][ta: tb + 1], xp[par][ta: tb + 1], where=oob[par][ta: tb + 1])
    return vp[m % 2][m // 2].copy()


def _am_valid(S, K, T, r, q, phi, s0, n):
    return (np.isfinite(S) & np.isfinite(K) & np.isfinite(T) & np.isfinite(r) & np.isfinite(q)
            & (S > 0) & (K > 0) & (T > 0) & (s0 > 0) & (np.abs(phi) == 1.0))


def _sig_floor(T, r, q, n):
    """The coarse (n/2-step) tree's CRR probability is in (0, 1) iff sigma sqrt(dt) > |r - q| dt."""
    return np.maximum(AM_SIG_FLOOR, 1.5 * np.abs(r - q) * np.sqrt(2.0 * T / n))


def _am_price(S, K, T, r, q, sig, phi, div_t, div_amt, n, s0):
    """BBSR (2 V_n - V_{n/2}), floored at immediate exercise; rows already validated."""
    v = 2.0 * _bbs_tree(s0, K, T, r, q, sig, phi, div_t, div_amt, n) \
        - _bbs_tree(s0, K, T, r, q, sig, phi, div_t, div_amt, n // 2)
    return np.maximum(v, np.maximum(phi * (S - K), 0.0))


def american_price(S, K, T, r, q, sigma, phi, div_t, div_amt, steps: int, threads: int) -> np.ndarray:
    """American option values (options/american.py's model): a ``steps``-step CRR tree on the
    escrowed spot with the Black-Scholes value at the last step, Richardson-extrapolated against
    the ``steps/2`` tree (BBSR, Broadie & Detemple 1996), floored at immediate exercise. NaN for an
    invalid contract (non-positive S, K, T, sigma or escrowed spot, or a sigma at which the coarse
    tree's CRR probability leaves (0, 1)). ``threads`` is ignored (no randomness)."""
    out = np.full(S.shape, np.nan)
    s0 = _escrowed(S, T, r, div_t, div_amt)
    with np.errstate(invalid="ignore"):
        ok = _am_valid(S, K, T, r, q, phi, s0, steps) & np.isfinite(sigma) & (sigma > 0)
        ok &= sigma * np.sqrt(2.0 * T / steps) > np.abs(r - q) * (2.0 * T / steps)
    idx = np.flatnonzero(ok)
    for lo in range(0, idx.size, AM_BLOCK):
        b = idx[lo:lo + AM_BLOCK]
        out[b] = _am_price(S[b], K[b], T[b], r[b], q[b], sigma[b], phi[b], div_t, div_amt, steps, s0[b])
    return out


def _brent_vec(f, xpre, xcur, fpre, fcur):
    """scipy's brentq (scipy/optimize/Zeros/brentq.c), one lane per contract, from a bracket with
    known values, also stopping once |f| is within AM_FTOL in x at the bracket's secant slope;
    ``f(x, rows)`` evaluates the rows still searching. NaN where not converged."""
    xblk = np.zeros_like(xcur)
    fblk = np.zeros_like(xcur)
    spre = np.zeros_like(xcur)
    scur = np.zeros_like(xcur)
    out = np.full(xcur.shape, np.nan)
    out_slope = np.full(xcur.shape, np.nan)
    act = np.ones(xcur.shape, dtype=bool)
    for _ in range(AM_MAXITER):
        with np.errstate(divide="ignore", invalid="ignore"):
            sc = act & (fpre != 0) & (fcur != 0) & (np.signbit(fpre) != np.signbit(fcur))
            xblk = np.where(sc, xpre, xblk)
            fblk = np.where(sc, fpre, fblk)
            spre = np.where(sc, xcur - xpre, spre)
            scur = np.where(sc, xcur - xpre, scur)
            sw = act & (np.abs(fblk) < np.abs(fcur))
            xpre, xcur, xblk = np.where(sw, xcur, xpre), np.where(sw, xblk, xcur), np.where(sw, xcur, xblk)
            fpre, fcur, fblk = np.where(sw, fcur, fpre), np.where(sw, fblk, fcur), np.where(sw, fcur, fblk)
            delta = (AM_XTOL + AM_RTOL * np.abs(xcur)) / 2.0
            sbis = (xblk - xcur) / 2.0
            # the secant only over a bracket narrower than AM_LOCAL_W (american.rs: a wide bracket's
            # secant can be far steeper than the function at xcur)
            slope = np.where(np.abs(xblk - xcur) <= AM_LOCAL_W, np.abs((fblk - fcur) / (xblk - xcur)), 0.0)
            conv = act & ((fcur == 0) | (np.abs(sbis) < delta) | (np.abs(fcur) <= AM_FTOL * slope))
            out = np.where(conv, xcur, out)
            out_slope = np.where(conv, slope, out_slope)
            act &= ~conv
            if not act.any():
                break
            interp = (np.abs(spre) > delta) & (np.abs(fcur) < np.abs(fpre))
            dpre = (fpre - fcur) / (xpre - xcur)
            dblk = (fblk - fcur) / (xblk - xcur)
            stry = np.where(xpre == xblk, -fcur * (xcur - xpre) / (fcur - fpre),
                            -fcur * (fblk * dblk - fpre * dpre) / (dblk * dpre * (fblk - fpre)))
            take = interp & (2.0 * np.abs(stry) < np.minimum(np.abs(spre), 3.0 * np.abs(sbis) - delta))
            spre = np.where(act, np.where(take, scur, sbis), spre)
            scur = np.where(act, np.where(take, stry, sbis), scur)
            xpre = np.where(act, xcur, xpre)
            fpre = np.where(act, fcur, fpre)
            xcur = np.where(act, xcur + np.where(np.abs(scur) > delta, scur, np.where(sbis > 0, delta, -delta)), xcur)
        rows = np.flatnonzero(act)
        fcur[rows] = f(xcur[rows], rows)
    return out, out_slope


def _identified(f, x, slope, tol, lo, hi):
    """The identifiability guard (american.rs ``identified``), one lane per root: a root stands only
    if one vol point up and down (clipped to [lo, hi]) each move the price by at least ``tol``; the
    two prices are skipped where the slope already shows GUARD_SKIP x tol per point."""
    out = x.copy()
    with np.errstate(invalid="ignore"):
        need = np.isfinite(x) & ~(np.isfinite(slope) & (slope * AM_VOL_PT >= AM_GUARD_SKIP * tol))
    up, dn = np.minimum(x + AM_VOL_PT, hi), np.maximum(x - AM_VOL_PT, lo)
    rows = np.flatnonzero(need & (up > x) & (dn < x))
    out[need & ~((up > x) & (dn < x))] = np.nan
    if rows.size:
        ok = (f(up[rows], rows) >= tol[rows]) & (-f(dn[rows], rows) >= tol[rows])
        out[rows[~ok]] = np.nan
    return out


def _am_iv_block(price, S, K, T, r, q, phi, guess, flat_tol, div_t, div_amt, n, s0):
    """american_iv on validated rows: bracket from the guess, then Brent, then the identifiability
    guard (see american_iv)."""
    m = price.size
    lo = _sig_floor(T, r, q, n)
    hi = np.full(m, AM_SIG_HI)
    out = np.full(m, np.nan)
    slope = np.zeros(m)                      # the local slope each root comes with (0: unknown)

    def f(x, rows):
        return _am_price(S[rows], K[rows], T[rows], r[rows], q[rows], x, phi[rows], div_t, div_amt, n,
                         s0[rows]) - price[rows]

    g = np.where(np.isfinite(guess), guess, AM_SIG_START)
    x0 = np.clip(g, lo, hi)
    alive = lo < hi
    rows = np.flatnonzero(alive)
    f0 = np.full(m, np.nan)
    f0[rows] = f(x0[rows], rows)
    hit = alive & (f0 == 0)
    out[hit] = x0[hit]
    alive &= ~hit
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        sd = x0 * np.sqrt(T)
        d1 = (np.log(s0 / K) + (r - q + 0.5 * x0 * x0) * T) / sd
        vega = s0 * np.exp(-q * T) * np.exp(-0.5 * d1 * d1) / math.sqrt(2.0 * math.pi) * np.sqrt(T)
        step = -1.25 * f0 / vega
    # the screen: one tree at the guess within AM_SCREEN vol of the price (negligible early-exercise
    # premium and tree error) -> one Newton step with the Black-Scholes vega lands within
    # AM_SCREEN |1 - vega_BS / vega_tree| of the root
    with np.errstate(invalid="ignore"):
        scr = alive & np.isfinite(vega) & (vega > 0) & (np.abs(f0) <= AM_SCREEN * vega)
    out[scr] = (x0 - f0 / vega)[scr]
    slope[scr] = vega[scr]
    alive &= ~scr
    step = np.where(np.isfinite(step) & (step != 0), step, np.where(f0 > 0, lo - x0, hi - x0))
    x1, f1 = x0.copy(), f0.copy()
    bracketed = np.zeros(m, dtype=bool)
    for _ in range(64):
        srch = alive & ~bracketed
        if not srch.any():
            break
        xn = np.clip(x0 + step, lo, hi)
        rows = np.flatnonzero(srch)
        fn = np.full(m, np.nan)
        fn[rows] = f(xn[rows], rows)
        hit = srch & (fn == 0)
        out[hit] = xn[hit]
        alive &= ~hit
        srch &= ~hit
        flip = srch & (np.signbit(fn) != np.signbit(f0))
        x1 = np.where(flip, xn, x1)
        f1 = np.where(flip, fn, f1)
        bracketed |= flip
        stuck = srch & ~flip & ((xn <= lo) | (xn >= hi))
        alive &= ~stuck
        move = srch & ~flip & ~stuck
        x0 = np.where(move, xn, x0)
        f0 = np.where(move, fn, f0)
        step = np.where(move, 2.0 * step, step)
    br = np.flatnonzero(alive & bracketed)
    if br.size:
        def fb(x, rr):
            return f(x, br[rr])
        out[br], slope[br] = _brent_vec(fb, x0[br], x1[br], f0[br], f1[br])
    return _identified(f, out, slope, flat_tol, lo, hi)


def american_iv(price, S, K, T, r, q, phi, guess, flat_tol, div_t, div_amt, steps: int,
                threads: int) -> np.ndarray:
    """The volatility at which american_price equals ``price``; NaN where no volatility in the
    search interval does (a price at or below immediate exercise, at or above the underlying for a
    call or the strike for a put, or below the tree's value at the volatility floor).

    From ``guess`` (clipped to [floor, 500%]; 25% when not finite) the search steps by 1.25 times
    the Black-Scholes Newton step -- doubling each time the sign of the pricing error does not
    change -- until it brackets the root, then runs Brent (scipy's brentq, xtol 1e-7). When the
    tree at the guess is already within 3e-5 x vega of the price, the guess is kept after one
    Newton step instead (the screen). The guess changes only the work, not the root (to 1e-6).

    Every root then passes the identifiability guard: moving the vol one point up and one point
    down must each move the price by at least ``flat_tol`` (per contract), else NaN -- a price flat
    in vol has no identifiable root, and any returned point of the flat stretch would be decided by
    the last bits of the arithmetic (a root at the volatility floor fails it: it cannot move down).
    """
    out = np.full(price.shape, np.nan)
    s0 = _escrowed(S, T, r, div_t, div_amt)
    with np.errstate(invalid="ignore"):
        upper = np.where(phi > 0, S, K)
        ok = _am_valid(S, K, T, r, q, phi, s0, steps) & np.isfinite(price)
        ok &= (price - np.maximum(phi * (S - K), 0.0) > AM_EX_TOL * K) & (price < upper)
    idx = np.flatnonzero(ok)
    for lo in range(0, idx.size, AM_BLOCK):
        b = idx[lo:lo + AM_BLOCK]
        out[b] = _am_iv_block(price[b], S[b], K[b], T[b], r[b], q[b], phi[b], guess[b], flat_tol[b], div_t,
                              div_amt, steps, s0[b])
    return out


def realized_vol_minute(ts_ns: np.ndarray, px: np.ndarray, session_bounds: np.ndarray) -> np.ndarray:
    """Daily realized variance: sum of squared consecutive log returns inside each [open, close)."""
    lo = np.searchsorted(ts_ns, session_bounds[:, 0], side="left")
    hi = np.searchsorted(ts_ns, session_bounds[:, 1], side="left")
    lp = np.log(px)
    out = np.full(len(session_bounds), np.nan)
    for d, (a, b) in enumerate(zip(lo, hi, strict=True)):
        if b - a >= 2:
            r = np.diff(lp[a:b])
            out[d] = float(np.sum(r * r))
    return out


# ------------------------------------------------------------------ Heston by COS (completion task 4.6)
HESTON_L = 12.0          # truncation half-width in standard deviations of ln(S_T / F) (cumulant c2)


def heston_cumulants(T: float, kappa: float, theta: float, sigma: float, rho: float, v0: float) -> tuple[float, float]:
    """Mean and variance (first two cumulants) of ``X = ln(S_T / F)`` under Heston, the forward a martingale:
    ``X = -Y/2 + M`` with ``Y = int_0^T v dt`` and ``M = int_0^T sqrt(v) dW_1``, so ``c1 = -E[Y]/2`` and
    ``c2 = E[Y] + Var(Y)/4 - Cov(Y, M)``, each in closed form from the CIR moments
    (``E v_s = theta + (v0 - theta) e^{-kappa s}``, ``Var v_s = sigma^2/kappa [theta/2 + (v0 - theta) e^{-kappa s}
    + (theta/2 - v0) e^{-2 kappa s}]``, ``Cov(v_s, v_t) = e^{-kappa (t - s)} Var v_s``, ``d E[v M] = (-kappa E[v M]
    + rho sigma E v) dt``). Derived here, not transcribed: it equals ``-d^2/du^2 ln phi(0)`` of :func:`heston_cf`
    (tests), and ``c2`` -> the integrated variance ``theta T + (v0 - theta)(1 - e^{-kappa T})/kappa`` as
    ``sigma -> 0``."""
    e1 = math.exp(-kappa * T)
    d = v0 - theta
    i1 = (1.0 - e1) / kappa
    i2 = (1.0 - math.exp(-2.0 * kappa * T)) / (2.0 * kappa)
    ey = theta * T + d * i1
    cov = rho * sigma * (theta * (T - i1) / kappa + d * (1.0 - e1 * (1.0 + kappa * T)) / (kappa * kappa))
    s2k = sigma * sigma / kappa
    int_var = s2k * (0.5 * theta * T + d * i1 + (0.5 * theta - v0) * i2)
    int_var_e = s2k * (0.5 * theta * i1 + d * T * e1 + (0.5 * theta - v0) * e1 * i1)
    var_y = 2.0 / kappa * (int_var - int_var_e)
    return -0.5 * ey, ey + 0.25 * var_y - cov


def heston_range(T: float, kappa: float, theta: float, sigma: float, rho: float, v0: float,
                 L: float = HESTON_L) -> tuple[float, float]:
    """The COS truncation range of ``ln(S_T / F)``: ``c1 -/+ L sqrt(c2)`` (Fang & Oosterlee 2008, eq. 49, the
    ``c4`` term left out; ``c2`` is a variance, positive for valid parameters, held at ``abs`` against rounding)."""
    c1, c2 = heston_cumulants(T, kappa, theta, sigma, rho, v0)
    h = L * math.sqrt(abs(c2))
    return c1 - h, c1 + h


def _clog1p(z: np.ndarray) -> np.ndarray:
    """Complex ``log(1 + z)``, accurate for small ``|z|``: ``log|1+z| = log1p(2x + x^2 + y^2) / 2``,
    ``arg = atan2(y, 1 + x)`` (heston.rs repeats it)."""
    x, y = z.real, z.imag
    return 0.5 * np.log1p(2.0 * x + x * x + y * y) + 1j * np.arctan2(y, 1.0 + x)


def heston_cf(u: np.ndarray, T: float, kappa: float, theta: float, sigma: float, rho: float,
              v0: float) -> np.ndarray:
    """``E[exp(i u ln(S_T / F))]``, Albrecher, Mayer, Schoutens & Tistaert (2007), "The little Heston trap":
    ``beta = kappa - i rho sigma u``, ``d = sqrt(beta^2 + sigma^2 (i u + u^2))`` (principal root),
    ``g = (beta - d) / (beta + d)``, ``e^{-dT}``, so the complex log stays on its principal branch::

        ln phi = kappa theta / sigma^2 [(beta - d) T - 2 ln((1 - g e^{-dT}) / (1 - g))]
                 + v0 (beta - d) / sigma^2 (1 - e^{-dT}) / (1 - g e^{-dT})

    Evaluated without the cancellation in ``beta - d`` as ``sigma -> 0``: ``(beta - d) / sigma^2 = -(i u + u^2) /
    (beta + d)``, and the log as ``log1p(g (1 - e^{-dT}) / (1 - g))`` (:func:`_clog1p`), so a vanishing vol of vol
    leaves the CF accurate (to the Black-76 limit)."""
    iu = 1j * u
    beta = kappa - rho * sigma * iu
    s2 = sigma * sigma
    d = np.sqrt(beta * beta + s2 * (iu + u * u))
    bd = beta + d
    r = -(iu + u * u) / bd                    # (beta - d) / sigma^2
    g = s2 * r / bd                           # (beta - d) / (beta + d)
    e = np.exp(-d * T)
    lg = _clog1p(g * (1.0 - e) / (1.0 - g))   # ln((1 - g e) / (1 - g))
    C = kappa * theta * (r * T - 2.0 * lg / s2)
    D = r * (1.0 - e) / (1.0 - g * e)
    return np.exp(C + D * v0)


HESTON_EXT = 0.5            # a strike gets at least HESTON_EXT * L sqrt(c2) of range beyond it (heston_price)
HESTON_MAX_TERMS = 16_384   # the cap on an extended series' length (heston.rs repeats both)


def heston_series_on(T: float, kappa: float, theta: float, sigma: float, rho: float, v0: float, a: float, b: float,
                     n_terms: int) -> tuple[np.ndarray, np.ndarray]:
    """``(u_k, A_k)`` on the range ``[a, b]``: ``u_k = k pi / (b - a)``, ``A_k = Re(phi(u_k) e^{-i u_k a})``, ``A_0``
    halved (the primed sum); the density of ``ln(S_T/F)`` is ``2/(b-a) sum_k A_k cos(u_k (x - a))``."""
    u = np.arange(n_terms) * (math.pi / (b - a))
    A = (heston_cf(u, T, kappa, theta, sigma, rho, v0) * np.exp(-1j * u * a)).real
    A[0] *= 0.5
    return u, A


def heston_series(T: float, kappa: float, theta: float, sigma: float, rho: float, v0: float, n_terms: int,
                  L: float = HESTON_L) -> tuple[float, float, np.ndarray, np.ndarray]:
    """``(a, b, u_k, A_k)`` on the cumulant range :func:`heston_range` (:func:`heston_series_on`)."""
    a, b = heston_range(T, kappa, theta, sigma, rho, v0, L)
    return (a, b, *heston_series_on(T, kappa, theta, sigma, rho, v0, a, b, n_terms))


def heston_contract_range(k: np.ndarray, a0: float, b0: float, c2: float, n_terms: int,
                          L: float = HESTON_L) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Each contract's COS range and series length, from its own log-moneyness ``k = ln(K/F)`` only (so a price
    never depends on what else is priced with it): the cumulant range ``[a0, b0]``, extended so at least
    ``HESTON_EXT L sqrt(c2)`` lies beyond the strike -- ``a = min(a0, k - h)``, ``b = max(b0, k + h)`` -- and the
    terms scaled with the range (``ceil(n (b - a) / (b0 - a0))``, at most HESTON_MAX_TERMS) so the resolution per
    unit of ``sqrt(c2)`` is kept. A strike inside ``[a0 + h, b0 - h]`` keeps the cumulant range and ``n_terms``. Without
    it a deep wing quote (k = -1.28 at 2.7 days under a sigma ~ 3 fit) sits outside the range and prices at 0."""
    h = HESTON_EXT * L * math.sqrt(abs(c2))
    a = np.minimum(a0, k - h)
    b = np.maximum(b0, k + h)
    n = np.where((a == a0) & (b == b0), float(n_terms),
                 np.minimum(np.ceil(n_terms * (b - a) / (b0 - a0)), float(HESTON_MAX_TERMS)))
    return a, b, n.astype(np.int64)


def _cos_otm(f: np.ndarray, k: np.ndarray, d: np.ndarray, a: float, b: float, u: np.ndarray,
             A: np.ndarray) -> np.ndarray:
    """The discounted OUT-of-the-money price (put at ``K <= F``, call above) of contracts on one series."""
    x = np.log(f / k)
    otm_call = k > f
    ya = x + a
    lo = np.where(otm_call, 0.0, ya)                       # the OTM option's payoff interval in y
    hi = np.where(otm_call, x + b, 0.0)
    live = hi > lo
    lo, hi = np.where(live, lo, 0.0), np.where(live, hi, 0.0)
    tc, td = np.outer(lo - ya, u), np.outer(hi - ya, u)    # u_k (c - a_y), u_k (d - a_y)
    cc, sc, cd, sd = np.cos(tc), np.sin(tc), np.cos(td), np.sin(td)
    elo, ehi = np.exp(lo)[:, None], np.exp(hi)[:, None]
    chi = (cd * ehi - cc * elo + u * (sd * ehi - sc * elo)) / (1.0 + u * u)
    psi = np.empty_like(chi)
    psi[:, 0] = hi - lo
    psi[:, 1:] = (sd[:, 1:] - sc[:, 1:]) / u[1:]
    sgn = np.where(otm_call, 1.0, -1.0)[:, None]           # call: chi - psi; put: psi - chi
    V = sgn * (chi - psi) * (2.0 / (b - a)) * k[:, None]
    otm = d * (V @ A)
    return np.where(live, np.maximum(otm, 0.0), 0.0)


def heston_price(F: np.ndarray, K: np.ndarray, T: np.ndarray, df: np.ndarray, phi: np.ndarray, kappa: float,
                 theta: float, sigma: float, rho: float, v0: float, n_terms: int, L: float = HESTON_L) -> np.ndarray:
    """European prices under Heston by COS (Fang & Oosterlee 2008). Per contract the OUT-of-the-money option is
    the cosine series (a put for ``K <= F``, a call above) and the other one is put-call parity, so parity holds to
    rounding and no wing price is a difference of two large numbers. With ``x = ln(F/K)``, ``y = x + ln(S_T/F)``
    on ``[x + a, x + b]``, the payoff coefficients are ``V_k = 2/(b-a) K (psi_k - chi_k)`` over ``[x + a, 0]``
    (put) and ``2/(b-a) K (chi_k - psi_k)`` over ``[0, x + b]`` (call); price ``= df sum' A_k V_k``. The range
    and length are each contract's (:func:`heston_contract_range`): contracts of one expiry inside the cumulant range
    share one series. NaN for an invalid contract (non-positive F, K, T or df)."""
    out = np.full(F.shape, np.nan)
    ok = np.isfinite(F) & np.isfinite(K) & np.isfinite(T) & np.isfinite(df) & (F > 0) & (K > 0) & (T > 0) & (df > 0)
    p = (kappa, theta, sigma, rho, v0)
    for t in np.unique(T[ok]):
        t = float(t)
        j = np.flatnonzero(ok & (T == t))
        c1, c2 = heston_cumulants(t, *p)
        a0, b0 = heston_range(t, *p, L)
        f, k, d, ph = F[j], K[j], df[j], phi[j]
        aj, bj, nj = heston_contract_range(np.log(k / f), a0, b0, c2, n_terms, L)
        otm = np.empty(j.size)
        base = (aj == a0) & (bj == b0)
        if base.any():
            u, A = heston_series_on(t, *p, a0, b0, n_terms)
            otm[base] = _cos_otm(f[base], k[base], d[base], a0, b0, u, A)
        for i in np.flatnonzero(~base):
            u, A = heston_series_on(t, *p, float(aj[i]), float(bj[i]), int(nj[i]))
            otm[i] = _cos_otm(f[i:i + 1], k[i:i + 1], d[i:i + 1], float(aj[i]), float(bj[i]), u, A)[0]
        otm_call = k > f
        is_call = ph > 0
        # OTM call -> put by parity, OTM put -> call by parity
        out[j] = np.where(is_call == otm_call, otm, otm + np.where(is_call, 1.0, -1.0) * d * (f - k))
    return out
