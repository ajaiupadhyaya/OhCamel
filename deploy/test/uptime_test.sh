#!/usr/bin/env bash
#
# scripts/uptime.sh and .github/workflows/uptime.yml, against canned answers.
#
#   deploy/test/uptime_test.sh
#
# Drives the real scripts/uptime.sh with shim `curl`, `openssl` and `sleep`
# binaries first on PATH, so no request leaves the machine. The curl shim is
# both hosts (pub.test, the public Quant site; live.test, the gated engine)
# and a directly reachable engine (engine.test, the --engine origin); the
# openssl shim hands each host a certificate whose remaining life this test
# sets. Then it pins, as text, the workflow that runs the script every
# fifteen minutes.
#
# What it pins:
#   - an all-good pair of hosts passes every check and exits 0;
#   - each check Task 17 names fails, by name, when its answer goes wrong:
#     the public / and /api/health, the engine counter frozen across 2 s,
#     /api/desk's shape (on --engine), the HTTP -> HTTPS redirect on either
#     host, the live host answering an anonymous caller, a certificate with
#     10 days or fewer left;
#   - a certificate with 21 days or fewer (but more than 10) warns and does
#     not fail;
#   - the public bridge being off (503, the documented default) is a SKIP;
#   - the script only ever GETs: no -X, no body, no credential, no header
#     carrying one;
#   - the workflow: every 15 minutes and on dispatch, `contents: read` and
#     nothing more, no secret named anywhere, the script is what it runs.
#
# Exits non-zero, naming the case, if any assertion fails. Picked up by the
# lint job's deploy/test/*.sh step with no change to ci.yml.

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."

script=scripts/uptime.sh
workflow=.github/workflows/uptime.yml

pass=0
fail=0
ok() { printf '  ok    %s\n' "$1"; pass=$((pass + 1)); }
no() { printf '  FAIL  %s -- %s\n' "$1" "$2"; fail=$((fail + 1)); }

work=$(mktemp -d "${TMPDIR:-/tmp}/uptime-test.XXXXXX")
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/bin"

# ---------------------------------------------------------------------------
# Shims
# ---------------------------------------------------------------------------
cat >"$work/bin/curl" <<'EOF'
#!/usr/bin/env bash
# The hosts. Every invocation is logged whole, one line, for the GET-only
# assertions.
log=${SHIM_LOG:?}
printf 'CURL %s\n' "$*" >>"$log"
out="" wfmt="" url=""
while [ $# -gt 0 ]; do
	case "$1" in
	-o) out=$2; shift 2 ;;
	-w) wfmt=$2; shift 2 ;;
	--max-time | -m) shift 2 ;;
	http://* | https://*) url=$1; shift ;;
	*) shift ;;
	esac
done
counter() {
	# A rising counter unless frozen: one more per read.
	local f="$SHIM_DIR/counter.$1" n
	n=$(cat "$f" 2>/dev/null || echo 100)
	[ "${S_FROZEN:-0}" = 1 ] || n=$((n + 7))
	echo "$n" >"$f"
	echo "$n"
}
code=000 body="" redir=""
case "$url" in
https://pub.test/) code=${S_PUB_ROOT:-200}; body='<!doctype html><div id="root"></div>' ;;
https://pub.test/api/health) code=${S_PUB_HEALTH:-200}; body='{"status":"ok","offline":false}' ;;
https://pub.test/api/engine/snapshot)
	code=${S_BRIDGE:-200}
	if [ "$code" = 200 ]; then
		body="{\"snapshot\":{\"gross_exposure\":1000.0,\"nodes_recomputed\":$(counter bridge),\"positions\":[{}]}}"
	else
		# The bridge's 503 says whether it is switched on: configured false
		# when OHCAMEL_QUANT_ENGINE_URL is unset, true when the engine behind
		# it is down, slow or answering non-200.
		body="{\"error\":\"engine_unavailable\",\"detail\":\"unavailable\",\"configured\":${S_BRIDGE_CONFIGURED:-false},\"reachable\":false}"
	fi
	;;
http://pub.test/) code=${S_PUB_REDIR:-308}; redir=${S_PUB_REDIR_TO:-https://pub.test/} ;;
http://live.test/) code=${S_LIVE_REDIR:-308}; redir=${S_LIVE_REDIR_TO:-https://live.test/} ;;
https://live.test/) code=${S_LIVE_ROOT:-401} ;;
https://live.test/api/desk) code=${S_LIVE_DESK:-401} ;;
http://engine.test:8001/api/snapshot)
	code=200
	body="{\"gross_exposure\":1000.0,\"nodes_recomputed\":$(counter engine),\"positions\":[{}]}"
	;;
