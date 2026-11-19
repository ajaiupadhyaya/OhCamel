#!/usr/bin/env bash
#
# Drives `deploy/backup.sh --catch-up` through shims (`docker`, `timeout`,
# `systemctl`) and reads the catch-up's systemd units (completion task 3.6):
#
#   deploy/test/backup_catchup_test.sh
#
# The nightly backup fires at 06:30 UTC. A host that is down then -- or that
# restarts while the backup runs -- misses that night's copy. The catch-up
# timer checks within 15 minutes of every boot, and every 15 minutes after,
# whether a backup has finished since the latest 06:30 UTC; if none has, it
# runs the backup, under the same rules as the nightly run:
#
#   U. the units: the catch-up timer fires 5 minutes after boot (at most one
#      minute late), so a missed backup starts within 15 minutes of boot, and
#      again 15 minutes after each check; its service runs backup.sh
#      --catch-up as the ohcamel user, after docker, inside the same 60-minute
#      ceiling as the nightly unit. The nightly timer is no longer
#      Persistent: its boot-time firing would ignore the rules below.
#   R. a backup missed by a reboot runs on the first check after boot: every
#      copy, the export last, one status record, and the run is remembered.
#   N. a backup that finished after the latest 06:30 UTC is not run again; a
#      check before 06:30 UTC looks back to yesterday's 06:30.
#   W. not inside the trading session window (09:25-17:10 New York,
#      weekdays) and not within the export's 60 minutes before a warehouse
#      writer's schedule: it waits, says why, and runs no container; the
#      next check after the window runs it.
#   C. not beside a running backup: another run holding the backup lock (the
#      nightly run, from systemd or cron, or one by hand), or today's export
#      container running, is a clean "nothing started" (exit 0); a docker
#      daemon not answering yet (a check right after boot) waits for the next
#      check; a leftover export container that a restart killed is removed by
#      name, then the run goes ahead (the name would otherwise refuse the run).
#
# Exits non-zero, naming the assertion, if any case disagrees. Needs bash and
# coreutils; no daemon, no image, no container, no network. The lock is
# flock(1) where it exists (Linux CI, the host) and a pid directory where it
# does not (macOS); case C2 holds it from a second process either way. Picked up by the
# lint job's deploy/test/*.sh step with no change to ci.yml.

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."

script=deploy/backup.sh
timer=deploy/systemd/ohcamel-backup-catchup.timer
service=deploy/systemd/ohcamel-backup-catchup.service
nightly_timer=deploy/systemd/ohcamel-backup.timer

pass=0
fail=0
ok() { printf '  ok    %s\n' "$1"; pass=$((pass + 1)); }
no() { printf '  FAIL  %s -- %s\n' "$1" "$2"; fail=$((fail + 1)); }
finish() {
	printf '\n  %d passed, %d failed\n\n' "$pass" "$fail"
	[ "$fail" -eq 0 ] || exit 1
}

for f in "$script" "$timer" "$service" "$nightly_timer" deploy/backup.list deploy/docker-compose.yml; do
	if [ ! -f "$f" ]; then
		no "$f" "missing"
		finish
	fi
done

# --- U: the units ---------------------------------------------------------------
# minutes VALUE: a systemd time span of the forms these units use (5min, 1min, 30s) in seconds.
seconds() {
	case "$1" in
	*min) echo $((${1%min} * 60)) ;;
	*s) echo "${1%s}" ;;
	*) echo 99999 ;;
	esac
}
directive() { sed -n "s/^$1=//p" "$2" | head -n 1; }
boot=$(directive OnBootSec "$timer")
acc=$(directive AccuracySec "$timer")
again=$(directive OnUnitInactiveSec "$timer")
if [ -n "$boot" ] && [ -n "$acc" ] && [ $(($(seconds "$boot") + $(seconds "$acc"))) -le 900 ]; then
	ok "U1 the catch-up's first check is within 15 minutes of boot (OnBootSec=$boot, AccuracySec=$acc)"
else
	no "U1 the catch-up's first check is within 15 minutes of boot" "OnBootSec=$boot AccuracySec=$acc"
