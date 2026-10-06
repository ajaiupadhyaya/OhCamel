//! (GJR-/E)GARCH(1,1) with unit-variance Student-t innovations: the negative
//! log-likelihood and its fit (contract II.4 `garch_nll`, `garch_fit`).
//!
//! Parameter vectors are in the units of `r` (callers pass PERCENT returns,
//! as `arch` and risk.garch.fit_garch_fast do):
//!   garch [mu, omega, alpha, beta, nu]; gjr and egarch [mu, omega, alpha, gamma, beta, nu].
//! Initialisation is arch's: the backcast b = sum w_i e_i^2 / sum w_i over the
//! first min(75, n) residuals e = r - mean(r), w_i = 0.94^i;
//!   garch/gjr: s_0 = omega + (alpha + gamma/2 + beta) b,
//!              s_t = omega + (alpha + gamma 1[e_{t-1} < 0]) e_{t-1}^2 + beta s_{t-1};
//!   egarch:    ln s_0 = omega + beta ln b,
//!              ln s_t = omega + alpha (|z_{t-1}| - sqrt(2/pi)) + gamma z_{t-1} + beta ln s_{t-1},
//!              z = e / sqrt(s), ln s capped at ln(f64::MAX) (arch's LNSIGMA_MAX).
//! (arch also clips s_t to loose data-driven bounds; at fitted parameters they
//! do not bind, which the parity test at arch's own fit checks.)
//!   NLL = -sum_t [lnG((nu+1)/2) - lnG(nu/2) - ln(pi (nu-2))/2 - ln(s_t)/2
//!                 - (nu+1)/2 ln(1 + e_t^2 / ((nu-2) s_t))],  e_t = r_t - mu.
//! The fit minimises NLL / n by Nelder-Mead (optim.rs) inside fit_garch_fast's
//! box and stationarity constraints (an infeasible point scores 1e10), restarted
//! from the best point until a restart improves the mean NLL by <= 1e-12.
use crate::optim::{nelder_mead, NmOptions};
use crate::special::ln_gamma;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Kind {
    Garch,
    Gjr,
    Egarch,
}

impl Kind {
    pub fn parse(s: &str) -> Result<Kind, String> {
        match s {
            "garch" => Ok(Kind::Garch),
            "gjr" => Ok(Kind::Gjr),
            "egarch" => Ok(Kind::Egarch),
            _ => Err(format!(
                "unknown GARCH kind {s:?}; use 'garch', 'gjr' or 'egarch'"
            )),
        }
    }
    pub fn n_params(self) -> usize {
        if self == Kind::Garch {
            5
        } else {
            6
        }
    }
}

const SQRT2_OV_PI: f64 = 0.797_884_560_802_865_4; // sqrt(2 / pi)
pub const MIN_OBS: usize = 100;

pub fn backcast(r: &[f64]) -> f64 {
    let mean = r.iter().sum::<f64>() / r.len() as f64;
    let (mut num, mut den) = (0.0, 0.0);
    for (i, &x) in r[..r.len().min(75)].iter().enumerate() {
        let w = 0.94f64.powi(i as i32);
        let e = x - mean;
        num += e * e * w;
        den += w;
    }
    num / den
}

/// sigma^2_t for every t; false unless all are positive and finite.
pub fn variance_path(p: &[f64], r: &[f64], kind: Kind, bc: f64, s2: &mut [f64]) -> bool {
    let (mu, om) = (p[0], p[1]);
    match kind {
        Kind::Egarch => {
            let (al, ga, be) = (p[2], p[3], p[4]);
            let lmax = f64::MAX.ln();
            let mut lns = (om + be * bc.ln()).min(lmax);
            s2[0] = lns.exp();
            for t in 1..r.len() {
                let z = (r[t - 1] - mu) / s2[t - 1].sqrt();
                lns = (om + al * (z.abs() - SQRT2_OV_PI) + ga * z + be * lns).min(lmax);
                s2[t] = lns.exp();
            }
        }
        _ => {
            let al = p[2];
            let ga = if kind == Kind::Gjr { p[3] } else { 0.0 };
            let be = p[p.len() - 2];
            s2[0] = om + (al + 0.5 * ga + be) * bc;
            for t in 1..r.len() {
                let e = r[t - 1] - mu;
                let g = if e < 0.0 { ga } else { 0.0 };
                s2[t] = om + (al + g) * e * e + be * s2[t - 1];
            }
        }
    }
    s2.iter().all(|&v| v.is_finite() && v > 0.0)
}

