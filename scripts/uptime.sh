#!/usr/bin/env bash
#
# Uptime and certificate watch for OhCamel's two public hostnames, from
# outside the droplet. .github/workflows/uptime.yml runs it every fifteen
# minutes; it runs the same from a laptop.
#
#   scripts/uptime.sh                                  # the production hosts
#   scripts/uptime.sh --public HOST --live HOST        # other hosts
#   scripts/uptime.sh ... --engine http://localhost:8001
#                                                      # + an engine reachable
#                                                      #   without a password
#
# Hosts default to OHCAMEL_DEMO_HOST / OHCAMEL_LIVE_HOST from the environment
# (deploy.env's names), then to the production names, which are public.
#
# Read-only by construction: every request is a plain GET, nothing is posted,
# no credential is sent and none is needed. The live host is checked only for
# refusing an anonymous caller.
#
# The checks, each printed as one PASS / WARN / FAIL / SKIP line:
#   https://PUBLIC/ and /api/health   answer 200
#   engine counter                    nodes_recomputed advances across 2 s.
#                                     With --engine, off the engine's own
#                                     /api/snapshot. Without it, through the
#                                     public host's read-only bridge
#                                     (/api/engine/snapshot); the bridge is off
#                                     unless the owner opts in, and its 503 is
#                                     then a SKIP, not a failure -- the public
#                                     host has served Quant, not the engine,
#                                     since the Quant cutover.
#   /api/desk shape                   status enabled|disabled and a venue.
#                                     Needs --engine: the only engine in
#                                     production is the live one, behind its
#                                     password, so without --engine it is a
#                                     SKIP and the live host's 401 below is
#                                     what is asserted of its desk.
#   http://PUBLIC/, http://LIVE/      redirect (301/302/307/308) to https on
#                                     the same host
#   https://LIVE/ and /api/desk       answer 401
#   cert PUBLIC, cert LIVE            more than 21 days left, else WARN; more
#                                     than 10, else FAIL. Read with
#                                     `openssl s_client | openssl x509
#                                     -noout -checkend`.
#
# Exit 0 when nothing failed (warnings allowed), 1 when any check failed,
# 2 on a usage error. Under GitHub Actions a WARN or FAIL is also written as
# an ::warning:: / ::error:: annotation, so a run's summary names it.
# deploy/test/uptime_test.sh drives this script against shims.

set -uo pipefail

PUBLIC="${OHCAMEL_DEMO_HOST:-ohcamel.ajaiupadhyaya.com}"
LIVE="${OHCAMEL_LIVE_HOST:-live.ohcamel.ajaiupadhyaya.com}"
ENGINE=""

# Seconds: a certificate with this much life left or less warns / fails.
WARN_SECS=$((21 * 86400))
FAIL_SECS=$((10 * 86400))

while [ $# -gt 0 ]; do
	case "$1" in
	--public) [ $# -ge 2 ] || { echo "uptime: --public requires a value" >&2; exit 2; }; PUBLIC="$2"; shift 2 ;;
	--live) [ $# -ge 2 ] || { echo "uptime: --live requires a value" >&2; exit 2; }; LIVE="$2"; shift 2 ;;
	--engine) [ $# -ge 2 ] || { echo "uptime: --engine requires a value" >&2; exit 2; }; ENGINE="${2%/}"; shift 2 ;;
	-h | --help) sed -n '2,/^$/s/^# \{0,1\}//p' "$0"; exit 0 ;;
	*) echo "uptime: unknown argument $1" >&2; exit 2 ;;
	esac
done

pass=0 warn=0 fail=0 skip=0
annotate() { [ "${GITHUB_ACTIONS:-}" = true ] && printf '::%s::%s -- %s\n' "$1" "$2" "$3"; return 0; }
ok()   { printf '  PASS  %-28s %s\n' "$1" "$2"; pass=$((pass + 1)); }
meh()  { printf '  SKIP  %-28s %s\n' "$1" "$2"; skip=$((skip + 1)); }
hmm()  { printf '  WARN  %-28s %s\n' "$1" "$2"; warn=$((warn + 1)); annotate warning "$1" "$2"; }
no()   { printf '  FAIL  %-28s %s\n' "$1" "$2"; fail=$((fail + 1)); annotate error "$1" "$2"; }

# status URL -- the HTTP status of one GET, "000" when nothing answered.
status() { curl -sS -o /dev/null -w '%{http_code}' --max-time 15 "$1" 2>/dev/null || true; }

printf '\nOhCamel uptime -- public %s, live %s%s\n\n' "$PUBLIC" "$LIVE" "${ENGINE:+, engine $ENGINE}"

# ---------------------------------------------------------------------------
# The public host answers
# ---------------------------------------------------------------------------
for path in / /api/health; do
	url="https://$PUBLIC$path"
	code=$(status "$url")
	if [ "$code" = 200 ]; then ok "$url " "200"; else no "$url " "${code:-000}, expected 200"; fi
done