fi
if [ "$again" = 15min ]; then ok "U2 and it checks again 15 minutes after each check"; else no "U2 recheck every 15 minutes" "OnUnitInactiveSec=$again"; fi
if [ "$(directive Unit "$timer")" = ohcamel-backup-catchup.service ] && grep -q '^WantedBy=timers.target$' "$timer"; then
	ok "U3 the timer starts the catch-up service and is installed under timers.target"
else
	no "U3 the timer's unit" "$(directive Unit "$timer")"
fi
if [ "$(directive ExecStart "$service")" = "/home/ohcamel/OhCamel/deploy/backup.sh --catch-up" ] &&
	[ "$(directive User "$service")" = ohcamel ] && [ "$(directive Type "$service")" = oneshot ] &&
	[ "$(directive TimeoutStartSec "$service")" = "$(directive TimeoutStartSec deploy/systemd/ohcamel-backup.service)" ] &&
	grep -q '^After=docker.service$' "$service" && grep -q '^Requires=docker.service$' "$service"; then
	ok "U4 the service runs backup.sh --catch-up as ohcamel, after docker, inside the nightly unit's ceiling"
else
	no "U4 the catch-up service" "$(grep -E '^(ExecStart|User|Type|TimeoutStartSec|After|Requires)=' "$service" | tr '\n' ' ')"
fi
if grep -q '^Persistent=false$' "$nightly_timer" && ! grep -q '^Persistent=true$' "$nightly_timer"; then
	ok "U5 the nightly timer no longer fires at boot (the catch-up decides when a missed run may start)"
else
	no "U5 the nightly timer is not Persistent" "$(grep '^Persistent' "$nightly_timer")"
fi

# --- the shims (docker, timeout) -----------------------------------------------
scratch=$(mktemp -d "${TMPDIR:-/tmp}/backup-catchup-test.XXXXXX")
trap 'rm -rf "$scratch"' EXIT
log="$scratch/docker.log"
backups="$scratch/backups"
state="$scratch/state"
repo="$scratch/repo"
mkdir -p "$scratch/bin"
cat >"$scratch/bin/docker" <<'SHIM'
#!/usr/bin/env bash
set -u
printf 'docker %s\n' "$*" >>"$SHIM_LOG"
case " $* " in
*" info "*) [ "${SHIM_DOCKER_DOWN:-0}" = 1 ] && exit 1; exit 0 ;;
*" ps -a "*)
	# the export container's state, by name: SHIM_EXPORT_STATE (running, exited) or nothing
	[ -n "${SHIM_EXPORT_STATE-}" ] && printf '%s\n' "$SHIM_EXPORT_STATE"
	exit 0
	;;
*" rm "*) exit 0 ;;
*" ps "*) printf '%s\n' "ghcr.io/x/ohcamel:abc123"; exit 0 ;;
*" ohcamel_quant.warehouse export "*)
	mkdir -p "$OHCAMEL_BACKUP_DIR/warehouse/$SHIM_TODAY"
	: >"$OHCAMEL_BACKUP_DIR/warehouse/$SHIM_TODAY/export.json"
	;;
*" journal-backup "*) : >"$OHCAMEL_BACKUP_DIR/desk-$SHIM_TODAY.db" ;;
*" ohcamel-backup-status "*) ;;
*" run "*) : >"$OHCAMEL_BACKUP_DIR/${!#}" ;;
esac
exit 0
SHIM
cat >"$scratch/bin/timeout" <<'SHIM'
#!/usr/bin/env bash
if [ "$1" = "-k" ]; then shift 2; fi
shift
exec "$@"
SHIM
chmod +x "$scratch/bin/docker" "$scratch/bin/timeout"

fresh() {
	rm -rf "$repo" "$backups" "$state"
	mkdir -p "$repo/deploy" "$backups"
	cp "$script" deploy/backup.list deploy/docker-compose.yml "$repo/deploy/"
	printf '%s\n' '((book catchup-test))' >"$repo/book.sexp"
	printf '%s\n' 'OHCAMEL_DEMO_HOST=catchup-test.invalid' >"$repo/deploy/.env"
	: >"$log"
}

# epoch ISO-UTC: seconds since the epoch, GNU or BSD date.
epoch() { date -u -d "$1" +%s 2>/dev/null || date -u -j -f '%Y-%m-%dT%H:%M:%SZ' "$1" +%s; }

