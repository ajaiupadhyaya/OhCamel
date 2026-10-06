#!/usr/bin/env bash
#
# Drives deploy/restore.sh through a shim `docker` on PATH and pins the two
# things it does -- the drill and the restore -- against hand-written
# expectations:
#
#   deploy/test/restore_test.sh
#
# Exits non-zero, naming the assertion, if any case disagrees. Needs bash and
# coreutils; no daemon, no image, no container, no network, no credential.
# restore.sh runs in place from this checkout (it sources deploy.sh for the
# market-hours guard and backup.sh for the image tag and never writes into the
# repository); the backup directory it reads is a scratch one under $TMPDIR,
# so /var/backups/ohcamel is never named by a running command here.
#
# Why this exists. A restore is the one operation that replaces the live
# journal, and it is run by hand, once, on a bad day. Everything it can get
# wrong is worse than the outage it is fixing: a drill that mounts the live
# volume "just to read it", a restore from a file nobody verified, a restore
# at 10:00 on a Tuesday, the engine restarted on a copy whose session count
# does not match what was promised, or a dry run that does something. Each is
# a case here:
#
#   D. --drill FILE verifies a COPY in a throwaway container: one `docker run`,
#      --rm, no network, read-only root, as uid 10001, on a scratch directory
#      that is not FILE's own and is removed afterwards; FILE is byte-identical
#      after; the live volume is never named. --dry-run prints those commands
#      and runs nothing. A failing verify is a failing drill.
#   G. --restore refuses inside the 09:25-16:10 America/New_York window on a
#      weekday unless --during-market, before any docker call -- the same
#      functions deploy.sh's guard uses, sourced from it, so the two cannot
#      drift (deploy/test/deploy_guard_test.sh owns the DST cases).
#   R. --restore runs the plan's sequence in order: the drill (a file that
#      fails it is refused BEFORE the engine is stopped), stop ohcamel-live,
#      move desk.db and its -wal and -shm aside INSIDE the volume (nothing is
#      deleted), copy the backup in, chown it to 10001, start the engine,
#      wait for `the first sync succeeded, so N session closes are restored`,
#      then compare /api/desk's `sessions` with the verify count and fail on a
#      mismatch. --dry-run prints every command in that order and runs none.
#   U. the arguments: a file outside the backup directory is refused (the
#      restore container sees that directory as /backups and nothing else);
#      --drill and --restore together, or neither, is a usage error.

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."

script=deploy/restore.sh

pass=0
fail=0
ok() { printf '  ok    %s\n' "$1"; pass=$((pass + 1)); }
no() { printf '  FAIL  %s -- %s\n' "$1" "$2"; fail=$((fail + 1)); }
finish() {
	printf '\n  %d passed, %d failed\n\n' "$pass" "$fail"
	[ "$fail" -eq 0 ] || exit 1
}

missing=0
for f in "$script" deploy/backup.sh deploy/deploy.sh deploy/docker-compose.yml; do
	if [ ! -f "$f" ]; then
		no "$f" "missing"
		missing=1
	fi
done
if [ "$missing" = 1 ]; then finish; fi

scratch=$(mktemp -d "${TMPDIR:-/tmp}/restore-test.XXXXXX")
trap 'rm -rf "$scratch"' EXIT

gid=$(id -g)
log="$scratch/docker.log"
backups="$scratch/backups"
# The file every case restores from: its contents are arbitrary bytes, because
# the shim's journal-verify never opens it -- what is pinned is that restore.sh
# copies it and never writes it.
name=desk-2026-09-27.db
file="$backups/$name"
pristine="$scratch/pristine.db"

# Epochs from deploy_guard_test.sh, derived there by hand (re-derived here:
# 2026-01-01T00:00Z = 1767225600; day 261 is 2026-09-19, a Saturday, so
# 1767225600 + 261*86400 + 15*3600 = 1789830000; day 256 is Monday 2026-09-14,
# and 13:25 UTC is 1767225600 + 256*86400 + 13*3600 + 25*60 = 1789392300):
#   1789830000  "Saturday 15:00 UTC = 11:00 EDT, a weekend" -- allowed
#   1789392300  "Monday 13:25 UTC = 09:25 EDT, the window opens" -- refused
#   1789392240  "Monday 13:24 UTC = 09:24 EDT, just before" -- allowed
saturday=1789830000
monday_0925=1789392300
monday_0924=1789392240

