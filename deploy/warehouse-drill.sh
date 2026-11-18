#!/usr/bin/env bash
#
# The warehouse restore drill (Task 3.3): export -> wipe -> restore -> regenerate -> pass, and a deliberately
# altered export that must fail. The transcript of a run is what docs/runbooks/restore-drill-2026-11.md records.
#
#   deploy/warehouse-drill.sh --fixture SCRATCH [--python PY]
#       On this machine, no docker, no network: builds the fixture warehouse (the nine committed ETFs), makes a
#       fixture artifact (risk.mc_atlas, small, its clock pinned at the fixtures' last session), exports the
#       warehouse exactly as the nightly backup does, deletes the warehouse, restores it from the export with
#       deploy/restore.sh --warehouse --local-python, re-runs the artifact from the export with
#       tools/regenerate.py (must exit 0), then alters one row of a copy of the export and re-runs again (must
#       NOT exit 0). Everything is written under SCRATCH, which must not exist yet.
#
#   deploy/warehouse-drill.sh --export EXPORT_DIR --artifact ARTIFACT_DIR [--artifacts ROOT] [--python PY]
#                             [--scratch SCRATCH] [--alter TABLE:COLUMN[:WHERE]]
#       The owner's drill, on a copy of last night's backup (deploy/pull-backups.sh brings
#       /var/backups/ohcamel/warehouse/YYYY-MM-DD to the laptop; copy an artifact made by the SAME deployed code
#       out of the quant_data volume beside it -- runbook). Verifies the export's record, restores it into a new
#       file under SCRATCH, and regenerates ARTIFACT_DIR from the export. --alter TABLE:COLUMN[:WHERE] (a value
#       the artifact read, e.g. "bars_daily:adj_close:ticker = 'SPY' AND date = DATE '2026-11-16'") adds the
#       negative check. Neither the export nor the artifact is written.
#
#   PY defaults to quant/.venv/bin/python (it must import ohcamel_quant). Exits non-zero at the first step that
#   does not do what the drill expects, naming it.

set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
repo=$(cd "$here/.." && pwd -P)
py="$repo/quant/.venv/bin/python"
mode="" scratch="" exp_dir="" artifact="" artifacts="" alter=""

usage() {
	sed -n '3,25p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' >&2
	exit 2
}
while [ $# -gt 0 ]; do
	case "$1" in
	--fixture) [ $# -ge 2 ] || usage; mode=fixture; scratch=$2; shift ;;
	--export) [ $# -ge 2 ] || usage; mode=owner; exp_dir=$2; shift ;;
	--artifact) [ $# -ge 2 ] || usage; artifact=$2; shift ;;
	--artifacts) [ $# -ge 2 ] || usage; artifacts=$2; shift ;;
	--scratch) [ $# -ge 2 ] || usage; scratch=$2; shift ;;
	--alter) [ $# -ge 2 ] || usage; alter=$2; shift ;;
	--python) [ $# -ge 2 ] || usage; py=$2; shift ;;
	*) usage ;;
	esac
	shift
done
[ -n "$mode" ] || usage

say() { printf '\n== drill: %s\n' "$*"; }
die() { printf '\n== drill: FAILED -- %s\n' "$*" >&2; exit 1; }
show() { printf '$ %s\n' "$*"; }
now_ms() { "$py" -c 'import time; print(int(time.time() * 1000))'; }

[ -x "$py" ] || die "$py is not an executable python (pass --python)"
"$py" -c 'import ohcamel_quant' 2>/dev/null || die "$py cannot import ohcamel_quant"
export OHCAMEL_QUANT_OFFLINE=1

# alter_row EXPORT TABLE:COLUMN[:WHERE] OUT: OUT is a copy of EXPORT with COLUMN of TABLE multiplied by 1.0001 in
# the rows WHERE selects (default: the newest row by the table's first DATE column, else its first row); every
# other byte is the same.
alter_row() {
	"$py" - "$1" "$2" "$3" <<'PY'
import shutil, sys
from pathlib import Path
import duckdb
from ohcamel_quant.warehouse.export import _load_files
src, spec, out = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
table, col, *rest = spec.split(":", 2)
shutil.copytree(src, out)
f = _load_files(out)[table]
con = duckdb.connect()
where = rest[0] if rest else "rn = 1"
tmp = out / "altered.tmp"
con.execute(f"COPY (SELECT * REPLACE (CASE WHEN {where} THEN \"{col}\" * 1.0001 ELSE \"{col}\" END AS \"{col}\") "
            f"FROM (SELECT *, row_number() OVER () AS rn FROM read_parquet('{f}'))) TO '{tmp}' (FORMAT PARQUET)")
n = con.execute(f"SELECT count(*) FROM (SELECT *, row_number() OVER () AS rn FROM read_parquet('{f}')) WHERE {where}").fetchone()[0]
con.execute(f"COPY (SELECT * EXCLUDE (rn) FROM read_parquet('{tmp}')) TO '{f}' (FORMAT PARQUET)")
tmp.unlink()
print(f"altered {table}.{col} by 1 part in 10^4 where {where} ({n} row(s), {f.name})")
PY
}

regen() { # ARTIFACT EXPORT [ROOT]
	local args=("$repo/tools/regenerate.py" "$1" --warehouse "$2")
	[ -z "${3:-}" ] || args+=(--artifacts "$3")
	show "python tools/regenerate.py $1 --warehouse $2${3:+ --artifacts $3}"
	"$py" "${args[@]}"
}