http://engine.test:8001/api/desk)
	code=200
	body=${S_DESK_BODY:-'{"status":"enabled","venue":"simulated","journal":"memory","sessions":3}'}
	;;
*) exit 7 ;;
esac
[ "$code" = 000 ] && exit 7
if [ -n "$out" ]; then
	[ "$out" = /dev/null ] || printf '%s' "$body" >"$out"
else
	printf '%s' "$body"
fi
if [ -n "$wfmt" ]; then
	wfmt=${wfmt//%\{http_code\}/$code}
	wfmt=${wfmt//%\{redirect_url\}/$redir}
	# shellcheck disable=SC2059 # curl's -w expands \n the same way
	printf "$wfmt"
fi
exit 0
EOF

cat >"$work/bin/openssl" <<'EOF'
#!/usr/bin/env bash
# s_client hands back a "certificate" naming its host; x509 reads the host
# off it and answers -checkend / -enddate from S_DAYS_<HOST>.
log=${SHIM_LOG:?}
printf 'OPENSSL %s\n' "$*" >>"$log"
case "${1:-}" in
s_client)
	host=""
	while [ $# -gt 0 ]; do
		case "$1" in -servername) host=$2; shift 2 ;; *) shift ;; esac
	done
	[ "$host" = "${S_NO_CERT:-}" ] && exit 1
	printf 'CONNECTED(00000003)\n---\n-----BEGIN CERTIFICATE-----\n%s\n-----END CERTIFICATE-----\n---\n' "$host"
	;;
x509)
	host=$(sed -n '2p')
	[ -n "$host" ] || { echo "unable to load certificate" >&2; exit 1; }
	case "$host" in
	pub.test) days=${S_DAYS_PUB:-60} ;;
	live.test) days=${S_DAYS_LIVE:-60} ;;
	*) exit 1 ;;
	esac
	shift
	while [ $# -gt 0 ]; do
		case "$1" in
		-checkend)
			if [ $((days * 86400)) -gt "$2" ]; then echo "Certificate will not expire"; else echo "Certificate will expire"; exit 1; fi
			shift 2 ;;
		-enddate) echo "notAfter=in $days days"; shift ;;
		*) shift ;;
		esac
	done
	;;
*) exit 1 ;;
esac
EOF

cat >"$work/bin/sleep" <<'EOF'
#!/usr/bin/env bash
printf 'SLEEP %s\n' "$*" >>"${SHIM_LOG:?}"
EOF
chmod +x "$work/bin/"*

# run NAME [ENV=VALUE ...] -- ARGS...
#
# One run of the script against the shims; leaves $out (stdout+stderr), $rc
# and $log (every shimmed call) for the case to read.
run() {
	local name=$1
	shift
	local envs=()
	while [ $# -gt 0 ] && [ "$1" != -- ]; do envs+=("$1"); shift; done
	[ "${1:-}" = -- ] && shift
	rm -rf "$work/case" && mkdir -p "$work/case"
	log="$work/case/log"
	: >"$log"
	set +e
	out=$(env PATH="$work/bin:$PATH" SHIM_LOG="$log" SHIM_DIR="$work/case" \
		GITHUB_ACTIONS= "${envs[@]+"${envs[@]}"}" bash "$script" --public pub.test --live live.test "$@" 2>&1)
	rc=$?
	set -e
	printf '%s\n' "$out" >"$work/case/out.$name"
}

# line LABEL -- the one output line whose check label begins LABEL.
line() { printf '%s\n' "$out" | grep -F -- "$1" | head -1; }

expect_line() { # case status label
	local l
	l=$(line "$3")
	case "$l" in
	*"$2"*) ok "$1: $2 $3" ;;
	*) no "$1" "expected $2 on '$3', got: ${l:-no such line}" ;;
	esac
}
expect_rc() { # case rc
	if [ "$rc" = "$2" ]; then ok "$1: exit $2"; else no "$1" "exit $rc, expected $2"; printf '%s\n' "$out" | sed 's/^/        /'; fi
}

if [ ! -f "$script" ]; then
	no "scripts/uptime.sh" "missing"
fi
if [ ! -f "$workflow" ]; then
	no ".github/workflows/uptime.yml" "missing"
