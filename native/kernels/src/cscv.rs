//! Probability of Backtest Overfitting by CSCV (Bailey, Borwein, Lopez de
//! Prado & Zhu 2017, sec. 2; contract II.4 `cscv_pbo`) -- the rule of
//! backtest/validation.py's vectorised NumPy, verbatim: S contiguous equal row
//! blocks (leading rows dropped so S | T); every C(S, S/2) combination in
//! lexicographic order (itertools.combinations); per-period Sharpe from block
//! sums, mean / sqrt((s2 - n mean^2) / (n - 1)), NaN unless s2 - n mean^2 >
//! ZERO_VAR_REL x s2 (a relative zero: the block sums leave a constant trial a
//! rounding residue of ~1e-15 s2 whose sign depends on summation order, so the
//! old `var > 0` test was order-dependent; reference.py uses the same rule, and
//! a real return series has (s2 - n mean^2) / s2 near 1); n* = argmax of the in-sample Sharpe (non-finite as -inf, first
//! maximum); its out-of-sample rank with ties averaged (scipy rankdata
//! 'average'; non-finite as -inf); w = rank / (N + 1); logit = ln(w / (1 - w));
//! PBO = the share of logits <= 0.
use crate::par;
use rayon::prelude::*;

pub const MAX_PARTITIONS: usize = 20;
/// s2 - n mean^2 <= ZERO_VAR_REL x s2 counts as zero variance (a per-period |Sharpe| above ~1e5).
pub const ZERO_VAR_REL: f64 = 1e-10;

pub struct Cscv {
    pub pbo: f64,
    pub logits: Vec<f64>,
    pub selected: Vec<usize>,
    pub is_sharpe: Vec<f64>,
    pub oos_sharpe: Vec<f64>,
}

pub fn combinations(s: usize, r: usize) -> Vec<u8> {
    let mut out = Vec::new();
    let mut c: Vec<usize> = (0..r).collect();
    loop {
        out.extend(c.iter().map(|&i| i as u8));
        let mut i = r as isize - 1;
        while i >= 0 && c[i as usize] == s - r + i as usize {
            i -= 1;
        }
        if i < 0 {
            return out;
        }
        let i = i as usize;
        c[i] += 1;
        for j in (i + 1)..r {
            c[j] = c[j - 1] + 1;
        }
    }
}

fn sharpe(s1: f64, s2: f64, n: f64) -> f64 {
    let mean = s1 / n;
    let dev = s2 - n * (mean * mean); // (n - 1) x variance
    if dev > ZERO_VAR_REL * s2 {
        mean / (dev / (n - 1.0)).sqrt()
    } else {
        f64::NAN // zero variance (relative rule, as reference.cscv_pbo): -inf in argmax and ranks
    }
}

fn finite_or_neg_inf(v: f64) -> f64 {
    if v.is_finite() {
        v
    } else {
        f64::NEG_INFINITY
    }
}