# --- the shim -----------------------------------------------------------------
# One line per invocation: the verb, then every argument shell-quoted. Each
# verb answers the way the real daemon would on a good day, unless a SHIM_*
# variable asks for the bad one.
mkdir -p "$scratch/bin"
cat >"$scratch/bin/docker" <<'SHIM'
#!/usr/bin/env bash
set -u
verb=""
skip=0
for a in "$@"; do
	if [ "$skip" = 1 ]; then skip=0; continue; fi
	case "$a" in
	compose) ;;
	-f | --file | --profile | -p | --project-name | --env-file) skip=1 ;;
	-*) ;;
	*) verb="$a"; break ;;
	esac
done
{
	printf 'verb=%s ::' "$verb"
	printf ' %q' "$@"
	printf '\n'
} >>"$SHIM_LOG"
case "$verb" in
ps)
	[ -n "${SHIM_PS_IMAGE-}" ] && printf '%s\n' "$SHIM_PS_IMAGE"
	exit 0
	;;
run)
	case " $* " in
	*" journal-verify "*)
		# The report Journal.Report.lines prints, counts first, verdict last.
		n=${SHIM_SESSIONS:-3}
		printf 'journal %s\n' "${!#}"
		printf '  %-18s %s\n' "schema version" 1 integrity_check ok \
			sessions "$n rows" marks "12 rows" orders "0 rows" \
			"newest session" 2026-09-26 "newest order" none
		if [ "${SHIM_VERIFY_EXIT:-0}" = 0 ]; then
			echo "  no problem found"
			exit 0
		fi
		echo "  PROBLEM: integrity_check answered database disk image is malformed"
		echo "ohcamel: ${!#} is not a journal this build would restore from" >&2
		exit "$SHIM_VERIFY_EXIT"
		;;
	*" --user 0:0 "*)
		exit "${SHIM_RESTORE_EXIT:-0}"
		;;
	esac
	exit 0
	;;
stop | start)
	exit "${SHIM_STOP_EXIT:-0}"
	;;
logs)
	if [ "${SHIM_LOG_SILENT:-0}" = 0 ]; then
		printf 'journal   the first sync succeeded, so %s session closes are restored into the equity trail\n' "${SHIM_LOG_SESSIONS:-3}"
	fi
	exit 0
	;;
exec)
	printf '{"status":"enabled","venue":"alpaca-paper","sessions":%s,"last_sync":"2026-09-27T06:35:00Z","last_error":null}\n' "${SHIM_API_SESSIONS:-3}"
	exit 0
	;;
esac
exit 0
SHIM
chmod +x "$scratch/bin/docker"

fresh() {
	rm -rf "$backups"
	mkdir -p "$backups"
	printf 'not a database, and never opened by this test\n' >"$file"
	cp "$file" "$pristine"
	: >"$log"
}

