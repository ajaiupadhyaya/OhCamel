//! American option prices and implied volatility (contract II.4 `american_price`,
//! `american_iv`) -- options/american.py's model, step for step as
//! kernels/reference.py computes it:
//!
//! * escrowed spot S* = S - sum_{0 < t_d < T} D_d e^{-r t_d} (schedule order);
//! * an n-step CRR tree on S* (u = e^{sigma sqrt dt}, d = 1/u, p = (e^{(r-q)dt} - d)/(u - d)),
//!   whose last step is the Black-Scholes value over dt (BBS), every node floored at
//!   exercise against S*_node + sum_{t_d < T, t_d/dt >= i - 1e-9} D_d e^{-r (t_d - t_i)};
//! * price = max(2 V_n - V_{n/2}, max(phi (S - K), 0)) (BBSR, Broadie & Detemple 1996);
//! * implied vol: from the guess (clipped to [floor, 5]; 0.25 if not finite), steps of 1.25 x the
//!   Black-Scholes Newton step, doubled while the error keeps its sign, until a bracket; then
//!   scipy's brentq (xtol 1e-10, rtol 4 eps, 100 iterations), NaN where no root.
//!
//! Every contract is independent: the rayon pool only splits the list.
use crate::par;
use rayon::prelude::*;

pub const SIG_FLOOR: f64 = 1e-3;
pub const SIG_HI: f64 = 5.0;
pub const SIG_START: f64 = 0.25;
pub const XTOL: f64 = 1e-10;
pub const RTOL: f64 = 4.0 * f64::EPSILON;
pub const MAXITER: usize = 100;
pub const DIV_EPS: f64 = 1e-9;
pub const MIN_STEPS: usize = 10;
pub const MAX_STEPS: usize = 10_000;

/// One contract's inputs (phi = +1 call, -1 put).
#[derive(Clone, Copy, Debug)]
pub struct Contract {
    pub s: f64,
    pub k: f64,
    pub t: f64,
    pub r: f64,
    pub q: f64,
    pub phi: f64,
}

/// The standard normal CDF as cephes' ndtr (scipy.special.ndtr): erf near 0, erfc elsewhere.
pub fn ndtr(a: f64) -> f64 {
    if a.is_nan() {
        return f64::NAN;
    }
    let x = a * std::f64::consts::FRAC_1_SQRT_2;
    let z = x.abs();
    if z < std::f64::consts::FRAC_1_SQRT_2 {
        0.5 + 0.5 * libm::erf(x)
    } else {
        let y = 0.5 * libm::erfc(z);
        if x > 0.0 {
            1.0 - y
        } else {
            y
        }
    }
}

fn escrowed(c: &Contract, div_t: &[f64], div_amt: &[f64]) -> f64 {
    let mut pv = 0.0;
    for (&td, &a) in div_t.iter().zip(div_amt) {
        if td > 0.0 && td < c.t {
            pv += a * (-c.r * td).exp();
        }
    }
    c.s - pv
}

fn valid(c: &Contract, s0: f64) -> bool {
    c.s.is_finite()
        && c.k.is_finite()
        && c.t.is_finite()
        && c.r.is_finite()
        && c.q.is_finite()
        && c.s > 0.0
        && c.k > 0.0
        && c.t > 0.0
        && s0 > 0.0
        && c.phi.abs() == 1.0
}

fn sig_floor(c: &Contract, n: usize) -> f64 {
    SIG_FLOOR.max(1.5 * (c.r - c.q).abs() * (2.0 * c.t / n as f64).sqrt())
}

/// Scratch space for one tree (reused across a worker's contracts).
#[derive(Default)]
pub struct Buf {
    s: Vec<f64>,
    v: Vec<f64>,
    w: Vec<f64>,
}

fn pv_at(i: usize, dt: f64, c: &Contract, div_t: &[f64], div_amt: &[f64]) -> f64 {
    let ti = i as f64 * dt;
    let mut out = 0.0;
    for (&td, &a) in div_t.iter().zip(div_amt) {
        if td < c.t && td / dt >= i as f64 - DIV_EPS {
            out += a * (-c.r * (td - ti)).exp();
        }
    }
    out
}

