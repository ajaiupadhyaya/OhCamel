//! Deterministic parallel filling of simulation output (contract II.4).
//!
//! Output is cut into chunks of `chunk_rows` rows; chunk c belongs to worker
//! t = c mod threads, and worker t draws from ChaCha20 seeded with `seed` on
//! stream t (`set_stream(thread_index)`). The assignment is static -- no work
//! stealing decides who fills what -- so the same (seed, threads) gives
//! bit-identical output on any machine, with any scheduling.
use rand::SeedableRng;
use rand_chacha::ChaCha20Rng;
use rayon::prelude::*;

/// Rows (paths, replications) per chunk: compute plan A2's 16k.
pub const CHUNK_ROWS: usize = 16_384;
/// The largest output a kernel accepts: 10M float64 = 80 MB.
pub const MAX_ROWS: usize = 10_000_000;
pub const MAX_THREADS: usize = 64;
/// The longest horizon, in sessions (ten years).
pub const MAX_HORIZON: usize = 2_520;

#[derive(Clone, Copy, Debug)]
pub struct Sim {
    pub horizon: usize,
    pub n_paths: usize,
    pub seed: u64,
    pub threads: usize,
}

impl Sim {
    pub fn validate(&self) -> Result<(), String> {
        if self.horizon == 0 || self.horizon > MAX_HORIZON {
            return Err(format!(
                "horizon must be in 1..={MAX_HORIZON}; got {}",
                self.horizon
            ));
        }
        if self.n_paths == 0 || self.n_paths > MAX_ROWS {
            return Err(format!(
                "n_paths must be in 1..={MAX_ROWS}; got {}",
                self.n_paths
            ));
        }
        check_threads(self.threads)
    }
}

pub fn check_threads(threads: usize) -> Result<(), String> {
    if threads == 0 || threads > MAX_THREADS {
        Err(format!(
            "threads must be in 1..={MAX_THREADS}; got {threads}"
        ))
    } else {
        Ok(())
    }
}

/// A rayon pool of `threads` workers for the RNG-free kernels.
pub fn pool(threads: usize) -> Result<rayon::ThreadPool, String> {
    check_threads(threads)?;
    rayon::ThreadPoolBuilder::new()
        .num_threads(threads)
        .build()
        .map_err(|e| e.to_string())
}

pub fn fill_rows<F>(
    out: &mut [f64],
    row_len: usize,
    chunk_rows: usize,
    seed: u64,
    threads: usize,
    f: F,
) -> Result<(), String>
where
    F: Fn(&mut ChaCha20Rng, &mut [f64]) + Sync,
{
    check_threads(threads)?;
    if row_len == 0 || chunk_rows == 0 || out.len() % row_len != 0 {
        return Err("fill_rows: output length must be a multiple of a positive row length".into());
    }
    let mut lanes: Vec<Vec<&mut [f64]>> = (0..threads).map(|_| Vec::new()).collect();
    for (c, chunk) in out.chunks_mut(chunk_rows * row_len).enumerate() {
        lanes[c % threads].push(chunk);
    }
    pool(threads)?.install(|| {
        lanes.into_par_iter().enumerate().for_each(|(t, chunks)| {
            let mut rng = ChaCha20Rng::seed_from_u64(seed);
            rng.set_stream(t as u64);
            for chunk in chunks {
                f(&mut rng, chunk);
            }
        });
    });
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use rand::Rng;

    fn draw(seed: u64, threads: usize, n: usize) -> Vec<f64> {
        let mut v = vec![-1.0; n];
        fill_rows(&mut v, 1, 1000, seed, threads, |rng, ch| {
            for x in ch.iter_mut() {
                *x = rng.gen::<f64>();
            }
        })
        .unwrap();
        v
    }

    #[test]
    fn same_seed_and_threads_is_bit_identical() {
        assert_eq!(draw(7, 2, 5000), draw(7, 2, 5000));
    }

    #[test]
    fn thread_count_changes_the_stream() {
        assert_ne!(draw(7, 1, 5000), draw(7, 2, 5000));
    }

    #[test]
    fn every_slot_is_written() {
        assert!(draw(1, 3, 4321).iter().all(|x| (0.0..1.0).contains(x)));
    }

    #[test]
    fn chunks_go_round_robin_to_streams() {
        // threads = 2, chunk = 1000 rows: chunk 0 (rows 0..1000) is stream 0's
        // first draws, chunk 1 (rows 1000..2000) stream 1's first draws.
        let v = draw(9, 2, 2000);
        let mut r0 = ChaCha20Rng::seed_from_u64(9);
        r0.set_stream(0);
        let mut r1 = ChaCha20Rng::seed_from_u64(9);
        r1.set_stream(1);
        assert_eq!(v[0], r0.gen::<f64>());
        assert_eq!(v[1000], r1.gen::<f64>());
    }

    #[test]
    fn rejects_bad_threads_and_sims() {
        assert!(check_threads(0).is_err());
        assert!(check_threads(65).is_err());
        let ok = Sim {
            horizon: 10,
            n_paths: 1000,
            seed: 1,
            threads: 2,
        };
        assert!(ok.validate().is_ok());
        assert!(Sim { horizon: 0, ..ok }.validate().is_err());
        assert!(Sim {
            horizon: 2521,
            ..ok
        }
        .validate()
        .is_err());
        assert!(Sim { n_paths: 0, ..ok }.validate().is_err());
        assert!(Sim {
            n_paths: 10_000_001,
            ..ok
        }
        .validate()
        .is_err());
        let mut v = vec![0.0; 10];
        assert!(fill_rows(&mut v, 3, 1, 1, 1, |_, _| {}).is_err()); // 10 % 3 != 0
    }
}