# run_restore [VAR=value ...] -- [restore.sh arguments ...]
rc=0
run_restore() {
	local envs=()
	while [ $# -gt 0 ] && [ "$1" != "--" ]; do
		envs+=("$1")
		shift
	done
	[ $# -gt 0 ] && shift
	rc=0
	env -u OHCAMEL_IMAGE -u OHCAMEL_BACKUP_VOLUME -u DEPLOY_NOW -u COMPOSE_PROJECT_NAME \
		PATH="$scratch/bin:$PATH" SHIM_LOG="$log" OHCAMEL_TAG=cafe1234 \
		OHCAMEL_BACKUP_DIR="$backups" RESTORE_WAIT_SECS=1 \
		${envs[@]+"${envs[@]}"} \
		bash "$script" "$@" >"$scratch/out" 2>"$scratch/err" || rc=$?
}

calls() { grep -c . "$log" || true; }
verb_lines() { grep "^verb=$1 " "$log" || true; }
plus_lines() { grep '^+ ' "$scratch/out" || true; }
expect_rc() {
	if [ "$rc" -eq "$2" ]; then
		ok "$1"
	else
		no "$1" "exit $rc, expected $2; stderr: $(head -c 300 "$scratch/err" | tr '\n' ' ')"
	fi
}
expect_eq() {
	if [ "$2" = "$3" ]; then ok "$1"; else no "$1" "got '$2', expected '$3'"; fi
}
expect_has() { # LABEL FILE FIXED-STRING
	if grep -qF -- "$3" "$2"; then ok "$1"; else no "$1" "'$3' not found in: $(head -c 400 "$2" | tr '\n' ' ')"; fi
}
expect_lacks() { # LABEL FILE FIXED-STRING
	if grep -qF -- "$3" "$2"; then no "$1" "'$3' found: $(grep -F -- "$3" "$2" | head -2 | cut -c1-200 | tr '\n' ' ')"; else ok "$1"; fi
}
expect_untouched() {
	if cmp -s "$file" "$pristine"; then ok "$1 FILE is byte-identical"; else no "$1 FILE is byte-identical" "it changed"; fi
	local got
	got=$(cd "$backups" && find . -mindepth 1 | sed 's|^\./||' | sort | tr '\n' ' ' | sed 's/ $//')
	expect_eq "$1 the backup directory holds only FILE" "$got" "$name"
}

# --- D. the drill ---------------------------------------------------------------
fresh
run_restore -- --drill --dry-run "$file"
expect_rc "D1 --drill --dry-run" 0
expect_eq "D1 --drill --dry-run makes no docker call" "$(calls)" "0"
expect_untouched "D1"
# Every printed command reads a copy: it is one of the five shapes below and
# nothing else. The docker line is the only one that runs anything, and it
# runs journal-verify on /drill/NAME inside a --rm container.
n_plus=$(plus_lines | grep -c . || true)
if [ "$n_plus" -ge 3 ]; then
	ok "D2 --drill --dry-run prints its commands ($n_plus lines beginning '+ ')"
else
	no "D2 --drill --dry-run prints its commands" "only $n_plus line(s) begin '+ '; stdout: $(head -c 400 "$scratch/out" | tr '\n' ' ')"
fi
plus_lines | while IFS= read -r line; do
	case "$line" in
	"+ mktemp "* | "+ cp "* | "+ chmod "* | "+ rm -rf "*) ;;
	"+ docker run "*"--rm"*"journal-verify /drill/$name") ;;
	*) echo "  FAIL  D2 a --drill --dry-run line is not one that reads a copy -- $line" ;;
	esac
done >"$scratch/d2"
if grep -q FAIL "$scratch/d2"; then
	cat "$scratch/d2"
	fail=$((fail + 1))
else
	ok "D2 every --drill --dry-run line is mktemp, cp, chmod, rm -rf of the scratch, or the one docker run"
fi
expect_has "D2 the cp reads FILE" "$scratch/out" "+ cp -- $file "
expect_has "D2 the docker run is --network none" "$scratch/out" " --network none "
expect_has "D2 the docker run is --read-only" "$scratch/out" " --read-only "
expect_has "D2 the docker run is uid 10001 in the invoking group" "$scratch/out" " --user 10001:$gid "
expect_has "D2 the docker run is the engine image at the tag" "$scratch/out" " ghcr.io/ajaiupadhyaya/ohcamel:cafe1234 journal-verify /drill/$name"
for word in desk_data /data/ compose stop start chown; do
	expect_lacks "D2 --drill --dry-run never names '$word'" "$scratch/out" "$word"
done

fresh
run_restore -- --drill "$file"
expect_rc "D3 --drill on a file the verify accepts" 0
expect_eq "D3 exactly one docker run" "$(verb_lines run | grep -c . || true)" "1"
expect_eq "D3 and no other docker call" "$(calls)" "1"
line=$(verb_lines run)
for word in " --rm " " --network none " " --read-only " " --user 10001:$gid " " journal-verify /drill/$name"; do
	case "$line" in
	*"$word"*) ok "D3 the run carries$word" ;;
	*) no "D3 the run carries$word" "run was: $(printf '%s' "$line" | cut -c1-300)" ;;
	esac
done
case "$line" in
*" -v "*":/drill "*) ok "D3 the run mounts a directory at /drill" ;;
*) no "D3 the run mounts a directory at /drill" "run was: $(printf '%s' "$line" | cut -c1-300)" ;;
esac
# The mount is the scratch copy's directory, never FILE's own.
mount_src=$(printf '%s\n' "$line" | sed -n 's/.* -v \([^ ]*\):\/drill .*/\1/p')
if [ -n "$mount_src" ] && [ "$mount_src" != "$backups" ] && [ "$mount_src" != "$backups/" ]; then
	ok "D3 the mounted directory ($mount_src) is not the backup directory"
