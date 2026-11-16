//! Raw SVI calibration (contract II.4 `svi_fit`) -- options/svi.calibrate, step
//! for step: Zeliade's quasi-explicit method (De Marco & Martini 2009). For fixed
//! (m, sigma), y = (k - m)/sigma and w = a + u (y + z)/2 + v (z - y)/2, z =
//! sqrt(y^2 + 1), is linear in (a, u, v); the weighted least-squares problem over
//! the box 0 <= u, v <= 2 sigma (both wing slopes b (1 +- rho) <= 2, Lee's bound),
//! a <= max w (a >= 0 when the free fit has negative minimum variance) is solved
//! exactly by enumerating the box's faces (the fast path takes the unconstrained
//! solution when it is feasible). The outer problem over (m, ln sigma) is a 9 x 7
//! grid, then Nelder-Mead (optim.rs, scipy's) from the three best grid points
//! (xatol 1e-7, fatol 1e-14, 600 iterations), with a quadratic penalty outside m in
//! [kmin, kmax] (the vertex stays inside the quotes), sigma in [1e-4, 4 span + 1],
//! and a candidate rejected (objective +inf) when Durrleman's g < -1e-10 or w <= 0
//! anywhere on 1201 points of [-3, 3] (K_MAX). Then b = c/sigma, rho = d/c
//! (clipped to +-0.999999; 0 when c <= 1e-14).
use crate::optim::{nelder_mead, NmOptions};

const SLOPE_CAP: f64 = 2.0;
const K_MAX: f64 = 3.0;
const G_TOL: f64 = 1e-10;
const N_GRID: usize = 1201;

pub struct SviFit {
    pub a: f64,
    pub b: f64,
    pub rho: f64,
    pub m: f64,
    pub sigma: f64,
    pub sse: f64,
    pub constrained_a: bool,
}

type M3 = [[f64; 3]; 3];

/// Solve G[idx, idx] x = rhs by Gaussian elimination with partial pivoting; None if a pivot is 0.
fn solve_sub(g: &M3, rhs: &[f64], idx: &[usize]) -> Option<Vec<f64>> {
    let n = idx.len();
    let mut a: Vec<Vec<f64>> = (0..n)
        .map(|i| {
            let mut row: Vec<f64> = idx.iter().map(|&j| g[idx[i]][j]).collect();
            row.push(rhs[i]);
            row
        })
        .collect();
    for col in 0..n {
        let piv = (col..n).max_by(|&p, &q| a[p][col].abs().total_cmp(&a[q][col].abs()))?;
        if a[piv][col] == 0.0 {
            return None;
        }
        a.swap(col, piv);
        for r in (col + 1)..n {
            let f = a[r][col] / a[col][col];
            for c in col..=n {
                a[r][c] -= f * a[col][c];
            }
        }
    }
    let mut x = vec![0.0; n];
    for i in (0..n).rev() {
        let s: f64 = ((i + 1)..n).map(|j| a[i][j] * x[j]).sum();
        x[i] = (a[i][n] - s) / a[i][i];
    }
    Some(x)
}

fn quad(g: &M3, h: &[f64; 3], x: &[f64; 3]) -> f64 {
    let mut q = 0.0;
    for i in 0..3 {
        for j in 0..3 {
            q += x[i] * g[i][j] * x[j];
        }
        q -= 2.0 * h[i] * x[i];
    }
    q
}

/// The faces in itertools.product order, grouped by free set in order of first appearance.
fn groups(a_boxed: bool) -> Vec<(Vec<usize>, Vec<usize>, Vec<[u8; 3]>)> {
    let a_codes: &[u8] = if a_boxed { &[0, 1, 2] } else { &[0, 2] };
    let mut out: Vec<(Vec<usize>, Vec<usize>, Vec<[u8; 3]>)> = Vec::new();
    for &ca in a_codes {
        for cu in 0..3u8 {
            for cv in 0..3u8 {
                let face = [ca, cu, cv];
                let free: Vec<usize> = (0..3).filter(|&i| face[i] == 0).collect();
                let fixed: Vec<usize> = (0..3).filter(|&i| face[i] != 0).collect();
                match out.iter_mut().find(|g| g.0 == free) {
                    Some(g) => g.2.push(face),
                    None => out.push((free, fixed, vec![face])),
                }
            }
        }
    }
    out
}

