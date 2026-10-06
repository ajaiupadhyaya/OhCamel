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
}

# A fresh journal copy, 1 h old at T0.
fresh_backup() {
	rm -f "$backups"/*
	: >"$backups/desk-2026-09-18.db"
}

reset_scene() {
	rm -rf "$state" "$work/ops.env" "$work/reboot-required"
	: >"$work/log"
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
