//! ohcamel-hostd: the host's CPU, steal, memory and per-slice use, sampled
//! every few seconds and served as JSON (contract II.6 of
//! docs/superpowers/plans/2026-09-24-quant-compute-program.md).
//!
//! `GET /v1/host` answers the snapshot, `GET /healthz` answers `ok`, and
//! everything else is a 404. The Quant API proxies `/v1/host` at
//! `/api/ops/host`, and the worker's admission control (Lane B, B3) reads
//! `mem_available` from it.
//!
//! Environment: `HOSTD_PROC` (default `/host/proc`), `HOSTD_CGROUP`
//! (default `/host/sys/fs/cgroup`), `HOSTD_GROUPS` (comma list, default the
//! three slices of I.3), `HOSTD_BIND` (default `0.0.0.0:9100`) and
//! `HOSTD_INTERVAL_S` (default 5).

mod cgroup;
mod proc;
mod ring;

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use serde::Serialize;

use crate::proc::{CpuTimes, Mem};
use crate::ring::Ring;

const DEFAULT_GROUPS: &str = "ohcamel-rt.slice,ohcamel-web.slice,ohcamel-batch.slice";

/// One slice's use over the last interval: `cpu` in cores, `mem` in bytes.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct GroupUse {
    pub cpu: Option<f64>,
    pub mem: u64,
}

/// One sample: fractions of all CPUs over the interval that ended at `t_ms`,
/// and the memory picture at `t_ms`. A group that could not be read is
/// `null`, never zero.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Sample {
    pub t_ms: u64,
    pub cpu: f64,
    pub steal: f64,
    pub iowait: f64,
    pub load1: Option<f64>,
    pub mem_total: u64,
    pub mem_available: u64,
    pub swap_used: u64,
    pub groups: BTreeMap<String, Option<GroupUse>>,
}

/// The compact form a sample takes in `history`.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct HistoryPoint {
    pub t_ms: u64,
    pub cpu: f64,
    pub steal: f64,
    pub mem_available: u64,
    pub groups_cpu: BTreeMap<String, Option<f64>>,
}

/// The body of `GET /v1/host`. `latest` is null until the first interval has
/// passed (a fraction needs two readings).
#[derive(Debug, Clone, Serialize)]
pub struct Snapshot {
    pub version: u32,
    pub interval_s: u64,
    pub cpus: usize,
    pub now_ms: u64,
    pub latest: Option<Sample>,
    pub history: Vec<HistoryPoint>,
}

/// Everything read from the host at one instant; the raw counters a sample
/// is the difference of.
#[derive(Debug, Clone)]
pub struct Reading {
    pub cpu: CpuTimes,
    pub mem: Mem,
    pub load1: Option<f64>,
    /// (usage_usec, memory.current) per configured group, in order.
    pub groups: Vec<(String, Option<(u64, u64)>)>,
}

/// The sample between two readings `wall_us` microseconds apart, stamped
/// `t_ms`. `None` when no CPU time passed between them.
pub fn build_sample(prev: &Reading, cur: &Reading, wall_us: u64, t_ms: u64) -> Option<Sample> {
    let (cpu, steal, iowait) = proc::fraction(prev.cpu, cur.cpu)?;
    let groups = cur
        .groups
        .iter()
        .map(|(name, now)| {
            let use_ = now.map(|(usage, mem)| {
                let before = prev
                    .groups
                    .iter()
                    .find(|(n, _)| n == name)
                    .and_then(|(_, g)| *g);
                GroupUse {
                    cpu: before.and_then(|(u0, _)| cgroup::cores(u0, usage, wall_us)),
                    mem,
                }
            });
            (name.clone(), use_)
        })
        .collect();
    Some(Sample {
        t_ms,
        cpu,
        steal,
        iowait,
        load1: cur.load1,
        mem_total: cur.mem.total,
        mem_available: cur.mem.available,
        swap_used: cur.mem.swap_total.saturating_sub(cur.mem.swap_free),
        groups,
    })
}

pub fn history_point(s: &Sample) -> HistoryPoint {
    HistoryPoint {
        t_ms: s.t_ms,
        cpu: s.cpu,
        steal: s.steal,
        mem_available: s.mem_available,
        groups_cpu: s
            .groups
            .iter()
            .map(|(n, g)| (n.clone(), g.as_ref().and_then(|g| g.cpu)))
            .collect(),
    }
}

pub fn snapshot(ring: &Ring<Sample>, interval_s: u64, cpus: usize, now_ms: u64) -> Snapshot {
    Snapshot {
        version: 1,
        interval_s,
        cpus,
        now_ms,
        latest: ring.latest().cloned(),
        history: ring.to_vec().iter().map(history_point).collect(),
    }
}

