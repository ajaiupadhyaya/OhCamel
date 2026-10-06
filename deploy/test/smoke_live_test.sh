#!/usr/bin/env bash
#
# deploy/smoke.sh --live-container, against canned answers.
#
#   deploy/test/smoke_live_test.sh
#
# Drives the real smoke.sh with shim `docker` and `curl` binaries first on
# PATH. The live checks run through
#
#   docker compose -f deploy/docker-compose.yml --profile live \
#     exec -T ohcamel-live curl ... http://localhost:8081/...
#
# so the docker shim is the live engine here: it answers /api/desk, /api/ops,
# /api/research and the three POSTed desk routes from environment variables
# this test sets, and plays the ohcamel-research container's `ps`, `inspect`
# and `exec printenv`. The host's own curl (the public-host section) is
# refused at once -- those lines fail by design and this test reads none of
# them; it reads only the lines the live block prints, every one of which
# begins "live:".
#
# What it pins: an all-good live host passes every live line; and each of the
# four regressions Task 15 names -- a set last_error, a mutating route
# answering 200, a sha mismatch, a clock.source of "unknown" -- is a FAIL that
# names it. Plus the two things the suite promises never to do: POST
# /api/desk/kill, or reach the engine any way other than from inside its own
# container.

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."

smoke=deploy/smoke.sh

pass=0
fail=0
ok() { printf '  ok    %s\n' "$1"; pass=$((pass + 1)); }
no() { printf '  FAIL  %s -- %s\n' "$1" "$2"; fail=$((fail + 1)); }

work=$(mktemp -d "${TMPDIR:-/tmp}/smoke-live-test.XXXXXX")
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/bin"

# curl on the host: every request refused. 7 is curl's "failed to connect".
cat >"$work/bin/curl" <<'EOF'
#!/usr/bin/env bash
exit 7
EOF

# docker: the live engine, its research neighbour, and nothing else.
cat >"$work/bin/docker" <<'EOF'
#!/usr/bin/env bash
log=${SHIM_LOG:?}
now_stamp() { date -u '+%Y-%m-%dT%H:%M:%S.123456789Z'; }
case "${1:-}" in
compose)
	printf 'COMPOSE %s\n' "$*" >>"$log"
	shift
	# Exactly the form the suite promises: -f .../deploy/docker-compose.yml
	# --profile live exec -T ohcamel-live curl ...
	[ "${1:-}" = -f ] || { echo "shim: no -f" >&2; exit 64; }
	case "${2:-}" in */deploy/docker-compose.yml | deploy/docker-compose.yml) ;; *) echo "shim: -f $2" >&2; exit 64 ;; esac
	[ "${3:-} ${4:-} ${5:-} ${6:-} ${7:-} ${8:-}" = "--profile live exec -T ohcamel-live curl" ] ||
		{ echo "shim: unexpected compose form: $*" >&2; exit 64; }
	shift 8
	method=GET url="" desk_header=0 origin=0 data=""
	while [ $# -gt 0 ]; do
		case "$1" in
		-X) method=$2; shift 2 ;;
		-H)
			case "$2" in
			"X-OhCamel-Desk: 1") desk_header=1 ;;
			Origin:* | origin:* | Sec-Fetch-Site:* | sec-fetch-site:*) origin=1 ;;
			esac
			shift 2 ;;
		-d | --data | --data-raw) data=$2; shift 2 ;;
		-w | --max-time | -o) shift 2 ;;
		http://*) url=$1; shift ;;
		*) shift ;;
		esac
	done
	path=${url#http://localhost:8081}
	printf 'REQ %s %s header=%s origin=%s data=%s\n' "$method" "$path" "$desk_header" "$origin" "$data" >>"$log"
	case "$url" in http://localhost:8081/*) ;; *) echo "shim: url $url" >&2; exit 64 ;; esac
	if [ "$method" = POST ]; then
		if [ "$path" = "${SHIM_OPEN_ROUTE:-}" ]; then
			printf '{"ok":true}\n200'
		elif [ "$desk_header" = 0 ]; then
			printf '{"error":"a request that changes the desk must carry X-OhCamel-Desk: 1"}\n403'
		elif [ "$origin" = 0 ]; then
			printf '{"error":"a request that changes the desk must come from this site"}\n403'
		else
			printf '{"ok":true}\n200'
		fi
		exit 0
	fi
	case "$path" in
	/api/desk)
		if [ "${SHIM_LAST_SYNC:-fresh}" = null ]; then sync=null; else sync="\"$(now_stamp)\""; fi
		if [ -n "${SHIM_LAST_ERROR:-}" ]; then err="\"$SHIM_LAST_ERROR\""; else err=null; fi
		printf '{"status":"enabled","reason":null,"venue":"alpaca-paper","kill_switch":"armed","journal":"file","sessions":3,"last_sync":%s,"last_error":%s,"clock":{"is_open":false,"read_at":"%s","source":"%s"},"account":{"status":"ACTIVE","trading_blocked":false}}\n200' \
			"$sync" "$err" "$(now_stamp)" "${SHIM_CLOCK_SOURCE:-venue-clock}"
		;;
	/api/ops)
		printf '{"mode":"live","uptime_s":42,"build":{"git_sha":"%s"}}\n200' "${SHIM_OPS_SHA:?}"
		;;
	/api/research)
		printf '{"intake":"running","strategies":[],"r8":"x"}\n200'
		;;
	*) printf '{"error":"not found"}\n404' ;;
	esac
	;;
