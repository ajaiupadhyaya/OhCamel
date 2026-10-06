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
}