rc=0
# catch_up NOW-ISO [VAR=value ...]
catch_up() {
	local now="$1" today
	shift
	today=${now%%T*}
	rc=0
	env -u OHCAMEL_TAG -u OHCAMEL_IMAGE -u OHCAMEL_BACKUP_VOLUME -u COMPOSE_PROJECT_NAME \
		PATH="$scratch/bin:$PATH" SHIM_LOG="$log" SHIM_TODAY="$today" \
		OHCAMEL_BACKUP_DIR="$backups" OHCAMEL_BACKUP_STATE="$state" BACKUP_NOW="$(epoch "$now")" \
		BACKUP_TODAY="$today" BACKUP_FINISHED_AT="$now" "$@" \
		bash "$repo/deploy/backup.sh" --catch-up >"$scratch/out" 2>"$scratch/err" || rc=$?
}
exports() { grep -c 'ohcamel_quant.warehouse export' "$log" || true; }
records() { grep -c 'ohcamel-backup-status' "$log" || true; }
runs() { grep -c ' run ' "$log" || true; }

# --- R: missed by a reboot ------------------------------------------------------
# Thu 2026-11-19: the host was down from 06:00 to 07:02 UTC (01:00-02:02 New York), so the 06:30 firing never
# happened; the last backup finished Wednesday 06:41. The first check is at boot + 5 min, 07:07 UTC.
fresh
mkdir -p "$state" && printf '%s ok\n' "$(epoch 2026-11-18T06:41:00Z)" >"$state/last-run"
catch_up 2026-11-19T07:07:00Z
if [ "$rc" = 0 ] && [ "$(exports)" = 1 ] && [ "$(records)" = 1 ] && [ -f "$backups/book-2026-11-19.sexp" ] &&
	[ -f "$backups/warehouse/2026-11-19/export.json" ]; then
	ok "R1 a backup missed by a reboot runs on the first check after boot: the copies, the export, one record"
else
	no "R1 the missed backup runs" "rc=$rc exports=$(exports) records=$(records) out=$(tr '\n' ' ' <"$scratch/out") err=$(tr '\n' ' ' <"$scratch/err")"
fi
if grep -q 'catch-up' "$scratch/out" && grep -q '06:30' "$scratch/out"; then ok "R2 it says it is catching up the 06:30 UTC run"; else no "R2 says why it runs" "$(cat "$scratch/out")"; fi
if [ "$(cut -d' ' -f1 "$state/last-run" 2>/dev/null)" = "$(epoch 2026-11-19T07:07:00Z)" ] && grep -q ' ok$' "$state/last-run"; then
	ok "R3 the finished run is remembered (state/last-run)"
else
	no "R3 the run is remembered" "$(cat "$state/last-run" 2>/dev/null)"
fi
# the next check, 15 minutes later: nothing to do
: >"$log"
catch_up 2026-11-19T07:22:00Z
if [ "$rc" = 0 ] && [ ! -s "$log" ] && grep -q 'nothing to catch up' "$scratch/out"; then
	ok "R4 the next check finds the run done and calls nothing (no docker at all)"
else
	no "R4 the next check does nothing" "rc=$rc log=$(tr '\n' ' ' <"$log") out=$(cat "$scratch/out")"
fi

# A run the restart KILLED: started 06:30, no finish recorded, its export container left behind (exited).
fresh
mkdir -p "$state" && printf '%s ok\n' "$(epoch 2026-11-18T06:41:00Z)" >"$state/last-run"
catch_up 2026-11-19T07:07:00Z SHIM_EXPORT_STATE=exited
if [ "$rc" = 0 ] && grep -q 'docker rm -f ohcamel-export-2026-11-19' "$log" && [ "$(exports)" = 1 ]; then
	rm_line=$(grep -n 'docker rm -f ohcamel-export-2026-11-19' "$log" | cut -d: -f1)
	export_line=$(grep -n 'ohcamel_quant.warehouse export' "$log" | cut -d: -f1)
	if [ "$rm_line" -lt "$export_line" ]; then
		ok "C1 a leftover export container a restart killed is removed by name before the export runs"
	else
		no "C1 the leftover is removed before the export" "rm at $rm_line, export at $export_line"
	fi
else
	no "C1 the leftover export container" "rc=$rc $(tr '\n' ' ' <"$log")"
fi