pub fn nll_with(p: &[f64], r: &[f64], kind: Kind, bc: f64, s2: &mut [f64]) -> f64 {
    let nu = p[p.len() - 1];
    if nu.is_nan()
        || nu <= 2.0
        || p.iter().any(|v| !v.is_finite())
        || !variance_path(p, r, kind, bc, s2)
    {
        return f64::INFINITY;
    }
    let c = ln_gamma((nu + 1.0) / 2.0)
        - ln_gamma(nu / 2.0)
        - 0.5 * (std::f64::consts::PI * (nu - 2.0)).ln();
    let mut ll = 0.0;
    for t in 0..r.len() {
        let e = r[t] - p[0];
        ll += c - 0.5 * s2[t].ln() - 0.5 * (nu + 1.0) * (e * e / ((nu - 2.0) * s2[t])).ln_1p();
    }
    if ll.is_finite() {
        -ll
    } else {
        f64::INFINITY
    }
}

fn check(r: &[f64], min: usize) -> Result<(), String> {
    if r.len() < min {
        return Err(format!(
            "GARCH needs at least {min} observations; got {}",
            r.len()
        ));
    }
    if r.iter().any(|v| !v.is_finite()) {
        return Err("returns must be finite".into());
    }
    Ok(())
}

pub fn garch_nll(p: &[f64], r: &[f64], kind: Kind) -> Result<f64, String> {
    if p.len() != kind.n_params() {
        return Err(format!(
            "{kind:?} takes {} parameters; got {}",
            kind.n_params(),
            p.len()
        ));
    }
    check(r, 2)?;
    let mut s2 = vec![0.0; r.len()];
    Ok(nll_with(p, r, kind, backcast(r), &mut s2))
}

fn moments(r: &[f64]) -> (f64, f64, f64) {
    let n = r.len() as f64;
    let mean = r.iter().sum::<f64>() / n;
    let var = r.iter().map(|x| (x - mean) * (x - mean)).sum::<f64>() / n;
    (mean, var, r.iter().fold(0.0f64, |a, x| a.max(x.abs())))
}

/// fit_garch_fast's starting values (garch, gjr); EGARCH's analogue in log-variance.
pub fn default_x0(r: &[f64], kind: Kind) -> Vec<f64> {
    let (mean, var, _) = moments(r);
    match kind {
        Kind::Garch => vec![mean, var * 0.05, 0.08, 0.87, 8.0],
        Kind::Gjr => vec![mean, var * 0.05, 0.03, 0.09, 0.88, 8.0],
        Kind::Egarch => vec![mean, (1.0 - 0.95) * var.ln(), 0.1, -0.05, 0.95, 8.0],
    }
}

/// fit_garch_fast's box (garch, gjr); for EGARCH, arch's omega bounds around ln var.
pub fn bounds(r: &[f64], kind: Kind) -> Vec<(f64, f64)> {
    let (_, var, amax) = moments(r);
    let m = (-10.0 * amax, 10.0 * amax);
    match kind {
        Kind::Garch => vec![m, (1e-8, 10.0 * var), (0.0, 1.0), (0.0, 1.0), (2.05, 500.0)],
        Kind::Gjr => vec![
            m,
            (1e-8, 10.0 * var),
            (0.0, 1.0),
            (-1.0, 2.0),
            (0.0, 1.0),
            (2.05, 500.0),
        ],
        Kind::Egarch => {
            let lnv = var.ln();
            let lc = 10_000f64.ln();
            vec![
                m,
                (lnv - lc, lnv + lc),
                (-5.0, 5.0),
                (-5.0, 5.0),
                (0.0, 0.9999),
                (2.05, 500.0),
            ]
        }
    }
}

