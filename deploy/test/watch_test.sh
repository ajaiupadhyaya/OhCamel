#!/usr/bin/env bash
#
# deploy/watch.sh and its systemd units, against canned answers.
#
#   deploy/test/watch_test.sh
#
# Drives the real deploy/watch.sh with shim `docker`, `df` and `curl`
# binaries first on PATH, so nothing touches a daemon, a disk or the network.
# The docker shim answers `docker ps` from a canned table, `docker exec ...
# curl .../api/desk` from a canned /api/desk body, `docker exec ... ls
# /data/signals` from a canned listing, and logs every `docker restart`.
# The clock is OHCAMEL_WATCH_NOW (epoch seconds) -- the shim's now -- so every
# threshold below is plain arithmetic against a stated instant.
#
# What it pins (finish plan Task 18, one hand-derived case per threshold):
#   - the 3-run restart: unhealthy at runs 1 and 2 restarts nothing, run 3
#     restarts exactly once, run 4 while still unhealthy restarts nothing more
#     (the state file records the restart);
#   - the 180 s sync: is_open with last_sync 179 s back is silent, 181 s back
#     alerts once; 181 s with is_open false is silent; 181 s with source
#     "stale" is silent and logs the skip; 181 s with source "venue-clock" but
#     read_at 60 s AFTER now is silent and logs the skip (plan 08972b1);
#   - the 30-minute session rule: next_close_date 2026-09-18 closing 20:00
#     UTC, no session row at 20:29 is silent, at 20:31 alerts;
#   - the 26 h backup rule, on desk-*.db only: 25 h 59 m silent, 26 h 01 m
#     alerts, and a fresh book-*.sexp beside a stale journal copy still alerts;
#   - the 80% disk rule: 80% silent, 81% alerts;
#   - edge triggering: the first failure alerts once, a repeat is silent,
#     recovery alerts once;
#   - last_error, the kill switch, the signal file after 01:00 UTC, and
#     /var/run/reboot-required each alert by name;
#   - Slack only when ops.env holds SLACK_WEBHOOK_URL, the healthchecks.io
#     ping when it holds HEALTHCHECKS_URL, and neither URL is ever printed;
#   - the units: every 5 minutes, as the ohcamel user, running this script.
#
# Exits non-zero, naming the case, if any assertion fails. Picked up by the
# lint job's deploy/test/*.sh step with no change to ci.yml.

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."

script=deploy/watch.sh
service=deploy/systemd/ohcamel-watch.service
timer=deploy/systemd/ohcamel-watch.timer

pass=0
fail=0
ok() { printf '  ok    %s\n' "$1"; pass=$((pass + 1)); }
no() { printf '  FAIL  %s -- %s\n' "$1" "$2"; fail=$((fail + 1)); }

export TZ=UTC
command -v jq >/dev/null 2>&1 || { echo "watch_test: jq is required (deploy/provision.sh installs it)"; exit 1; }

work=$(mktemp -d "${TMPDIR:-/tmp}/watch-test.XXXXXX")
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/bin"

epoch() { jq -n --arg s "$1" '$s | fromdateiso8601'; }

# ---------------------------------------------------------------------------
# Shims
# ---------------------------------------------------------------------------
cat >"$work/bin/docker" <<'EOF'
#!/usr/bin/env bash
log=${SHIM_LOG:?}
printf 'DOCKER %s\n' "$*" >>"$log"
case "${1:-}" in
ps)
	[ "${S_PS_FAIL:-0}" = 1 ] && { echo "Cannot connect to the Docker daemon" >&2; exit 1; }
	cat "$SHIM_DIR/ps"
	;;
exec)
	shift
	name=$1; shift
	case "$*" in
	*"/api/desk"*)
		[ "${S_DESK_FAIL:-0}" = 1 ] && { echo "curl: (7) Failed to connect" >&2; exit 7; }
		cat "$SHIM_DIR/desk.json"
		;;
	*"ls /data/signals"*) cat "$SHIM_DIR/signals" ;;
	*) exit 9 ;;
	esac
	;;
restart) echo "$2" ;;
logs) cat "$SHIM_DIR/logs" 2>/dev/null || true ;;
*) exit 9 ;;
esac
EOF

cat >"$work/bin/df" <<'EOF'
#!/usr/bin/env bash
printf 'DF %s\n' "$*" >>"${SHIM_LOG:?}"
echo "Filesystem     1024-blocks     Used Available Capacity Mounted on"
echo "/dev/vda1         50000000 40000000  10000000      ${S_DISK:-42}% /"
EOF

cat >"$work/bin/curl" <<'EOF'
#!/usr/bin/env bash
# Slack and healthchecks.io. The URL may arrive as an argument or through
# --config FILE (url = "..."); either way it is logged here, never by
# watch.sh itself.
log=${SHIM_LOG:?}
args="$*" url="" data=""
while [ $# -gt 0 ]; do
	case "$1" in
	--config | -K) url=$(sed -n 's/^url = "\(.*\)"$/\1/p' "$2"); shift 2 ;;
	--data-binary | -d) data=$2; shift 2 ;;
	-m | --max-time | -H | --retry) shift 2 ;;
	http://* | https://*) url=$1; shift ;;
	*) shift ;;
	esac
done
printf 'CURL url=%s data=%s args=%s\n' "$url" "$data" "$args" >>"$log"
exit 0
EOF
chmod +x "$work/bin/"*

# ---------------------------------------------------------------------------
# The scene: a healthy host at 2026-09-18 15:00 UTC (market open), every
# check quiet. Each case changes one thing.
# ---------------------------------------------------------------------------
T0=$(epoch 2026-09-18T15:00:00Z)
CLOSE=$(epoch 2026-09-18T20:00:00Z)

state="$work/state"
backups="$work/backups"
mkdir -p "$backups"
book="$work/book.sexp"
cat >"$book" <<'EOF'
; (signals ((strategies (((name commented_out))))))
((positions ((SPY 0.6) (TLT 0.4)))
 (signals
  ((strategies
    (((name exp_a01_spy) (symbols (SPY)) (max_age 3) (sizing advisory) (capital_fraction 0.5))
     ((name exp_a01_tlt) (symbols (TLT)) (max_age 3) (sizing advisory) (capital_fraction 0.5)))))))
