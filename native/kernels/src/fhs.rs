//! Filtered historical simulation paths (contract II.4 `fhs_paths`).
//!
//! Barone-Adesi, Giannopoulos & Vosper (1999): a path draws whole rows j of the
//! standardized residuals (so the cross-section keeps its dependence), scales
//! asset i's draw by its GJR-GARCH(1,1) sigma, and updates sigma^2 with the
//! drawn shock -- parameters in PERCENT units, as fitted on 100 r:
//!   e = sigma_i z_{j,i};  r_i = (mu_i + e) / 100;
//!   sigma^2_i <- omega_i + (alpha_i + gamma_i 1[e < 0]) e^2 + beta_i sigma^2_i.
//! The book is rebalanced daily to `w` (risk.core's convention), so the h-day
//! P&L is prod_k (1 + sum_i w_i r_{k,i}) - 1; a day at or below -100 % is a
//! total loss and the path's P&L is -1 (risk.garch._fhs_paths's rule).
use crate::par::{self, Sim};
use rand::Rng;
use rand_chacha::ChaCha20Rng;

pub struct GarchPath<'a> {
    pub mu: &'a [f64],
    pub omega: &'a [f64],
    pub alpha: &'a [f64],
    pub gamma: &'a [f64],
    pub beta: &'a [f64],
    pub sigma2_next: &'a [f64],
}

impl GarchPath<'_> {
    fn validate(&self, k: usize) -> Result<(), String> {
        let fields = [
            ("mu", self.mu),
            ("omega", self.omega),
            ("alpha", self.alpha),
            ("gamma", self.gamma),
            ("beta", self.beta),
            ("sigma2_next", self.sigma2_next),
        ];
        for (name, v) in fields {
            if v.len() != k {
                return Err(format!("{name} has {} entries for {k} assets", v.len()));
            }
            if v.iter().any(|x| !x.is_finite()) {
                return Err(format!("{name} must be finite"));
            }
        }
        if self.sigma2_next.iter().any(|&s| s <= 0.0) {
            return Err("sigma2_next must be positive".into());
        }
        Ok(())
    }
}

pub fn fhs_paths(
    z: &[f64],
    n: usize,
    k: usize,
    g: &GarchPath,
    w: &[f64],
    sim: &Sim,
) -> Result<Vec<f64>, String> {
    sim.validate()?;
    if n == 0 || k == 0 || z.len() != n * k {
        return Err(format!(
            "std_resid must be n x k with n, k >= 1; got {} values for {n} x {k}",
            z.len()
        ));
    }
    if z.iter().any(|x| !x.is_finite()) {
        return Err("std_resid must be finite (drop the NaN rows first)".into());
    }
    if w.len() != k || w.iter().any(|x| !x.is_finite()) {
        return Err(format!("weights must be {k} finite values"));
    }
    g.validate(k)?;
    let mut out = vec![0.0; sim.n_paths];
    par::fill_rows(
        &mut out,
        1,
        par::CHUNK_ROWS,
        sim.seed,
        sim.threads,
        |rng, chunk| {
            let mut s2 = vec![0.0; k];
            for slot in chunk.iter_mut() {
                s2.copy_from_slice(g.sigma2_next);
                *slot = one_path(rng, z, n, k, g, w, sim.horizon, &mut s2);
            }
        },
    )?;
    Ok(out)
}

