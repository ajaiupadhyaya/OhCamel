//! Student-t copula paths with empirical marginals (contract II.4 `copula_t_paths`).
//!
//! Dependence: R_ij = sin(pi tau_ij / 2) from Kendall's tau-b (Lindskog, McNeil
//! & Schmock 2003, "Kendall's tau for elliptical distributions"), which holds
//! for every elliptical copula and needs no nu. Each day draws Z ~ N(0, R)
//! (Cholesky), W ~ chi^2_nu / nu, T = Z / sqrt(W), U_i = t_nu(T_i), and asset
//! i's return is its empirical quantile at U_i: the order statistic
//! x_(ceil(U n)) of its observed returns, clamped to 1..n. The book is
//! rebalanced daily to `w`; the h-day P&L compounds as in `fhs`.
use crate::par::{self, Sim};
use crate::special::t_cdf;
use rand::Rng;
use rand_distr::{ChiSquared, Distribution, StandardNormal};
use rayon::prelude::*;

pub fn kendall_tau_b(x: &[f64], y: &[f64]) -> f64 {
    let n = x.len();
    let (mut c, mut d, mut tx, mut ty) = (0i64, 0i64, 0i64, 0i64);
    for i in 0..n {
        for j in (i + 1)..n {
            let (dx, dy) = (x[i] - x[j], y[i] - y[j]);
            if dx == 0.0 {
                tx += 1;
            }
            if dy == 0.0 {
                ty += 1;
            }
            if dx != 0.0 && dy != 0.0 {
                if (dx > 0.0) == (dy > 0.0) {
                    c += 1;
                } else {
                    d += 1;
                }
            }
        }
    }
    let n0 = (n * (n - 1) / 2) as i64;
    (c - d) as f64 / (((n0 - tx) as f64).sqrt() * ((n0 - ty) as f64).sqrt())
}

fn columns(x: &[f64], n: usize, k: usize) -> Vec<Vec<f64>> {
    (0..k)
        .map(|i| (0..n).map(|t| x[t * k + i]).collect())
        .collect()
}

pub fn kendall_corr(
    returns: &[f64],
    n: usize,
    k: usize,
    threads: usize,
) -> Result<Vec<f64>, String> {
    if n < 2 || k == 0 || returns.len() != n * k {
        return Err(format!(
            "returns must be n x k with n >= 2; got {} values for {n} x {k}",
            returns.len()
        ));
    }
    if returns.iter().any(|v| !v.is_finite()) {
        return Err("returns must be finite".into());
    }
    let cols = columns(returns, n, k);
    for (i, c) in cols.iter().enumerate() {
        if c.iter().all(|&v| v == c[0]) {
            return Err(format!(
                "column {i} is constant: Kendall's tau is undefined"
            ));
        }
    }
    let pairs: Vec<(usize, usize)> = (0..k)
        .flat_map(|i| ((i + 1)..k).map(move |j| (i, j)))
        .collect();
    let taus: Vec<f64> = par::pool(threads)?.install(|| {
        pairs
            .par_iter()
            .map(|&(i, j)| kendall_tau_b(&cols[i], &cols[j]))
            .collect()
    });
    let mut r = vec![0.0; k * k];
    for i in 0..k {
        r[i * k + i] = 1.0;
    }
    for (&(i, j), &tau) in pairs.iter().zip(&taus) {
        let v = (std::f64::consts::FRAC_PI_2 * tau).sin();
        r[i * k + j] = v;
        r[j * k + i] = v;
    }
    Ok(r)
}

pub fn cholesky(a: &[f64], k: usize) -> Result<Vec<f64>, String> {
    let mut l = vec![0.0; k * k];
    for i in 0..k {
        for j in 0..=i {
            let mut s = a[i * k + j];
            for p in 0..j {
                s -= l[i * k + p] * l[j * k + p];
            }
            if i == j {
                if s <= 0.0 {
                    return Err(
                        "the Kendall sin-transform correlation matrix is not positive definite"
                            .into(),
                    );
                }
                l[i * k + i] = s.sqrt();
            } else {
                l[i * k + j] = s / l[j * k + j];
            }
        }
    }
    Ok(l)
}

pub fn empirical_quantile(sorted: &[f64], u: f64) -> f64 {
    let n = sorted.len() as isize;
    let idx = (u * n as f64).ceil() as isize - 1;
    sorted[idx.clamp(0, n - 1) as usize]
}

