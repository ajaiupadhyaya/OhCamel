//! cgroup v2 readers for the three slices of the compute plan's I.3
//! (ohcamel-rt.slice, ohcamel-web.slice, ohcamel-batch.slice). The text
//! parsers are pure; `read_group` is the only function that touches a file.

use std::path::{Path, PathBuf};

/// The `usage_usec N` line of a group's cpu.stat.
pub fn usage_usec(cpu_stat: &str) -> Option<u64> {
    cpu_stat
        .lines()
        .find_map(|l| l.strip_prefix("usage_usec "))?
        .trim()
        .parse()
        .ok()
}

/// The directory of a group relative to the cgroup root. A systemd slice
/// name nests on its dashes, as systemd places it: `ohcamel-rt.slice` lives
/// at `ohcamel.slice/ohcamel-rt.slice` (Docker's systemd cgroup driver, owner
/// check O-3). Any other name (the cgroupfs fallback, `/ohcamel-rt`) is a
/// plain path with its leading '/' stripped, so it stays under the root
/// instead of replacing it.
pub fn group_dir(name: &str) -> PathBuf {
    let name = name.trim_start_matches('/');
    let Some(stem) = name.strip_suffix(".slice") else {
        return PathBuf::from(name);
    };
    if name.contains('/') || stem.is_empty() || stem.split('-').any(str::is_empty) {
        return PathBuf::from(name);
    }
    let parts: Vec<&str> = stem.split('-').collect();
    (1..=parts.len())
        .map(|i| format!("{}.slice", parts[..i].join("-")))
        .collect()
}

/// (usage_usec, memory.current) of the group `name` under `root` (see
/// `group_dir`), or `None` when the group does not exist or either file
/// cannot be read or parsed -- reported as null, never as a zero.
pub fn read_group(root: &Path, name: &str) -> Option<(u64, u64)> {
    let dir = root.join(group_dir(name));
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
    fn group_dir_nests_systemd_slices_on_their_dashes() {
        // systemd treats '-' in a slice name as a hierarchy separator.
        assert_eq!(
            group_dir("ohcamel-rt.slice"),
            PathBuf::from("ohcamel.slice/ohcamel-rt.slice")
        );
        assert_eq!(
            group_dir("a-b-c.slice"),
            PathBuf::from("a.slice/a-b.slice/a-b-c.slice")
        );
        assert_eq!(group_dir("system.slice"), PathBuf::from("system.slice"));
        // cgroupfs names are a plain path under the root, never absolute.
        assert_eq!(group_dir("/ohcamel-rt"), PathBuf::from("ohcamel-rt"));
        assert_eq!(group_dir("ohcamel-rt"), PathBuf::from("ohcamel-rt"));
    }

    #[test]
    fn read_group_finds_a_systemd_slice_nested_under_its_parent() {
        let root = std::env::temp_dir().join(format!("hostd-cgroup-sd-{}", std::process::id()));
        let g = root.join("ohcamel.slice").join("ohcamel-rt.slice");
        fs::create_dir_all(&g).unwrap();
        fs::write(g.join("cpu.stat"), "usage_usec 7\n").unwrap();
        fs::write(g.join("memory.current"), "1000\n").unwrap();
        assert_eq!(read_group(&root, "ohcamel-rt.slice"), Some((7, 1000)));
        fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn read_group_keeps_an_absolute_cgroupfs_name_under_the_root() {
        let root = std::env::temp_dir().join(format!("hostd-cgroup-fs-{}", std::process::id()));
        let g = root.join("ohcamel-rt");
        fs::create_dir_all(&g).unwrap();
        fs::write(g.join("cpu.stat"), "usage_usec 9\n").unwrap();
        fs::write(g.join("memory.current"), "2000\n").unwrap();
        assert_eq!(read_group(&root, "/ohcamel-rt"), Some((9, 2000)));
        fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn read_group_reads_cpu_stat_and_memory_current() {
        let root = std::env::temp_dir().join(format!("hostd-cgroup-test-{}", std::process::id()));
        let g = root.join("ohcamel.slice").join("ohcamel-rt.slice");
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