ps)
	service=""
	for a in "$@"; do
		case "$a" in label=com.docker.compose.service=*) service=${a#label=com.docker.compose.service=} ;; esac
	done
	case "$service" in
	ohcamel-research) echo "feed00000002" ;;
	ohcamel-quant | ohcamel-live) echo "c0ffee000001" ;;
	esac
	;;
inspect)
	case "$*" in
	*State.Running*) echo "true ${SHIM_RESEARCH_REV:?}" ;;
	*) echo "${SHIM_OPS_SHA} $(now_stamp)" ;;
	esac
	;;
exec)
	printf 'EXEC %s\n' "$*" >>"$log"
	[ "$*" = "exec feed00000002 printenv OHCAMEL_GIT_SHA" ] || { echo "shim: exec $*" >&2; exit 64; }
	echo "${SHIM_RESEARCH_ENV:?}"
	;;
*)
	echo "docker shim: unexpected: $*" >&2
	exit 64
	;;
esac
EOF
chmod +x "$work/bin/curl" "$work/bin/docker"

sha=0123456789abcdef0123456789abcdef01234567
other=fedcba9876543210fedcba9876543210fedcba98

# run_live VAR=VALUE...: the suite's output for one canned live host, colours
# stripped. Defaults are an all-good host on $sha; each argument overrides one.
run_live() {
	: >"$work/log"
	(
		export SHIM_LOG="$work/log" SHIM_OPS_SHA="$sha" SHIM_RESEARCH_REV="$sha" SHIM_RESEARCH_ENV="$sha"
		export SMOKE_SYNC_WAIT_S=0
		unset COMPOSE_PROJECT_NAME SHIM_LAST_ERROR SHIM_OPEN_ROUTE SHIM_CLOCK_SOURCE SHIM_LAST_SYNC
		for kv in "$@"; do export "${kv?}"; done
		PATH="$work/bin:$PATH" bash "$smoke" http://127.0.0.1:9 --live-container --expect-sha "$sha" 2>&1 || true
	) | sed $'s/\033\\[[0-9;]*m//g'
}

# live_lines OUTPUT: only the lines the live block printed.
live_lines() { grep -E '^ +(PASS|FAIL|SKIP) +live:' <<<"$1" || true; }

# expect NAME OUTPUT VERDICT PATTERN: exactly one live line matches PATTERN,
# and it begins with VERDICT.
expect() {
	local name="$1" out="$2" verdict="$3" pattern="$4" lines
	lines=$(live_lines "$out" | grep -E -- "$pattern" || true)
	if [ -z "$lines" ]; then
		no "$name" "no live line matching /$pattern/; live lines: $(live_lines "$out" | tr -s ' ' | tr '\n' '|')"
	elif [ "$(wc -l <<<"$lines" | tr -d ' ')" -ne 1 ]; then
		no "$name" "more than one live line matching /$pattern/: $(tr '\n' '|' <<<"$lines")"
	elif ! grep -qE "^ +$verdict " <<<"$lines"; then
		no "$name" "expected $verdict, got: $(tr -s ' ' <<<"$lines")"
	else
		ok "$name"
	fi
}

# --- 1. An all-good live host: every live line passes ----------------------
out=$(run_live)
n_live=$(live_lines "$out" | wc -l | tr -d ' ')
n_bad=$(live_lines "$out" | grep -cvE '^ +PASS ' || true)
if [ "$n_live" -ge 20 ] && [ "$n_bad" -eq 0 ]; then
	ok "an all-good live host passes all $n_live live lines"
else
	no "an all-good live host passes every live line" \
		"$n_live live lines, $n_bad not PASS: $(live_lines "$out" | grep -vE '^ +PASS ' | tr -s ' ' | tr '\n' '|')"