fn feasible(p: &[f64], kind: Kind) -> bool {
    match kind {
        Kind::Garch => p[2] + p[3] <= 0.9999,
        Kind::Gjr => p[2] + 0.5 * p[3] + p[4] <= 0.9999 && p[2] + p[3] >= 0.0,
        Kind::Egarch => true,
    }
}

pub struct Fit {
    pub params: Vec<f64>,
    pub nll: f64,
    pub converged: bool,
    pub iterations: usize,
    pub sigma2: Vec<f64>,
    pub next_variance: f64,
    pub std_resid: Vec<f64>,
}

pub fn garch_fit(r: &[f64], kind: Kind, x0: Option<&[f64]>) -> Result<Fit, String> {
    check(r, MIN_OBS)?;
    let b = bounds(r, kind);
    let mut x = match x0 {
        Some(v) if v.len() != kind.n_params() => {
            return Err(format!(
                "x0 needs {} parameters; got {}",
                kind.n_params(),
                v.len()
            ))
        }
        Some(v) => v.to_vec(),
        None => default_x0(r, kind),
    };
    for (xi, (lo, hi)) in x.iter_mut().zip(&b) {
        *xi = xi.clamp(*lo, *hi);
    }
    let bc = backcast(r);
    let n = r.len() as f64;
    let mut work = vec![0.0; r.len()];
    let mut obj = |p: &[f64]| -> f64 {
        if !feasible(p, kind) {
            return 1e10;
        }
        let v = nll_with(p, r, kind, bc, &mut work) / n;
        if v.is_finite() {
            v
        } else {
            1e10
        }
    };
    let opts = NmOptions {
        xatol: 1e-10,
        fatol: 1e-14,
        maxiter: 5_000,
        maxfev: 10_000,
        bounds: Some(&b),
    };
    let mut best = obj(&x);
    let (mut iterations, mut converged) = (0usize, false);
    for _ in 0..8 {
        let res = nelder_mead(&mut obj, &x, &opts);
        iterations += res.nit;
        let gain = best - res.fun;
        if res.fun <= best {
            x = res.x;
            best = res.fun;
        }
        if gain <= 1e-12 * best.abs().max(1.0) {
            converged = res.converged;
            break;
        }
    }
    let mut s2 = vec![0.0; r.len()];
    let nll = nll_with(&x, r, kind, bc, &mut s2);
    if !nll.is_finite() {
        return Err("the GARCH fit ended at a non-finite likelihood".into());
    }
    let (mu, last) = (x[0], r.len() - 1);
    let (e, s) = (r[last] - mu, s2[last]);
    let next_variance = match kind {
        Kind::Egarch => {
            let z = e / s.sqrt();
            (x[1] + x[2] * (z.abs() - SQRT2_OV_PI) + x[3] * z + x[4] * s.ln())
                .min(f64::MAX.ln())
                .exp()
        }
        _ => {
            let ga = if kind == Kind::Gjr && e < 0.0 {
                x[3]
            } else {
                0.0
            };
            x[1] + (x[2] + ga) * e * e + x[x.len() - 2] * s
        }
    };
    let std_resid = r
        .iter()
        .zip(&s2)
        .map(|(v, s)| (v - mu) / s.sqrt())
        .collect();
    Ok(Fit {
        params: x,
        nll,
        converged,
        iterations,
        sigma2: s2,
        next_variance,
        std_resid,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use rand::SeedableRng;
    use rand_chacha::ChaCha20Rng;
    use rand_distr::{Distribution, StudentT};

    fn series(n: usize) -> Vec<f64> {
        // a GJR-t(6) path in percent units: mu .05, omega .02, alpha .04, gamma .12, beta .85
        let mut rng = ChaCha20Rng::seed_from_u64(42);
        let t6 = StudentT::new(6.0).unwrap();
        let scale = (4.0f64 / 6.0).sqrt(); // unit variance: sd of t6 is sqrt(6/4)
        let (mut s2, mut out): (f64, Vec<f64>) =
            (0.02 / (1.0 - 0.04 - 0.06 - 0.85), Vec::with_capacity(n));
        for _ in 0..n {
            let e = s2.sqrt() * t6.sample(&mut rng) * scale;
            out.push(0.05 + e);
            s2 = 0.02 + (0.04 + if e < 0.0 { 0.12 } else { 0.0 }) * e * e + 0.85 * s2;
        }
        out
    }

    #[test]
    fn gjr_with_zero_gamma_is_garch() {
        let r = series(500);
        let g = garch_nll(&[0.05, 0.02, 0.08, 0.88, 7.0], &r, Kind::Garch).unwrap();
        let j = garch_nll(&[0.05, 0.02, 0.08, 0.0, 0.88, 7.0], &r, Kind::Gjr).unwrap();
        assert_eq!(g, j); // identical recursions and terms
    }

    #[test]
    fn egarch_with_constant_variance_is_closed_form() {
        // alpha = gamma = beta = 0: ln s_t = omega, s_t = e^omega for every t, so
        // NLL = -sum[c - omega/2 - (nu+1)/2 ln(1 + e_t^2 / ((nu-2) e^omega))]
        let r = series(300);
        let (mu, om, nu) = (0.05, 0.3, 8.0);
        let c = ln_gamma((nu + 1.0) / 2.0)
            - ln_gamma(nu / 2.0)
            - 0.5 * (std::f64::consts::PI * (nu - 2.0)).ln();
        let want: f64 = -r
            .iter()
            .map(|x| {
                let e = x - mu;
                c - 0.5 * om - 0.5 * (nu + 1.0) * (e * e / ((nu - 2.0) * om.exp())).ln_1p()
            })
            .sum::<f64>();
        let got = garch_nll(&[mu, om, 0.0, 0.0, 0.0, nu], &r, Kind::Egarch).unwrap();
        assert!((got - want).abs() < 1e-9 * want.abs(), "{got} vs {want}");
    }

    #[test]
    fn infeasible_is_infinite() {
        let r = series(200);
        assert_eq!(
            garch_nll(&[0.0, 0.02, 0.05, 0.9, 2.0], &r, Kind::Garch).unwrap(),
            f64::INFINITY
        );
        assert_eq!(
            garch_nll(&[0.0, -50.0, 0.05, 0.9, 6.0], &r, Kind::Garch).unwrap(),
            f64::INFINITY
        );
        assert!(garch_nll(&[0.0, 0.02, 0.05, 0.9], &r, Kind::Garch).is_err()); // wrong length
    }

    #[test]
    fn fit_recovers_a_gjr_path_and_warm_starts_faster() {
        let r = series(3000);
        let f = garch_fit(&r, Kind::Gjr, None).unwrap();
        // the generating parameters, within sampling error of a 3000-day path
        assert!(
            (f.params[2] - 0.04).abs() < 0.04
                && (f.params[3] - 0.12).abs() < 0.08
                && (f.params[4] - 0.85).abs() < 0.06,
            "{:?}",
            f.params
        );
        let again = garch_fit(&r, Kind::Gjr, Some(&f.params)).unwrap();
        assert!(again.iterations < f.iterations);
        assert!(again.nll <= f.nll + 1e-9 * f.nll.abs());
        assert_eq!(f.sigma2.len(), 3000);
        assert!(garch_fit(&r[..99], Kind::Gjr, None).is_err());
    }
}