/// (status, content type, body) for one request. Pure, so the routing is
/// tested without a socket.
pub fn route(
    method: &str,
    url: &str,
    snap: impl FnOnce() -> Snapshot,
) -> (u16, &'static str, String) {
    match (method, url) {
        ("GET", "/v1/host") => match serde_json::to_string(&snap()) {
            Ok(body) => (200, "application/json", body),
            Err(e) => (500, "text/plain", format!("serialize: {e}")),
        },
        ("GET", "/healthz") => (200, "text/plain", "ok".to_string()),
        _ => (404, "text/plain", "not found".to_string()),
    }
}

fn read(proc_root: &Path, cgroup_root: &Path, groups: &[String]) -> Option<Reading> {
    let stat = std::fs::read_to_string(proc_root.join("stat")).ok()?;
    let meminfo = std::fs::read_to_string(proc_root.join("meminfo")).ok()?;
    let loadavg = std::fs::read_to_string(proc_root.join("loadavg")).unwrap_or_default();
    Some(Reading {
        cpu: proc::parse_stat(&stat)?,
        mem: proc::parse_meminfo(&meminfo)?,
        load1: proc::parse_load1(&loadavg),
        groups: groups
            .iter()
            .map(|g| (g.clone(), cgroup::read_group(cgroup_root, g)))
            .collect(),
    })
}

fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}

fn env_or(key: &str, default: &str) -> String {
    std::env::var(key)
        .ok()
        .filter(|v| !v.trim().is_empty())
        .unwrap_or_else(|| default.to_string())
}

