#!/usr/bin/env bash
# The tiers of docs/superpowers/plans/2026-09-24-quant-compute-program.md I.3:
# the engine is protected, the batch tier is preemptible, and the kernel's OOM
# killer reaches the batch tier first -- ohcamel-worker (compute plan Lane B),
# the batch tier's first casualty, before ohcamel-research. Renders the compose file with dummy
# values for the variables it requires and asserts each service's budget.
#
#   deploy/test/resource_budget_test.sh
#
# Needs `docker compose` (the render is client-side: no daemon, no image).
# The live profile's two required env_files are pointed at a scratch dummy
# through an `!override`, as deploy/test/compose_tags_test.sh does, so
# /etc/ohcamel is never read. Compose 5.x renders `mem_limit` in
# `config --format json` as a byte-count STRING ("536870912"); older
# versions print an integer and a hand-written file may say "512m", so
# every memory value goes through parse_mem and is compared in bytes.
#
# ohcamel-quant's ceiling is 1024m: B5 (compute plan Lane B) has landed, so
# heavy requests over their synchronous caps run on ohcamel-worker instead of
# in the API process, and the 1536m ceiling it held until then is lowered
# here and in the compose file (Task 0.3 Step 3).
set -euo pipefail
cd "$(dirname "$0")/../.."

if ! docker compose version >/dev/null 2>&1; then
  echo "docker compose is not available -- this test renders the compose file and cannot run without it"
  exit 1
fi

scratch=$(mktemp -d "${TMPDIR:-/tmp}/resource-budget-test.XXXXXX")
trap 'rm -rf "$scratch"' EXIT
dummy="$scratch/live.env"
printf '%s\n' 'ALPACA_API_KEY=test-dummy-not-a-key' >"$dummy"
override="$scratch/live-env-override.yml"
cat >"$override" <<YAML
services:
  ohcamel-live:
    env_file: !override
      - path: $dummy
        required: true
  ohcamel-research:
    env_file: !override
      - path: $dummy
        required: true
YAML

export OHCAMEL_DEMO_HOST=x OHCAMEL_LIVE_HOST=x OHCAMEL_LIVE_USER=x OHCAMEL_LIVE_HASH=x \
       OHCAMEL_PUBLIC_HOST=x OHCAMEL_ACME_EMAIL=x OHCAMEL_TAG=deadbeef
json="$scratch/render.json"
docker compose -f deploy/docker-compose.yml -f "$override" --profile live --profile demo \
  config --format json >"$json"

python3 - "$json" <<'PY'
import json, re, sys

def parse_mem(v):
    """Bytes from compose's rendering of a memory value: an int, a digit
    string ("536870912", Compose 5.x) or a unit string ("512m")."""
    if v is None:
        return None
    if isinstance(v, int):
        return v
    m = re.fullmatch(r"\s*(\d+)\s*([bkmg]?)b?\s*", str(v).lower())
    if not m:
        return v
    return int(m.group(1)) * {"": 1, "b": 1, "k": 2**10, "m": 2**20, "g": 2**30}[m.group(2)]

with open(sys.argv[1]) as f:
    svc = json.load(f)["services"]
want = {  # service: (slice, cpu_shares, mem bytes, oom_score_adj)
    "ohcamel-live":     ("ohcamel-rt.slice",    4096,  512 * 2**20, -800),
    "ohcamel-demo":     ("ohcamel-rt.slice",    4096,  512 * 2**20, -800),
    "caddy":            ("ohcamel-web.slice",   1024,  128 * 2**20, -500),
    "ohcamel-quant":    ("ohcamel-web.slice",   1024, 1024 * 2**20,    0),
    "ohcamel-research": ("ohcamel-batch.slice",  256,  384 * 2**20,  500),
    "ohcamel-hostd":    ("ohcamel-web.slice",   1024,   32 * 2**20, -500),
    "ohcamel-worker":   ("ohcamel-batch.slice",  128, 1280 * 2**20,  800),
}
bad = []
for name, (slice_, shares, mem, oom) in want.items():
    s = svc.get(name)
    if s is None:
        bad.append(f"{name}: missing"); continue
    # Compose drops a zero oom_score_adj from the render, and 0 is also the
    # kernel's own default, so absent reads as 0.
    got = (s.get("cgroup_parent"), s.get("cpu_shares"), parse_mem(s.get("mem_limit")), s.get("oom_score_adj", 0))
    if got != (slice_, shares, mem, oom):
        bad.append(f"{name}: got {got}, want {(slice_, shares, mem, oom)}")
live = svc.get("ohcamel-live", {})
if parse_mem(live.get("mem_reservation")) != 256 * 2**20:
    bad.append(f"ohcamel-live: mem_reservation {live.get('mem_reservation')}, want 256 MiB")

def parse_dur(v):
    """Seconds from compose's rendering of a duration ("25s", "1m30s") or nanoseconds."""
    if isinstance(v, int):
        return v / 1e9
    total, num = 0.0, ""
    for ch in str(v or ""):
        if ch.isdigit() or ch == ".":
            num += ch
        else:
            total += float(num or 0) * {"h": 3600, "m": 60, "s": 1}.get(ch, 0)
            num = ""
    return total

worker = svc.get("ohcamel-worker", {})
if float(worker.get("cpus", 0) or 0) != 1.75:
    bad.append(f"ohcamel-worker: cpus {worker.get('cpus')}, want 1.75")
if worker.get("command") != ["worker"]:
    bad.append(f"ohcamel-worker: command {worker.get('command')}, want ['worker']")
vols = [(v.get("source"), v.get("target"), bool(v.get("read_only", False))) for v in worker.get("volumes", [])]
if ("quant_data", "/data", False) not in vols:
    bad.append(f"ohcamel-worker: volumes {vols}, want quant_data at /data read-write")
if parse_dur(worker.get("stop_grace_period")) < 25:
    bad.append(f"ohcamel-worker: stop_grace_period {worker.get('stop_grace_period')}, want >= 25s "
               "(SIGTERM gives a running job 15 s, plus the child's terminate/kill)")
ooms = {n: svc[n].get("oom_score_adj", 0) for n in svc if n in want}
if not (ooms.get("ohcamel-live", 0) < ooms.get("ohcamel-quant", 0) < ooms.get("ohcamel-research", 0)
        < ooms.get("ohcamel-worker", 0)):
    bad.append(f"OOM order wrong: {ooms}")
if bad:
    print("resource_budget_test: FAIL"); print("\n".join("  " + b for b in bad)); sys.exit(1)
print("resource_budget_test: ok (%d services)" % len(want))
PY