EOF
nobook="$work/nobook.sexp"
echo '((positions ((SPY 1.0))))' >"$nobook"

ps_all_healthy() {
	printf '%s\n' \
		"caddy|ohcamel-caddy-1|running|Up 2 hours" \
		"ohcamel-quant|ohcamel-ohcamel-quant-1|running|Up 2 hours (healthy)" \
		"ohcamel-live|ohcamel-ohcamel-live-1|running|Up 2 hours (healthy)" \
		"ohcamel-research|ohcamel-ohcamel-research-1|running|Up 2 hours (healthy)" >"$work/ps"
}

rfc() { jq -rn --argjson e "$1" '$e | strftime("%Y-%m-%dT%H:%M:%S") + ".000000000Z"'; }

# desk NOW [key=value ...]: write /api/desk's body as the engine would at NOW.
desk() {
	local now=$1; shift
	local is_open=true source=venue-clock read_at=$((now - 30)) last_sync=$((now - 20))
	local extra='{}'
	local last_error=null kill='"clear"' close_date=2026-09-18 close=$CLOSE sessions='"2026-09-17"'
	local kv
	for kv in "$@"; do
		case "$kv" in
		is_open=*) is_open=${kv#*=} ;;
		source=*) source=${kv#*=} ;;
		read_at=*) read_at=${kv#*=} ;;
		last_sync=*) last_sync=${kv#*=} ;;
		last_error=*) last_error=${kv#*=} ;;
		kill=*) kill=${kv#*=} ;;
		sessions=*) sessions=${kv#*=} ;;
		extra=*) extra=${kv#*=} ;;
		*) echo "desk: unknown $kv" >&2; exit 2 ;;
		esac
	done
	local sess_json
	sess_json=$(printf '%s' "$sessions" | tr ',' '\n' | sed '/^$/d' | sed 's/^"\(.*\)"$/{"date":"\1","equity_close":100000.0}/' | paste -sd, -)
	cat >"$work/desk.json" <<JSON
{"status":"enabled","reason":null,"venue":"alpaca-paper","trading":true,"kill_switch":$kill,
 "sessions":12,"last_sync":"$(rfc "$last_sync")","last_error":$last_error,
 "clock":{"is_open":$is_open,"next_open":"2026-09-21T13:30:00.000000000Z","next_close":"$(rfc "$close")",
          "next_close_date":"$close_date","read_at":"$(rfc "$read_at")","source":"$source"},
 "recent_sessions":[$sess_json]}
JSON
	jq -c --argjson x "$extra" '. + $x' "$work/desk.json" >"$work/desk.json.new" && mv "$work/desk.json.new" "$work/desk.json"
}