if [ "$mode" = fixture ]; then
	[ ! -e "$scratch" ] || die "$scratch exists; the drill writes into a new directory"
	mkdir -p "$scratch"
	scratch=$(cd "$scratch" && pwd -P)
	wh="$scratch/live/warehouse.duckdb"
	backups="$scratch/backups"
	day=2026-06-01
	say "1. the warehouse: the fixture warehouse (the committed ETFs, logged as full-history fetches)"
	show "python -m ohcamel_quant.warehouse build-fixture $wh --requested-start 1990-01-01"
	"$py" -m ohcamel_quant.warehouse build-fixture "$wh" --requested-start 1990-01-01
	say "2. a published number: risk.mc_atlas on it, the clock pinned at the fixtures' last session"
	params='{"n_paths": 20000, "member_paths": 20000, "members": ["SPY", "QQQ", "TLT"]}'
	show "python -m ohcamel_quant.jobs.local risk.mc_atlas --artifacts $scratch/artifacts --warehouse $wh --offline --now 2026-06-01T21:00:00Z --params '$params'"
	art=$("$py" -m ohcamel_quant.jobs.local risk.mc_atlas --artifacts "$scratch/artifacts" --warehouse "$wh" \
		--offline --now 2026-06-01T21:00:00Z --params "$params" | tail -n 1)
	"$py" -c 'import json, sys; m = json.load(open(sys.argv[1] + "/manifest.json")); print("artifact", m["kind"], m["id"]); print("  data_hash", m["data_hash"]); print("  seed", m["seed"], " lock_hash", m["lock_hash"], " threads", m["threads"], " kernels", m["kernels"]); print("  reads", m["reads"]["count"], "-", ", ".join(m["reads"]["sources"]), "(reads.json)")' "$art"
	say "3. the nightly export, as backup.sh runs it (read-only EXPORT DATABASE, verified, renamed)"
	show "python -m ohcamel_quant.warehouse export $wh $backups/warehouse --date $day"
	t0=$(now_ms)
	"$py" -m ohcamel_quant.warehouse export "$wh" "$backups/warehouse" --date "$day"
	t1=$(now_ms)
	exp_dir="$backups/warehouse/$day"
	echo "export took $((t1 - t0)) ms; on disk: $(du -sk "$exp_dir" | awk '{print $1}') KiB; warehouse file: $(du -sk "$wh" | awk '{print $1}') KiB"
	say "4. wipe: the warehouse is deleted; only the export is left"
	show "rm $wh"
	rm -f -- "$wh" "$wh.wal"
	[ ! -e "$wh" ] || die "the warehouse is still there"
	say "5. restore, through deploy/restore.sh, into a new file"
	show "deploy/restore.sh --warehouse $exp_dir --local-python $py --to $scratch/restored/warehouse.duckdb"
	OHCAMEL_BACKUP_DIR="$backups" "$here/restore.sh" --warehouse "$exp_dir" --local-python "$py" \
		--to "$scratch/restored/warehouse.duckdb"
	say "6. regenerate the published number from the export"
	regen "$art" "$exp_dir" || die "regenerate did not reproduce the artifact from a good export"
	say "7. the negative check: one altered row in a copy of the export must fail the re-run"
	alter_row "$exp_dir" "bars_daily:adj_close:ticker = 'SPY' AND date = DATE '2026-05-29'" "$scratch/altered/$day"
	rc=0
	regen "$art" "$scratch/altered/$day" || rc=$?
	[ "$rc" -ne 0 ] || die "regenerate passed on an altered export"
	echo "regenerate exited $rc on the altered export, as it must"
	say "PASS -- export, wipe, restore and regenerate on the fixture warehouse; the altered export was caught"
	exit 0
fi

# --export: the owner's drill on last night's backup.
[ -n "$artifact" ] || usage
[ -f "$exp_dir/export.json" ] || die "$exp_dir has no export.json: not a verified export"
[ -f "$artifact/manifest.json" ] || die "$artifact has no manifest.json"
scratch=${scratch:-$(mktemp -d "${TMPDIR:-/tmp}/ohcamel-drill.XXXXXX")}
mkdir -p "$scratch"
say "1. the export's record"
show "python -m ohcamel_quant.warehouse verify $exp_dir"
"$py" -m ohcamel_quant.warehouse verify "$exp_dir"
echo "on disk: $(du -sk "$exp_dir" | awk '{print $1}') KiB"
say "2. restore into a new file (the live warehouse is not on this machine and is never named)"
t0=$(now_ms)
show "deploy/restore.sh --warehouse $exp_dir --local-python $py --to $scratch/restored/warehouse.duckdb"
OHCAMEL_BACKUP_DIR="$(dirname "$(dirname "$exp_dir")")" "$here/restore.sh" --warehouse "$exp_dir" --local-python "$py" \
	--to "$scratch/restored/warehouse.duckdb"
echo "restore took $(($(now_ms) - t0)) ms"
say "3. regenerate the artifact from the export"
regen "$artifact" "$exp_dir" "$artifacts" || die "regenerate did not reproduce $artifact from $exp_dir"
if [ -n "$alter" ]; then
	say "4. the negative check: one altered row of $alter must fail the re-run"
	alter_row "$exp_dir" "$alter" "$scratch/altered/$(basename "$exp_dir")"
	rc=0
	regen "$artifact" "$scratch/altered/$(basename "$exp_dir")" "$artifacts" || rc=$?
	[ "$rc" -ne 0 ] || die "regenerate passed on an altered export (did the artifact read $alter?)"
	echo "regenerate exited $rc on the altered export, as it must"
fi
say "PASS -- $exp_dir restores and reproduces $artifact (scratch: $scratch)"