else
	no "D3 the mounted directory is not the backup directory" "mount source: '$mount_src'"
fi
if [ -n "$mount_src" ] && [ ! -e "$mount_src" ]; then
	ok "D3 the scratch directory is removed after the drill"
else
	no "D3 the scratch directory is removed after the drill" "'$mount_src' still exists"
fi
expect_lacks "D3 the run never names the backup directory" "$log" "$backups"
expect_lacks "D3 the run never names desk_data" "$log" "desk_data"
expect_untouched "D3"
expect_has "D3 the report is printed" "$scratch/out" "sessions           3 rows"
expect_has "D3 and the drill says what it verified" "$scratch/out" "3 session closes"

fresh
run_restore SHIM_VERIFY_EXIT=1 --
expect_rc "D4 no arguments is a usage error" 2
run_restore SHIM_VERIFY_EXIT=1 -- --drill "$file"
expect_rc "D4 a file the verify refuses fails the drill" 1
expect_has "D4 and the report is still shown" "$scratch/out" "PROBLEM: integrity_check"
expect_has "D4 and the verdict names the file" "$scratch/err" "$name"
expect_untouched "D4"

fresh
run_restore -- --drill "$backups/absent.db"
expect_rc "D5 a missing file is refused" 1
expect_eq "D5 before any docker call" "$(calls)" "0"
expect_has "D5 and named" "$scratch/err" "absent.db"

# --- G. the market-hours guard ---------------------------------------------------
fresh
run_restore DEPLOY_NOW=$monday_0925 -- --restore --dry-run "$file"
expect_rc "G1 Monday 09:25 EDT: a restore is refused" 1
expect_eq "G1 before any docker call" "$(calls)" "0"
expect_has "G1 the refusal names the window" "$scratch/err" "09:25-16:10"
expect_has "G1 and the override" "$scratch/err" "--during-market"
expect_untouched "G1"

fresh
run_restore DEPLOY_NOW=$monday_0925 -- --restore --dry-run --during-market "$file"
expect_rc "G2 the same instant with --during-market proceeds" 0

fresh
run_restore DEPLOY_NOW=$monday_0924 -- --restore --dry-run "$file"
expect_rc "G3 Monday 09:24 EDT, a minute before the window, proceeds" 0

fresh
run_restore DEPLOY_NOW=$saturday -- --restore --dry-run "$file"
expect_rc "G4 Saturday proceeds" 0

# --- R. the restore sequence ----------------------------------------------------
# The dry run: every command printed, in the plan's order, none run.
fresh
run_restore DEPLOY_NOW=$saturday -- --restore --dry-run "$file"
expect_rc "R1 --restore --dry-run" 0
expect_eq "R1 makes no docker call" "$(calls)" "0"
expect_untouched "R1"
order_ok=1
prev=0
for pat in "docker run .*journal-verify /drill/$name" \
	"docker compose .* stop ohcamel-live" \
	"docker compose .* run .* --user 0:0 .*ohcamel-backup" \
	"docker compose .* start ohcamel-live" \
	"docker compose .* logs " \
	"docker compose .* exec .*ohcamel-live curl .*/api/desk"; do
	at=$(grep -nE "^\+ .*$pat" "$scratch/out" | head -n1 | cut -d: -f1)
	if [ -z "$at" ]; then
		no "R2 --dry-run prints '$pat'" "not found; stdout: $(grep '^+ ' "$scratch/out" | cut -c1-120 | tr '\n' '|')"
		order_ok=0
	elif [ "$at" -le "$prev" ]; then
		no "R2 '$pat' comes after the previous step" "line $at is not after line $prev"
		order_ok=0
	else
		ok "R2 --dry-run prints '$pat' (line $at)"
		prev=$at
	fi
done
[ "$order_ok" = 1 ] && ok "R2 the six steps print in the plan's order"
# What the restore container does, read off the printed command.
restore_cmd=$(grep -E '^\+ .*docker compose .* run .* --user 0:0 ' "$scratch/out" | head -n1)
for word in "/data/desk.db" "desk.db-wal" "desk.db-shm" "pre-restore" "/backups/" "chown 10001:10001"; do
	case "$restore_cmd" in
	*"$word"*) ok "R3 the restore container's script carries '$word'" ;;
	*) no "R3 the restore container's script carries '$word'" "not in: $(printf '%s' "$restore_cmd" | cut -c1-400)" ;;
	esac