pub fn cscv_pbo(m: &[f64], t: usize, nt: usize, s: usize, threads: usize) -> Result<Cscv, String> {
    if nt < 2 || m.len() != t * nt {
        return Err("CSCV needs a T x N matrix with N >= 2 trials".into());
    }
    if s < 2 || s % 2 == 1 {
        return Err("n_partitions must be an even integer >= 2".into());
    }
    if s > MAX_PARTITIONS {
        return Err(format!(
            "n_partitions must be <= {MAX_PARTITIONS} (C(20, 10) = 184,756 combinations)"
        ));
    }
    if m.iter().any(|v| v.is_nan()) {
        return Err("returns matrix contains NaN".into());
    }
    if t < 4 * s {
        return Err(format!("need at least {} rows for {s} partitions", 4 * s));
    }
    let blen = t / s;
    let rows = blen * s;
    let off = t - rows;
    let (mut b1, mut b2) = (vec![0.0; s * nt], vec![0.0; s * nt]);
    for blk in 0..s {
        for r in 0..blen {
            let row = &m[(off + blk * blen + r) * nt..][..nt];
            for j in 0..nt {
                b1[blk * nt + j] += row[j];
                b2[blk * nt + j] += row[j] * row[j];
            }
        }
    }
    let (mut t1, mut t2) = (vec![0.0; nt], vec![0.0; nt]);
    for blk in 0..s {
        for j in 0..nt {
            t1[j] += b1[blk * nt + j];
            t2[j] += b2[blk * nt + j];
        }
    }
    let r = s / 2;
    let combos = combinations(s, r);
    let ncomb = combos.len() / r;
    let n_is = (r * blen) as f64;
    let n_oos = (rows - r * blen) as f64;
    let res: Vec<(usize, f64, f64, f64)> = par::pool(threads)?.install(|| {
        (0..ncomb)
            .into_par_iter()
            .map(|c| {
                let combo = &combos[c * r..(c + 1) * r];
                let (mut is_sr, mut oos_sr) = (vec![0.0; nt], vec![0.0; nt]);
                for j in 0..nt {
                    let (mut a1, mut a2) = (0.0, 0.0);
                    for &blk in combo {
                        a1 += b1[blk as usize * nt + j];
                        a2 += b2[blk as usize * nt + j];
                    }
                    is_sr[j] = sharpe(a1, a2, n_is);
                    oos_sr[j] = sharpe(t1[j] - a1, t2[j] - a2, n_oos);
                }
                let mut best = 0usize;
                let mut best_v = finite_or_neg_inf(is_sr[0]);
                for j in 1..nt {
                    let v = finite_or_neg_inf(is_sr[j]);
                    if v > best_v {
                        best = j;
                        best_v = v;
                    }
                }
                let x = finite_or_neg_inf(oos_sr[best]);
                let (mut less, mut eq) = (0usize, 0usize);
                for j in 0..nt {
                    let v = finite_or_neg_inf(oos_sr[j]);
                    if v < x {
                        less += 1;
                    } else if v == x {
                        eq += 1;
                    }
                }
                let rank = less as f64 + (eq as f64 + 1.0) / 2.0;
                let w = rank / (nt as f64 + 1.0);
                (best, is_sr[best], oos_sr[best], (w / (1.0 - w)).ln())
            })
            .collect()
    });
    let pbo = res.iter().filter(|x| x.3 <= 0.0).count() as f64 / ncomb as f64;
    Ok(Cscv {
        pbo,
        logits: res.iter().map(|x| x.3).collect(),
        selected: res.iter().map(|x| x.0).collect(),
        is_sharpe: res.iter().map(|x| x.1).collect(),
        oos_sharpe: res.iter().map(|x| x.2).collect(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn combinations_are_lexicographic() {
        assert_eq!(combinations(4, 2), vec![0, 1, 0, 2, 0, 3, 1, 2, 1, 3, 2, 3]);
        assert_eq!(combinations(20, 10).len() / 10, 184_756); // C(20, 10)
    }

    /// Two trials, eight rows, S = 2: `good` = [.02, .01, .02, .01], `bad` = [-.01, 0, -.01, 0].
    fn two(first: (&[f64; 4], &[f64; 4]), second: (&[f64; 4], &[f64; 4])) -> Vec<f64> {
        let mut m = Vec::new();
        for (a, b) in [first, second] {
            for i in 0..4 {
                m.push(a[i]);
                m.push(b[i]);
            }
        }
        m
    }

    #[test]
    fn the_in_sample_winner_that_always_loses_out_of_sample_is_pbo_one() {
        // block 0: A good, B bad; block 1: A bad, B good. Each half picks the trial that is worst
        // in the other half: rank 1 of 2, w = 1/3, logit = ln(1/2) < 0, PBO = 1.
        let (g, b) = ([0.02, 0.01, 0.02, 0.01], [-0.01, 0.0, -0.01, 0.0]);
        let r = cscv_pbo(&two((&g, &b), (&b, &g)), 8, 2, 2, 1).unwrap();
        assert_eq!(r.pbo, 1.0);
        assert_eq!(r.selected, vec![0, 1]);
        assert!(r.logits.iter().all(|l| (l - 0.5f64.ln()).abs() < 1e-15));
    }

    #[test]
    fn a_trial_best_everywhere_is_pbo_zero() {
        // A good in both blocks: rank 2 of 2, w = 2/3, logit = ln 2 > 0, PBO = 0
        let (g, b) = ([0.02, 0.01, 0.02, 0.01], [-0.01, 0.0, -0.01, 0.0]);
        let r = cscv_pbo(&two((&g, &b), (&g, &b)), 8, 2, 2, 1).unwrap();
        assert_eq!(r.pbo, 0.0);
        assert!(r.logits.iter().all(|l| (l - 2f64.ln()).abs() < 1e-15));
    }

    #[test]
    fn tied_trials_take_the_first_maximum_and_an_averaged_rank() {
        // trials [A, A, B], A = good and B = bad in every block, 16 rows, S = 4 (6 combinations).
        // The two A columns are summed identically, so they tie exactly in and out of sample.
        // n* = 0 in every combination (the first maximum; `>=` would pick 1). A's OOS rank is
        // 1 + (2 + 1)/2 = 2.5 of 3, w = 2.5/4 = 0.625, logit = ln(5/3) > 0, PBO = 0. Ranked by the
        // minimum instead, the rank is 2, w = 1/2, logit = 0 and PBO = 1 (replayed in float64).
        let (g, b) = ([0.02, 0.01, 0.02, 0.01], [-0.01, 0.0, -0.01, 0.0]);
        let mut m = Vec::with_capacity(48);
        for _ in 0..4 {
            for i in 0..4 {
                m.extend([g[i], g[i], b[i]]);
            }
        }
        let r = cscv_pbo(&m, 16, 3, 4, 1).unwrap();
        assert_eq!(r.selected, vec![0; 6]);
        assert!(r
            .logits
            .iter()
            .all(|l| (l - (5.0f64 / 3.0).ln()).abs() < 1e-15));
        assert_eq!(r.pbo, 0.0);
    }

    #[test]
    fn a_constant_trial_is_never_selected() {
        // trial 0 varies (mean ~0), trial 1 is constant. Row-by-row sums leave the constant a
        // residue of ~6e-16 s2 (0.0001) and ~9e-16 s2 (pi/1000); with `var > 0` it would get an
        // enormous finite Sharpe and win every in-sample argmax (checked in float64 on this data).
        // The relative rule makes it NaN -> -inf: trial 0 wins everywhere, ranks 2 of 2, PBO = 0.
        for c in [0.0001, std::f64::consts::PI / 1000.0, 0.0] {
            let mut m = Vec::with_capacity(800);
            for i in 0..400 {
                m.push(((i * 7919) % 101) as f64 / 1000.0 - 0.05);
                m.push(c);
            }
            let r = cscv_pbo(&m, 400, 2, 8, 1).unwrap();
            assert!(r.selected.iter().all(|&j| j == 0), "c = {c}");
            assert!(r.is_sharpe.iter().all(|v| v.is_finite()));
            assert_eq!(r.pbo, 0.0);
        }
    }

    #[test]
    fn same_answer_on_any_thread_count() {
        let m: Vec<f64> = (0..400)
            .map(|i| ((i * 7919) % 101) as f64 / 1000.0 - 0.05)
            .collect();
        let a = cscv_pbo(&m, 100, 4, 8, 1).unwrap();
        let b = cscv_pbo(&m, 100, 4, 8, 3).unwrap();
        assert_eq!(a.logits, b.logits);
        assert_eq!(a.pbo, b.pbo);
    }

    #[test]
    fn rejects_bad_input() {
        let m = vec![0.01; 200];
        assert!(cscv_pbo(&m, 100, 2, 3, 1).is_err()); // odd S
        assert!(cscv_pbo(&m, 100, 2, 22, 1).is_err()); // over the cap
        assert!(cscv_pbo(&m, 200, 1, 2, 1).is_err()); // one trial
        assert!(cscv_pbo(&m[..14], 7, 2, 2, 1).is_err()); // 7 rows < 4 S
        let mut nan = m.clone();
        nan[5] = f64::NAN;
        assert!(cscv_pbo(&nan, 100, 2, 2, 1).is_err());
    }
}