fi
if [ "$fail" -gt 0 ]; then
	printf '\nuptime_test: %d passed, %d failed\n' "$pass" "$fail"
	exit 1
fi

# ---------------------------------------------------------------------------
# 1. All good
# ---------------------------------------------------------------------------
run good --
expect_rc "all good" 0
for label in \
	"https://pub.test/ " \
	"https://pub.test/api/health" \
	"engine counter" \
	"http://pub.test/ " \
	"http://live.test/ " \
	"https://live.test/ " \
	"https://live.test/api/desk" \
	"cert pub.test" \
	"cert live.test"; do
	expect_line "all good" PASS "$label"
done
if printf '%s\n' "$out" | grep -q 'FAIL\|WARN'; then
	no "all good" "a FAIL or WARN line in an all-good run"
else
	ok "all good: no FAIL or WARN line"
fi
case "$out" in
*"9 pass, 0 warn, 0 fail, 1 skip"*) ok "all good: the summary counts nine passes and the desk SKIP" ;;
*) no "all good" "summary line: $(printf '%s\n' "$out" | tail -1)" ;;
esac
if grep -q '^SLEEP 2' "$log"; then ok "all good: the counter is read 2 s apart"; else no "all good" "no sleep 2 between the counter reads"; fi
if grep -q -- '-checkend 864000' "$log" && grep -q -- '-checkend 1814400' "$log"; then
	ok "all good: certificates checked at 10 d and 21 d"
else
	no "all good" "openssl x509 -checkend 864000 and 1814400 not both run"
fi
if grep -q 'OPENSSL s_client .*-servername pub.test' "$log" && grep -q 'OPENSSL s_client .*-servername live.test' "$log"; then
	ok "all good: SNI names each host"
else
	no "all good" "s_client without -servername for both hosts"
fi

# ---------------------------------------------------------------------------
# 2. GET only, no credential
# ---------------------------------------------------------------------------
if grep '^CURL' "$log" | grep -q -E -- '(^| )(-X|--request|-d|--data[a-z-]*|-F|--form|-u|--user|-T|--upload-file|-I|--head)( |$)'; then
	no "read-only" "a curl call that is not a plain GET: $(grep '^CURL' "$log" | grep -E -- ' (-X|-d|--data|-F|-u|--user|-T|-I) ' | head -1)"
else
	ok "read-only: every curl call is a plain GET"
fi
if grep '^CURL' "$log" | grep -q -i 'authorization\|cookie\|token'; then
	no "read-only" "a curl call carrying a credential header"
else
	ok "read-only: no credential header"
fi
if grep '^CURL' "$log" | grep -q -- ' -L\| --location'; then
	no "read-only" "curl follows redirects, so the redirect check reads the HTTPS answer"
else
	ok "read-only: redirects are read, not followed"
fi

# ---------------------------------------------------------------------------
# 3. Each check fails by name
# ---------------------------------------------------------------------------
run root S_PUB_ROOT=502 --
expect_rc "public / 502" 1
expect_line "public / 502" FAIL "https://pub.test/ "

run health S_PUB_HEALTH=503 --
expect_rc "public /api/health 503" 1
expect_line "public /api/health 503" FAIL "https://pub.test/api/health"

run frozen S_FROZEN=1 --
expect_rc "counter frozen" 1
expect_line "counter frozen" FAIL "engine counter"
case "$(line "engine counter")" in *frozen* | *FROZEN* | *stuck*) ok "counter frozen: says so" ;; *) no "counter frozen" "the line does not say frozen: $(line "engine counter")" ;; esac

run bridge-off S_BRIDGE=503 --
expect_rc "bridge off" 0
expect_line "bridge off" SKIP "engine counter"

run bridge-dead S_BRIDGE=503 S_BRIDGE_CONFIGURED=true --
expect_rc "bridge on, engine dead" 1
expect_line "bridge on, engine dead" FAIL "engine counter"

run pub-noredir S_PUB_REDIR=200 S_PUB_REDIR_TO= --
expect_rc "public no redirect" 1
expect_line "public no redirect" FAIL "http://pub.test/ "

run live-elsewhere S_LIVE_REDIR_TO=https://evil.test/ --
expect_rc "live redirects elsewhere" 1
expect_line "live redirects elsewhere" FAIL "http://live.test/ "

run live-open S_LIVE_ROOT=200 --
expect_rc "live / open" 1
expect_line "live / open" FAIL "https://live.test/ "