fn one_path(
    rng: &mut ChaCha20Rng,
    z: &[f64],
    n: usize,
    k: usize,
    g: &GarchPath,
    w: &[f64],
    horizon: usize,
    s2: &mut [f64],
) -> f64 {
    let mut log_growth = 0.0;
    let mut wiped = false;
    for _ in 0..horizon {
        let j = rng.gen_range(0..n);
        let row = &z[j * k..(j + 1) * k];
        let mut rp = 0.0;
        for i in 0..k {
            let e = s2[i].sqrt() * row[i];
            rp += w[i] * ((g.mu[i] + e) / 100.0);
            let neg = if e < 0.0 { 1.0 } else { 0.0 };
            s2[i] = g.omega[i] + (g.alpha[i] + g.gamma[i] * neg) * e * e + g.beta[i] * s2[i];
        }
        if rp <= -1.0 {
            wiped = true;
        } else {
            log_growth += rp.ln_1p();
        }
    }
    if wiped {
        -1.0
    } else {
        log_growth.exp_m1()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn one_asset<'a>(
        mu: &'a [f64],
        s2: &'a [f64],
        zero: &'a [f64],
        g: &'a [f64],
        b: &'a [f64],
    ) -> GarchPath<'a> {
        GarchPath {
            mu,
            omega: zero,
            alpha: zero,
            gamma: g,
            beta: b,
            sigma2_next: s2,
        }
    }

    #[test]
    fn horizon_one_draws_scaled_residuals() {
        // one day: r = (mu + sqrt(s2) z_j) / 100 for a drawn row j
        let z = [-2.0, -0.5, 0.25, 1.5];
        let (mu, s2, zero, b) = ([0.05], [4.0], [0.0], [0.9]);
        let g = one_asset(&mu, &s2, &zero, &zero, &b);
        let sim = Sim {
            horizon: 1,
            n_paths: 2000,
            seed: 3,
            threads: 2,
        };
        let out = fhs_paths(&z, 4, 1, &g, &[1.0], &sim).unwrap();
        let support: Vec<f64> = z.iter().map(|zj| (0.05 + 2.0 * zj) / 100.0).collect();
        assert!(out.iter().all(|x| support.iter().any(|s| s == x)));
    }

    #[test]
    fn same_seed_threads_identical_and_streams_differ() {
        let z: Vec<f64> = (0..50).map(|i| (i as f64 - 25.0) / 10.0).collect();
        let (mu, s2, om, a, gm, b) = ([0.04], [1.2], [0.02], [0.05], [0.1], [0.88]);
        let g = GarchPath {
            mu: &mu,
            omega: &om,
            alpha: &a,
            gamma: &gm,
            beta: &b,
            sigma2_next: &s2,
        };
        let sim = Sim {
            horizon: 10,
            n_paths: 40_000,
            seed: 11,
            threads: 2,
        };
        let x = fhs_paths(&z, 50, 1, &g, &[1.0], &sim).unwrap();
        assert_eq!(x, fhs_paths(&z, 50, 1, &g, &[1.0], &sim).unwrap());
        assert_ne!(
            x,
            fhs_paths(&z, 50, 1, &g, &[1.0], &Sim { threads: 1, ..sim }).unwrap()
        );
    }

    #[test]
    fn a_day_at_minus_100_percent_wipes_the_path() {
        // z = -1000, sigma = 1, mu = 0: r = -10 <= -1, so every path is -1
        let (mu, s2, zero, b) = ([0.0], [1.0], [0.0], [0.0]);
        let g = one_asset(&mu, &s2, &zero, &zero, &b);
        let out = fhs_paths(
            &[-1000.0],
            1,
            1,
            &g,
            &[1.0],
            &Sim {
                horizon: 3,
                n_paths: 10,
                seed: 1,
                threads: 1,
            },
        )
        .unwrap();
        assert!(out.iter().all(|&x| x == -1.0));
    }

    #[test]
    fn rejects_bad_input() {
        let (mu, s2, zero, b) = ([0.0], [1.0], [0.0], [0.0]);
        let g = one_asset(&mu, &s2, &zero, &zero, &b);
        let sim = Sim {
            horizon: 1,
            n_paths: 10,
            seed: 1,
            threads: 1,
        };
        assert!(fhs_paths(&[f64::NAN], 1, 1, &g, &[1.0], &sim).is_err());
        assert!(fhs_paths(&[1.0], 1, 1, &g, &[1.0, 0.0], &sim).is_err());
        assert!(fhs_paths(&[1.0, 2.0], 1, 1, &g, &[1.0], &sim).is_err());
        let bad_s2 = [0.0];
        let g0 = one_asset(&mu, &bad_s2, &zero, &zero, &b);
        assert!(fhs_paths(&[1.0], 1, 1, &g0, &[1.0], &sim).is_err());
    }
}