fn box_qp(g: &M3, h: &[f64; 3], lo: &[f64; 3], hi: &[f64; 3], a_boxed: bool) -> Option<[f64; 3]> {
    let feasible = |x: &[f64; 3]| (0..3).all(|i| x[i] >= lo[i] - 1e-12 && x[i] <= hi[i] + 1e-12);
    if let Some(s) = solve_sub(g, h, &[0, 1, 2]) {
        let x0 = [s[0], s[1], s[2]];
        if feasible(&x0) {
            return Some(x0);
        }
    }
    let (mut best, mut best_x) = (f64::INFINITY, None);
    'group: for (free, fixed, faces) in groups(a_boxed) {
        let mut cand: Option<([f64; 3], f64)> = None;
        for face in &faces {
            let mut x = [0.0; 3];
            for &i in &fixed {
                x[i] = if face[i] == 1 { lo[i] } else { hi[i] };
            }
            if !free.is_empty() {
                let rhs: Vec<f64> = free
                    .iter()
                    .map(|&i| h[i] - fixed.iter().map(|&j| g[i][j] * x[j]).sum::<f64>())
                    .collect();
                match solve_sub(g, &rhs, &free) {
                    Some(s) => {
                        for (p, &i) in free.iter().enumerate() {
                            x[i] = s[p];
                        }
                    }
                    None => continue 'group,
                }
            }
            if feasible(&x) {
                let obj = quad(g, h, &x);
                if cand.is_none_or(|(_, o)| obj < o) {
                    cand = Some((x, obj));
                }
            }
        }
        if let Some((x, obj)) = cand {
            if obj < best {
                best = obj;
                best_x = Some(x);
            }
        }
    }
    best_x
}

fn inner(
    k: &[f64],
    w: &[f64],
    wt: &[f64],
    m: f64,
    s: f64,
    a_nonneg: bool,
) -> Option<([f64; 3], f64, bool)> {
    let (mut g, mut h, mut wmax) = ([[0.0; 3]; 3], [0.0; 3], f64::NEG_INFINITY);
    let mut rows = Vec::with_capacity(k.len());
    for i in 0..k.len() {
        let y = (k[i] - m) / s;
        let z = (y * y + 1.0).sqrt();
        let a = [1.0, 0.5 * (y + z), 0.5 * (z - y)];
        for p in 0..3 {
            for q in 0..3 {
                g[p][q] += a[p] * (a[q] * wt[i]);
            }
            h[p] += a[p] * wt[i] * w[i];
        }
        wmax = wmax.max(w[i]);
        rows.push(a);
    }
    let hi = [wmax, SLOPE_CAP * s, SLOPE_CAP * s];
    let lo = [if a_nonneg { 0.0 } else { f64::NEG_INFINITY }, 0.0, 0.0];
    let x = box_qp(&g, &h, &lo, &hi, a_nonneg)?;
    let (a, u, v) = (x[0], x[1], x[2]);
    if !a_nonneg && a + (u * v).max(0.0).sqrt() < 0.0 {
        return inner(k, w, wt, m, s, true);
    }
    let sse = (0..k.len())
        .map(|i| {
            let r = rows[i][0] * a + rows[i][1] * u + rows[i][2] * v - w[i];
            wt[i] * r * r
        })
        .sum();
    Some(([a, 0.5 * (u + v), 0.5 * (u - v)], sse, a_nonneg))
}

/// (a, b, rho) from the inner solution (a, c, d), as options/svi._to_params.
fn params(x: &[f64; 3], s: f64) -> (f64, f64, f64) {
    let (a, c, d) = (x[0], x[1], x[2]);
    let rho = if c > 1e-14 {
        (d / c).clamp(-0.999_999, 0.999_999)
    } else {
        0.0
    };
    (a, c / s, rho)
}

/// options/svi.butterfly_free: g >= -G_TOL and w > 0 on every grid point, with SVIParams'
/// w, dw, d2w and butterfly_g evaluated in the same operation order (bit-for-bit the same).
fn butterfly_free(x: &[f64; 3], m: f64, s: f64, grid: &[f64]) -> bool {
    let (a, b, rho) = params(x, s);
    grid.iter().all(|&k| {
        let xk = k - m;
        let q = xk * xk + s * s;
        let r = q.sqrt();
        let w = a + b * (rho * xk + r);
        let w1 = b * (rho + xk / r);
        let w2 = b * (s * s) / (q * r);
        let t = 1.0 - k * w1 / (2.0 * w);
        let g = t * t - (w1 * w1 / 4.0) * (1.0 / w + 0.25) + w2 / 2.0;
        g >= -G_TOL && w > 0.0
    })
}

