//! The historical tail rule shared by every VaR/ES kernel: risk.core.tail_count.

/// `k = max(1, ceil(n (1 - alpha) - 1e-9))`, verbatim from risk.core.tail_count:
/// VaR is the k-th largest loss and ES the mean of the k largest, so the
/// empirical tail frequency k / n is the smallest one >= 1 - alpha. The 1e-9
/// keeps a product that should be an integer (500 x 0.05) from rounding up.
pub fn tail_count(n: usize, alpha: f64) -> usize {
    let k = ((n as f64) * (1.0 - alpha) - 1e-9).ceil();
    if k < 1.0 {
        1
    } else {
        k as usize
    }
}

/// Historical VaR and ES (positive loss fractions) of a P&L sample: losses are
/// -pnl with non-finite values dropped, k = tail_count(n, alpha), VaR is the
/// k-th largest loss and ES the mean of the k largest -- risk.core.empirical_var_es.
/// (NaN, NaN) for an empty sample.
pub fn var_es_from_pnl(pnl: &[f64], alpha: f64) -> (f64, f64) {
    let mut losses: Vec<f64> = pnl.iter().filter(|x| x.is_finite()).map(|x| -x).collect();
    if losses.is_empty() {
        return (f64::NAN, f64::NAN);
    }
    let k = tail_count(losses.len(), alpha).min(losses.len());
    // the k largest losses to the front (unordered), then ascending, as
    // empirical_var_es sorts its top-k before reading top[0] and the mean
    losses.select_nth_unstable_by(k - 1, |a, b| b.partial_cmp(a).unwrap());
    let top = &mut losses[..k];
    top.sort_by(|a, b| a.partial_cmp(b).unwrap());
    (top[0], top.iter().sum::<f64>() / k as f64)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tail_count_pinned() {
        // ceil(1000 x 0.01 - 1e-9) = ceil(9.999999999) = 10 (compute plan A2's pin)
        assert_eq!(tail_count(1000, 0.99), 10);
        // ceil(250 x 0.01 - 1e-9) = ceil(2.499999999) = 3
        assert_eq!(tail_count(250, 0.99), 3);
        // ceil(99 x 0.01 - 1e-9) = 1, and never below 1
        assert_eq!(tail_count(99, 0.99), 1);
        assert_eq!(tail_count(1, 0.99), 1);
        // 1 - 0.95 = 0.050000000000000044, x 500 = 25.000000000000021; the 1e-9
        // brings it to 24.999999999, so k = 25, not 26
        assert_eq!(tail_count(500, 0.95), 25);
    }

    #[test]
    fn var_es_hand() {
        // losses 0.001..=1.000; k = 10 at 0.99: VaR = 0.991, ES = mean(0.991..=1.000) = 0.9955
        let pnl: Vec<f64> = (1..=1000).map(|i| -(i as f64) / 1000.0).collect();
        let (v, e) = var_es_from_pnl(&pnl, 0.99);
        assert!((v - 0.991).abs() < 1e-15, "{v}");
        assert!((e - 0.9955).abs() < 1e-12, "{e}");
    }

    #[test]
    fn var_es_drops_non_finite_and_empty_is_nan() {
        let (v, e) = var_es_from_pnl(&[f64::NAN, f64::INFINITY], 0.99);
        assert!(v.is_nan() && e.is_nan());
        // finite losses 0.5 and -0.1; k = ceil(2 x 0.4 - 1e-9) = 1, so VaR = ES = 0.5
        assert_eq!(var_es_from_pnl(&[-0.5, f64::NAN, 0.1], 0.6), (0.5, 0.5));
    }
}