done
case "$restore_cmd" in
*" rm "*) no "R3 the restore container deletes nothing" "an rm is in it" ;;
*) ok "R3 the restore container deletes nothing (the old files are moved aside, not removed)" ;;
esac
expect_lacks "R3 --dry-run names live.env nowhere" "$scratch/out" "live.env"

# The real sequence, through the shims.
fresh
run_restore DEPLOY_NOW=$saturday -- --restore "$file"
expect_rc "R4 a restore whose counts agree" 0
got=$(sed -n 's/^verb=\([a-z]*\) .*/\1/p' "$log" | tr '\n' ' ' | sed 's/ $//')
expect_eq "R4 the docker calls, in order: verify, stop, restore, start, logs, exec" "$got" "run stop run start logs exec"
first_run=$(verb_lines run | sed -n 1p)
second_run=$(verb_lines run | sed -n 2p)
case "$first_run" in
*" journal-verify /drill/$name") ok "R4 the first run is the drill" ;;
*) no "R4 the first run is the drill" "$(printf '%s' "$first_run" | cut -c1-200)" ;;
esac
case "$second_run" in
*" --profile backup "*"run "*"--rm "*"-T "*"--user 0:0 "*"--entrypoint sh "*"ohcamel-backup "*) ok "R4 the second run is the backup service as root, entrypoint sh" ;;
*) no "R4 the second run is the backup service as root, entrypoint sh" "$(printf '%s' "$second_run" | cut -c1-300)" ;;
esac
case "$second_run" in
*" $name "*) ok "R4 the restore container is told the file's name" ;;
*) no "R4 the restore container is told the file's name" "$(printf '%s' "$second_run" | rev | cut -c1-200 | rev)" ;;
esac
expect_has "R4 the outcome names the verify count and the engine's" "$scratch/out" "3"
expect_untouched "R4"

fresh
run_restore DEPLOY_NOW=$saturday SHIM_VERIFY_EXIT=1 -- --restore "$file"
expect_rc "R5 a file that fails the drill is refused" 1
expect_eq "R5 and the engine was never stopped" "$(verb_lines stop | grep -c . || true)" "0"
expect_eq "R5 the only docker call was the drill" "$(calls)" "1"
expect_has "R5 and the refusal says so" "$scratch/err" "not restoring"
expect_untouched "R5"

fresh
run_restore DEPLOY_NOW=$saturday SHIM_API_SESSIONS=2 -- --restore "$file"
expect_rc "R6 the engine reporting 2 sessions against a verify count of 3 fails" 1
expect_has "R6 and the message names both counts" "$scratch/err" "3"
expect_has "R6 and the message names both counts (2)" "$scratch/err" "2"
expect_eq "R6 the engine was started back regardless" "$(verb_lines start | grep -c . || true)" "1"

fresh
run_restore DEPLOY_NOW=$saturday SHIM_LOG_SILENT=1 -- --restore "$file"
expect_rc "R7 no first-sync line within the wait fails" 1
expect_has "R7 and says the line it waited for" "$scratch/err" "first sync succeeded"
expect_eq "R7 the engine was started" "$(verb_lines start | grep -c . || true)" "1"

fresh
run_restore DEPLOY_NOW=$saturday SHIM_RESTORE_EXIT=1 -- --restore "$file"
expect_rc "R8 the restore container failing fails the restore" 1
expect_eq "R8 and the engine is started back on whatever is in the volume" "$(verb_lines start | grep -c . || true)" "1"

# --- U. the arguments -------------------------------------------------------------
fresh
outside="$scratch/elsewhere.db"
cp "$file" "$outside"
run_restore DEPLOY_NOW=$saturday -- --restore "$outside"
expect_rc "U1 a file outside the backup directory is refused" 1
expect_eq "U1 before any docker call" "$(calls)" "0"
expect_has "U1 and the refusal names the directory" "$scratch/err" "$backups"

fresh
run_restore -- --drill --restore "$file"
expect_rc "U2 --drill and --restore together is a usage error" 2
run_restore -- "$file"
expect_rc "U2 neither is a usage error" 2
run_restore -- --drill
expect_rc "U2 --drill with no file is a usage error" 2
run_restore -- --frobnicate "$file"
expect_rc "U2 an unknown flag is a usage error" 2
expect_eq "U2 and none of those made a docker call" "$(calls)" "0"

finish
