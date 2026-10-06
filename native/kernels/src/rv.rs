//! Daily realized variance from one-minute prices (contract II.4 `realized_vol_minute`):
//! for each session [open, close) (half-open, nanosecond epoch), the sum of squared
//! log returns between consecutive prices inside it (Andersen & Bollerslev 1998).
//! The overnight move is never counted. NaN for a session with fewer than two
//! prices. Take the square root for realized volatility.
pub fn realized_variance(ts: &[i64], px: &[f64], bounds: &[i64]) -> Result<Vec<f64>, String> {
    if ts.len() != px.len() {
        return Err("ts_ns and px must have one length".into());
    }
    if ts.windows(2).any(|w| w[1] < w[0]) {
        return Err("ts_ns must be non-decreasing".into());
    }
    if px.iter().any(|&p| !(p.is_finite() && p > 0.0)) {
        return Err("px must be finite and positive".into());
    }
    if bounds.len() % 2 != 0 {
        return Err("session_bounds must be n_days x 2".into());
    }
    let days = bounds.len() / 2;
    for d in 0..days {
        if bounds[2 * d] >= bounds[2 * d + 1] || (d > 0 && bounds[2 * d] < bounds[2 * d - 1]) {
            return Err(
                "sessions must be non-empty, ordered and non-overlapping [open, close)".into(),
            );
        }
    }
    let mut out = vec![f64::NAN; days];
    let mut j = 0usize;
    for d in 0..days {
        let (open, close) = (bounds[2 * d], bounds[2 * d + 1]);
        while j < ts.len() && ts[j] < open {
            j += 1;
        }
        let (mut prev, mut acc, mut count) = (None::<f64>, 0.0, 0usize);
        while j < ts.len() && ts[j] < close {
            let lp = px[j].ln();
            if let Some(p) = prev {
                let r = lp - p;
                acc += r * r;
                count += 1;
            }
            prev = Some(lp);
            j += 1;
        }
        if count >= 1 {
            out[d] = acc;
        }
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hand_series() {
        // session 1 [1000, 2000): 100, 101, 100, 100 -> ln(1.01)^2 + ln(100/101)^2 + 0 = 2 ln(1.01)^2
        //   (the print at 500 is before the open; the one at 2000 is at the close, so outside)
        // session 2 [5000, 6000): 50, 55 -> ln(1.1)^2 (the 100 -> 50 overnight move is not counted)
        // session 3 [9000, 10000): one print -> NaN
        let ts = [500, 1000, 1060, 1120, 1999, 2000, 5000, 5060, 9000];
        let px = [99.0, 100.0, 101.0, 100.0, 100.0, 250.0, 50.0, 55.0, 10.0];
        let rv = realized_variance(&ts, &px, &[1000, 2000, 5000, 6000, 9000, 10000]).unwrap();
        // relative 1e-12, as test_rv_hand_series: the kernel takes ln(101) - ln(100) and
        // ln(55) - ln(50), which differ from ln(1.01) and ln(1.1) by rounding (absolute errors
        // of 8.6e-18 and 5.2e-17 measured in float64, so an absolute 1e-18 bound fails)
        let l = 1.01f64.ln();
        let (want0, want1) = (2.0 * l * l, 1.1f64.ln().powi(2));
        assert!(
            (rv[0] - want0).abs() <= 1e-12 * want0,
            "{} vs {}",
            rv[0],
            want0
        );
        assert!(
            (rv[1] - want1).abs() <= 1e-12 * want1,
            "{} vs {}",
            rv[1],
            want1
        );
        assert!(rv[2].is_nan());
    }

    #[test]
    fn rejects_bad_input() {
        assert!(realized_variance(&[2, 1], &[1.0, 1.0], &[0, 5]).is_err()); // unsorted
        assert!(realized_variance(&[1, 2], &[1.0, 0.0], &[0, 5]).is_err()); // non-positive price
        assert!(realized_variance(&[1, 2], &[1.0, 1.0], &[0, 5, 4, 8]).is_err()); // overlapping sessions
        assert!(realized_variance(&[1, 2], &[1.0, 1.0], &[5, 5]).is_err()); // empty session
    }
}