fn main() {
    let proc_root = PathBuf::from(env_or("HOSTD_PROC", "/host/proc"));
    let cgroup_root = PathBuf::from(env_or("HOSTD_CGROUP", "/host/sys/fs/cgroup"));
    let groups: Vec<String> = env_or("HOSTD_GROUPS", DEFAULT_GROUPS)
        .split(',')
        .map(|g| g.trim().to_string())
        .filter(|g| !g.is_empty())
        .collect();
    let bind = env_or("HOSTD_BIND", "0.0.0.0:9100");
    let interval_s: u64 = env_or("HOSTD_INTERVAL_S", "5")
        .parse()
        .ok()
        .filter(|&s| s > 0)
        .unwrap_or(5);
    let cpus = std::fs::read_to_string(proc_root.join("stat"))
        .map(|t| proc::parse_cpus(&t))
        .unwrap_or(0);

    let ring: Arc<Mutex<Ring<Sample>>> = Arc::new(Mutex::new(Ring::new(ring::CAPACITY)));

    {
        let ring = Arc::clone(&ring);
        std::thread::spawn(move || {
            let mut prev = read(&proc_root, &cgroup_root, &groups).map(|r| (r, Instant::now()));
            loop {
                std::thread::sleep(Duration::from_secs(interval_s));
                let Some(cur) = read(&proc_root, &cgroup_root, &groups) else {
                    eprintln!("hostd: could not read {}", proc_root.display());
                    continue;
                };
                let at = Instant::now();
                if let Some((p, p_at)) = &prev {
                    let wall_us = at.duration_since(*p_at).as_micros() as u64;
                    if let Some(s) = build_sample(p, &cur, wall_us, now_ms()) {
                        if let Ok(mut r) = ring.lock() {
                            r.push(s);
                        }
                    }
                }
                prev = Some((cur, at));
            }
        });
    }

    let server = match tiny_http::Server::http(&bind) {
        Ok(s) => s,
        Err(e) => {
            eprintln!("hostd: cannot bind {bind}: {e}");
            std::process::exit(1);
        }
    };
    eprintln!("hostd: listening on {bind}, sampling every {interval_s}s");
    for request in server.incoming_requests() {
        let method = request.method().as_str().to_string();
        let url = request.url().to_string();
        let (status, ctype, body) = route(&method, &url, || {
            let r = ring
                .lock()
                .map(|r| r.clone())
                .unwrap_or_else(|p| p.into_inner().clone());
            snapshot(&r, interval_s, cpus, now_ms())
        });
        let header = tiny_http::Header::from_bytes(&b"Content-Type"[..], ctype.as_bytes())
            .expect("a static content type is a valid header");
        let response = tiny_http::Response::from_string(body)
            .with_status_code(status)
            .with_header(header);
        let _ = request.respond(response);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn reading(stat: &str, avail_kb: u64, groups: Vec<(&str, Option<(u64, u64)>)>) -> Reading {
        Reading {
            cpu: proc::parse_stat(stat).unwrap(),
            mem: Mem {
                total: 4_008_992 * 1024,
                available: avail_kb * 1024,
                swap_total: 2_097_148 * 1024,
                swap_free: 1_860_604 * 1024,
            },
            load1: Some(0.8),
            groups: groups
                .into_iter()
                .map(|(n, g)| (n.to_string(), g))
                .collect(),
        }
    }

    const STAT_A: &str = "cpu  100 0 50 800 20 0 10 20 0 0\n";
    const STAT_B: &str = "cpu  160 0 70 900 20 0 10 40 0 0\n";

    fn pair() -> (Reading, Reading) {
        let a = reading(
            STAT_A,
            1_500_000,
            vec![
                ("ohcamel-rt.slice", Some((1_000_000, 28_000_000))),
                ("ohcamel-batch.slice", None),
            ],
        );
        let b = reading(
            STAT_B,
            1_494_016,
            vec![
                ("ohcamel-rt.slice", Some((1_100_000, 29_000_000))),
                ("ohcamel-batch.slice", None),
            ],
        );
        (a, b)
    }

    #[test]
    fn sample_is_the_difference_of_two_readings() {
        let (a, b) = pair();
        let s = build_sample(&a, &b, 5_000_000, 1_790_000_000_000).unwrap();
        // proc.rs's hand derivation: Δtotal 200, Δbusy 100, Δsteal 20, Δiowait 0.
        assert_eq!((s.cpu, s.steal, s.iowait), (0.5, 0.1, 0.0));
        assert_eq!(s.t_ms, 1_790_000_000_000);
        assert_eq!(s.load1, Some(0.8));
        // Memory is the current reading's, in bytes.
        assert_eq!(s.mem_total, 4_008_992 * 1024);
        assert_eq!(s.mem_available, 1_494_016 * 1024);
        assert_eq!(s.swap_used, (2_097_148 - 1_860_604) * 1024);
        // rt: 100_000 µs of CPU over 5_000_000 µs of wall = 0.02 cores.
        assert_eq!(
            s.groups["ohcamel-rt.slice"],
            Some(GroupUse {
                cpu: Some(0.02),
                mem: 29_000_000
            })
        );
        // A group that could not be read is null, never zero.
        assert_eq!(s.groups["ohcamel-batch.slice"], None);
        assert!(build_sample(&a, &a, 5_000_000, 0).is_none());
    }

    #[test]
    fn a_group_that_appears_has_memory_but_no_cpu_yet() {
        let (mut a, b) = pair();
        a.groups[0].1 = None;
        let s = build_sample(&a, &b, 5_000_000, 1).unwrap();
        assert_eq!(
            s.groups["ohcamel-rt.slice"],
            Some(GroupUse {
                cpu: None,
                mem: 29_000_000
            })
        );
    }

    #[test]
    fn snapshot_has_exactly_the_contract_keys() {
        let (a, b) = pair();
        let mut r = Ring::new(ring::CAPACITY);
        r.push(build_sample(&a, &b, 5_000_000, 1_790_000_000_000).unwrap());
        let v = serde_json::to_value(snapshot(&r, 5, 2, 1_790_000_000_500)).unwrap();
        let mut got: Vec<&str> = v.as_object().unwrap().keys().map(|k| k.as_str()).collect();
        got.sort();
        let mut want = vec![
            "version",
            "interval_s",
            "cpus",
            "now_ms",
            "latest",
            "history",
        ];
        want.sort();
        assert_eq!(got, want);
        assert_eq!(v["version"], 1);
        assert_eq!(v["interval_s"], 5);
        assert_eq!(v["cpus"], 2);
        assert_eq!(v["now_ms"], 1_790_000_000_500u64);
        assert_eq!(v["latest"]["cpu"], 0.5);
        assert_eq!(v["latest"]["groups"]["ohcamel-rt.slice"]["cpu"], 0.02);
        assert!(v["latest"]["groups"]["ohcamel-batch.slice"].is_null());
        let h = &v["history"][0];
        let mut hk: Vec<&str> = h.as_object().unwrap().keys().map(|k| k.as_str()).collect();
        hk.sort();
        assert_eq!(
            hk,
            vec!["cpu", "groups_cpu", "mem_available", "steal", "t_ms"]
        );
        assert_eq!(h["groups_cpu"]["ohcamel-rt.slice"], 0.02);
        assert!(h["groups_cpu"]["ohcamel-batch.slice"].is_null());
    }

    #[test]
    fn an_empty_ring_has_a_null_latest() {
        let r: Ring<Sample> = Ring::new(ring::CAPACITY);
        let v = serde_json::to_value(snapshot(&r, 5, 2, 9)).unwrap();
        assert!(v["latest"].is_null());
        assert_eq!(v["history"], serde_json::json!([]));
    }

    #[test]
    fn routes() {
        let empty = || snapshot(&Ring::new(1), 5, 2, 9);
        let (st, ct, body) = route("GET", "/v1/host", empty);
        assert_eq!((st, ct), (200, "application/json"));
        let v: serde_json::Value = serde_json::from_str(&body).unwrap();
        assert_eq!(v["version"], 1);
        assert_eq!(
            route("GET", "/healthz", empty),
            (200, "text/plain", "ok".to_string())
        );
        assert_eq!(route("GET", "/", empty).0, 404);
        assert_eq!(route("GET", "/v1/host/x", empty).0, 404);
        assert_eq!(route("POST", "/v1/host", empty).0, 404);
    }
}