fn linspace(a: f64, b: f64, n: usize) -> Vec<f64> {
    let step = (b - a) / (n - 1) as f64;
    (0..n)
        .map(|i| if i == n - 1 { b } else { a + step * i as f64 })
        .collect()
}

pub fn svi_fit(k: &[f64], w: &[f64], wt: &[f64]) -> Result<SviFit, String> {
    let n = k.len();
    if n < 5 || w.len() != n || wt.len() != n {
        return Err(format!(
            "SVI needs at least 5 quotes of equal-length k, w and weights; got {n}"
        ));
    }
    if k.iter().chain(w).chain(wt).any(|v| !v.is_finite())
        || w.iter().any(|&v| v <= 0.0)
        || wt.iter().any(|&v| v <= 0.0)
    {
        return Err("k, w and weights must be finite, with w and weights positive".into());
    }
    let kmin = k.iter().cloned().fold(f64::INFINITY, f64::min);
    let kmax = k.iter().cloned().fold(f64::NEG_INFINITY, f64::max);
    let span = (kmax - kmin).max(1e-3);
    let (m_lo, m_hi, s_lo, s_hi) = (kmin, kmax, 1e-4, 4.0 * span + 1.0);
    let grid = linspace(-K_MAX, K_MAX, N_GRID);
    let obj = |th: &[f64]| -> f64 {
        let (mut m, ls) = (th[0], th[1]);
        let mut s = ls.exp();
        let mut pen = 0.0;
        if m < m_lo || m > m_hi {
            pen += 1e3 * (m_lo - m).max(m - m_hi).powi(2);
            m = m.clamp(m_lo, m_hi);
        }
        if s < s_lo || s > s_hi {
            pen += 1e3 * (s_lo.ln() - ls).max(ls - s_hi.ln()).powi(2);
            s = s.clamp(s_lo, s_hi);
        }
        let sse = match inner(k, w, wt, m, s, false) {
            Some((x, sse, _)) if butterfly_free(&x, m, s, &grid) => sse,
            _ => f64::INFINITY, // no feasible face, or arbitrage somewhere on [-K_MAX, K_MAX]
        };
        sse + pen
    };
    let m_grid = linspace(kmin, kmax, 9);
    let s_grid: Vec<f64> = linspace((0.01 * span + 1e-4).ln(), (1.5 * span + 1e-3).ln(), 7)
        .iter()
        .map(|v| v.exp())
        .collect();
    let mut starts: Vec<(f64, f64, f64)> = Vec::with_capacity(63);
    for &m in &m_grid {
        for &s in &s_grid {
            starts.push((obj(&[m, s.ln()]), m, s));
        }
    }
    starts.sort_by(|a, b| {
        a.0.total_cmp(&b.0)
            .then(a.1.total_cmp(&b.1))
            .then(a.2.total_cmp(&b.2))
    });
    let opts = NmOptions {
        xatol: 1e-7,
        fatol: 1e-14,
        maxiter: 600,
        maxfev: usize::MAX,
        bounds: None,
    };
    let mut best: Option<crate::optim::NmResult> = None;
    for &(_, m0, s0) in starts.iter().take(3) {
        let r = nelder_mead(obj, &[m0, s0.ln()], &opts);
        if best.as_ref().is_none_or(|b| r.fun < b.fun) {
            best = Some(r);
        }
    }
    let best = best.expect("three starts");
    if !best.fun.is_finite() {
        return Err(format!(
            "SVI: no smile with its vertex inside the quotes is free of butterfly arbitrage on k in [{}, {}]",
            -K_MAX, K_MAX
        ));
    }
    let m = best.x[0].clamp(m_lo, m_hi);
    let s = best.x[1].exp().clamp(s_lo, s_hi);
    let (x, sse, constrained_a) = inner(k, w, wt, m, s, false)
        .ok_or_else(|| "SVI inner problem: no feasible face".to_string())?;
    let (a, b, rho) = params(&x, s);
    Ok(SviFit {
        a,
        b,
        rho,
        m,
        sigma: s,
        sse,
        constrained_a,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn raw(a: f64, b: f64, rho: f64, m: f64, s: f64, k: f64) -> f64 {
        a + b * (rho * (k - m) + ((k - m) * (k - m) + s * s).sqrt())
    }

    #[test]
    fn recovers_an_exact_raw_svi_smile() {
        // w(k) from raw SVI (a, b, rho, m, sigma) = (0.004, 0.04, -0.6, 0.02, 0.12) on 41 points:
        // the fit is exact, so it returns those parameters and SSE ~ 0
        let k: Vec<f64> = (0..41).map(|i| -0.4 + 0.7 * i as f64 / 40.0).collect();
        let w: Vec<f64> = k
            .iter()
            .map(|&x| raw(0.004, 0.04, -0.6, 0.02, 0.12, x))
            .collect();
        let f = svi_fit(&k, &w, &vec![1.0; 41]).unwrap();
        for (got, want) in [
            (f.a, 0.004),
            (f.b, 0.04),
            (f.rho, -0.6),
            (f.m, 0.02),
            (f.sigma, 0.12),
        ] {
            assert!((got - want).abs() < 1e-6, "{got} vs {want}");
        }
        assert!(f.sse < 1e-14);
    }

    #[test]
    fn caps_both_wings_at_lees_bound() {
        // raw SVI (a, b, rho, m, sigma) = (0.3, 1.25, 0.7, 0.0, 0.5) has a right wing slope
        // b (1 + rho) = 2.125, past Lee's bound of 2 (SLOPE_CAP): the fit cannot reproduce it
        // exactly, and its wings stay within the bound
        let k: Vec<f64> = (0..41).map(|i| -0.4 + 0.7 * i as f64 / 40.0).collect();
        let w: Vec<f64> = k
            .iter()
            .map(|&x| raw(0.3, 1.25, 0.7, 0.0, 0.5, x))
            .collect();
        let f = svi_fit(&k, &w, &vec![1.0; 41]).unwrap();
        assert!(
            f.b * (1.0 + f.rho.abs()) <= SLOPE_CAP + 1e-9,
            "{} {}",
            f.b,
            f.rho
        );
        assert!(f.sse > 1e-6, "{}", f.sse);
    }

    #[test]
    fn rejects_a_smile_with_no_arbitrage_free_fit() {
        // (0.01, 2.5, 0.3, 0.0, 0.1): right wing 3.25 on a narrow vertex; every candidate inside
        // the quotes breaks g >= 0 somewhere on [-3, 3], so there is no fit to return
        let k: Vec<f64> = (0..41).map(|i| -0.4 + 0.7 * i as f64 / 40.0).collect();
        let w: Vec<f64> = k
            .iter()
            .map(|&x| raw(0.01, 2.5, 0.3, 0.0, 0.1, x))
            .collect();
        let e = svi_fit(&k, &w, &vec![1.0; 41]).err().expect("no fit");
        assert!(e.contains("butterfly"), "{e}");
    }

    #[test]
    fn keeps_the_vertex_inside_the_quotes() {
        // an exact raw SVI smile whose vertex m = 0.5 lies beyond the last quote at k = 0.3:
        // the fit may not put m there, so m ends inside [kmin, kmax]
        let k: Vec<f64> = (0..41).map(|i| -0.4 + 0.7 * i as f64 / 40.0).collect();
        let w: Vec<f64> = k
            .iter()
            .map(|&x| raw(0.02, 0.1, 0.2, 0.5, 0.1, x))
            .collect();
        let f = svi_fit(&k, &w, &vec![1.0; 41]).unwrap();
        assert!((-0.4..=0.3).contains(&f.m), "{}", f.m);
    }

    #[test]
    fn rejects_bad_input() {
        assert!(svi_fit(&[0.0; 4], &[0.01; 4], &[1.0; 4]).is_err()); // < 5 points
        assert!(svi_fit(
            &[0.0, 0.1, 0.2, 0.3, 0.4],
            &[0.01, 0.0, 0.01, 0.01, 0.01],
            &[1.0; 5]
        )
        .is_err()); // w <= 0
    }
}
