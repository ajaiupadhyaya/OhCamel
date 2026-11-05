#!/usr/bin/env bash
#
# deploy/smoke.sh and the public host's password (deploy/Caddyfile: closed
# until the site is finished).
#
#   deploy/test/smoke_gate_test.sh
#
# Drives the real smoke.sh against a shim `curl` first on PATH that plays
# pub.test (the gated public host: 401 to an anonymous caller everywhere but
# /api/health) and live.test (401 to everyone), and logs every call whole.
# Only the gate's lines are read; the rest of the suite fails against the
# shim by design.
#
# What it pins:
#   G1. with OHCAMEL_SMOKE_AUTH and a gated host: "GET / anonymous" passes on
#       401 and "/api/health anonymous" passes on 200;
#   G2. the credential (-K FILE) goes to https://pub.test and nowhere else:
#       never the live host, never a plain-http URL;
#   G3. the anonymous checks carry no credential;
#   G4. a gated host and no OHCAMEL_SMOKE_AUTH: the suite fails, saying so;
#   G5. OHCAMEL_SMOKE_AUTH set and the host open: a SKIP, not a failure;
#   G6. OHCAMEL_SMOKE_AUTH naming an unreadable file: exit 2;
#   G7. an http:// BASE never gets the credential.
#
# Exits non-zero, naming the case, if any assertion fails.

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."

pass=0
fail=0
ok() { printf '  ok    %s\n' "$1"; pass=$((pass + 1)); }
no() { printf '  FAIL  %s -- %s\n' "$1" "$2"; fail=$((fail + 1)); }

work=$(mktemp -d "${TMPDIR:-/tmp}/smoke-gate-test.XXXXXX")
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/bin"

cat >"$work/bin/curl" <<'EOF'
#!/usr/bin/env bash
printf 'CURL %s\n' "$*" >>"${SHIM_LOG:?}"
url="" wfmt="" auth=0 out=""
while [ $# -gt 0 ]; do
	case "$1" in
	-K) auth=1; shift 2 ;;
	-w) wfmt=$2; shift 2 ;;
	-o) out=$2; shift 2 ;;
	--max-time | -m | -X | -H | -d | -D) shift 2 ;;
	http://* | https://*) url=$1; shift ;;
	*) shift ;;
	esac
done
code=000
case "$url" in
https://pub.test/api/health) code=200 ;;
https://pub.test | https://pub.test/*)
	if [ "$auth" = 1 ] || [ "${S_OPEN:-0}" = 1 ]; then code=200; else code=401; fi
	;;
http://pub.test:8000/*) code=200 ;;
https://live.test/*) code=401 ;;
*) exit 7 ;;
esac
[ -n "$out" ] || printf '{}'
if [ -n "$wfmt" ]; then
	wfmt=${wfmt//%\{http_code\}/$code}
	wfmt=${wfmt//%\{content_type\}/application/json}
	wfmt=${wfmt//%\{redirect_url\}/}
	# shellcheck disable=SC2059 # curl's -w expands \n the same way
	printf "$wfmt"
fi
exit 0
EOF
cat >"$work/bin/sleep" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
chmod +x "$work/bin/"*

printf 'user = "smoke:%s"\n' 0123456789abcdef >"$work/auth"
chmod 600 "$work/auth"

# run NAME BASE [ENV=VALUE ...]: the suite against the shims; output in $out,
# status in $rc, every curl call in $log.
run() {
	local name=$1 base=$2
	shift 2
	log="$work/log.$name"
	: >"$log"
	set +e
	out=$(env PATH="$work/bin:$PATH" SHIM_LOG="$log" "$@" bash deploy/smoke.sh "$base" --live https://live.test 2>&1)
	rc=$?
	set -e
	printf '%s\n' "$out" >"$work/out.$name"
}
line() { printf '%s\n' "$out" | grep -F -- "$1" | head -1; }

# --- G1-G3: gated, with the smoke account ---------------------------------------
run gated https://pub.test OHCAMEL_SMOKE_AUTH="$work/auth"
case "$(line "GET / anonymous")" in *PASS*401*) ok "G1 GET / anonymous: PASS on 401" ;; *) no "G1 GET / anonymous" "$(line "GET / anonymous")" ;; esac
case "$(line "GET /api/health anonymous")" in *PASS*200*) ok "G1 /api/health anonymous: PASS on 200" ;; *) no "G1 /api/health anonymous" "$(line "GET /api/health anonymous")" ;; esac
if grep -q -- "-K $work/auth" "$log"; then ok "G2 the credential is sent"; else no "G2 the credential is sent" "no -K in any call"; fi
if grep -- "-K " "$log" | grep -qv "https://pub.test"; then
	no "G2 the credential goes only to https://pub.test" "$(grep -- '-K ' "$log" | grep -v 'https://pub.test' | head -1)"
else
	ok "G2 the credential goes only to https://pub.test"
fi
if grep -- "-K " "$log" | grep -q "live.test\|http://"; then no "G2 never to the live host or plain http" "$(grep -- '-K ' "$log" | grep 'live.test\|http://' | head -1)"; else ok "G2 never to the live host or plain http"; fi
if grep -E "CURL .*https://pub.test/? *$|CURL .*https://pub.test/api/health *$" "$log" | grep -E -- "-w %\{http_code\} --max-time 15" | grep -q -- "-K "; then
	no "G3 the anonymous checks carry no credential" "$(grep -E -- '-w %\{http_code\} --max-time 15 https://pub.test/(api/health)?$' "$log" | head -1)"
elif grep -qE -- "-w %\{http_code\} --max-time 15 https://pub.test/$" "$log"; then
	ok "G3 the anonymous checks carry no credential"
else
	no "G3 the anonymous checks carry no credential" "no anonymous GET / in the log"
fi

# --- G4: gated, no account ---------------------------------------------------------
run noauth https://pub.test
case "$(line "GET / anonymous")" in *FAIL*"no password"*) ok "G4 a gated host and no account: FAIL, saying so" ;; *) no "G4 a gated host and no account" "$(line "GET / anonymous")" ;; esac
if [ "$rc" != 0 ]; then ok "G4 the suite exits non-zero"; else no "G4 the suite exits non-zero" "rc 0"; fi

# --- G5: account, open host -------------------------------------------------------
run open https://pub.test OHCAMEL_SMOKE_AUTH="$work/auth" S_OPEN=1
case "$(line "GET / anonymous")" in *SKIP*200*) ok "G5 an open host with an account: SKIP" ;; *) no "G5 an open host with an account" "$(line "GET / anonymous")" ;; esac

# --- G6: unreadable account file ---------------------------------------------------
run unreadable https://pub.test OHCAMEL_SMOKE_AUTH="$work/nope"
if [ "$rc" = 2 ] && printf '%s' "$out" | grep -q "not readable"; then ok "G6 an unreadable OHCAMEL_SMOKE_AUTH: exit 2"; else no "G6 an unreadable OHCAMEL_SMOKE_AUTH" "rc $rc: $(printf '%s' "$out" | head -2 | tr '\n' ' ')"; fi

# --- G7: http BASE ----------------------------------------------------------------
run http http://pub.test:8000 OHCAMEL_SMOKE_AUTH="$work/auth"
if grep -q -- "-K " "$log"; then no "G7 an http:// BASE never gets the credential" "$(grep -- '-K ' "$log" | head -1)"; else ok "G7 an http:// BASE never gets the credential"; fi

printf '\nsmoke_gate_test: %d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