/// Root of the n-step BBS tree on the escrowed spot s0.
fn bbs_tree(
    c: &Contract,
    s0: f64,
    sig: f64,
    div_t: &[f64],
    div_amt: &[f64],
    n: usize,
    b: &mut Buf,
) -> f64 {
    let (k, r, q, phi) = (c.k, c.r, c.q, c.phi);
    let dt = c.t / n as f64;
    let sq = sig * dt.sqrt();
    let u = sq.exp();
    let d = 1.0 / u;
    let p = (((r - q) * dt).exp() - d) / (u - d);
    let disc = (-r * dt).exp();
    let (pu, pd) = (disc * p, disc * (1.0 - p));
    let divs = !div_t.is_empty();
    let m = n - 1;
    b.s.clear();
    b.v.clear();
    let (eq, er) = ((-q * dt).exp(), (-r * dt).exp());
    let drift = (r - q + 0.5 * sig * sig) * dt;
    let pv_m = if divs {
        pv_at(m, dt, c, div_t, div_amt)
    } else {
        0.0
    };
    for j in 0..n {
        let s = s0 * ((2.0 * j as f64 - m as f64) * sq).exp();
        let d1 = ((s / k).ln() + drift) / sq;
        let d2 = d1 - sq;
        let bs = phi * (s * eq * ndtr(phi * d1) - k * er * ndtr(phi * d2));
        let ex = if divs {
            phi * (s + pv_m - k)
        } else {
            phi * (s - k)
        };
        b.s.push(s);
        b.v.push(bs.max(ex));
    }
    b.w.clear();
    b.w.resize(n, 0.0);
    // Every value is finite for a valid contract (S* > 0, sigma > 0, p in (0, 1)), so f64::max
    // needs no NaN rule; the loops are written over slices so they vectorize.
    for i in (0..m).rev() {
        let (src, dst) = (&b.v[..=i + 1], &mut b.w[..=i]);
        let s = &mut b.s[..=i];
        let pairs = src.iter().zip(&src[1..]);
        if divs {
            let pv = pv_at(i, dt, c, div_t, div_amt);
            for ((o, sj), (lo, hi)) in dst.iter_mut().zip(s.iter_mut()).zip(pairs) {
                *sj *= u;
                *o = (pu * hi + pd * lo).max(phi * (*sj + pv - k));
            }
        } else {
            for ((o, sj), (lo, hi)) in dst.iter_mut().zip(s.iter_mut()).zip(pairs) {
                *sj *= u;
                *o = (pu * hi + pd * lo).max(phi * (*sj - k));
            }
        }
        std::mem::swap(&mut b.v, &mut b.w);
    }
    b.v[0]
}

fn price_valid(
    c: &Contract,
    s0: f64,
    sig: f64,
    div_t: &[f64],
    div_amt: &[f64],
    n: usize,
    b: &mut Buf,
) -> f64 {
    let v = 2.0 * bbs_tree(c, s0, sig, div_t, div_amt, n, b)
        - bbs_tree(c, s0, sig, div_t, div_amt, n / 2, b);
    let floor = (c.phi * (c.s - c.k)).max(0.0);
    if v >= floor || v.is_nan() {
        v
    } else {
        floor
    }
}

/// One contract's American value (NaN if invalid).
pub fn american_price_one(
    c: &Contract,
    sig: f64,
    div_t: &[f64],
    div_amt: &[f64],
    n: usize,
    b: &mut Buf,
) -> f64 {
    let s0 = escrowed(c, div_t, div_amt);
    let h = 2.0 * c.t / n as f64;
    if !(valid(c, s0) && sig.is_finite() && sig > 0.0 && sig * h.sqrt() > (c.r - c.q).abs() * h) {
        return f64::NAN;
    }
    price_valid(c, s0, sig, div_t, div_amt, n, b)
}

