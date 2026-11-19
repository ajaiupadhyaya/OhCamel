#!/usr/bin/env bash
#
# The drill, and the restore, of the desk's journal from a backup.
#
#   deploy/restore.sh --drill FILE   [--dry-run]
#   deploy/restore.sh --restore FILE [--dry-run] [--during-market]
#   deploy/restore.sh --warehouse EXPORT_DIR [--to FILE] [--local-python PY] [--dry-run]
#   deploy/restore.sh --warehouse EXPORT_DIR --replace-live --services-stopped [--no-compose-check] [--dry-run]
#
# --warehouse EXPORT_DIR (Task 3.3) rebuilds a DuckDB warehouse from one of
# backup.sh's nightly exports (/var/backups/ohcamel/warehouse/YYYY-MM-DD),
# with the Quant image's `python -m ohcamel_quant.warehouse restore`
# (quant/src/ohcamel_quant/warehouse/export.py): IMPORT DATABASE into a new
# file, every table's count checked against the export's export.json, then
# renamed into place. EXPORT_DIR must be in the backup directory (the
# container sees it as /backups) and must carry export.json -- a partial or
# unverified export is refused before anything runs.
#
#   - By default the restore writes a NEW file, /var/backups/ohcamel/restored/
#     warehouse-YYYY-MM-DD.duckdb (--to FILE, inside the backup directory,
#     chooses another), as uid 10002; the live warehouse is never named.
#   - --replace-live replaces quant_data's /data/warehouse.duckdb, and only with
#     --services-stopped, the operator's word that ohcamel-worker and
#     ohcamel-quant -- the warehouse's only users -- are stopped; compose is
#     asked too (with the engine's tag, which docker-compose.yml needs to
#     parse), and a running one is refused -- and so is a compose that does not
#     answer: the check fails closed. --no-compose-check, typed by the
#     operator, skips the second guard (said on stderr). The live file is moved aside
#     inside the volume as warehouse.duckdb.pre-restore-<UTC>, never deleted.
#     Start them again afterwards: docker compose up -d ohcamel-worker ohcamel-quant.
#   - --local-python PY runs the same restore with a host python that has
#     ohcamel_quant (quant/.venv/bin/python), no docker at all; --to FILE is
#     then required and must not exist. It cannot replace the live warehouse,
#     which is in a volume. deploy/warehouse-drill.sh uses it.
#
# --drill FILE verifies a COPY of FILE and never touches the live volume: the
# file is copied into a scratch directory, a throwaway container of the engine
# image (--rm, no network, read-only root, uid 10001) runs
# `ohcamel journal-verify` on the copy, the report is printed, the scratch is
# removed. Owner step O14 runs this on the first nightly backup; the output --
# schema version, integrity ok, the per-table counts, the newest session --
# is what docs/status.md records as the drill.
#
# --restore FILE replaces the live journal with FILE, in the plan's order:
#
#   0. FILE must be in /var/backups/ohcamel (OHCAMEL_BACKUP_DIR), because the
#      restore container is the backup service, which sees that directory as
#      /backups and nothing else of the host; copy a pulled-back file there
#      first. Refused on a weekday inside 09:25-17:10 America/New_York unless
#      --during-market, by the same two functions deploy.sh's guard uses,
#      sourced from it (deploy/test/deploy_guard_test.sh owns the DST cases).
#      DEPLOY_NOW overrides the clock the way it does there.
#   1. The drill. A file that fails it is refused, before anything is stopped:
#      a restore from an unverified file is the one way to make a bad day
#      worse.
#   2. `docker compose --profile live stop ohcamel-live`.
#   3. Inside the volume, as root in the backup service (the volume's files
#      are uid 10001's): the backup is copied to /data/desk.db.restoring and
#      chowned to 10001 FIRST, so a failed copy leaves the old journal in
#      place; then desk.db and its -wal and -shm are moved aside as
#      *.pre-restore-<UTC>, never deleted; then the copy takes the name.
#   4. `start ohcamel-live`, then up to RESTORE_WAIT_SECS (180) for the engine
#      to log `the first sync succeeded, so N session closes are restored into
#      the equity trail` -- the line bin/main.ml prints when the journal has
#      been read back into the trail.
#   5. /api/desk's `sessions`, read in-container with curl (no password, no
#      credential), must equal the drill's `sessions` count: the engine is
#      then running on the file you restored, and says so.
#
# --dry-run prints every command, in that order, and runs none of them.
#
# deploy/test/restore_test.sh drives this file through a shim `docker` on PATH.

