//! Nelder-Mead (Nelder & Mead 1965), a port of scipy.optimize's
//! `_minimize_neldermead` with adaptive = False: reflection 1, expansion 2,
//! contraction 0.5, shrink 0.5; initial simplex x0 plus, per coordinate,
//! x0_k x 1.05 (0.00025 where x0_k = 0); stop when the simplex spread is
//! <= xatol in x AND <= fatol in f (NaN never counts as converged). With
//! `bounds`, x0 and every trial point are clipped to the box and initial
//! vertices above an upper bound are first reflected (2 ub - x), as scipy does.
//! svi_fit relies on this being scipy's algorithm step for step.

pub struct NmOptions<'a> {
    pub xatol: f64,
    pub fatol: f64,
    pub maxiter: usize,
    pub maxfev: usize,
    pub bounds: Option<&'a [(f64, f64)]>,
}

pub struct NmResult {
    pub x: Vec<f64>,
    pub fun: f64,
    pub nit: usize,
    pub nfev: usize,
    pub converged: bool,
}

fn max_abs<I: Iterator<Item = f64>>(it: I) -> f64 {
    let mut m = 0.0;
    for v in it {
        if v.is_nan() {
            return f64::NAN;
        }
        if v > m {
            m = v;
        }
    }
    m
}

fn clip(x: &mut [f64], bounds: Option<&[(f64, f64)]>) {
    if let Some(b) = bounds {
        for (xi, (lo, hi)) in x.iter_mut().zip(b) {
            *xi = xi.clamp(*lo, *hi);
        }
    }
}

fn order(sim: &mut Vec<Vec<f64>>, fsim: &mut Vec<f64>) {
    let mut idx: Vec<usize> = (0..fsim.len()).collect();
    idx.sort_by(|&a, &b| fsim[a].total_cmp(&fsim[b])); // stable, like numpy's sort of a short array
    *sim = idx.iter().map(|&i| sim[i].clone()).collect();
    *fsim = idx.iter().map(|&i| fsim[i]).collect();
}

pub fn nelder_mead<F: FnMut(&[f64]) -> f64>(mut f: F, x0: &[f64], o: &NmOptions) -> NmResult {
    let n = x0.len();
    let (rho, chi, psi, sigma) = (1.0, 2.0, 0.5, 0.5);
    let mut start = x0.to_vec();
    clip(&mut start, o.bounds);
    let mut sim = vec![start.clone()];
    for k in 0..n {
        let mut y = start.clone();
        y[k] = if y[k] != 0.0 { 1.05 * y[k] } else { 0.00025 };
        sim.push(y);
    }
    if let Some(b) = o.bounds {
        for p in sim.iter_mut() {
            for (xi, (lo, hi)) in p.iter_mut().zip(b) {
                if *xi > *hi {
                    *xi = 2.0 * hi - *xi;
                }
                *xi = xi.clamp(*lo, *hi);
            }
        }
    }
    let mut nfev = 0usize;
    let mut fsim: Vec<f64> = sim
        .iter()
        .map(|p| {
            nfev += 1;
            f(p)
        })
        .collect();
    order(&mut sim, &mut fsim);
    let (mut nit, mut converged) = (0usize, false);
    let mut eval = |x: &[f64], nfev: &mut usize| {
        *nfev += 1;
        f(x)
    };
    while nfev < o.maxfev && nit < o.maxiter {
        let xs = max_abs(
            sim[1..]
                .iter()
                .flat_map(|p| p.iter().zip(&sim[0]).map(|(a, b)| (a - b).abs())),
        );
        let fs = max_abs(fsim[1..].iter().map(|v| (fsim[0] - v).abs()));
        if xs <= o.xatol && fs <= o.fatol {
            converged = true;
            break;
        }
        let xbar: Vec<f64> = (0..n)
            .map(|j| sim[..n].iter().map(|p| p[j]).sum::<f64>() / n as f64)
            .collect();
        let worst = sim[n].clone();
        let lin = |a: f64, b: f64| -> Vec<f64> {
            let mut v: Vec<f64> = xbar
                .iter()
                .zip(&worst)
                .map(|(xb, w)| a * xb - b * w)
                .collect();
            clip(&mut v, o.bounds);
            v
        };
        let xr = lin(1.0 + rho, rho);
        let fxr = eval(&xr, &mut nfev);
        let mut shrink = false;
        if fxr < fsim[0] {
            let xe = lin(1.0 + rho * chi, rho * chi);
            let fxe = eval(&xe, &mut nfev);
            if fxe < fxr {
                sim[n] = xe;
                fsim[n] = fxe;
            } else {
                sim[n] = xr;
                fsim[n] = fxr;
            }
        } else if fxr < fsim[n - 1] {
            sim[n] = xr;
            fsim[n] = fxr;
        } else if fxr < fsim[n] {
            let xc = lin(1.0 + psi * rho, psi * rho);
            let fxc = eval(&xc, &mut nfev);
            if fxc <= fxr {
                sim[n] = xc;
                fsim[n] = fxc;
            } else {
                shrink = true;
            }
        } else {
            let xcc = lin(1.0 - psi, -psi);
            let fxcc = eval(&xcc, &mut nfev);
            if fxcc < fsim[n] {
                sim[n] = xcc;
                fsim[n] = fxcc;
            } else {
                shrink = true;
            }
        }
        if shrink {
            for j in 1..=n {
                let mut p: Vec<f64> = sim[0]
                    .iter()
                    .zip(&sim[j])
                    .map(|(b, x)| b + sigma * (x - b))
                    .collect();
                clip(&mut p, o.bounds);
                fsim[j] = eval(&p, &mut nfev);
                sim[j] = p;
            }
        }
        order(&mut sim, &mut fsim);
        nit += 1;
    }
    NmResult {
        x: sim[0].clone(),
        fun: fsim[0],
        nit,
        nfev,
        converged,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn opts(bounds: Option<&[(f64, f64)]>) -> NmOptions<'_> {
        NmOptions {
            xatol: 1e-10,
            fatol: 1e-14,
            maxiter: 5000,
            maxfev: 10_000,
            bounds,
        }
    }

    #[test]
    fn rosenbrock() {
        // minimum (1, 1), value 0
        let r = nelder_mead(
            |x: &[f64]| 100.0 * (x[1] - x[0] * x[0]).powi(2) + (1.0 - x[0]).powi(2),
            &[-1.2, 1.0],
            &opts(None),
        );
        assert!(
            r.converged && (r.x[0] - 1.0).abs() < 1e-6 && (r.x[1] - 1.0).abs() < 1e-6,
            "{:?}",
            r.x
        );
    }

    #[test]
    fn initial_simplex_is_scipys() {
        // x0 = (0, 2): vertices (0, 2), (0.00025, 2), (0, 2.1) -- the first three evaluations
        let mut seen = Vec::new();
        nelder_mead(
            |x: &[f64]| {
                seen.push(x.to_vec());
                x[0] * x[0] + x[1] * x[1]
            },
            &[0.0, 2.0],
            &NmOptions {
                maxiter: 0,
                ..opts(None)
            },
        );
        assert_eq!(
            seen,
            vec![vec![0.0, 2.0], vec![0.00025, 2.0], vec![0.0, 2.1]]
        );
    }

    #[test]
    fn bounds_clip() {
        // min (x - 3)^2 on [0, 1] is at x = 1
        let b = [(0.0, 1.0)];
        let r = nelder_mead(|x: &[f64]| (x[0] - 3.0).powi(2), &[0.5], &opts(Some(&b)));
        assert_eq!(r.x[0], 1.0);
    }
}