run live-desk-open S_LIVE_DESK=200 --
expect_rc "live /api/desk open" 1
expect_line "live /api/desk open" FAIL "https://live.test/api/desk"

run cert-warn S_DAYS_PUB=15 --
expect_rc "cert 15 d" 0
expect_line "cert 15 d" WARN "cert pub.test"
expect_line "cert 15 d" PASS "cert live.test"

run cert-fail S_DAYS_LIVE=5 --
expect_rc "cert 5 d" 1
expect_line "cert 5 d" FAIL "cert live.test"

run cert-none S_NO_CERT=pub.test --
expect_rc "no cert" 1
expect_line "no cert" FAIL "cert pub.test"

# Under Actions a WARN and a FAIL become annotations on the run.
run annot GITHUB_ACTIONS=true S_DAYS_PUB=15 S_LIVE_ROOT=200 --
case "$out" in *"::warning::"*"cert pub.test"*) ok "annotations: ::warning:: for a WARN" ;; *) no "annotations" "no ::warning:: line for the 15-day cert" ;; esac
case "$out" in *"::error::"*"https://live.test/"*) ok "annotations: ::error:: for a FAIL" ;; *) no "annotations" "no ::error:: line for the open live host" ;; esac

# ---------------------------------------------------------------------------
# 4. --engine: a directly reachable engine (the local harness)
# ---------------------------------------------------------------------------
run engine -- --engine http://engine.test:8001
expect_rc "--engine good" 0
expect_line "--engine good" PASS "engine counter"
expect_line "--engine good" PASS "/api/desk shape"
if grep -q 'CURL .*http://engine.test:8001/api/snapshot' "$log"; then ok "--engine: the counter is read off the engine's own /api/snapshot"; else no "--engine" "the engine's /api/snapshot was not read"; fi

run engine-bad S_DESK_BODY='{"status":"weird"}' -- --engine http://engine.test:8001
expect_rc "--engine bad desk" 1
expect_line "--engine bad desk" FAIL "/api/desk shape"

run engine-frozen S_FROZEN=1 -- --engine http://engine.test:8001
expect_rc "--engine frozen" 1
expect_line "--engine frozen" FAIL "engine counter"

# Without --engine there is no anonymous /api/desk to read; the line says so.
run nodesk --
expect_line "no --engine" SKIP "/api/desk shape"

run badarg -- --bogus
if [ "$rc" = 2 ]; then ok "unknown argument: exit 2"; else no "unknown argument" "exit $rc, expected 2"; fi

# ---------------------------------------------------------------------------
# 5. The workflow, as text
# ---------------------------------------------------------------------------
wf=$(cat "$workflow")
if grep -q -E "^\s*-\s*cron:\s*'\*/15 \* \* \* \*'\s*$" "$workflow"; then ok "workflow: every 15 minutes"; else no "workflow" "no cron '*/15 * * * *'"; fi
if grep -q -E '^\s*workflow_dispatch:' "$workflow"; then ok "workflow: on dispatch"; else no "workflow" "no workflow_dispatch"; fi
perm=$(awk '/^permissions:/{f=1; next} f && /^[^ #]/{f=0} f && NF && $1 !~ /^#/' "$workflow" | sed 's/[[:space:]]*#.*//; s/^[[:space:]]*//')
if [ "$perm" = "contents: read" ]; then ok "workflow: top-level permissions are exactly contents: read"; else no "workflow" "top-level permissions: '${perm:-none}'"; fi
if printf '%s\n' "$wf" | grep -v '^\s*#' | grep -q 'permissions:' && [ "$(printf '%s\n' "$wf" | grep -v '^\s*#' | grep -c 'permissions:')" != 1 ]; then
	no "workflow" "a job declares its own permissions"
else
	ok "workflow: no job widens permissions"
fi
if printf '%s\n' "$wf" | grep -v '^\s*#' | grep -q 'secrets\.\|write'; then
	no "workflow" "names a secret or a write scope: $(printf '%s\n' "$wf" | grep -v '^\s*#' | grep 'secrets\.\|write' | head -1)"
else
	ok "workflow: no secret, no write scope"
fi
if printf '%s\n' "$wf" | grep -q 'scripts/uptime.sh'; then ok "workflow: runs scripts/uptime.sh"; else no "workflow" "does not run scripts/uptime.sh"; fi
if printf '%s\n' "$wf" | grep -q 'timeout-minutes:'; then ok "workflow: bounded by timeout-minutes"; else no "workflow" "no timeout-minutes"; fi

printf '\nuptime_test: %d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