fi
log=$(cat "$work/log")
expect "status enabled" "$out" PASS 'live: +/api/desk status +enabled'
expect "venue alpaca-paper" "$out" PASS 'live: +/api/desk venue +alpaca-paper'
expect "journal file" "$out" PASS 'live: +/api/desk journal +file'
expect "kill_switch present" "$out" PASS 'live: +/api/desk kill_switch'
expect "clock.source read" "$out" PASS 'live: +/api/desk clock.source'
expect "last_sync fresh" "$out" PASS 'live: +/api/desk last_sync'
expect "last_error null" "$out" PASS 'live: +/api/desk last_error'
expect "account ACTIVE" "$out" PASS 'live: +/api/desk account.status'
expect "trading not blocked" "$out" PASS 'live: +/api/desk account.trading_blocked'
expect "/api/ops mode live" "$out" PASS 'live: +/api/ops mode'
expect "/api/ops sha matches" "$out" PASS 'live: +/api/ops build.git_sha'
expect "/api/ops uptime under 300" "$out" PASS 'live: +/api/ops uptime_s'
expect "research running" "$out" PASS 'live: +ohcamel-research running'
expect "research label" "$out" PASS 'live: +ohcamel-research image revision'
expect "research env" "$out" PASS 'live: +ohcamel-research OHCAMEL_GIT_SHA'
expect "/api/research intake running" "$out" PASS 'live: +/api/research +intake running, 0 strategies'
for route in orders cancel kill/reset; do
	expect "POST $route without the header is 403" "$out" PASS "live: +POST /api/desk/$route +403 without X-OhCamel-Desk"
	expect "POST $route with the header, no origin, is 403" "$out" PASS "live: +POST /api/desk/$route +403 with the header but no Origin"
done

# The suite never POSTs /api/desk/kill (ruling 13).
if grep -qE '^REQ POST /api/desk/kill ' <<<"$log"; then
	no "/api/desk/kill is never POSTed" "the log has: $(grep -E '^REQ POST /api/desk/kill ' <<<"$log" | head -1)"
else
	ok "/api/desk/kill is never POSTed"
fi
# The three probes each carry the body the task names.
if grep -qE '^REQ POST /api/desk/orders header=0 origin=0 data=\{\}$' <<<"$log"; then
	ok "orders is POSTed with body {}"
else
	no "orders is POSTed with body {}" "$(grep 'POST /api/desk/orders' <<<"$log" | head -1)"
fi
if grep -qE '^REQ POST /api/desk/cancel header=0 origin=0 data=\{"client_order_id":"[^"]+"\}$' <<<"$log"; then
	ok "cancel is POSTed with a nonexistent id"
else
	no "cancel is POSTed with a nonexistent id" "$(grep 'POST /api/desk/cancel' <<<"$log" | head -1)"
fi
if grep -E '^REQ POST /api/desk/kill/reset ' <<<"$log" | grep -q confirm; then
	no "kill/reset is POSTed with no confirm field" "$(grep 'POST /api/desk/kill/reset' <<<"$log" | head -1)"
else
	ok "kill/reset is POSTed with no confirm field"
fi
# Every request to the engine went through compose exec into its container
# (the shim refuses any other form with exit 64 and logs nothing for it), and
# at least the eight the suite names arrived.
n_req=$(grep -c '^REQ ' <<<"$log" || true)
n_compose=$(grep -c '^COMPOSE ' <<<"$log" || true)
if [ "$n_req" -ge 9 ] && [ "$n_req" -eq "$n_compose" ]; then
	ok "all $n_req engine requests ran inside ohcamel-live through compose exec"
else
	no "every engine request runs inside ohcamel-live" "$n_req requests, $n_compose compose calls"
fi

# --- 2. The four regressions Task 15 names ----------------------------------
out=$(run_live SHIM_LAST_ERROR="alpaca: 401 unauthorized")
expect "a set last_error fails, naming it" "$out" FAIL 'live: +/api/desk last_error.*401 unauthorized'

out=$(run_live SHIM_OPEN_ROUTE=/api/desk/orders)
expect "orders answering 200 without the header fails" "$out" FAIL 'live: +POST /api/desk/orders +200, expected 403 without X-OhCamel-Desk'

out=$(run_live SHIM_OPEN_ROUTE=/api/desk/kill/reset)
expect "kill/reset answering 200 fails" "$out" FAIL 'live: +POST /api/desk/kill/reset +200, expected 403 without X-OhCamel-Desk'

out=$(run_live SHIM_OPS_SHA="$other")
expect "an /api/ops sha mismatch fails, naming both" "$out" FAIL "live: +/api/ops build.git_sha +$other, expected 0123456"

out=$(run_live SHIM_CLOCK_SOURCE=unknown)
expect "clock.source unknown fails" "$out" FAIL 'live: +/api/desk clock.source +"unknown"'

# --- 3. The rest of the list -------------------------------------------------
out=$(run_live SHIM_RESEARCH_REV="$other")
expect "a research image built from another sha fails" "$out" FAIL "live: +ohcamel-research image revision +$other"

out=$(run_live SHIM_RESEARCH_ENV="$other")
expect "a research OHCAMEL_GIT_SHA from another sha fails" "$out" FAIL "live: +ohcamel-research OHCAMEL_GIT_SHA +$other"

out=$(run_live SHIM_LAST_SYNC=null)
expect "no sync within the wait fails" "$out" FAIL 'live: +/api/desk last_sync +null'

printf '\n  %d passed, %d failed\n\n' "$pass" "$fail"
[ "$fail" -eq 0 ] || exit 1
