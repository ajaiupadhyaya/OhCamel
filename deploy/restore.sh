#!/usr/bin/env bash
#
# The drill, and the restore, of the desk's journal from a backup.
#
#   deploy/restore.sh --drill FILE   [--dry-run]
#   deploy/restore.sh --restore FILE [--dry-run] [--during-market]
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
#      first. Refused on a weekday inside 09:25-16:10 America/New_York unless
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

  --drill     verify a COPY of FILE with `ohcamel journal-verify` in a
              throwaway container; the live volume is never touched
  --restore   stop ohcamel-live, move its journal aside inside the volume,
              copy FILE in, start the engine, wait for its first sync, and
              check /api/desk's session count against the drill's
  --dry-run   print every command and run none
  --during-market   override the weekday 09:25-16:10 America/New_York refusal
EOF
	exit 2
}

restore_main() {
	local mode="" file="" dry=0 during=0 arg
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
			restore_err "refusing a restore -- it is $hhmm America/New_York time (ISO weekday $dow), inside the 09:25-16:10 trading window; stopping the engine drops the one allowed stream and forces reconciliation; pass --during-market to override"
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
