//! Special functions for the Student-t copula: ln Gamma (libm's lgamma), the
//! regularized incomplete beta by Lentz's continued fraction (Numerical
//! Recipes, 3rd ed., section 6.4) and the Student-t CDF built from it:
//!   F(t; nu) = 1 - I_x(nu/2, 1/2) / 2 for t > 0, I_x(nu/2, 1/2) / 2 otherwise,
//!   x = nu / (nu + t^2).

pub fn ln_gamma(x: f64) -> f64 {
    libm::lgamma(x)
}

pub fn betainc(a: f64, b: f64, x: f64) -> f64 {
    if x <= 0.0 {
        return 0.0;
    }
    if x >= 1.0 {
        return 1.0;
    }
    let ln_front = ln_gamma(a + b) - ln_gamma(a) - ln_gamma(b) + a * x.ln() + b * (1.0 - x).ln();
    let front = ln_front.exp();
    if x < (a + 1.0) / (a + b + 2.0) {
        front * betacf(a, b, x) / a
    } else {
        1.0 - front * betacf(b, a, 1.0 - x) / b
    }
}

/// The continued fraction for I_x(a, b) (modified Lentz).
fn betacf(a: f64, b: f64, x: f64) -> f64 {
    const MAXIT: usize = 300;
    const EPS: f64 = 1e-15;
    const FPMIN: f64 = 1e-300;
    let (qab, qap, qam) = (a + b, a + 1.0, a - 1.0);
    let mut c = 1.0;
    let mut d = 1.0 - qab * x / qap;
    if d.abs() < FPMIN {
        d = FPMIN;
    }
    d = 1.0 / d;
    let mut h = d;
    for m in 1..=MAXIT {
        let m = m as f64;
        let m2 = 2.0 * m;
        let aa = m * (b - m) * x / ((qam + m2) * (a + m2));
        d = 1.0 + aa * d;
        if d.abs() < FPMIN {
            d = FPMIN;
        }
        c = 1.0 + aa / c;
        if c.abs() < FPMIN {
            c = FPMIN;
        }
        d = 1.0 / d;
        h *= d * c;
        let aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2));
        d = 1.0 + aa * d;
        if d.abs() < FPMIN {
            d = FPMIN;
        }
        c = 1.0 + aa / c;
        if c.abs() < FPMIN {
            c = FPMIN;
        }
        d = 1.0 / d;
        let del = d * c;
        h *= del;
        if (del - 1.0).abs() < EPS {
            break;
        }
    }
    h
}

pub fn t_cdf(t: f64, nu: f64) -> f64 {
    if t.is_nan() {
        return f64::NAN;
    }
    if t.is_infinite() {
        return if t > 0.0 { 1.0 } else { 0.0 };
    }
    let tail = 0.5 * betainc(0.5 * nu, 0.5, nu / (nu + t * t));
    if t > 0.0 {
        1.0 - tail
    } else {
        tail
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn t_cdf_closed_forms() {
        // nu = 1 is Cauchy: F(t) = 1/2 + atan(t)/pi, so F(1) = 3/4 and F(-1) = 1/4
        assert!((t_cdf(1.0, 1.0) - 0.75).abs() < 1e-14);
        assert!((t_cdf(-1.0, 1.0) - 0.25).abs() < 1e-14);
        // nu = 2: F(t) = 1/2 + t / (2 sqrt(2 + t^2)), so F(2) = 1/2 + 1/sqrt(6)
        assert!((t_cdf(2.0, 2.0) - (0.5 + 1.0 / 6f64.sqrt())).abs() < 1e-14);
        assert_eq!(t_cdf(0.0, 7.3), 0.5);
        // nu -> infinity is the normal: Phi(1.5) = 0.9331927987311419
        assert!((t_cdf(1.5, 1e7) - 0.933_192_798_731_141_9).abs() < 1e-6);
        assert_eq!(t_cdf(f64::INFINITY, 5.0), 1.0);
        assert_eq!(t_cdf(f64::NEG_INFINITY, 5.0), 0.0);
    }

    #[test]
    fn betainc_closed_forms() {
        // I_x(1, 1) = x
        assert!((betainc(1.0, 1.0, 0.3) - 0.3).abs() < 1e-14);
        // integer a, b: I_x(a, b) = P(Bin(a + b - 1, x) >= a); I_0.4(2, 3) = 1 - 0.6^4 - 4(0.4)(0.6^3) = 0.5248
        assert!((betainc(2.0, 3.0, 0.4) - 0.5248).abs() < 1e-14);
        assert_eq!(betainc(2.0, 3.0, 0.0), 0.0);
        assert_eq!(betainc(2.0, 3.0, 1.0), 1.0);
    }
}
