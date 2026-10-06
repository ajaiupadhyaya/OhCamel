//! The stationary bootstrap (Politis & Romano 1994) of column means (contract
//! II.4 `stationary_bootstrap_means`).
//!
//! A replication walks n indices: the first is uniform on 0..n; each later one
//! starts a new block (a fresh uniform index) with probability p = 1/mean_block,
//! else continues to the next index circularly. Block lengths are geometric with
//! mean `mean_block` (pinned by `walk_blocks_have_the_expected_mean_length`). The
//! index stream depends only on (seed, threads, n, mean_block, reps), never on the
//! values, so the columns of an n x k input share one resample: bootstrap_sharpe
//! passes [x, x^2] and reads a replication's mean and variance off one draw.
use crate::par;
use rand::Rng;
use rand_chacha::ChaCha20Rng;

pub fn walk(rng: &mut ChaCha20Rng, n: usize, p: f64, mut visit: impl FnMut(usize, bool)) {
    let mut i = rng.gen_range(0..n);
    visit(i, true);
    for _ in 1..n {
        if rng.gen::<f64>() < p {
            i = rng.gen_range(0..n);
            visit(i, true);
        } else {
            i += 1;
            if i == n {
                i = 0;
            }
            visit(i, false);
        }
    }
}

pub fn stationary_bootstrap_means(
    x: &[f64],
    n: usize,
    k: usize,
    mean_block: f64,
    reps: usize,
    seed: u64,
    threads: usize,
) -> Result<Vec<f64>, String> {
    if n < 2 || k == 0 || x.len() != n * k {
        return Err(format!(
            "x must be n x k with n >= 2; got {} values for {n} x {k}",
            x.len()
        ));
    }
    if x.iter().any(|v| !v.is_finite()) {
        return Err("x must be finite".into());
    }
    if !(mean_block.is_finite() && mean_block >= 1.0) {
        return Err(format!("mean_block must be >= 1; got {mean_block}"));
    }
    if reps == 0 || reps * k > par::MAX_ROWS {
        return Err(format!(
            "reps x columns must be in 1..={}; got {}",
            par::MAX_ROWS,
            reps * k
        ));
    }
    let p = 1.0 / mean_block;
    let mut out = vec![0.0; reps * k];
    par::fill_rows(&mut out, k, par::CHUNK_ROWS, seed, threads, |rng, chunk| {
        for row in chunk.chunks_mut(k) {
            row.fill(0.0);
            walk(rng, n, p, |i, _| {
                let src = &x[i * k..(i + 1) * k];
                for c in 0..k {
                    row[c] += src[c];
                }
            });
            for v in row.iter_mut() {
                *v /= n as f64;
            }
        }
    })?;
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;
    use rand::SeedableRng;

    #[test]
    fn walk_blocks_have_the_expected_mean_length() {
        // geometric block lengths, p = 1/5: mean 5, sd sqrt(1 - p)/p = 4.47; ~80,000 blocks
        // give a standard error of 0.016, and one truncated block per walk biases the mean by
        // < 0.01, so 0.06 is ~3 se plus the bias.
        let mut rng = ChaCha20Rng::seed_from_u64(11);
        let (mut draws, mut blocks) = (0usize, 0usize);
        for _ in 0..40 {
            walk(&mut rng, 10_000, 0.2, |_, new| {
                draws += 1;
                if new {
                    blocks += 1;
                }
            });
        }
        let mean = draws as f64 / blocks as f64;
        assert!((mean - 5.0).abs() < 0.06, "mean block length {mean}");
    }

    #[test]
    fn walk_continues_circularly() {
        // p = 0: one block, so indices run i, i+1, ... wrapping at n
        let mut rng = ChaCha20Rng::seed_from_u64(2);
        let mut seen = Vec::new();
        walk(&mut rng, 7, 0.0, |i, _| seen.push(i));
        for t in 1..7 {
            assert_eq!(seen[t], (seen[t - 1] + 1) % 7);
        }
    }

    #[test]
    fn constant_series_means_are_the_constant() {
        let out = stationary_bootstrap_means(&[3.0; 50], 50, 1, 4.0, 100, 1, 2).unwrap();
        assert!(out.iter().all(|&m| m == 3.0)); // 50 x 3.0 / 50 is exact
    }

    #[test]
    fn a_huge_mean_block_is_one_circular_block() {
        // mean_block = 1e12 -> p = 1e-12: P(any restart in 100 x 63 steps) ~ 6e-9, so every
        // replicate is one circular block from a random start and visits each of 0..64 once:
        // mean = (0 + ... + 63) / 64 = 2016 / 64 = 31.5 exactly (integer sums are exact).
        // With p = mean_block (>= 1) every step restarts, and the means scatter.
        let x: Vec<f64> = (0..64).map(|i| i as f64).collect();
        let out = stationary_bootstrap_means(&x, 64, 1, 1e12, 100, 5, 2).unwrap();
        assert!(out.iter().all(|&m| m == 31.5), "{:?}", &out[..5]);
    }

    #[test]
    fn columns_share_the_index_stream() {
        // column 1 = 2 x column 0, and doubling is exact, so every mean doubles exactly
        let x: Vec<f64> = (0..60)
            .flat_map(|i| [i as f64 * 0.37, i as f64 * 0.74])
            .collect();
        let out = stationary_bootstrap_means(&x, 60, 2, 3.0, 500, 9, 2).unwrap();
        for r in out.chunks(2) {
            assert_eq!(r[1], 2.0 * r[0]);
        }
    }

    #[test]
    fn rejects_bad_input() {
        assert!(stationary_bootstrap_means(&[1.0], 1, 1, 2.0, 10, 1, 1).is_err());
        assert!(stationary_bootstrap_means(&[1.0, 2.0], 2, 1, 0.5, 10, 1, 1).is_err());
        assert!(stationary_bootstrap_means(&[1.0, 2.0], 2, 1, 2.0, 0, 1, 1).is_err());
        assert!(stationary_bootstrap_means(&[1.0, f64::NAN], 2, 1, 2.0, 10, 1, 1).is_err());
    }
}