# ---------------------------------------------------------------------------
# The engine is recomputing: nodes_recomputed advances across 2 s
# ---------------------------------------------------------------------------
# counter URL -- prints "CODE N", N empty when the body carries no counter.
counter() {
	local raw code body
	raw=$(curl -sS --max-time 15 -w '\n%{http_code}' "$1" 2>/dev/null || true)
	code=${raw##*$'\n'}
	body=${raw%$'\n'*}
	[ "$raw" = "$code" ] && body=""
	# The bridge wraps the engine's snapshot as {"snapshot": {...}}; the
	# engine's own /api/snapshot is the bare object. The first
	# "nodes_recomputed" in either is the one.
	printf '%s %s\n' "${code:-000}" "$(printf '%s' "$body" | grep -o '"nodes_recomputed": *[0-9]*' | head -1 | grep -o '[0-9]*$')"
}

if [ -n "$ENGINE" ]; then
	src="$ENGINE/api/snapshot"
else
	src="https://$PUBLIC/api/engine/snapshot"
fi
read -r code_a n_a <<<"$(counter "$src")"
if [ -z "$ENGINE" ] && [ "$code_a" = 503 ]; then
	meh "engine counter" "the public bridge is off (503 from $src); nothing to read without a password"
elif [ "$code_a" != 200 ] || [ -z "$n_a" ]; then
	no "engine counter" "$src answered ${code_a:-000} with no nodes_recomputed"
else
	sleep 2
	read -r code_b n_b <<<"$(counter "$src")"
	if [ "$code_b" != 200 ] || [ -z "$n_b" ]; then
		no "engine counter" "$src answered ${code_b:-000} on the second read"
	elif [ "$n_b" -le "$n_a" ]; then
		no "engine counter" "frozen: nodes_recomputed stuck at $n_a across 2 s ($src)"
	else
		ok "engine counter" "$((n_b - n_a)) nodes recomputed in 2 s ($src)"
	fi
fi

# ---------------------------------------------------------------------------
# /api/desk has its shape (an anonymous engine only)
# ---------------------------------------------------------------------------
if [ -n "$ENGINE" ]; then
	desk=$(curl -sS --max-time 15 "$ENGINE/api/desk" 2>/dev/null || true)
	dstatus=$(printf '%s' "$desk" | grep -o '"status": *"[a-z]*"' | head -1 | sed 's/.*"\([a-z]*\)"$/\1/')
	dvenue=$(printf '%s' "$desk" | grep -o '"venue": *"[A-Za-z0-9_.-]*"' | head -1 | sed 's/.*"\([^"]*\)"$/\1/')
	case "$dstatus" in
	enabled | disabled)
		if [ -n "$dvenue" ]; then
			ok "/api/desk shape" "status $dstatus, venue $dvenue"
		else
			no "/api/desk shape" "no venue in: ${desk:0:200}"
		fi
		;;
	*) no "/api/desk shape" "status ${dstatus:-missing}, expected enabled or disabled: ${desk:0:200}" ;;
	esac
else
	meh "/api/desk shape" "no anonymous engine (--engine); the live desk's 401 is checked below"
fi

# ---------------------------------------------------------------------------
# HTTP redirects to HTTPS, on both hosts. Read, never followed.
# ---------------------------------------------------------------------------
for host in "$PUBLIC" "$LIVE"; do
	url="http://$host/"
	read -r code to <<<"$(curl -sS -o /dev/null -w '%{http_code} %{redirect_url}' --max-time 15 "$url" 2>/dev/null || true)"
	case "${code:-000}:${to:-}" in
	301:"https://$host/"* | 302:"https://$host/"* | 307:"https://$host/"* | 308:"https://$host/"*)
		ok "$url " "$code to $to" ;;
	*) no "$url " "${code:-000}${to:+ to $to}, expected a redirect to https://$host/" ;;
	esac
done

# ---------------------------------------------------------------------------
# The live host refuses an anonymous caller
# ---------------------------------------------------------------------------
for path in / /api/desk; do
	url="https://$LIVE$path"
	code=$(status "$url")
	if [ "$code" = 401 ]; then ok "$url " "401"; else no "$url " "${code:-000}, expected 401: the live host must ask for its password"; fi
done

# ---------------------------------------------------------------------------
# Certificates: > 21 days, else warn; > 10 days, else fail
# ---------------------------------------------------------------------------
tmo=()
command -v timeout >/dev/null 2>&1 && tmo=(timeout 20)
for host in "$PUBLIC" "$LIVE"; do
	pem=$("${tmo[@]+"${tmo[@]}"}" openssl s_client -connect "$host:443" -servername "$host" </dev/null 2>/dev/null |
		sed -n '/-----BEGIN CERTIFICATE-----/,/-----END CERTIFICATE-----/p')
	if [ -z "$pem" ]; then
		no "cert $host" "no certificate from $host:443"
		continue
	fi
	until=$(printf '%s\n' "$pem" | openssl x509 -noout -enddate 2>/dev/null | sed 's/^notAfter=//')
	if ! printf '%s\n' "$pem" | openssl x509 -noout -checkend "$FAIL_SECS" >/dev/null 2>&1; then
		no "cert $host" "10 days or fewer left (notAfter ${until:-unknown}): renewal has failed"
	elif ! printf '%s\n' "$pem" | openssl x509 -noout -checkend "$WARN_SECS" >/dev/null 2>&1; then
		hmm "cert $host" "21 days or fewer left (notAfter ${until:-unknown}): Caddy should have renewed by now"
	else
		ok "cert $host" "more than 21 days left (notAfter ${until:-unknown})"
	fi
done

printf '\nuptime: %d pass, %d warn, %d fail, %d skip\n' "$pass" "$warn" "$fail" "$skip"
[ "$fail" -eq 0 ]