pub fn copula_t_paths(
    returns: &[f64],
    n: usize,
    k: usize,
    w: &[f64],
    nu: f64,
    sim: &Sim,
) -> Result<Vec<f64>, String> {
    sim.validate()?;
    if !(nu.is_finite() && nu > 0.0) {
        return Err(format!("nu must be positive and finite; got {nu}"));
    }
    if w.len() != k || w.iter().any(|x| !x.is_finite()) {
        return Err(format!("weights must be {k} finite values"));
    }
    let r = kendall_corr(returns, n, k, sim.threads)?;
    let l = cholesky(&r, k)?;
    let mut cols = columns(returns, n, k);
    for c in cols.iter_mut() {
        c.sort_by(|a, b| a.partial_cmp(b).unwrap());
    }
    let chi = ChiSquared::<f64>::new(nu).map_err(|e| e.to_string())?;
    let mut out = vec![0.0; sim.n_paths];
    par::fill_rows(
        &mut out,
        1,
        par::CHUNK_ROWS,
        sim.seed,
        sim.threads,
        |rng, chunk| {
            let mut zi = vec![0.0; k];
            for slot in chunk.iter_mut() {
                let mut log_growth = 0.0;
                let mut wiped = false;
                for _ in 0..sim.horizon {
                    for v in zi.iter_mut() {
                        *v = rng.sample(StandardNormal);
                    }
                    let s = (chi.sample(rng) / nu).sqrt();
                    let mut rp = 0.0;
                    for i in 0..k {
                        let mut x = 0.0;
                        for j in 0..=i {
                            x += l[i * k + j] * zi[j];
                        }
                        rp += w[i] * empirical_quantile(&cols[i], t_cdf(x / s, nu));
                    }
                    if rp <= -1.0 {
                        wiped = true;
                    } else {
                        log_growth += rp.ln_1p();
                    }
                }
                *slot = if wiped { -1.0 } else { log_growth.exp_m1() };
            }
        },
    )?;
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn kendall_tau_b_hand() {
        // x = 1,2,3,4; y = 1,3,2,4: 5 concordant, 1 discordant pair of 6 -> tau = 4/6
        assert!(
            (kendall_tau_b(&[1.0, 2.0, 3.0, 4.0], &[1.0, 3.0, 2.0, 4.0]) - 2.0 / 3.0).abs() < 1e-15
        );
        // x = 1,1,2; y = 1,2,3: C = 2, D = 0, n0 = 3, one x-tie: tau_b = 2 / sqrt((3-1)(3-0)) = 2/sqrt(6)
        assert!(
            (kendall_tau_b(&[1.0, 1.0, 2.0], &[1.0, 2.0, 3.0]) - 2.0 / 6f64.sqrt()).abs() < 1e-15
        );
    }

    #[test]
    fn kendall_corr_is_sin_transform() {
        // two columns of the hand example: R_01 = sin(pi/2 x 2/3) = sin(pi/3)
        let r = kendall_corr(&[1.0, 1.0, 2.0, 3.0, 3.0, 2.0, 4.0, 4.0], 4, 2, 1).unwrap();
        assert!((r[1] - (std::f64::consts::PI / 3.0).sin()).abs() < 1e-15);
        assert_eq!((r[0], r[3]), (1.0, 1.0));
        assert!(kendall_corr(&[1.0, 5.0, 2.0, 5.0, 3.0, 5.0], 3, 2, 1).is_err());
        // column 1 constant
    }

    #[test]
    fn cholesky_hand_and_not_pd() {
        // [[1, .5], [.5, 1]] = L L' with L = [[1, 0], [.5, sqrt(.75)]]
        let l = cholesky(&[1.0, 0.5, 0.5, 1.0], 2).unwrap();
        assert_eq!(&l[..3], &[1.0, 0.0, 0.5]);
        assert!((l[3] - 0.75f64.sqrt()).abs() < 1e-15);
        assert!(cholesky(&[1.0, 2.0, 2.0, 1.0], 2).is_err());
    }

    #[test]
    fn empirical_quantile_is_the_ceiling_order_statistic() {
        let s = [1.0, 2.0, 3.0, 4.0];
        // ceil(u n) - 1, clamped: u = .25 -> 0, .26 -> 1, 1 -> 3, 0 -> 0
        assert_eq!(empirical_quantile(&s, 0.25), 1.0);
        assert_eq!(empirical_quantile(&s, 0.26), 2.0);
        assert_eq!(empirical_quantile(&s, 1.0), 4.0);
        assert_eq!(empirical_quantile(&s, 0.0), 1.0);
    }

    #[test]
    fn one_asset_one_day_draws_observed_returns() {
        let x = [-0.02, 0.01, 0.0, 0.03, -0.01, 0.015];
        let sim = Sim {
            horizon: 1,
            n_paths: 5000,
            seed: 4,
            threads: 2,
        };
        let out = copula_t_paths(&x, 6, 1, &[1.0], 5.0, &sim).unwrap();
        assert!(out.iter().all(|v| x.contains(v)));
        assert_eq!(out, copula_t_paths(&x, 6, 1, &[1.0], 5.0, &sim).unwrap());
    }
}