# --- N: done already ------------------------------------------------------------
fresh
mkdir -p "$state" && printf '%s ok\n' "$(epoch 2026-11-19T06:44:00Z)" >"$state/last-run"
catch_up 2026-11-19T10:07:00Z
if [ "$rc" = 0 ] && [ ! -s "$log" ]; then ok "N1 a backup that finished after today's 06:30 UTC is not run again"; else no "N1 done already" "rc=$rc $(cat "$log")"; fi
# 05:10 UTC Thursday: the latest slot is Wednesday 06:30, and Wednesday's run finished at 06:41
fresh
mkdir -p "$state" && printf '%s failed\n' "$(epoch 2026-11-18T06:41:00Z)" >"$state/last-run"
catch_up 2026-11-19T05:10:00Z
if [ "$rc" = 0 ] && [ ! -s "$log" ]; then
	ok "N2 before 06:30 UTC the check looks back to yesterday's slot; a run that finished (even failed) is not missed"
else
	no "N2 yesterday's slot" "rc=$rc $(cat "$log")"
fi
# no run ever recorded (a fresh install), at a quiet hour: it runs
fresh
catch_up 2026-11-19T07:07:00Z
if [ "$rc" = 0 ] && [ "$(exports)" = 1 ]; then ok "N3 with no run recorded at all, the first check runs one"; else no "N3 no record" "rc=$rc"; fi

# --- W: the windows -------------------------------------------------------------
fresh
mkdir -p "$state" && printf '%s ok\n' "$(epoch 2026-11-18T06:41:00Z)" >"$state/last-run"
# Thu 2026-11-19 15:00 UTC = 10:00 EST, inside the session window
catch_up 2026-11-19T15:00:00Z
if [ "$rc" = 0 ] && [ "$(runs)" = 0 ] && ! grep -q '^docker' "$log" && grep -q 'session window' "$scratch/out"; then
	ok "W1 inside the trading session window it waits, says so and starts nothing"
else
	no "W1 the session window" "rc=$rc out=$(cat "$scratch/out") log=$(tr '\n' ' ' <"$log")"
fi
[ ! -e "$state/last-run.new" ] && [ "$(cut -d' ' -f1 "$state/last-run")" = "$(epoch 2026-11-18T06:41:00Z)" ] &&
	ok "W2 a held check records no run" || no "W2 a held check records no run" "$(cat "$state/last-run")"
# 17:30 New York (22:30 UTC in November, EST) is past the window, but the 18:20 corporate actions are due
# within the export's hour, so it waits; 19:30 EST (00:30 UTC Friday) has no writer within the hour: it runs
catch_up 2026-11-19T22:30:00Z
if [ "$rc" = 0 ] && [ "$(runs)" = 0 ] && grep -q '18:20' "$scratch/out"; then
	ok "W3 within the export's 60 minutes before a warehouse writer (18:20 New York) it waits and names the writer"
else
	no "W3 a writer due within the hour" "rc=$rc out=$(cat "$scratch/out")"
fi
catch_up 2026-11-20T00:30:00Z
if [ "$rc" = 0 ] && [ "$(exports)" = 1 ]; then ok "W4 the first check clear of both runs it"; else no "W4 runs after the window" "rc=$rc out=$(cat "$scratch/out")"; fi
# summer time: Thu 2026-07-16 13:20 UTC = 09:20 EDT, before the window; 13:25 UTC = 09:25 EDT, inside it
fresh
mkdir -p "$state" && printf '%s ok\n' "$(epoch 2026-07-15T06:41:00Z)" >"$state/last-run"
catch_up 2026-07-16T13:25:00Z
if [ "$rc" = 0 ] && [ "$(runs)" = 0 ] && grep -q 'session window' "$scratch/out"; then ok "W5 the window is New York time in summer too (09:25 EDT = 13:25 UTC)"; else no "W5 EDT" "rc=$rc $(cat "$scratch/out")"; fi
# a Saturday at 10:00 New York: no session, no writer within the hour -- it runs
fresh
mkdir -p "$state" && printf '%s ok\n' "$(epoch 2026-11-20T06:41:00Z)" >"$state/last-run"
catch_up 2026-11-21T15:00:00Z
if [ "$rc" = 0 ] && [ "$(exports)" = 1 ]; then ok "W6 a weekend daytime check runs it"; else no "W6 weekend" "rc=$rc $(cat "$scratch/out")"; fi