/// scipy's brentq from a bracket with known values; NaN if not converged.
fn brent<F: FnMut(f64) -> f64>(
    mut f: F,
    mut xpre: f64,
    mut xcur: f64,
    mut fpre: f64,
    mut fcur: f64,
) -> f64 {
    let (mut xblk, mut fblk, mut spre, mut scur) = (0.0, 0.0, 0.0, 0.0);
    for _ in 0..MAXITER {
        if fpre != 0.0 && fcur != 0.0 && (fpre.is_sign_negative() != fcur.is_sign_negative()) {
            xblk = xpre;
            fblk = fpre;
            spre = xcur - xpre;
            scur = xcur - xpre;
        }
        if fblk.abs() < fcur.abs() {
            xpre = xcur;
            xcur = xblk;
            xblk = xpre;
            fpre = fcur;
            fcur = fblk;
            fblk = fpre;
        }
        let delta = (XTOL + RTOL * xcur.abs()) / 2.0;
        let sbis = (xblk - xcur) / 2.0;
        if fcur == 0.0 || sbis.abs() < delta {
            return xcur;
        }
        if spre.abs() > delta && fcur.abs() < fpre.abs() {
            let stry = if xpre == xblk {
                -fcur * (xcur - xpre) / (fcur - fpre)
            } else {
                let dpre = (fpre - fcur) / (xpre - xcur);
                let dblk = (fblk - fcur) / (xblk - xcur);
                -fcur * (fblk * dblk - fpre * dpre) / (dblk * dpre * (fblk - fpre))
            };
            if 2.0 * stry.abs() < spre.abs().min(3.0 * sbis.abs() - delta) {
                spre = scur;
                scur = stry;
            } else {
                spre = sbis;
                scur = sbis;
            }
        } else {
            spre = sbis;
            scur = sbis;
        }
        xpre = xcur;
        fpre = fcur;
        xcur += if scur.abs() > delta {
            scur
        } else if sbis > 0.0 {
            delta
        } else {
            -delta
        };
        fcur = f(xcur);
    }
    f64::NAN
}

/// One contract's implied volatility (NaN where none).
pub fn american_iv_one(
    c: &Contract,
    price: f64,
    guess: f64,
    div_t: &[f64],
    div_amt: &[f64],
    n: usize,
    b: &mut Buf,
) -> f64 {
    let s0 = escrowed(c, div_t, div_amt);
    let upper = if c.phi > 0.0 { c.s } else { c.k };
    if !(valid(c, s0)
        && price.is_finite()
        && price > (c.phi * (c.s - c.k)).max(0.0)
        && price < upper)
    {
        return f64::NAN;
    }
    let lo = sig_floor(c, n);
    let hi = SIG_HI;
    if lo >= hi {
        return f64::NAN;
    }
    let mut f = |x: f64| price_valid(c, s0, x, div_t, div_amt, n, b) - price;
    let g = if guess.is_finite() { guess } else { SIG_START };
    let mut x0 = g.max(lo).min(hi);
    let mut f0 = f(x0);
    if f0 == 0.0 {
        return x0;
    }
    let sd = x0 * c.t.sqrt();
    let d1 = ((s0 / c.k).ln() + (c.r - c.q + 0.5 * x0 * x0) * c.t) / sd;
    let vega = s0 * (-c.q * c.t).exp() * (-0.5 * d1 * d1).exp()
        / (2.0 * std::f64::consts::PI).sqrt()
        * c.t.sqrt();
    let mut step = -1.25 * f0 / vega;
    if !(step.is_finite() && step != 0.0) {
        step = if f0 > 0.0 { lo - x0 } else { hi - x0 };
    }
    for _ in 0..64 {
        let xn = (x0 + step).max(lo).min(hi);
        let fnew = f(xn);
        if fnew == 0.0 {
            return xn;
        }
        if fnew.is_sign_negative() != f0.is_sign_negative() {
            return brent(&mut f, x0, xn, f0, fnew);
        }
        if xn <= lo || xn >= hi {
            return f64::NAN;
        }
        x0 = xn;
        f0 = fnew;
        step *= 2.0;
    }
    f64::NAN
}

fn check(n: usize, div_t: &[f64], div_amt: &[f64], lens: &[usize]) -> Result<(), String> {
    if !(MIN_STEPS..=MAX_STEPS).contains(&n) || n % 2 != 0 {
        return Err(format!(
            "steps must be an even number in {MIN_STEPS}..{MAX_STEPS}; got {n}"
        ));
    }
    if div_t.len() != div_amt.len() {
        return Err("div_t and div_amt must have one length".into());
    }
    if !div_t.iter().chain(div_amt).all(|x| x.is_finite()) || div_amt.iter().any(|&a| a < 0.0) {
        return Err(
            "dividend times must be finite and dividend amounts finite and non-negative".into(),
        );
    }
    if lens.windows(2).any(|w| w[0] != w[1]) {
        return Err("every per-contract input must have one length".into());
    }
    Ok(())
}

