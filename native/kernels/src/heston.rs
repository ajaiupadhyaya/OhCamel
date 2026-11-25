//! Heston European prices by the COS method (completion task 4.6), step for
//! step as quant/src/ohcamel_quant/kernels/reference.py `heston_price`:
//! the little-trap characteristic function of ln(S_T/F) (Albrecher et al.
//! 2007), evaluated without the cancellation in beta - d; the c2-only
//! cumulant range [c1 - L sqrt(c2), c1 + L sqrt(c2)] (Fang & Oosterlee 2008);
//! the out-of-the-money option is the cosine series and the other is
//! put-call parity. One series per distinct maturity (contracts of one
//! expiry share it). NaN for an invalid contract.

#[derive(Clone, Copy, Debug)]
struct C {
    re: f64,
    im: f64,
}

impl C {
    fn new(re: f64, im: f64) -> C {
        C { re, im }
    }
    fn add(self, o: C) -> C {
        C::new(self.re + o.re, self.im + o.im)
    }
    fn sub(self, o: C) -> C {
        C::new(self.re - o.re, self.im - o.im)
    }
    fn mul(self, o: C) -> C {
        C::new(
            self.re * o.re - self.im * o.im,
            self.re * o.im + self.im * o.re,
        )
    }
    fn scale(self, s: f64) -> C {
        C::new(self.re * s, self.im * s)
    }
    fn div(self, o: C) -> C {
        let d = o.re * o.re + o.im * o.im;
        C::new(
            (self.re * o.re + self.im * o.im) / d,
            (self.im * o.re - self.re * o.im) / d,
        )
    }
    fn exp(self) -> C {
        let m = self.re.exp();
        C::new(m * self.im.cos(), m * self.im.sin())
    }
    /// Principal square root (C99 csqrt: the imaginary part takes y's sign).
    fn sqrt(self) -> C {
        let r = self.re.hypot(self.im);
        let a = ((r + self.re) * 0.5).sqrt();
        let b = ((r - self.re) * 0.5).sqrt();
        C::new(a, if self.im < 0.0 { -b } else { b })
    }
    /// log(1 + z), accurate for small |z| (reference `_clog1p`).
    fn log1p(self) -> C {
        let (x, y) = (self.re, self.im);
        C::new(0.5 * (2.0 * x + x * x + y * y).ln_1p(), y.atan2(1.0 + x))
    }
}

/// Mean and variance of ln(S_T / F) (reference `heston_cumulants`).
pub fn cumulants(t: f64, kappa: f64, theta: f64, sigma: f64, rho: f64, v0: f64) -> (f64, f64) {
    let e1 = (-kappa * t).exp();
    let d = v0 - theta;
    let i1 = (1.0 - e1) / kappa;
    let i2 = (1.0 - (-2.0 * kappa * t).exp()) / (2.0 * kappa);
    let ey = theta * t + d * i1;
    let cov = rho
        * sigma
        * (theta * (t - i1) / kappa + d * (1.0 - e1 * (1.0 + kappa * t)) / (kappa * kappa));
    let s2k = sigma * sigma / kappa;
    let int_var = s2k * (0.5 * theta * t + d * i1 + (0.5 * theta - v0) * i2);
    let int_var_e = s2k * (0.5 * theta * i1 + d * t * e1 + (0.5 * theta - v0) * e1 * i1);
    let var_y = 2.0 / kappa * (int_var - int_var_e);
    (-0.5 * ey, ey + 0.25 * var_y - cov)
}

fn cf(u: f64, t: f64, kappa: f64, theta: f64, sigma: f64, rho: f64, v0: f64) -> C {
    let iu = C::new(0.0, u);
    let beta = C::new(kappa, 0.0).sub(iu.scale(rho * sigma));
    let s2 = sigma * sigma;
    let q = iu.add(C::new(u * u, 0.0));
    let d = beta.mul(beta).add(q.scale(s2)).sqrt();
    let bd = beta.add(d);
    let r = C::new(0.0, 0.0).sub(q).div(bd);
    let g = r.scale(s2).div(bd);
    let e = C::new(0.0, 0.0).sub(d.scale(t)).exp();
    let one = C::new(1.0, 0.0);
    let ome = one.sub(e);
    let lg = g.mul(ome).div(one.sub(g)).log1p();
    let c = r.scale(t).sub(lg.scale(2.0 / s2)).scale(kappa * theta);
    let dd = r.mul(ome).div(one.sub(g.mul(e)));
    c.add(dd.scale(v0)).exp()
}

/// (a, b, u_k, A_k) of one maturity (reference `heston_series`).
fn series(t: f64, p: &[f64; 5], n_terms: usize, l: f64) -> (f64, f64, Vec<f64>, Vec<f64>) {
    let (c1, c2) = cumulants(t, p[0], p[1], p[2], p[3], p[4]);
    let h = l * c2.abs().sqrt();
    let (a, b) = (c1 - h, c1 + h);
    let w = std::f64::consts::PI / (b - a);
    let mut us = Vec::with_capacity(n_terms);
    let mut aa = Vec::with_capacity(n_terms);
    for k in 0..n_terms {
        let u = k as f64 * w;
        let f = cf(u, t, p[0], p[1], p[2], p[3], p[4]);
        let shift = C::new(0.0, -u * a).exp();
        let mut ak = f.mul(shift).re;
        if k == 0 {
            ak *= 0.5;
        }
        us.push(u);
        aa.push(ak);
    }
    (a, b, us, aa)
}