# A fresh journal copy, 1 h old at T0.
fresh_backup() {
	rm -f "$backups"/*
	: >"$backups/desk-2026-09-18.db"
}

reset_scene() {
	rm -rf "$state" "$work/ops.env" "$work/reboot-required"
	: >"$work/log" "$work/logs"
	ps_all_healthy
	printf 'exp_a01_spy-2026-09-17.json\nexp_a01_tlt-2026-09-17.json\n' >"$work/signals"
	fresh_backup
}

out="" rc=0
# run NOW [VAR=value ...]: one watch.sh run at NOW; stdout+stderr in $out.
run() {
	local now=$1; shift
	: >"$work/log"
	set +e
	out=$(env PATH="$work/bin:$PATH" SHIM_LOG="$work/log" SHIM_DIR="$work" \
		OHCAMEL_WATCH_NOW="$now" \
		OHCAMEL_WATCH_STATE_DIR="$state" \
		OHCAMEL_WATCH_OPS_ENV="$work/ops.env" \
		OHCAMEL_WATCH_BOOK="${BOOK:-$book}" \
		OHCAMEL_WATCH_REBOOT_FILE="$work/reboot-required" \
		OHCAMEL_BACKUP_DIR="$backups" \
		"$@" bash "$script" 2>&1)
	rc=$?
	set -e
}
count() { printf '%s\n' "$out" | grep -c -F -- "$1" || true; }
expect_count() { # label needle n
	local n
	n=$(count "$2")
	if [ "$n" = "$3" ]; then ok "$1"; else no "$1" "'$2' x$n, expected x$3"$'\n'"$out"; fi
}
has() { # FILE PATTERN label why-not
	if grep -q "$2" "$1"; then ok "$3"; else no "$3" "$4"; fi
}
restarts() { grep -c '^DOCKER restart' "$work/log" || true; }

# touch_at FILE EPOCH: set a backup copy's mtime to an instant on the same
# clock as OHCAMEL_WATCH_NOW (TZ=UTC, so touch -t reads it as UTC).
touch_at() { touch -t "$(jq -rn --argjson e "$2" '$e | strftime("%Y%m%d%H%M.%S")')" "$1"; }

# ---------------------------------------------------------------------------
# 0. All quiet
# ---------------------------------------------------------------------------
echo "== all quiet"
reset_scene
desk "$T0"
touch_at "$backups/desk-2026-09-18.db" $((T0 - 3600))
run "$T0"
if [ "$rc" = 0 ]; then ok "quiet: exit 0"; else no "quiet: exit" "rc=$rc"$'\n'"$out"; fi
expect_count "quiet: no alert" "ALERT" 0
expect_count "quiet: no recovery" "RECOVERED" 0
if [ "$(restarts)" = 0 ]; then ok "quiet: no restart"; else no "quiet" "restarted"; fi
if grep -q '^CURL' "$work/log"; then no "quiet: no ops.env" "curl ran: $(grep '^CURL' "$work/log")"; else ok "quiet: no ops.env, no Slack, no ping"; fi
if grep -q '^DOCKER exec ohcamel-ohcamel-live-1 curl -fsS --max-time [0-9]* http://localhost:8081/api/desk$' "$work/log"; then
	ok "desk: read in-container over plain HTTP, no credential"
else
	no "desk: read" "$(grep '^DOCKER exec' "$work/log")"
fi

# ---------------------------------------------------------------------------
# 1. The 3-run restart
# ---------------------------------------------------------------------------
echo "== the 3-run restart"
reset_scene
touch_at "$backups/desk-2026-09-18.db" $((T0 - 3600))
desk "$T0"
sed -i.bak 's/^ohcamel-research|\(.*\)|running|Up 2 hours (healthy)$/ohcamel-research|\1|running|Up 2 hours (unhealthy)/' "$work/ps"
run "$T0"
if [ "$(restarts)" = 0 ]; then ok "restart: run 1 unhealthy restarts nothing"; else no "restart run 1" "restarted"; fi
expect_count "restart: run 1 is silent" "ALERT" 0
run $((T0 + 300))
if [ "$(restarts)" = 0 ]; then ok "restart: run 2 unhealthy restarts nothing"; else no "restart run 2" "restarted"; fi
run $((T0 + 600))
if [ "$(restarts)" = 1 ] && grep -q '^DOCKER restart ohcamel-ohcamel-research-1$' "$work/log"; then
	ok "restart: run 3 restarts ohcamel-research exactly once"
else
	no "restart run 3" "$(grep '^DOCKER restart' "$work/log" || echo none)"
fi
expect_count "restart: run 3 alerts once" "ALERT health.ohcamel-research" 1
if [ -s "$state/restarted.ohcamel-research" ]; then ok "restart: the state file records the restart"; else no "restart" "no state/restarted.ohcamel-research"; fi
run $((T0 + 900))
if [ "$(restarts)" = 0 ]; then ok "restart: run 4 still unhealthy restarts nothing more"; else no "restart run 4" "restarted again"; fi
expect_count "restart: run 4 is silent" "ALERT" 0
sed -i.bak 's/(unhealthy)/(health: starting)/' "$work/ps"
run $((T0 + 1200))
expect_count "restart: health: starting neither recovers nor alerts" "RECOVERED" 0
sed -i.bak 's/(health: starting)/(healthy)/' "$work/ps"
run $((T0 + 1500))
expect_count "restart: healthy again recovers once" "RECOVERED health.ohcamel-research" 1
if [ ! -e "$state/restarted.ohcamel-research" ]; then ok "restart: recovery clears the restart record"; else no "restart" "state/restarted.ohcamel-research kept"; fi

echo "== container states"
reset_scene
touch_at "$backups/desk-2026-09-18.db" $((T0 - 3600))
desk "$T0"
sed -i.bak 's/^caddy|ohcamel-caddy-1|running|Up 2 hours$/caddy|ohcamel-caddy-1|exited|Exited (1) 3 minutes ago/' "$work/ps"
run "$T0"
expect_count "state: an exited container alerts" "ALERT state.caddy" 1
if [ "$(restarts)" = 0 ]; then ok "state: an exited container is not restarted by the watch (restart policy owns it)"; else no "state" "restarted"; fi
grep -v '^ohcamel-quant|' "$work/ps" >"$work/ps.new" && mv "$work/ps.new" "$work/ps"
run "$T0"
expect_count "state: a missing container alerts" "ALERT state.ohcamel-quant" 1
expect_count "state: the exited one is not repeated" "ALERT state.caddy" 0
run "$T0" S_PS_FAIL=1
expect_count "state: docker ps failing alerts" "ALERT docker" 1

# ---------------------------------------------------------------------------
# 2. The 180 s sync
# ---------------------------------------------------------------------------
echo "== the 180 s sync"
reset_scene
touch_at "$backups/desk-2026-09-18.db" $((T0 - 3600))
desk "$T0" last_sync=$((T0 - 179))
run "$T0"
expect_count "sync: open, 179 s is silent" "ALERT" 0
desk "$T0" last_sync=$((T0 - 181))
run "$T0"
expect_count "sync: open, 181 s alerts once" "ALERT sync" 1
run "$T0"
expect_count "sync: the same 181 s again is silent (edge)" "ALERT" 0
desk "$T0" last_sync=$((T0 - 20))
run "$T0"
expect_count "sync: recovery alerts once" "RECOVERED sync" 1
run "$T0"
expect_count "sync: steady after recovery is silent" "RECOVERED" 0

desk "$T0" last_sync=$((T0 - 181)) is_open=false
run "$T0"
expect_count "sync: 181 s with is_open false is silent" "ALERT" 0

desk "$T0" last_sync=$((T0 - 181)) source=stale
run "$T0"
expect_count "sync: 181 s with source stale is silent" "ALERT" 0
expect_count "sync: source stale logs the skip once" "skipped" 1
expect_count "sync: the skip line names the source" 'clock.source is "stale"' 1

desk "$T0" last_sync=$((T0 - 181)) source=unknown
run "$T0"
expect_count "sync: 181 s with source unknown is silent" "ALERT" 0
expect_count "sync: source unknown logs the skip once" "skipped" 1

desk "$T0" last_sync=$((T0 - 181)) read_at=$((T0 + 60))
run "$T0"
expect_count "sync: venue-clock with read_at 60 s in the future is silent" "ALERT" 0
expect_count "sync: the future read_at logs the skip once" "skipped" 1
expect_count "sync: the skip line says the read_at is in the future" "60 s in this host's future" 1

# The race, not a clock step: the watch takes its now as it starts and reads the
# desk afterwards, so a reading a second ahead is ordinary. On the droplet 18 of
# about 240 runs in 2026-11-03 skipped both checks for exactly that.
desk "$T0" last_sync=$((T0 - 181)) read_at=$((T0 + 1))
run "$T0"
expect_count "sync: read_at 1 s ahead still runs the check, and 181 s alerts" "ALERT sync" 1
expect_count "sync: and logs no skip" "skipped" 0

# ---------------------------------------------------------------------------
# 3. The 30-minute session rule
# ---------------------------------------------------------------------------
echo "== the 30-minute session rule"
reset_scene
touch_at "$backups/desk-2026-09-18.db" $((T0 - 3600))
# The close is learned while the market is open, from next_close ...
desk "$T0"
run "$T0"
expect_count "session: before the close, silent" "ALERT" 0
# ... and checked after it, against recent_sessions.
desk "$((CLOSE + 29 * 60))" is_open=false
run $((CLOSE + 29 * 60))
expect_count "session: no row at 20:29 UTC is silent" "ALERT" 0
desk "$((CLOSE + 31 * 60))" is_open=false
run $((CLOSE + 31 * 60))
expect_count "session: no row at 20:31 UTC alerts" "ALERT session.2026-09-18" 1
run $((CLOSE + 36 * 60))
expect_count "session: still none at 20:36 is silent (edge)" "ALERT" 0
desk "$((CLOSE + 41 * 60))" is_open=false sessions='"2026-09-17","2026-09-18"'
run $((CLOSE + 41 * 60))
expect_count "session: the row landing recovers once" "RECOVERED session.2026-09-18" 1
# Once the venue rolls next_close forward the old close is not lost: a fresh
# state directory that only ever saw the 20:00 close while it was pending
# still checks it after the reading has moved on.
reset_scene
touch_at "$backups/desk-2026-09-18.db" $((T0 - 3600))
desk "$T0"
run "$T0"
desk "$((CLOSE + 31 * 60))" is_open=false
sed -i.bak "s/\"next_close\":\"[^\"]*\"/\"next_close\":\"2026-09-21T20:00:00.000000000Z\"/; s/\"next_close_date\":\"2026-09-18\"/\"next_close_date\":\"2026-09-21\"/" "$work/desk.json"
run $((CLOSE + 31 * 60))
expect_count "session: a close learned earlier is checked after next_close rolls forward" "ALERT session.2026-09-18" 1
# A stale clock skips the session rule too, with the same one line.
reset_scene
touch_at "$backups/desk-2026-09-18.db" $((T0 - 3600))
desk "$((CLOSE + 31 * 60))" is_open=false source=stale
run $((CLOSE + 31 * 60))
expect_count "session: source stale skips the session rule" "ALERT session" 0

# ---------------------------------------------------------------------------
# 4. The 26 h backup rule
# ---------------------------------------------------------------------------
echo "== the 26 h backup rule"
reset_scene
desk "$T0" is_open=false
b="$backups/desk-2026-09-18.db"
touch_at "$b" $((T0 - 26 * 3600 + 60))
run "$T0"
expect_count "backup: 25 h 59 m is silent" "ALERT" 0
touch_at "$b" $((T0 - 26 * 3600 - 60))
run "$T0"
expect_count "backup: 26 h 01 m alerts" "ALERT backup" 1
# A book copy is written nightly even when the journal copy failed; it must
# not mask a stale journal (the rule reads desk-*.db, not the newest file).
reset_scene
desk "$T0" is_open=false
touch_at "$b" $((T0 - 26 * 3600 - 60))
: >"$backups/book-2026-09-18.sexp"
touch_at "$backups/book-2026-09-18.sexp" $((T0 - 600))
: >"$backups/pre-deploy-20260918T140000Z-abc1234.db"
touch_at "$backups/pre-deploy-20260918T140000Z-abc1234.db" $((T0 - 600))
run "$T0"
expect_count "backup: a fresh book or pre-deploy copy does not mask a stale desk-*.db" "ALERT backup" 1
rm -f "$backups"/*
rm -rf "$state"
run "$T0"
expect_count "backup: no desk-*.db at all alerts" "ALERT backup" 1

# ---------------------------------------------------------------------------
# 5. The 80% disk rule, the reboot flag, last_error, the kill switch
# ---------------------------------------------------------------------------
echo "== disk, reboot, last_error, kill switch"
reset_scene
touch_at "$backups/desk-2026-09-18.db" $((T0 - 3600))
desk "$T0"
run "$T0" S_DISK=80
expect_count "disk: 80% is silent (the rule is over 80)" "ALERT" 0
run "$T0" S_DISK=81
expect_count "disk: 81% alerts" "ALERT disk" 1
run "$T0" S_DISK=81
expect_count "disk: 81% again is silent (edge)" "ALERT" 0
run "$T0" S_DISK=70
expect_count "disk: recovery alerts once" "RECOVERED disk" 1

: >"$work/reboot-required"
run "$T0"
expect_count "reboot: /var/run/reboot-required alerts" "ALERT reboot" 1
rm -f "$work/reboot-required"

desk "$T0" last_error='"alpaca: 503 Service Unavailable"'
run "$T0"
expect_count "last_error: set alerts" "ALERT last_error" 1
expect_count "last_error: the alert carries the error" "alpaca: 503 Service Unavailable" 1
desk "$T0" kill='"tripped"'
run "$T0"
expect_count "kill switch: tripped alerts" "ALERT kill_switch" 1
expect_count "kill switch: last_error clearing recovers" "RECOVERED last_error" 1

run "$T0" S_DESK_FAIL=1
expect_count "desk: an unreadable /api/desk alerts" "ALERT desk" 1

# ---------------------------------------------------------------------------
# 6. The signal file after 01:00 UTC
# ---------------------------------------------------------------------------
echo "== the signal file"
reset_scene
touch_at "$backups/desk-2026-09-18.db" $((T0 - 3600))
N0059=$(epoch 2026-09-19T00:59:00Z)
N0101=$(epoch 2026-09-19T01:01:00Z)
printf 'exp_a01_spy-2026-09-17.json\nexp_a01_tlt-2026-09-17.json\n' >"$work/signals"
desk "$N0059" is_open=false sessions='"2026-09-18"'
touch_at "$backups/desk-2026-09-18.db" $((N0059 - 3600))
run "$N0059"
expect_count "signal: none for 2026-09-18 at 00:59 UTC is silent" "ALERT signals" 0
desk "$N0101" is_open=false sessions='"2026-09-18"'
run "$N0101"
expect_count "signal: none for 2026-09-18 at 01:01 UTC alerts" "ALERT signals" 1
expect_count "signal: the alert names both strategies" "exp_a01_spy, exp_a01_tlt" 1
printf 'exp_a01_spy-2026-09-18.json\nexp_a01_tlt-2026-09-18.json\n' >"$work/signals"
run "$N0101"
expect_count "signal: both landing recovers once" "RECOVERED signals" 1
printf 'exp_a01_spy-2026-09-17.json\n' >"$work/signals"
rm -rf "$state"
BOOK=$nobook run "$N0101"
expect_count "signal: a book with no strategies is silent" "ALERT signals" 0
# No session row for the day (a holiday, a weekend): nothing was owed.
desk "$N0101" is_open=false sessions='"2026-09-17"'
run "$N0101"
expect_count "signal: a day with no session row owes no signal" "ALERT signals" 0

# A farm cell writes only on its rebalance dates: it owes SOME document, the
# newest no older than 35 days, never one for the day. Its newest at 10 days
# old is fine; a cell with no document at all alarms, by name; and one whose
# newest is 36 days old alarms, naming its date.
farmbook="$work/farmbook.sexp"
cat >"$farmbook" <<'EOF'
((positions ((SPY 0.6) (TLT 0.4)))
 (signals
  ((strategies
    (((name exp_a01_spy) (symbols (SPY)) (max_age 3) (sizing incubation) (capital_fraction 0.025))
     ((name farm_tsmom_rates) (symbols (TLT)) (max_age 3) (sizing incubation) (capital_fraction 0.025))
     ((name farm_sma_trend_commodities) (symbols (SPY)) (max_age 3) (sizing incubation) (capital_fraction 0.025)))))))
EOF
rm -rf "$state"
desk "$N0101" is_open=false sessions='"2026-09-18"'
printf 'exp_a01_spy-2026-09-18.json\nfarm_tsmom_rates-2026-09-08.json\nfarm_tsmom_rates-2026-08-31.json\nfarm_sma_trend_commodities-2026-09-08.json\n' >"$work/signals"
BOOK=$farmbook run "$N0101"
expect_count "signal: a farm cell's newest document 10 days old is fine" "ALERT signals" 0
printf 'exp_a01_spy-2026-09-18.json\nfarm_tsmom_rates-2026-09-08.json\n' >"$work/signals"
BOOK=$farmbook run "$N0101"
expect_count "signal: a farm cell with no document at all alarms" "ALERT signals" 1
expect_count "signal: naming it alone" "from: farm_sma_trend_commodities (no document)" 1
printf 'exp_a01_spy-2026-09-18.json\nfarm_tsmom_rates-2026-08-13.json\nfarm_sma_trend_commodities-2026-09-08.json\n' >"$work/signals"
rm -rf "$state"
BOOK=$farmbook run "$N0101"
expect_count "signal: a farm cell's newest document 36 days old alarms" "farm_tsmom_rates (newest 2026-08-13)" 1

# ---------------------------------------------------------------------------
# 7. Slack and the healthchecks.io ping
# ---------------------------------------------------------------------------
echo "== Slack and healthchecks.io"
reset_scene
touch_at "$backups/desk-2026-09-18.db" $((T0 - 3600))
desk "$T0"
cat >"$work/ops.env" <<'EOF'
# ops.env
SLACK_WEBHOOK_URL="https://hooks.slack.test/services/T000/B000/XXXX"
HEALTHCHECKS_URL=https://hc-ping.test/uuid-1234
EOF
run "$T0" S_DISK=81
if grep -q '^CURL url=https://hooks.slack.test/services/T000/B000/XXXX data={"text":".*disk' "$work/log"; then
	ok "slack: an alert is posted to SLACK_WEBHOOK_URL as {\"text\": ...}"
else
	no "slack" "$(grep '^CURL' "$work/log" || echo 'no curl')"
fi
if grep -q '^CURL url=https://hc-ping.test/uuid-1234/fail ' "$work/log"; then ok "healthchecks: a run with a firing alert pings /fail"; else no "healthchecks fail" "$(grep '^CURL' "$work/log" || echo none)"; fi
if printf '%s\n' "$out" | grep -q 'hooks.slack.test\|hc-ping.test'; then no "urls" "a URL from ops.env was printed"; else ok "ops.env: neither URL is printed"; fi
if grep '^CURL' "$work/log" | grep 'hooks.slack.test' | grep -q 'args=.*hooks.slack.test'; then
	no "slack url on argv" "the webhook URL was on curl's command line"
else
	ok "slack: the webhook URL never reaches curl's argv"
fi
run "$T0" S_DISK=42
if grep -q '^CURL url=https://hc-ping.test/uuid-1234 ' "$work/log"; then ok "healthchecks: a clean run pings the bare URL"; else no "healthchecks ok" "$(grep '^CURL' "$work/log" || echo none)"; fi
if [ "$(grep -c '^CURL url=https://hooks.slack.test' "$work/log")" = 1 ]; then ok "slack: the recovery is posted too"; else no "slack recovery" "$(grep '^CURL' "$work/log")"; fi


# ---------------------------------------------------------------------------
# 6b. Paging a person (Task 1.11): OHCAMEL_ALERT_URL in deploy/.env
# ---------------------------------------------------------------------------
echo "== the watch pages a person"
ALERT_URL="https://alert.test/hook/SECRET-TOKEN-1234"
VS_RED_EARLY='{"var_score":{"scores":[{"estimator":"ewma","n":120,"light":"red"}]}}'
set_alert_url() { printf '# ops.env\nHEALTHCHECKS_URL_UNUSED=x\nOHCAMEL_ALERT_URL=%s\n' "$ALERT_URL" >"$work/ops.env"; }
pages() { grep -c '^CURL url=https://alert.test/hook' "$work/log" || true; }
paged_text() { grep '^CURL url=https://alert.test/hook' "$work/log" | head -n 1; }
TRIP='{"switch":{"state":"tripped","limit":"dd-cap","why":null,"at":"__AT__"}}'
tripped_at() { printf '%s' "${TRIP/__AT__/$(rfc "$1")}"; }

# --- a trip over more than one session pages once a day
reset_scene
touch_at "$backups/desk-2026-09-18.db" $((T0 - 3600))
set_alert_url
TRIP_MON=$(epoch 2026-09-14T15:00:00Z)
# Tripped Monday 09-14; sessions recorded Monday only: the trip is one session old.
desk "$T0" kill='"tripped"' sessions='"2026-09-14"' extra="$(tripped_at "$TRIP_MON")"
run "$T0"
expect_count "page: a trip inside its first session does not page" "PAGE trip" 0
if [ "$(pages)" = 0 ]; then ok "page: ... and posts nothing"; else no "page trip 1 session" "$(paged_text)"; fi
# Monday and Tuesday both recorded: more than one session.
desk "$T0" kill='"tripped"' sessions='"2026-09-14","2026-09-15"' extra="$(tripped_at "$TRIP_MON")"
run "$T0"
expect_count "page: a trip over more than one session pages" "PAGE trip" 1
if [ "$(pages)" = 1 ]; then ok "page: ... and posts once to OHCAMEL_ALERT_URL"; else no "page trip post" "pages=$(pages)"; fi
run $((T0 + 300))
if [ "$(pages)" = 0 ]; then ok "page: the next run (5 min on) does not page again"; else no "page trip repeat" "pages=$(pages)"; fi
expect_count "page: ... and does not log a second PAGE" "PAGE trip" 0
run $((T0 + 23 * 3600))
expect_count "page: 23 h later it is still once a day" "PAGE trip" 0
run $((T0 + 24 * 3600 + 60))
expect_count "page: 24 h later, still tripped, it pages again" "PAGE trip" 1
# A restart (a fresh process, same state dir) does not re-page.
run $((T0 + 24 * 3600 + 120))
expect_count "page: a re-run on the same state (a restart) does not re-page" "PAGE trip" 0
# Clear: silent.
reset_scene
set_alert_url
desk "$T0"
run "$T0"
expect_count "page: a clear switch is silent" "PAGE" 0
# Halted by hand (since 2026-11-16): no page, one status line a day.
reset_scene
set_alert_url
touch_at "$backups/desk-2026-09-18.db" $((T0 - 3600))
HALT_AT=$(epoch 2026-09-01T15:00:00Z)
desk "$T0" kill='"halted"' sessions='"2026-09-01","2026-09-02","2026-09-03"' \
	extra='{"switch":{"state":"halted","limit":null,"why":"by hand","at":"'"$(rfc "$HALT_AT")"'"}}'
run "$T0"
expect_count "page: a by-hand halt over many sessions does not PAGE" "PAGE trip" 0
if [ "$(pages)" = 0 ]; then ok "page: ... and posts nothing to the webhook"; else no "halt post" "$(paged_text)"; fi
expect_count "page: the daily status line says halted by hand since the date" "halted by hand since 2026-09-01" 1
run $((T0 + 300))
expect_count "page: the status line is once a day" "halted by hand since" 0
run $((T0 + 24 * 3600 + 60))
expect_count "page: ... and comes again the next day" "halted by hand since 2026-09-01" 1

# A tripped switch with no switch.at still pages.
reset_scene; set_alert_url; touch_at "$backups/desk-2026-09-18.db" $((T0 - 3600))
desk "$T0" kill='"tripped"' extra='{"switch":{"state":"tripped","limit":"dd-cap","why":null,"at":null}}'
run "$T0"
expect_count "page: a trip with no time still pages (since unknown)" "PAGE trip" 1
# A bad timestamp in one field does not blank the other pages.
reset_scene; set_alert_url; touch_at "$backups/desk-2026-09-18.db" $((T0 - 3600))
desk "$T0" extra="$(jq -cn --argjson v "$VS_RED_EARLY" '$v + {"overlay":{"as_of":"garbage","age_s":null,"state":"stale"}}')"
run "$T0"
expect_count "page: an unparseable overlay as_of does not silence a red light" "PAGE varscore" 1
# The trip's New York date, EST (no DST): a trip at 2026-12-01 03:00Z is 22:00 EST on 11-30.
reset_scene; set_alert_url; touch_at "$backups/desk-2026-09-18.db" $((T0 - 3600))
TE=$(epoch 2026-12-01T03:00:00Z)
desk "$T0" kill='"tripped"' sessions='"2026-11-30","2026-12-01"' extra="$(tripped_at "$TE")"
run "$T0"
expect_count "page: the trip's date is New York's (22:00 EST on 11-30), so two sessions count" "since 2026-11-30, across 2 sessions" 1
# And in EDT: 2026-09-15 02:30Z is 22:30 EDT on 09-14.
reset_scene; set_alert_url; touch_at "$backups/desk-2026-09-18.db" $((T0 - 3600))
TD=$(epoch 2026-09-15T02:30:00Z)
desk "$T0" kill='"tripped"' sessions='"2026-09-14","2026-09-15"' extra="$(tripped_at "$TD")"
run "$T0"
expect_count "page: ... and in EDT (22:30 on 09-14)" "since 2026-09-14, across 2 sessions" 1

# --- a sub-ledger break
reset_scene
set_alert_url
touch_at "$backups/desk-2026-09-18.db" $((T0 - 3600))
desk "$T0"
run "$T0"
expect_count "page: no sub-ledger line, no sub-ledger page" "PAGE subledger" 0
printf '%s\n' '  2026-09-18 14:30:00.000000Z    desk      sub-ledger at a sync: SPY: the strategies sum to 123.0 and the account holds 456.0; nothing that grows a strategy'"'"'s position in SPY is sent, and a reduction only if it also shrinks the account'"'"'s' >"$work/logs"
run "$T0"
expect_count "page: a sub-ledger break pages" "PAGE subledger" 1
if grep '^CURL url=https://alert.test/hook' "$work/log" | sed 's/^.* data=//' | grep -q '123\|456'; then no "page subledger figures" "a figure reached the webhook"; else ok "page: the sub-ledger page carries no figure"; fi
if grep -q '^DOCKER logs .*--since 1h' "$work/log"; then ok "page: the live log is read for the last hour"; else no "page logs" "$(grep '^DOCKER logs' "$work/log" || echo 'no docker logs call')"; fi
run $((T0 + 300))
expect_count "page: the same line still in the hour does not page twice" "PAGE subledger" 0
CLOSE_LINE='  2026-09-18 20:35:00.000000Z    desk      sub-ledger at the close of 2026-09-18: SPY: the strategies sum to 1 and the account holds 2; nothing that grows a strategy'"'"'s position in SPY is sent, and a reduction only if it also shrinks the account'"'"'s'
printf '%s\n' "$CLOSE_LINE" >"$work/logs"
run $((T0 + 5 * 3600))
expect_count "page: the same day's close repeat pages although the onset did" "PAGE subledger-close" 1
run $((T0 + 5 * 3600 + 300))
expect_count "page: ... once" "PAGE subledger-close" 0
run $((T0 + 24 * 3600 + 5 * 3600))
expect_count "page: the next day's close repeat pages again (a standing break is not quiet)" "PAGE subledger-close" 1
printf '%s\n' '  x    desk      sub-ledger at the close of 2026-09-18: 4 names checked, no break' \
	'  x    desk      sub-ledger at the close of 2026-09-18 could not be checked: unreadable' >"$work/logs"
reset_scene; set_alert_url; desk "$T0"
run "$T0"
expect_count "page: the close's no-break and could-not-check lines are not breaks" "PAGE subledger" 0
{ printf '%s\n' "$CLOSE_LINE"; printf '%s\n' "$CLOSE_LINE" | sed 's/SPY/QQQ/g'; } >"$work/logs"
run "$T0"
expect_count "page: each broken symbol is its own page (two symbols, two pages)" "PAGE subledger-close" 2

# --- completions exhausted
reset_scene
set_alert_url
touch_at "$backups/desk-2026-09-18.db" $((T0 - 3600))
desk "$T0"
run "$T0"
expect_count "page: no cap line, no completion page" "PAGE completions" 0
printf '%s\n' '  2026-09-18 14:50:00.000000Z    intake    "exp_a01_spy" 12: 3 of its orders ended unfilled, and its completion was tried 3 times this session; it waits for the next' >"$work/logs"
run "$T0"
expect_count "page: completions exhausted pages" "PAGE completions" 1
if paged_text | grep -q 'exp_a01_spy'; then ok "page: ... naming the strategy"; else no "page completions name" "$(paged_text)"; fi
printf '%s\n' '  2026-09-18 14:51:00.000000Z    intake    "exp_a01_tlt" is stopped and still holds shares; its flatten was tried 3 times this session and waits for the next' >>"$work/logs"
run $((T0 + 60))
expect_count "page: a second strategy's cap is its own page" "PAGE completions" 1
run $((T0 + 120))
expect_count "page: and neither repeats within the day" "PAGE completions" 0

# --- the overlay older than 4 session hours
OV='{"overlay":{"value":0.8,"as_of":"__AS__","age_s":__AGE__,"state":"stale","reason":null,"governor_cap":null}}'
overlay_at() { # received-epoch now-epoch
	local x=${OV/__AS__/$(rfc "$1")}
	printf '%s' "${x/__AGE__/$(($2 - $1))}"
}
# Friday 2026-09-18 11:00 EDT = 15:00Z (T0). Sessions on record: Thursday 09-17.
reset_scene; set_alert_url; touch_at "$backups/desk-2026-09-18.db" $((T0 - 3600))
R=$(epoch 2026-09-17T18:00:00Z)   # Thu 14:00 EDT: 2 h left Thu + 1.5 h Fri = 3.5 h
desk "$T0" extra="$(overlay_at "$R" "$T0")"
run "$T0"
expect_count "page: an overlay 3.5 session hours old is silent" "PAGE overlay" 0
R=$(epoch 2026-09-17T17:30:00Z)   # Thu 13:30 EDT: 2.5 + 1.5 = 4.0 h exactly
desk "$T0" extra="$(overlay_at "$R" "$T0")"
run "$T0"
expect_count "page: exactly 4.0 session hours is silent (older than, not as old as)" "PAGE overlay" 0
R=$(epoch 2026-09-17T17:29:00Z)
desk "$T0" extra="$(overlay_at "$R" "$T0")"
run "$T0"
expect_count "page: 4.02 session hours pages" "PAGE overlay" 1
run $((T0 + 300))
expect_count "page: and not again within the day" "PAGE overlay" 0
# Friday's overlay, Monday 09:31 EDT: a weekend is not 4 hours.
reset_scene; set_alert_url; touch_at "$backups/desk-2026-09-21.db" 0
MON=$(epoch 2026-09-21T13:31:00Z)
R=$(epoch 2026-09-18T19:00:00Z)   # Fri 15:00 EDT: 1 h Fri + 1 min Mon
desk "$MON" sessions='"2026-09-17","2026-09-18"' extra="$(overlay_at "$R" "$MON")"
run "$MON"
expect_count "page: Friday 15:00's overlay at Monday 09:31 is silent (weekend and nights do not count)" "PAGE overlay" 0
# ... Friday's overlay at Monday 14:00 EDT = 18:00Z: 1 h + 4.5 h = 5.5 h.
MON2=$(epoch 2026-09-21T18:00:00Z)
desk "$MON2" sessions='"2026-09-17","2026-09-18"' extra="$(overlay_at "$R" "$MON2")"
run "$MON2"
expect_count "page: ... and at Monday 14:00 (5.5 session hours) it pages" "PAGE overlay" 1
# A holiday Monday (no row) is not a session: Friday 15:00's overlay at Tuesday 09:31.
reset_scene; set_alert_url
TUE=$(epoch 2026-09-22T13:31:00Z)
desk "$TUE" sessions='"2026-09-17","2026-09-18"' extra="$(overlay_at "$R" "$TUE")"
run "$TUE"
expect_count "page: a weekday with no session row (a holiday) adds no session hours" "PAGE overlay" 0
# Absent overlay: not this alert's business.
reset_scene; set_alert_url
desk "$T0" extra='{"overlay":{"value":null,"as_of":null,"age_s":null,"state":"absent","reason":null,"governor_cap":null}}'
run "$T0"
expect_count "page: an absent overlay does not page as stale" "PAGE overlay" 0

# --- var_score.light = red
VS_RED='{"var_score":{"source":"x","as_of":"2026-09-17","window":250,"level":0.99,"min_sessions":60,"scores":[{"estimator":"ewma","n":120,"exceptions":9,"kupiec_p":0.001,"christoffersen_p":0.4,"light":"red","message":null},{"estimator":"historical","n":120,"exceptions":1,"kupiec_p":0.5,"christoffersen_p":0.4,"light":"green","message":null}]}}'
reset_scene; set_alert_url; touch_at "$backups/desk-2026-09-18.db" $((T0 - 3600))
desk "$T0" extra="$VS_RED"
run "$T0"
expect_count "page: a red light pages" "PAGE varscore" 1
if paged_text | grep -q 'ewma' && ! paged_text | grep -q '120\|0.001'; then ok "page: ... names the estimator and carries no count or p-value"; else no "page var text" "$(paged_text)"; fi
run $((T0 + 300))
expect_count "page: not again within the day" "PAGE varscore" 0
desk "$T0" extra='{"var_score":{"scores":[{"estimator":"ewma","n":120,"light":"yellow"},{"estimator":"historical","n":90,"light":"green"}]}}'
run "$T0"
expect_count "page: yellow and green are silent" "PAGE varscore" 0
desk "$T0" extra='{"var_score":{"scores":[{"estimator":"ewma","n":41,"light":null,"message":"41 of 60 sessions scored"}]}}'
run "$T0"
expect_count "page: an absent light (41 of 60) is silent" "PAGE varscore" 0
desk "$T0" extra='{"var_score":{"reason":"simulated sessions: not a 1-day VaR, not scored","as_of":"2026-09-17","scores":[]}}'
run "$T0"
expect_count "page: a simulated venue's var_score (reason only) is silent" "PAGE varscore" 0
desk "$T0"
run "$T0"
expect_count "page: no var_score at all is silent" "PAGE varscore" 0

# --- OHCAMEL_ALERT_URL unset: log only, said once a day
reset_scene; touch_at "$backups/desk-2026-09-18.db" $((T0 - 3600))
desk "$T0" kill='"tripped"' sessions='"2026-09-14","2026-09-15"' extra="$(tripped_at "$TRIP_MON")"
run "$T0"
expect_count "page: with no OHCAMEL_ALERT_URL the page is still logged" "PAGE trip" 1
if grep -q '^CURL' "$work/log"; then no "page: unset URL" "curl ran: $(grep '^CURL' "$work/log")"; else ok "page: ... and nothing is posted"; fi
expect_count "page: unset URL is said" "OHCAMEL_ALERT_URL is not set" 1
run $((T0 + 300))
expect_count "page: ... once a day, not every run" "OHCAMEL_ALERT_URL is not set" 0
run $((T0 + 24 * 3600 + 60))
expect_count "page: ... and again the next day" "OHCAMEL_ALERT_URL is not set" 1
# An empty value is unset too.
printf 'OHCAMEL_ALERT_URL=\n' >"$work/ops.env"
rm -rf "$state"; run "$T0"
expect_count "page: an empty OHCAMEL_ALERT_URL is unset" "OHCAMEL_ALERT_URL is not set" 1

# --- the URL and secrets stay out of the output; no position or limit values
reset_scene; set_alert_url; touch_at "$backups/desk-2026-09-18.db" $((T0 - 3600))
printf '%s\n' '  x  desk      sub-ledger at a sync: SPY: sum 123.0 account 456.0; position in SPY is sent' >"$work/logs"
desk "$T0" kill='"tripped"' sessions='"2026-09-14","2026-09-15"' extra="$(jq -cn --argjson a "$(tripped_at "$TRIP_MON")" --argjson b "$VS_RED" '$a + $b')"
run "$T0"
if printf '%s\n' "$out" | grep -q 'alert.test\|SECRET-TOKEN'; then no "page: URL printed" "the alert URL or its token reached stdout"; else ok "page: the alert URL is never printed"; fi
if grep '^CURL' "$work/log" | grep 'alert.test' | grep -q 'args=.*alert.test'; then no "page: URL on argv" "on curl's command line"; else ok "page: the alert URL never reaches curl's argv"; fi
if [ "$(pages)" = 3 ]; then ok "page: three kinds in one run are three posts"; else no "page three" "pages=$(pages)"; fi
if grep '^CURL url=https://alert.test' "$work/log" | sed 's/^.* data=//' | grep -q 'dd-cap\|0.99\|0.001\|120'; then no "page: values" "a limit name or figure reached the webhook"; else ok "page: no limit name, threshold or observed value in a post"; fi
if grep '^CURL url=https://alert.test' "$work/log" | grep -qi 'runbook\|desk.md'; then ok "page: each post names its runbook section"; else no "page: runbook" "no runbook pointer"; fi
if grep -q 'ALERT' <<<"$(printf '%s\n' "$out" | grep -i 'PAGE')"; then no "page: vocabulary" "PAGE lines say ALERT"; else ok "page: PAGE lines are their own vocabulary"; fi

# ---------------------------------------------------------------------------
# 8. Journald, the state directory, the units
# ---------------------------------------------------------------------------
echo "== the units"
if grep -q 'HOME/.local/state/ohcamel-watch' "$script"; then ok "state: defaults under ~/.local/state/ohcamel-watch"; else no "state" "default state dir is not ~/.local/state/ohcamel-watch"; fi
if [ -f "$service" ] && [ -f "$timer" ]; then
	ok "units: ohcamel-watch.service and .timer exist"
	has "$service" '^User=ohcamel$' "service: User=ohcamel" "no User=ohcamel"
	has "$service" '^Type=oneshot$' "service: Type=oneshot" "not oneshot"
	has "$service" '^ExecStart=/home/ohcamel/OhCamel/deploy/watch.sh$' "service: runs deploy/watch.sh" "ExecStart is not deploy/watch.sh"
	has "$service" '^StandardOutput=journal$' "service: alerts go to journald" "no StandardOutput=journal"
	has "$service" '^SyslogIdentifier=ohcamel-watch$' "service: SyslogIdentifier=ohcamel-watch" "no SyslogIdentifier"
	has "$timer" '^OnCalendar=\*:0/5$' "timer: every 5 minutes" "not OnCalendar=*:0/5"
	has "$timer" '^Unit=ohcamel-watch.service$' "timer: fires ohcamel-watch.service" "no Unit="
	has "$timer" '^WantedBy=timers.target$' "timer: WantedBy=timers.target" "no WantedBy"
else
	no "units" "missing $service or $timer"
fi
if [ -x "$script" ]; then ok "watch.sh is executable"; else no "watch.sh" "not executable"; fi

printf '\nwatch_test: %d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