set -euo pipefail

restore_here=$(dirname "${BASH_SOURCE[0]}")
case "$restore_here" in
/*) ;;
*) restore_here="$PWD/$restore_here" ;;
esac

# in_market_window, ny_wall_clock, should_refuse_live_deploy -- the guard's
# own functions, so a restore and a deploy agree on what "inside the market"
# means. deploy.sh guards its main() against being sourced.
# shellcheck source=deploy/deploy.sh
source "$restore_here/deploy.sh"
# engine_tag, and the same BASH_SOURCE guard.
# shellcheck source=deploy/backup.sh
source "$restore_here/backup.sh"

restore_say() { printf 'restore: %s\n' "$*"; }
restore_err() { printf 'restore: %s\n' "$*" >&2; }

# step DRY ARG...: run the command, or print it shell-quoted under --dry-run.
step() {
	local dry="$1"
	shift
	if [ "$dry" = 1 ]; then
		printf '+ %s\n' "$(quoted "$@")"
		return 0
	fi
	"$@"
}

# What the restore container runs, as root, with the live volume at /data and
# the backup directory at /backups: `sh -ec SCRIPT restore NAME TS`.
restore_script='set -eu
name=$1
ts=$2
[ -f "/backups/$name" ] || { echo "restore: /backups/$name is not in the backup directory" >&2; exit 1; }
cp -- "/backups/$name" /data/desk.db.restoring
chown 10001:10001 /data/desk.db.restoring
chmod 0640 /data/desk.db.restoring
for f in /data/desk.db /data/desk.db-wal /data/desk.db-shm; do
	if [ -e "$f" ]; then
		mv -- "$f" "$f.pre-restore-$ts"
	fi
done
mv -- /data/desk.db.restoring /data/desk.db
'

# drill FILE TAG GID DRY: verify a copy of FILE in a throwaway container.
# Prints the report; sets drill_sessions; returns journal-verify's status.
drill_sessions=""
drill() {
	local file="$1" tag="$2" gid="$3" dry="$4"
	local base="${file##*/}" image="${OHCAMEL_IMAGE:-ghcr.io/ajaiupadhyaya/ohcamel}:$tag"
	local scratch rc=0
	local run=(docker run --rm --network none --read-only --tmpfs /tmp
		--security-opt no-new-privileges:true --user "10001:$gid")
	if [ "$dry" = 1 ]; then
		scratch="${TMPDIR:-/tmp}/ohcamel-drill.XXXXXX"
		printf '+ mktemp -d %s\n' "$(printf '%q' "$scratch")"
		printf '+ cp -- %s %s\n' "$(printf '%q' "$file")" "$(printf '%q' "$scratch/$base")"
		printf '+ chmod 0770 %s\n' "$(printf '%q' "$scratch")"
		printf '+ chmod 0640 %s\n' "$(printf '%q' "$scratch/$base")"
		printf '+ %s\n' "$(quoted "${run[@]}" -v "$scratch:/drill" "$image" journal-verify "/drill/$base")"
		printf '+ rm -rf %s\n' "$(printf '%q' "$scratch")"
		return 0
	fi
	scratch=$(mktemp -d "${TMPDIR:-/tmp}/ohcamel-drill.XXXXXX")
	# Group-readable for uid 10001 in the invoking user's group, and the
	# directory group-writable because a WAL-header copy makes the read-only
	# open want a -shm beside it.
	cp -- "$file" "$scratch/$base"
	chmod 0770 "$scratch"
	chmod 0640 "$scratch/$base"
	restore_say "drill: verifying a copy of $file in a throwaway container ($image)"
	"${run[@]}" -v "$scratch:/drill" "$image" journal-verify "/drill/$base" \
		>"$scratch/report" 2>"$scratch/report.err" || rc=$?
	cat "$scratch/report"
	cat "$scratch/report.err" >&2
	drill_sessions=$(sed -n 's/^  sessions  *\([0-9][0-9]*\) rows\{0,1\}$/\1/p' "$scratch/report" | head -n 1)
	rm -rf "$scratch"
	return "$rc"
}