/// Prices of contracts (F, K, T, df, phi = +1 call / -1 put) under one
/// parameter set (kappa, theta, sigma, rho, v0).
pub fn heston_price(
    f: &[f64],
    k: &[f64],
    t: &[f64],
    df: &[f64],
    phi: &[f64],
    p: &[f64; 5],
    n_terms: usize,
    l: f64,
) -> Result<Vec<f64>, String> {
    let n = f.len();
    if k.len() != n || t.len() != n || df.len() != n || phi.len() != n {
        return Err("F, K, T, df and phi must have one length".into());
    }
    if !(p[0] > 0.0 && p[1] > 0.0 && p[2] > 0.0 && p[4] > 0.0 && p[3].abs() < 1.0)
        || p.iter().any(|v| !v.is_finite())
    {
        return Err("Heston needs kappa, theta, sigma, v0 > 0 and |rho| < 1, all finite".into());
    }
    if n_terms < 2 || !(l.is_finite() && l > 0.0) {
        return Err("n_terms must be at least 2 and L finite and positive".into());
    }
    let mut out = vec![f64::NAN; n];
    let mut cache: Vec<(u64, (f64, f64, Vec<f64>, Vec<f64>))> = Vec::new();
    for j in 0..n {
        let (fj, kj, tj, dj) = (f[j], k[j], t[j], df[j]);
        let ok = [fj, kj, tj, dj].iter().all(|v| v.is_finite() && *v > 0.0);
        if !ok {
            continue;
        }
        let key = tj.to_bits();
        let idx = match cache.iter().position(|(kk, _)| *kk == key) {
            Some(i) => i,
            None => {
                cache.push((key, series(tj, p, n_terms, l)));
                cache.len() - 1
            }
        };
        let (a, b, us, aa) = &cache[idx].1;
        let x = (fj / kj).ln();
        let otm_call = kj > fj;
        let ya = x + a;
        let (mut lo, mut hi) = if otm_call { (0.0, x + b) } else { (ya, 0.0) };
        let live = hi > lo;
        if !live {
            lo = 0.0;
            hi = 0.0;
        }
        let (elo, ehi) = (lo.exp(), hi.exp());
        let sgn = if otm_call { 1.0 } else { -1.0 };
        let scale = 2.0 / (b - a) * kj;
        let mut acc = 0.0;
        for (i, (&u, &ak)) in us.iter().zip(aa.iter()).enumerate() {
            let (tc, td) = ((lo - ya) * u, (hi - ya) * u);
            let (cc, sc, cd, sd) = (tc.cos(), tc.sin(), td.cos(), td.sin());
            let chi = (cd * ehi - cc * elo + u * (sd * ehi - sc * elo)) / (1.0 + u * u);
            let psi = if i == 0 { hi - lo } else { (sd - sc) / u };
            acc += sgn * (chi - psi) * scale * ak;
        }
        let otm = if live { (dj * acc).max(0.0) } else { 0.0 };
        let is_call = phi[j] > 0.0;
        out[j] = if is_call == otm_call {
            otm
        } else {
            otm + if is_call { 1.0 } else { -1.0 } * dj * (fj - kj)
        };
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    const REF: [f64; 5] = [1.5768, 0.0398, 0.5751, -0.5711, 0.0175];

    #[test]
    fn the_published_reference_call() {
        let c = heston_price(&[100.0], &[100.0], &[1.0], &[1.0], &[1.0], &REF, 160, 12.0).unwrap();
        assert!((c[0] - 5.785155450).abs() < 5e-5, "{}", c[0]);
        let w = heston_price(&[100.0], &[100.0], &[1.0], &[1.0], &[1.0], &REF, 512, 20.0).unwrap();
        assert!((w[0] - 5.785155450).abs() < 1e-7, "{}", w[0]);
    }

    #[test]
    fn put_call_parity() {
        let k: Vec<f64> = (0..41).map(|i| 60.0 + 2.5 * i as f64).collect();
        let n = k.len();
        let (f, t, d) = (vec![101.0; n], vec![0.5; n], vec![0.98; n]);
        let c = heston_price(&f, &k, &t, &d, &vec![1.0; n], &REF, 160, 12.0).unwrap();
        let p = heston_price(&f, &k, &t, &d, &vec![-1.0; n], &REF, 160, 12.0).unwrap();
        for i in 0..n {
            assert!((c[i] - p[i] - 0.98 * (101.0 - k[i])).abs() < 1e-10);
        }
    }

    #[test]
    fn invalid_inputs() {
        let bad = [1.0, 0.04, 0.5, -1.0, 0.04];
        assert!(heston_price(&[1.0], &[1.0], &[1.0], &[1.0], &[1.0], &bad, 160, 12.0).is_err());
        let r = heston_price(&[1.0], &[-1.0], &[1.0], &[1.0], &[1.0], &REF, 160, 12.0).unwrap();
        assert!(r[0].is_nan());
    }
}