fn contracts(s: &[f64], k: &[f64], t: &[f64], r: &[f64], q: &[f64], phi: &[f64]) -> Vec<Contract> {
    (0..s.len())
        .map(|i| Contract {
            s: s[i],
            k: k[i],
            t: t[i],
            r: r[i],
            q: q[i],
            phi: phi[i],
        })
        .collect()
}

pub fn american_price(
    s: &[f64],
    k: &[f64],
    t: &[f64],
    r: &[f64],
    q: &[f64],
    sigma: &[f64],
    phi: &[f64],
    div_t: &[f64],
    div_amt: &[f64],
    steps: usize,
    threads: usize,
) -> Result<Vec<f64>, String> {
    let lens = [
        s.len(),
        k.len(),
        t.len(),
        r.len(),
        q.len(),
        sigma.len(),
        phi.len(),
    ];
    check(steps, div_t, div_amt, &lens)?;
    let cs = contracts(s, k, t, r, q, phi);
    let out = par::pool(threads)?.install(|| {
        cs.par_iter()
            .zip(sigma.par_iter())
            .map_init(Buf::default, |b, (c, &sig)| {
                american_price_one(c, sig, div_t, div_amt, steps, b)
            })
            .collect()
    });
    Ok(out)
}

pub fn american_iv(
    price: &[f64],
    s: &[f64],
    k: &[f64],
    t: &[f64],
    r: &[f64],
    q: &[f64],
    phi: &[f64],
    guess: &[f64],
    div_t: &[f64],
    div_amt: &[f64],
    steps: usize,
    threads: usize,
) -> Result<Vec<f64>, String> {
    let lens = [
        price.len(),
        s.len(),
        k.len(),
        t.len(),
        r.len(),
        q.len(),
        phi.len(),
        guess.len(),
    ];
    check(steps, div_t, div_amt, &lens)?;
    let cs = contracts(s, k, t, r, q, phi);
    let out = par::pool(threads)?.install(|| {
        (0..cs.len())
            .into_par_iter()
            .map_init(Buf::default, |b, i| {
                american_iv_one(&cs[i], price[i], guess[i], div_t, div_amt, steps, b)
            })
            .collect()
    });
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn bs(s: f64, k: f64, t: f64, r: f64, q: f64, sig: f64, phi: f64) -> f64 {
        let sd = sig * t.sqrt();
        let d1 = ((s / k).ln() + (r - q + 0.5 * sig * sig) * t) / sd;
        let d2 = d1 - sd;
        phi * (s * (-q * t).exp() * ndtr(phi * d1) - k * (-r * t).exp() * ndtr(phi * d2))
    }

    fn one(
        s: f64,
        k: f64,
        t: f64,
        r: f64,
        q: f64,
        sig: f64,
        phi: f64,
        dt: &[f64],
        da: &[f64],
        n: usize,
    ) -> f64 {
        let c = Contract { s, k, t, r, q, phi };
        american_price_one(&c, sig, dt, da, n, &mut Buf::default())
    }

    #[test]
    fn ndtr_hand_values() {
        assert_eq!(ndtr(0.0), 0.5);
        assert!((ndtr(1.959963984540054) - 0.975).abs() < 1e-15);
        assert!((ndtr(-1.0) - 0.15865525393145707).abs() < 1e-16);
        assert!((ndtr(-8.0) - 6.22096057427178e-16).abs() < 1e-28);
    }

    #[test]
    fn call_without_dividends_is_black_scholes() {
        // Merton (1973): never exercised early; BBSR at 400 steps is within 1e-3 of the formula
        for &(k, t, sig) in &[(80.0, 0.25, 0.2), (100.0, 1.0, 0.2), (120.0, 1.0, 0.4)] {
            let a = one(100.0, k, t, 0.05, 0.0, sig, 1.0, &[], &[], 400);
            let e = bs(100.0, k, t, 0.05, 0.0, sig, 1.0);
            assert!((a - e).abs() < 1e-3, "{k} {t} {sig}: {a} vs {e}");
        }
    }

    #[test]
    fn put_is_worth_intrinsic_deep_in_the_money_and_more_than_european() {
        let a = one(100.0, 130.0, 1.0, 0.05, 0.0, 0.2, -1.0, &[], &[], 400);
        assert!(a >= 30.0 && a > bs(100.0, 130.0, 1.0, 0.05, 0.0, 0.2, -1.0) + 0.5);
    }

    #[test]
    fn yield_above_rate_makes_the_call_worth_more_than_european() {
        let a = one(100.0, 100.0, 0.5, 0.08, 0.12, 0.2, 1.0, &[], &[], 400);
        assert!(a - bs(100.0, 100.0, 0.5, 0.08, 0.12, 0.2, 1.0) > 0.05);
    }

    #[test]
    fn dividend_call_is_worth_holding_or_exercising_before_the_dividend() {
        let (s, k, t, r, sig, div): (f64, f64, f64, f64, f64, f64) =
            (100.0, 80.0, 0.5, 0.05, 0.2, 2.0);
        let sstar = s - div * (-r * t / 2.0).exp();
        let a = one(s, k, t, r, 0.0, sig, 1.0, &[t / 2.0], &[div], 400);
        let hold = bs(sstar, k, t, r, 0.0, sig, 1.0);
        let early = bs(sstar, k - div, t / 2.0, r, 0.0, sig, 1.0);
        assert!(early > hold + 0.5 && a >= early, "{a} {hold} {early}");
    }

    #[test]
    fn iv_round_trips_and_is_nan_outside_the_bounds() {
        let mut b = Buf::default();
        for &(k, phi) in &[(90.0, -1.0), (115.0, -1.0), (100.0, 1.0), (80.0, 1.0)] {
            let c = Contract {
                s: 100.0,
                k,
                t: 0.5,
                r: 0.05,
                q: 0.02,
                phi,
            };
            let p = american_price_one(&c, 0.3, &[], &[], 400, &mut b);
            let iv = american_iv_one(&c, p, f64::NAN, &[], &[], 400, &mut b);
            assert!((iv - 0.3).abs() < 1e-8, "{k} {phi}: {iv}");
        }
        let c = Contract {
            s: 100.0,
            k: 120.0,
            t: 1.0,
            r: 0.05,
            q: 0.0,
            phi: -1.0,
        };
        assert!(american_iv_one(&c, 20.0, 0.2, &[], &[], 400, &mut b).is_nan());
        assert!(american_iv_one(&c, 120.0, 0.2, &[], &[], 400, &mut b).is_nan());
    }

    #[test]
    fn bad_steps_and_dividends_are_refused() {
        assert!(american_price(
            &[100.0],
            &[100.0],
            &[1.0],
            &[0.05],
            &[0.0],
            &[0.2],
            &[1.0],
            &[],
            &[],
            401,
            1
        )
        .is_err());
        assert!(american_price(
            &[100.0],
            &[100.0],
            &[1.0],
            &[0.05],
            &[0.0],
            &[0.2],
            &[1.0],
            &[0.5],
            &[-1.0],
            400,
            1
        )
        .is_err());
        let v = american_price(
            &[100.0],
            &[100.0],
            &[0.0],
            &[0.05],
            &[0.0],
            &[0.2],
            &[1.0],
            &[],
            &[],
            400,
            1,
        )
        .unwrap();
        assert!(v[0].is_nan());
    }

    #[test]
    fn threads_do_not_change_the_result() {
        let k: Vec<f64> = (0..40).map(|i| 70.0 + 1.5 * i as f64).collect();
        let n = k.len();
        let ph: Vec<f64> = (0..n)
            .map(|i| if i % 2 == 0 { 1.0 } else { -1.0 })
            .collect();
        let args = |th| {
            american_price(
                &vec![100.0; n],
                &k,
                &vec![0.7; n],
                &vec![0.04; n],
                &vec![0.01; n],
                &vec![0.25; n],
                &ph,
                &[0.3],
                &[1.0],
                400,
                th,
            )
            .unwrap()
        };
        assert_eq!(args(1), args(3));
    }
}