usage() {
	cat >&2 <<'EOF'
usage: deploy/restore.sh --drill FILE [--dry-run]
       deploy/restore.sh --restore FILE [--dry-run] [--during-market]
       deploy/restore.sh --warehouse EXPORT_DIR [--to FILE] [--local-python PY] [--dry-run]
       deploy/restore.sh --warehouse EXPORT_DIR --replace-live --services-stopped [--no-compose-check] [--dry-run]

  --drill     verify a COPY of FILE with `ohcamel journal-verify` in a
              throwaway container; the live volume is never touched
  --restore   stop ohcamel-live, move its journal aside inside the volume,
              copy FILE in, start the engine, wait for its first sync, and
              check /api/desk's session count against the drill's
  --dry-run   print every command and run none
  --during-market   override the weekday 09:25-17:10 America/New_York refusal
  --warehouse  rebuild a warehouse from a nightly export: a new file by
              default; --replace-live (with --services-stopped) for the live one
EOF
	exit 2
}

QUANT_IMAGE=ghcr.io/ajaiupadhyaya/ohcamel-quant

# restore_warehouse ARG...: --warehouse mode (the header).
restore_warehouse() {
	local export="" to="" py="" dry=0 replace=0 stopped=0 compose_check=1
	while [ $# -gt 0 ]; do
		case "$1" in
		--warehouse) [ $# -ge 2 ] || usage; export=$2; shift ;;
		--to) [ $# -ge 2 ] || usage; to=$2; shift ;;
		--local-python) [ $# -ge 2 ] || usage; py=$2; shift ;;
		--dry-run) dry=1 ;;
		--replace-live) replace=1 ;;
		--services-stopped) stopped=1 ;;
		--no-compose-check) compose_check=0 ;;
		*) usage ;;
		esac
		shift
	done
	[ -n "$export" ] || usage
	if [ -n "$py" ] && [ "$replace" = 1 ]; then
		restore_err "--local-python cannot replace the live warehouse: it is in the quant_data volume; use the container (drop --local-python)"
		exit 2
	fi
	local compose="$restore_here/docker-compose.yml" root="$restore_here/.."
	local dir="${OHCAMEL_BACKUP_DIR:-/var/backups/ohcamel}"
	local export_real dir_real day rel target in_target running tag gid ps_out ps_rc ps_err
	if [ ! -d "$export" ]; then
		restore_err "$export is not a directory"
		return 1
	fi
	if [ ! -f "$export/export.json" ]; then
		restore_err "$export has no export.json: it is not a verified export (a partial or failed one never takes its dated name), so it is not restored"
		return 1
	fi
	export_real=$(cd "$export" && pwd -P)
	day=${export_real##*/}

	if [ -n "$py" ]; then
		if [ -z "$to" ]; then
			restore_err "--local-python needs --to FILE, a new file"
			return 1
		fi
		if [ -e "$to" ]; then
			restore_err "$to exists; a restore writes a new file and never overwrites one"
			return 1
		fi
		if [ "$dry" = 1 ]; then
			printf '+ %s\n' "$(quoted "$py" -m ohcamel_quant.warehouse restore "$export" "$to")"
			return 0
		fi
		"$py" -m ohcamel_quant.warehouse restore "$export" "$to" || return 1
		restore_say "warehouse restored from $export into $to (a new file; nothing live was touched)"
		return 0
	fi

	if [ ! -d "$dir" ]; then
		restore_err "$dir does not exist; the restore container reads it as /backups"
		return 1
	fi
	dir_real=$(cd "$dir" && pwd -P)
	case "$export_real/" in
	"$dir_real"/*) rel=${export_real#"$dir_real"/} ;;
	*)
		restore_err "$export is not in $dir, the only host directory the restore container sees (as /backups); copy it there first"
		return 1
		;;
	esac
	tag=$(engine_tag "$compose" "$root") || return 1
	gid=$(id -g)
	local run=(docker compose -f "$compose" --profile backup run --rm -T --user "10002:$gid" --entrypoint python
		ohcamel-backup -m ohcamel_quant.warehouse restore "/backups/$rel")

	if [ "$replace" = 0 ]; then
		target="${to:-$dir/restored/warehouse-$day.duckdb}"
		case "$target" in
		"$dir"/*) in_target="/backups/${target#"$dir"/}" ;;
		*)
			restore_err "--to $target is not in $dir (the container writes only there); use --local-python to restore elsewhere"
			return 1
			;;
		esac
		if [ -e "$target" ]; then
			restore_err "$target exists; a restore writes a new file and never overwrites one"
			return 1
		fi
		if [ "$dry" = 1 ]; then
			printf '+ OHCAMEL_TAG=%s OHCAMEL_IMAGE=%s OHCAMEL_BACKUP_VOLUME=quant_data OHCAMEL_BACKUP_DIR=%s %s\n' \
				"$(printf '%q' "$tag")" "$QUANT_IMAGE" "$(printf '%q' "$dir")" "$(quoted "${run[@]}" "$in_target")"
			return 0
		fi
		mkdir -p "$(dirname "$target")"
		if ! OHCAMEL_TAG="$tag" OHCAMEL_IMAGE="$QUANT_IMAGE" OHCAMEL_BACKUP_VOLUME=quant_data \
			OHCAMEL_BACKUP_DIR="$dir" "${run[@]}" "$in_target"; then
			restore_err "the restore container failed; nothing live was touched"
			return 1
		fi
		restore_say "warehouse restored from $export into $target (a new file; the live warehouse was not touched)"
		return 0
	fi

	# --replace-live: the operator's word, then compose's.
	if [ "$stopped" = 0 ]; then
		restore_err "--replace-live replaces the live warehouse; stop ohcamel-worker and ohcamel-quant first (docker compose stop ohcamel-worker ohcamel-quant) and say so with --services-stopped"
		return 1
	fi
	# compose's answer counts only when compose answered: a failed `ps` (no tag, no
	# daemon, no compose plugin) is a refusal, never "nothing is running". The tag
	# is supplied because docker-compose.yml requires it to parse at all.
	if [ "$compose_check" = 1 ] && [ "$dry" = 1 ]; then
		printf '+ OHCAMEL_TAG=%s %s   # would refuse if ohcamel-worker or ohcamel-quant runs, or if compose does not answer\n' \
			"$(printf '%q' "$tag")" "$(quoted docker compose -f "$compose" ps --status running --services)"
		running=""
	elif [ "$compose_check" = 1 ]; then
		ps_rc=0
		ps_err=$(mktemp "${TMPDIR:-/tmp}/ohcamel-restore-ps.XXXXXX")
		ps_out=$(OHCAMEL_TAG="$tag" docker compose -f "$compose" ps --status running --services 2>"$ps_err") || ps_rc=$?
		if [ "$ps_rc" -ne 0 ]; then
			restore_err "refusing: could not ask compose which services are running (docker compose ps exited $ps_rc: $(head -c 200 "$ps_err" | tr '\n' ' ')) -- with the worker and the API confirmed stopped by other means, pass --no-compose-check"
			rm -f "$ps_err"
			return 1
		fi
		rm -f "$ps_err"
		running=$(printf '%s\n' "$ps_out" | grep -xE 'ohcamel-worker|ohcamel-quant' | tr '\n' ' ' || true)
	else
		restore_err "--no-compose-check: compose was not asked whether ohcamel-worker or ohcamel-quant runs; the operator's --services-stopped is the only guard"
		running=""
	fi
	if [ -n "$running" ]; then
		restore_err "refusing: compose reports ${running% } still running -- the warehouse's users must be stopped before it is replaced (docker compose stop ohcamel-worker ohcamel-quant)"
		return 1
	fi
	if [ "$dry" = 1 ]; then
		printf '+ OHCAMEL_TAG=%s OHCAMEL_IMAGE=%s OHCAMEL_BACKUP_VOLUME=quant_data OHCAMEL_BACKUP_DIR=%s %s\n' \
			"$(printf '%q' "$tag")" "$QUANT_IMAGE" "$(printf '%q' "$dir")" "$(quoted "${run[@]}" /data/warehouse.duckdb --replace)"
		return 0
	fi
	if ! OHCAMEL_TAG="$tag" OHCAMEL_IMAGE="$QUANT_IMAGE" OHCAMEL_BACKUP_VOLUME=quant_data \
		OHCAMEL_BACKUP_DIR="$dir" "${run[@]}" /data/warehouse.duckdb --replace; then
		restore_err "the restore container failed; the live warehouse is as it was (a failed import never replaces it)"
		return 1
	fi
	restore_say "the live warehouse is restored from $export; the previous file is kept in quant_data as warehouse.duckdb.pre-restore-<UTC>. Start its users again: docker compose -f $compose up -d ohcamel-worker ohcamel-quant"
	return 0
}

restore_main() {
	local mode="" file="" dry=0 during=0 arg
	for arg in "$@"; do
		if [ "$arg" = --warehouse ]; then
			restore_warehouse "$@"
			return
		fi
	done
	for arg in "$@"; do
		case "$arg" in
		--drill | --restore)
			[ -z "$mode" ] || usage
			mode=${arg#--}
			;;
		--dry-run) dry=1 ;;
		--during-market) during=1 ;;
		-*) usage ;;
		*)
			[ -z "$file" ] || usage
			file=$arg
			;;
		esac
	done
	[ -n "$mode" ] && [ -n "$file" ] || usage

	local compose="$restore_here/docker-compose.yml" root="$restore_here/.."
	local dir="${OHCAMEL_BACKUP_DIR:-/var/backups/ohcamel}"
	local wait_secs="${RESTORE_WAIT_SECS:-180}"
	local tag gid base file_dir dir_real wall dow hhmm ts since rc=0 restored="" api="" waited=0 line

	if [ ! -f "$file" ]; then
		restore_err "$file does not exist, or is not a file"
		return 1
	fi
	base=${file##*/}

	if [ "$mode" = restore ]; then
		if [ ! -d "$dir" ]; then
			restore_err "$dir does not exist; the restore container reads it as /backups"
			return 1
		fi
		file_dir=$(cd "$(dirname "$file")" && pwd -P)
		dir_real=$(cd "$dir" && pwd -P)
		if [ "$file_dir" != "$dir_real" ]; then
			restore_err "$file is not in $dir, the only host directory the restore container sees (as /backups); copy it there first: cp -- $file $dir/"
			return 1
		fi
		# The market-hours guard, deploy.sh's own. DEPLOY_NOW (a Unix epoch)
		# overrides the clock, as it does there.
		if [ -n "${DEPLOY_NOW:-}" ]; then
			wall=$(ny_wall_clock "$DEPLOY_NOW")
		else
			wall=$(ny_wall_clock)
		fi
		dow=${wall%% *}
		hhmm=${wall##* }
		if should_refuse_live_deploy "$during" "$dow" "$hhmm"; then
			restore_err "refusing a restore -- it is $hhmm America/New_York time (ISO weekday $dow), inside the 09:25-17:10 trading window; stopping the engine drops the one allowed stream and forces reconciliation; pass --during-market to override"
			return 1
		fi
	fi

	tag=$(engine_tag "$compose" "$root") || return 1
	gid=$(id -g)

	# 1. The drill, in both modes.
	if ! drill "$file" "$tag" "$gid" "$dry"; then
		restore_err "$file FAILED the drill -- not restoring from an unverified file"
		return 1
	fi
	if [ "$mode" = drill ]; then
		if [ "$dry" = 1 ]; then
			restore_say "dry run -- the drill would run the commands above, all on a copy; nothing was run"
		else
			restore_say "drill ok: $file verifies, with ${drill_sessions:-?} session closes recorded; the live volume was not touched"
		fi
		return 0
	fi
	if [ "$dry" = 0 ] && [ -z "$drill_sessions" ]; then
		restore_err "the drill's report has no 'sessions' count to compare /api/desk against -- not restoring"
		return 1
	fi

	ts=$(date -u +%Y%m%dT%H%M%SZ)
	since=$(date -u +%Y-%m-%dT%H:%M:%SZ)
	if [ "$dry" = 1 ]; then
		restore_say "dry run -- the restore would run, in this order (nothing below is run):"
	else
		restore_say "restoring $file over the live journal (the old files stay in the volume as *.pre-restore-$ts)"
	fi

	# 2. Stop the engine. A stop that fails has touched nothing.
	if ! step "$dry" docker compose -f "$compose" --profile live stop ohcamel-live; then
		restore_err "could not stop ohcamel-live; nothing was changed"
		return 1
	fi

	# 3. Copy in, move aside, rename -- as root, inside the backup service.
	if [ "$dry" = 1 ]; then
		printf '+ OHCAMEL_TAG=%s OHCAMEL_BACKUP_DIR=%s %s\n' "$(printf '%q' "$tag")" "$(printf '%q' "$dir")" \
			"$(quoted docker compose -f "$compose" --profile backup run --rm -T --user 0:0 --entrypoint sh ohcamel-backup -ec "$restore_script" restore "$base" "$ts")"
	else
		OHCAMEL_TAG="$tag" OHCAMEL_BACKUP_DIR="$dir" docker compose -f "$compose" --profile backup run --rm -T \
			--user 0:0 --entrypoint sh ohcamel-backup -ec "$restore_script" restore "$base" "$ts" || rc=$?
		if [ "$rc" -ne 0 ]; then
			restore_err "the restore container exited $rc; the old journal is in place (a failed copy never replaces it) -- starting the engine back on it"
			docker compose -f "$compose" --profile live start ohcamel-live || true
			return 1
		fi
	fi

	# 4. Start, and wait for the first sync to read the journal back.
	if ! step "$dry" docker compose -f "$compose" --profile live start ohcamel-live; then
		restore_err "could not start ohcamel-live; the restored journal is in the volume, the old one beside it as *.pre-restore-$ts"
		return 1
	fi
	if [ "$dry" = 1 ]; then
		printf '+ %s   # polled every 5 s for up to %s s, for: the first sync succeeded, so N session closes are restored\n' \
			"$(quoted docker compose -f "$compose" --profile live logs --since "$since" --no-log-prefix ohcamel-live)" "$wait_secs"
	else
		while :; do
			line=$(docker compose -f "$compose" --profile live logs --since "$since" --no-log-prefix ohcamel-live 2>/dev/null |
				grep -E 'the first sync succeeded, so [0-9]+ session closes are restored' | head -n 1) || line=""
			if [ -n "$line" ]; then
				restored=$(printf '%s\n' "$line" | sed -n 's/.*succeeded, so \([0-9][0-9]*\) session closes.*/\1/p')
				restore_say "engine: $line"
				break
			fi
			if [ "$waited" -ge "$wait_secs" ]; then
				restore_err "ohcamel-live did not log 'the first sync succeeded, so N session closes are restored' within $wait_secs s; it is running on the restored journal -- read docker compose logs ohcamel-live before trusting it"
				return 1
			fi
			sleep 5
			waited=$((waited + 5))
		done
	fi

	# 5. The engine's own count against the drill's.
	if [ "$dry" = 1 ]; then
		printf '+ %s   # its "sessions" must equal the drill'"'"'s\n' \
			"$(quoted docker compose -f "$compose" --profile live exec -T ohcamel-live curl -fsS -m 10 http://localhost:8081/api/desk)"
		return 0
	fi
	api=$(docker compose -f "$compose" --profile live exec -T ohcamel-live curl -fsS -m 10 http://localhost:8081/api/desk 2>/dev/null |
		grep -oE '"sessions": *[0-9]+' | grep -oE '[0-9]+$' | head -n 1) || api=""
	if [ -z "$api" ]; then
		restore_err "/api/desk did not answer with a sessions count; the engine is up on the restored journal but unconfirmed"
		return 1
	fi
	if [ "$api" != "$drill_sessions" ]; then
		restore_err "/api/desk reports $api sessions but the verified copy held $drill_sessions -- the engine may not be running on the file you restored; read docker compose logs ohcamel-live, and the *.pre-restore-$ts files are still in the volume"
		return 1
	fi
	restore_say "restore ok: /api/desk reports $api sessions, the verified copy held $drill_sessions, and the engine restored ${restored:-?} session closes into the equity trail; the previous journal is in the volume as desk.db.pre-restore-$ts"
	return 0
}

if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
	restore_main "$@"
fi
