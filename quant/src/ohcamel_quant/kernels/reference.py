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
    e_last, s_last = float(r[-1] - x[0]), float(s2[-1])
    z = e_last / math.sqrt(s_last)
    nxt = math.exp(min(x[1] + x[2] * (abs(z) - SQRT2_OV_PI) + x[3] * z + x[4] * math.log(s_last), LNSIGMA_MAX))
    return GarchFitResult("egarch", x, float(garch_nll(x, r, "egarch")), converged, nit, s2, nxt,
                          (r - x[0]) / np.sqrt(s2))


# ------------------------------------------------------------------ A6: backtest
def backtest_weights(prices: np.ndarray, target_w: np.ndarray, decision_idx: np.ndarray, cost_bps: float,
                     borrow_bps: float, rf: np.ndarray):
    """backtest/engine.run_weights's accounting loop at 0b87dec, moved here unchanged."""
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
    c = cost_bps / 1e4
    b = borrow_bps / 1e4 / 252
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
            gs = float(wr.sum()) + (1.0 - wsum) * rf_l[s]
            bs = b * 0.5 * (float(np.abs(w).sum()) - wsum) if b else 0.0
            gross[s] = gs
            borrow[s] = bs
            g = gs - bs
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
            costs[s] = c * to
            net[s] = (1.0 + net[s]) * (1.0 - costs[s]) - 1.0
            w = tw.copy()
        W[s] = w
    return BacktestPath(gross, net, W, turnover, trades, costs, borrow, ruined)


# ------------------------------------------------------------------ A7: options
def svi_fit(k: np.ndarray, w: np.ndarray, weights: np.ndarray):
    """options.svi.calibrate, unchanged (the legacy SVI calibration)."""
    from ..options.svi import calibrate
    from .types import SviFitResult

    p, sse, constrained = calibrate(k, w, weights)
    return SviFitResult(float(p.a), float(p.b), float(p.rho), float(p.m), float(p.sigma), float(sse), bool(constrained))


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
