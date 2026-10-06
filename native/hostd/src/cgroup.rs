//! cgroup v2 readers for the three slices of the compute plan's I.3
//! (ohcamel-rt.slice, ohcamel-web.slice, ohcamel-batch.slice). The text
//! parsers are pure; `read_group` is the only function that touches a file.

use std::path::Path;

/// The `usage_usec N` line of a group's cpu.stat.
pub fn usage_usec(cpu_stat: &str) -> Option<u64> {
    cpu_stat
        .lines()
        .find_map(|l| l.strip_prefix("usage_usec "))?
        .trim()
        .parse()
        .ok()
}

/// (usage_usec, memory.current) of `root/name`, or `None` when the group
/// does not exist or either file cannot be read or parsed -- reported as
/// null, never as a zero.
pub fn read_group(root: &Path, name: &str) -> Option<(u64, u64)> {
    let dir = root.join(name);
    let usage = usage_usec(&std::fs::read_to_string(dir.join("cpu.stat")).ok()?)?;
    let mem = std::fs::read_to_string(dir.join("memory.current"))
        .ok()?
        .trim()
        .parse()
        .ok()?;
    Some((usage, mem))
}

/// A group's CPU use in cores between two samples: Δusage_usec / Δwall_µs.
pub fn cores(usage_a: u64, usage_b: u64, wall_us: u64) -> Option<f64> {
    if wall_us == 0 {
        return None;
    }
    let used = usage_b.checked_sub(usage_a)?;
    Some(used as f64 / wall_us as f64)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    #[test]
    fn usage_usec_reads_its_own_line() {
        assert_eq!(
            usage_usec("usage_usec 123456\nuser_usec 1\nsystem_usec 2\n"),
            Some(123456)
        );
        assert_eq!(usage_usec("user_usec 1\nusage_usec 9\n"), Some(9));
        assert_eq!(usage_usec(""), None);
    }

    #[test]
    fn cores_is_delta_usage_over_delta_wall() {
        // 500_000 µs of CPU over 1_000_000 µs of wall time = half a core.
        assert_eq!(cores(1_000_000, 1_500_000, 1_000_000), Some(0.5));
        // Two whole cores: 2 s of CPU in 1 s.
        assert_eq!(cores(0, 2_000_000, 1_000_000), Some(2.0));
        assert_eq!(cores(5, 6, 0), None); // no wall time passed
        assert_eq!(cores(6, 5, 1_000), None); // a counter reset is not negative use
    }

    #[test]
    fn read_group_reads_cpu_stat_and_memory_current() {
        let root = std::env::temp_dir().join(format!("hostd-cgroup-test-{}", std::process::id()));
        let g = root.join("ohcamel-rt.slice");
        fs::create_dir_all(&g).unwrap();
        fs::write(g.join("cpu.stat"), "usage_usec 42\nuser_usec 40\n").unwrap();
        fs::write(g.join("memory.current"), "28000000\n").unwrap();
        assert_eq!(
            read_group(&root, "ohcamel-rt.slice"),
            Some((42, 28_000_000))
        );
        // A slice that does not exist is None (null on the wire), never 0.
        assert_eq!(read_group(&root, "ohcamel-batch.slice"), None);
        fs::remove_dir_all(&root).unwrap();
    }
}