# --- C: beside a running backup -------------------------------------------------
fresh
mkdir -p "$state" && printf '%s ok\n' "$(epoch 2026-11-18T06:41:00Z)" >"$state/last-run"
# another run holds the lock: a second process takes it (backup.sh is sourceable) and sleeps with it
OHCAMEL_BACKUP_STATE="$state" bash -c 'source "$1"; backup_lock || exit 9; exec sleep 30' holder "$repo/deploy/backup.sh" &
holder=$!
sleep 1
catch_up 2026-11-19T07:07:00Z
if [ "$rc" = 0 ] && [ "$(runs)" = 0 ] && grep -q 'already running' "$scratch/out"; then
	ok "C2 while another backup run holds the lock (the nightly run, by systemd or cron), the catch-up starts nothing and exits 0"
else
	no "C2 another run holds the lock" "rc=$rc out=$(cat "$scratch/out") log=$(tr '\n' ' ' <"$log")"
fi
kill "$holder" 2>/dev/null || true
wait "$holder" 2>/dev/null || true
catch_up 2026-11-19T07:07:00Z
if [ "$rc" = 0 ] && [ "$(exports)" = 1 ]; then ok "C2b once that run is gone, the next check runs the backup"; else no "C2b the lock is free again" "rc=$rc out=$(cat "$scratch/out")"; fi
fresh
mkdir -p "$state" && printf '%s ok\n' "$(epoch 2026-11-18T06:41:00Z)" >"$state/last-run"
catch_up 2026-11-19T07:07:00Z SHIM_DOCKER_DOWN=1
if [ "$rc" = 0 ] && [ "$(runs)" = 0 ] && grep -q 'not answering' "$scratch/out" &&
	[ "$(cut -d' ' -f1 "$state/last-run")" = "$(epoch 2026-11-18T06:41:00Z)" ]; then
	ok "C6 right after boot, a docker daemon that does not answer yet is waited for: nothing started, nothing remembered"
else
	no "C6 docker not answering" "rc=$rc out=$(cat "$scratch/out")"
fi
fresh
mkdir -p "$state" && printf '%s ok\n' "$(epoch 2026-11-18T06:41:00Z)" >"$state/last-run"
catch_up 2026-11-19T07:07:00Z SHIM_EXPORT_STATE=running
if [ "$rc" = 0 ] && [ "$(runs)" = 0 ] && ! grep -q 'docker rm' "$log" && grep -q 'ohcamel-export-2026-11-19' "$scratch/out"; then
	ok "C3 while today's export container runs, the catch-up starts no second export and exits 0"
else
	no "C3 an export is running" "rc=$rc out=$(cat "$scratch/out") log=$(tr '\n' ' ' <"$log")"
fi
# and the NIGHTLY run beside a running export container: a named, failed step, not docker's name conflict
fresh
rc=0
env -u OHCAMEL_TAG PATH="$scratch/bin:$PATH" SHIM_LOG="$log" SHIM_TODAY=2026-11-19 SHIM_EXPORT_STATE=running \
	OHCAMEL_BACKUP_DIR="$backups" OHCAMEL_BACKUP_STATE="$state" BACKUP_TODAY=2026-11-19 \
	BACKUP_FINISHED_AT=2026-11-19T06:31:00Z bash "$repo/deploy/backup.sh" >"$scratch/out" 2>"$scratch/err" || rc=$?
if [ "$rc" = 1 ] && [ "$(exports)" = 0 ] && grep -q 'already running' "$scratch/err" && grep -q ' warehouse' "$log"; then
	ok "C4 the nightly run beside a running export fails that step by name and starts no second export"
else
	no "C4 nightly beside a running export" "rc=$rc err=$(tr '\n' ' ' <"$scratch/err")"
fi
# a held or skipped check with a missing backup directory runs no container either
fresh
rm -rf "$backups"
catch_up 2026-11-19T07:07:00Z
if [ "$rc" = 0 ] && [ ! -s "$log" ] && grep -q 'does not exist' "$scratch/out"; then
	ok "C5 a missing backup directory is said and left to the nightly run's record (no container every 15 minutes)"
else
	no "C5 missing directory" "rc=$rc out=$(cat "$scratch/out") log=$(tr '\n' ' ' <"$log")"
fi

finish
