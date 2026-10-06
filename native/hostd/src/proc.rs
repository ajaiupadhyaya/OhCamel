//! Parsers for /proc. Pure functions over the file text, so they are tested
//! against captured text rather than the machine running the tests.

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct CpuTimes {
    pub busy: u64,
    pub idle: u64,
    pub steal: u64,
    pub iowait: u64,
    pub total: u64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct Mem {
    pub total: u64,
    pub available: u64,
    pub swap_total: u64,
    pub swap_free: u64,
}

/// The aggregate `cpu ` line of /proc/stat, in clock ticks.
pub fn parse_stat(text: &str) -> Option<CpuTimes> {
    let line = text.lines().find(|l| l.starts_with("cpu "))?;
    let v: Vec<u64> = line
        .split_whitespace()
        .skip(1)
        .map(|x| x.parse().ok())
        .collect::<Option<_>>()?;
    if v.len() < 8 {
        return None;
    }
    // guest and guest_nice are already inside user/nice: count only the first eight.
    let total: u64 = v[..8].iter().sum();
    let idle = v[3] + v[4];
    Some(CpuTimes {
        busy: total - idle,
        idle,
        steal: v[7],
        iowait: v[4],
        total,
    })
}

/// (busy, steal, iowait) as fractions of all CPUs between two samples;
/// `None` when no time passed (or the counters went backwards).
pub fn fraction(a: CpuTimes, b: CpuTimes) -> Option<(f64, f64, f64)> {
    let dt = b.total.checked_sub(a.total)?;
    if dt == 0 {
        return None;
    }
    let d = |x: u64, y: u64| y.saturating_sub(x) as f64 / dt as f64;
    Some((
        d(a.busy, b.busy),
        d(a.steal, b.steal),
        d(a.iowait, b.iowait),
    ))
}

/// /proc/meminfo, converted from kB to bytes. MemTotal and MemAvailable are
/// required; a host with no swap reports 0 for both swap fields.
pub fn parse_meminfo(text: &str) -> Option<Mem> {
    let kb = |key: &str| -> Option<u64> {
        text.lines()
            .find(|l| l.starts_with(key))?
            .split_whitespace()
            .nth(1)?
            .parse::<u64>()
            .ok()
            .map(|k| k * 1024)
    };
    Some(Mem {
        total: kb("MemTotal:")?,
        available: kb("MemAvailable:")?,
        swap_total: kb("SwapTotal:").unwrap_or(0),
        swap_free: kb("SwapFree:").unwrap_or(0),
    })
}

/// The one-minute load average, the first field of /proc/loadavg.
pub fn parse_load1(text: &str) -> Option<f64> {
    text.split_whitespace().next()?.parse().ok()
}

/// The number of CPUs: the `cpuN` lines of /proc/stat.
pub fn parse_cpus(text: &str) -> usize {
    text.lines()
        .filter(|l| {
            l.strip_prefix("cpu")
                .and_then(|rest| {
                    rest.split_whitespace()
                        .next()
                        .filter(|_| !rest.starts_with(' '))
                })
                .is_some_and(|n| !n.is_empty() && n.chars().all(|c| c.is_ascii_digit()))
        })
        .count()
}

#[cfg(test)]
mod tests {
    use super::*;
    // Captured from a 2-vCPU droplet. Fields: user nice system idle iowait irq softirq steal guest guest_nice
    const STAT_A: &str = "cpu  100 0 50 800 20 0 10 20 0 0\ncpu0 50 0 25 400 10 0 5 10 0 0\n";
    const STAT_B: &str = "cpu  160 0 70 900 20 0 10 40 0 0\n";

    #[test]
    fn stat_splits_busy_idle_steal() {
        let a = parse_stat(STAT_A).unwrap();
        // idle = idle + iowait = 800 + 20; total = user..steal = 100+0+50+800+20+0+10+20 = 1000
        assert_eq!(
            a,
            CpuTimes {
                busy: 180,
                idle: 820,
                steal: 20,
                iowait: 20,
                total: 1000
            }
        );
    }

    #[test]
    fn fraction_is_delta_over_delta() {
        let (a, b) = (parse_stat(STAT_A).unwrap(), parse_stat(STAT_B).unwrap());
        // total 1000 -> 1200 (Δ200); busy 180 -> 280 (Δ100); steal 20 -> 40 (Δ20); iowait Δ0
        assert_eq!(fraction(a, b), Some((0.5, 0.1, 0.0)));
        assert_eq!(fraction(a, a), None); // no time passed
    }

    #[test]
    fn meminfo_in_bytes() {
        let t = "MemTotal:        4008992 kB\nMemFree:  100 kB\nMemAvailable:    1494016 kB\nSwapTotal:       2097148 kB\nSwapFree:        1860604 kB\n";
        let m = parse_meminfo(t).unwrap();
        assert_eq!(m.total, 4008992 * 1024);
        assert_eq!(m.available, 1494016 * 1024);
        assert_eq!(m.swap_total - m.swap_free, (2097148 - 1860604) * 1024); // 231 MiB used
    }

    #[test]
    fn load1_is_the_first_field() {
        assert_eq!(parse_load1("0.17 0.21 0.23 1/234 5678\n"), Some(0.17));
        assert_eq!(parse_load1(""), None);
    }

    #[test]
    fn cpus_counts_the_numbered_lines() {
        // STAT_A has the aggregate line and one cpu0 line: one CPU.
        assert_eq!(parse_cpus(STAT_A), 1);
        assert_eq!(parse_cpus("cpu  1 2\ncpu0 1\ncpu1 1\nintr 5\nctxt 9\n"), 2);
        assert_eq!(parse_cpus(""), 0);
    }
}
