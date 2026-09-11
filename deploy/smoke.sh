#!/usr/bin/env bash
#
# Post-deploy verification for OhCamel.
#
#   ./smoke.sh                              # the localhost harness on :8000
#   ./smoke.sh https://ohcamel.example.com  # production
#   ./smoke.sh https://ohcamel.example.com --live https://live.ohcamel.example.com
#   ./smoke.sh https://ohcamel.example.com --live https://live.ohcamel.example.com \
#              --expect-sha "$(git rev-parse HEAD)"      # what deploy.sh runs
#
# Deployment has no unit tests worth writing. What it has is a handful of
# assertions run against the real thing after every deploy, and one of them
# carries all the weight -- see STREAM below.
#
# Exits non-zero on the first hard failure, naming the assertion rather than
# printing a stack trace.

set -uo pipefail

BASE="http://localhost:8000"
LIVE=""
SSE_WINDOW=20
EXPECT_SHA=""

# The routes table, as this suite knows it. lib/server.ml's `routes` is the
# one table the dispatcher and the 404 body are both generated from, and this
# string is its shadow: a shell script cannot read OCaml, so it carries the
# list and asserts the 404 body equals it, in order. Adding a route means
# adding it here in the same commit -- the assertion fails until you do,
# which is the point of having it.
EXPECTED_ROUTES="/ /ops /api/snapshot /api/health /api/stream /api/history /api/stress /api/graph /api/reports /api/reports/garch /api/ops"

# The first bare argument is the base URL; everything else is a flag. Written
# out rather than clever, because a smoke script that misparses its own
# arguments reports on the wrong host and is worse than no smoke script.
if [ $# -gt 0 ] && [ "${1#-}" = "$1" ]; then
	BASE="$1"
	shift
fi
while [[ $# -gt 0 ]]; do
	case "$1" in
	--live) [ $# -ge 2 ] || { echo "smoke: --live requires a value" >&2; exit 2; }; LIVE="$2"; shift 2 ;;
	--sse-window) [ $# -ge 2 ] || { echo "smoke: --sse-window requires a value" >&2; exit 2; }; SSE_WINDOW="$2"; shift 2 ;;
	--expect-sha) [ $# -ge 2 ] || { echo "smoke: --expect-sha requires a value" >&2; exit 2; }; EXPECT_SHA="$2"; shift 2 ;;
	*) echo "smoke: unknown argument $1" >&2; exit 2 ;;
	esac
done

BASE="${BASE%/}"
LIVE="${LIVE%/}"

pass=0; fail=0; skip=0
ok()   { printf '  \033[32mPASS\033[0m  %s\n' "$1"; pass=$((pass + 1)); }
no()   { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; [ -n "${2:-}" ] && printf '        %s\n' "$2"; fail=$((fail + 1)); }
meh()  { printf '  \033[33mSKIP\033[0m  %s\n' "$1"; skip=$((skip + 1)); }

printf '\nOhCamel smoke -- %s\n\n' "$BASE"

# ---------------------------------------------------------------------------
# 1. The dashboard itself
# ---------------------------------------------------------------------------
code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 15 "$BASE/" 2>/dev/null)
[ "$code" = "200" ] && ok "GET /                       200" \
	|| no "GET /                       $code" "the dashboard did not render"

# ---------------------------------------------------------------------------
# 2. Health
# ---------------------------------------------------------------------------
code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 15 "$BASE/api/health" 2>/dev/null)
[ "$code" = "200" ] && ok "GET /api/health             200" \
	|| no "GET /api/health             $code"

# ---------------------------------------------------------------------------
# 3. A snapshot that is actually a snapshot
#
# 200 with an empty body would pass a naive check. Two things are asserted
# instead. First, that the engine stabilized and produced numbers: a book with
# positions and a gross exposure. Second -- and this is the one worth having --
# that nodes_recomputed ADVANCES between two reads a second apart.
#
# A served-from-cache snapshot, a wedged scheduler, or an engine that
# stabilized once at startup and then stopped all produce a perfectly valid
# JSON body forever. Only a graph that is still recomputing produces a rising
# counter, and that counter is the project's own evidence of its thesis.
# ---------------------------------------------------------------------------
read_snapshot() { curl -sS --max-time 15 "$BASE/api/snapshot" 2>/dev/null; }

snap_a=$(read_snapshot)
/bin/sleep 2
snap_b=$(read_snapshot)

if command -v python3 >/dev/null 2>&1; then
	detail=$(printf '%s\n%s' "$snap_a" "$snap_b" | python3 -c '
import json, sys
raw = sys.stdin.read().splitlines()
try:
    a = json.loads(raw[0]); b = json.loads(raw[-1])
except Exception as e:
    print("NOTJSON %s" % e); raise SystemExit
g = a.get("gross_exposure")
if not isinstance(g, (int, float)):
    print("NOGROSS gross_exposure=%r" % (g,)); raise SystemExit
if not a.get("positions"):
    print("NOPOSITIONS the book is empty"); raise SystemExit
ra, rb = a.get("nodes_recomputed"), b.get("nodes_recomputed")
if not isinstance(ra, int) or not isinstance(rb, int):
    print("NOCOUNTER nodes_recomputed=%r" % (ra,)); raise SystemExit
if rb <= ra:
    print("FROZEN nodes_recomputed stuck at %d across 2s" % ra); raise SystemExit
print("OK %d positions, gross %.0f, %d nodes recomputed in 2s"
      % (len(a["positions"]), g, rb - ra))
' 2>/dev/null)
else
	case "$snap_a" in
	*'"gross_exposure"'*) detail="OK (grep only; python3 unavailable for a real parse)" ;;
	*) detail="NOGROSS" ;;
	esac
fi
case "$detail" in
OK*) ok "GET /api/snapshot           ${detail#OK }" ;;
FROZEN*) no "GET /api/snapshot           the graph is not recomputing" "$detail" ;;
*)   no "GET /api/snapshot           malformed" "${detail:-no response}" ;;
esac

# ---------------------------------------------------------------------------
# 4. STREAM
#
# Counting frames is not enough. A proxy that buffers still eventually emits
# what it accumulated, so a naive count can pass against a dashboard that is,
# to a human watching it, frozen. What separates streaming from buffering is
# not how many frames arrive but WHEN: a streaming connection spreads them
# across the whole window, a buffered one delivers the pile at once. So this
# records the arrival second of every frame and requires the first and last to
# be meaningfully apart.
#
# On what this assertion actually catches, honestly. Three deliberate proxy
# misconfigurations were tried against it -- flush_interval 30s, gzip applied
# to the stream, and nginx with proxy_buffering on -- and it passed all three,
# because Caddy and nginx both special-case text/event-stream and flush it
# regardless. The buffering scenario is real in principle and hard to provoke
# in practice.
#
# What it does catch, verified by pausing the engine container mid-run: an
# engine that has died, wedged, or stopped ticking. All four assertions in this
# suite failed and the suite exited 1, which is the outcome that matters --
# a deploy must not report success while serving a dashboard that never moves.
# The three distinct failure messages below exist because those three causes
# want different first debugging steps.
# ---------------------------------------------------------------------------
frames_file=$(mktemp)
trap 'rm -f "$frames_file"' EXIT

# --compressed matters more than it looks. A browser sends
# `Accept-Encoding: gzip` on every request including the EventSource one; curl
# sends it only when asked. Without this flag the probe never exercises the
# compression path, so a proxy configured to gzip the stream -- which buffers
# it, because gzip emits blocks -- would pass this suite and fail every real
# visitor. The probe has to ask for what a browser asks for.
curl -sS -N --compressed --max-time "$SSE_WINDOW" -H 'Accept: text/event-stream' \
	"$BASE/api/stream" 2>/dev/null |
	while IFS= read -r line; do
		case "$line" in
		data:*) printf '%s\t%s\n' "$(date +%s)" "$line" >>"$frames_file" ;;
		esac
	done

total=$(wc -l <"$frames_file" | tr -d ' ')
distinct=$(cut -f2- <"$frames_file" | sort -u | wc -l | tr -d ' ')
if [ "$total" -ge 2 ]; then
	first=$(head -1 "$frames_file" | cut -f1)
	last=$(tail -1 "$frames_file" | cut -f1)
	spread=$((last - first))
else
	spread=0
fi

if [ "$total" -lt 2 ]; then
	no "SSE /api/stream            $total frame(s) in ${SSE_WINDOW}s" \
		"expected a continuous stream; the engine may not be ticking"
elif [ "$distinct" -lt 2 ]; then
	no "SSE /api/stream            $total frames, all identical" \
		"frames arrive but nothing changes -- the graph may not be stabilizing"
elif [ "$spread" -lt 2 ]; then
	no "SSE /api/stream            $total frames delivered in ${spread}s -- BUFFERED" \
		"the proxy is accumulating frames instead of flushing them."
	printf '        %s\n' "check flush_interval -1 in deploy/Caddyfile.snippets"
else
	ok "SSE /api/stream            $distinct distinct frames spread over ${spread}s"
fi

# ---------------------------------------------------------------------------
# 4a. Which build this is, the page that says so, and the 404 that lists it
#
# Before this block the suite could pass against yesterday's container: `up -d`
# is a no-op when the image did not change, and a build that failed left the
# old image serving, so "9 passed" said nothing about whether the commit just
# pushed was the one answering. /api/ops carries the sha dune was given at
# build time, deploy.sh hands this suite the sha it built from, and the two
# must agree; and uptime must be under five minutes, because a matching sha
# on a container that has been up for a week means the deploy replaced
# nothing. Neither check needs the page; both are what the page would show a
# human, made mechanical.
# ---------------------------------------------------------------------------
ops_sha=""; ops_up=""
if command -v python3 >/dev/null 2>&1; then
	ops=$(curl -sS --max-time 15 "$BASE/api/ops" 2>/dev/null | python3 -c '
import json, sys
try:
    o = json.load(sys.stdin)
except Exception as e:
    print("NOTJSON %s" % e); raise SystemExit
mode = o.get("mode"); up = o.get("uptime_s"); b = o.get("build") or {}
if mode not in ("demo", "live"):
    print("NOMODE mode=%r" % (mode,)); raise SystemExit
if not isinstance(up, (int, float)):
    print("NOUPTIME uptime_s=%r" % (up,)); raise SystemExit
if not isinstance(b.get("git_sha"), str) or not b.get("git_sha"):
    print("NOSHA build.git_sha=%r" % (b.get("git_sha"),)); raise SystemExit
print("OK %s %s %d" % (mode, b["git_sha"], up))
' 2>/dev/null)
	case "$ops" in
	OK*)
		read -r _ ops_mode ops_sha ops_up <<<"$ops"
		ok "GET /api/ops                mode $ops_mode, build ${ops_sha:0:7}, up ${ops_up} s"
		;;
	*) no "GET /api/ops                malformed" "${ops:-no response}" ;;
	esac

	if [ -n "$EXPECT_SHA" ]; then
		if [ -n "$ops_sha" ] && [ "$ops_sha" = "$EXPECT_SHA" ]; then
			ok "build.git_sha               matches ${EXPECT_SHA:0:7}"
		else
			no "build.git_sha               ${ops_sha:-unreadable}, expected ${EXPECT_SHA:0:7}" \
				"the image answering was not built from this checkout: the build failed, or up -d kept the old image"
		fi
		if [ -n "$ops_up" ] && [ "$ops_up" -lt 300 ]; then
			ok "uptime_s                    ${ops_up} s, under 300: this deploy's container"
		else
			no "uptime_s                    ${ops_up:-unreadable} s, expected under 300" \
				"a container this old was not replaced by this deploy"
		fi
	else
		meh "build sha                  not given (--expect-sha SHA), skipping"
	fi
else
	if [ -n "$EXPECT_SHA" ]; then
		no "build sha                  python3 missing, cannot verify" \
			"--expect-sha ${EXPECT_SHA:0:7} was given but there is no python3 on this host to parse /api/ops"
	else
		meh "GET /api/ops                python3 unavailable for a real parse; build sha and uptime not checked"
	fi
fi

# The page exists and is the page: both host columns are in the body, which
# is the DOM contract ops.js fills. No python needed; the two ids are literal.
page=$(curl -sS --max-time 15 -w '\n%{http_code}' "$BASE/ops" 2>/dev/null)
page_code="${page##*$'\n'}"
case "$page_code:$page" in
200:*'id="this-host"'*'id="peer"'*) ok "GET /ops                    200, both host columns present" ;;
200:*) no "GET /ops                    200, but not the ops page" "the body has no #this-host / #peer" ;;
*)     no "GET /ops                    ${page_code:-no response}" ;;
esac

# The 404 body is generated from the same table `handle` dispatches on, and
# EXPECTED_ROUTES is that table's shadow. Equality in order, not membership: a
# route that is served and not listed is the exact drift the table was
# introduced to make impossible, and a suite that only asked "is /api/ops in
# there" would wave it through.
if command -v python3 >/dev/null 2>&1; then
	listed=$(curl -sS --max-time 15 -w '\n%{http_code}' "$BASE/api/nope" 2>/dev/null | python3 -c '
import json, sys
raw = sys.stdin.read().rsplit("\n", 1)
try:
    body = json.loads(raw[0]); code = raw[1]
except Exception as e:
    print("NOTJSON %s" % e); raise SystemExit
if code != "404":
    print("CODE %s, expected 404" % code); raise SystemExit
if body.get("error") != "not found":
    print("ERROR error=%r" % (body.get("error"),)); raise SystemExit
print("OK " + " ".join(body.get("routes") or []))
' 2>/dev/null)
	case "$listed" in
	OK*)
		if [ "${listed#OK }" = "$EXPECTED_ROUTES" ]; then
			ok "GET /api/nope               404, lists the $(echo "$EXPECTED_ROUTES" | wc -w | tr -d ' ') routes in the table's order"
		else
			no "GET /api/nope               404, but its routes are not EXPECTED_ROUTES" "served:   ${listed#OK }"
			printf '        %s\n' "expected: $EXPECTED_ROUTES"
		fi
		;;
	*) no "GET /api/nope               malformed" "${listed:-no response}" ;;
	esac
else
	meh "GET /api/nope               python3 unavailable for a real parse; the 404 route list not checked"
fi
# -- Phase 5's block 4b (the routes the page reads) is inserted immediately below this line --

# ---------------------------------------------------------------------------
# 4b. The reports the page reads
#
# The argument below the ledger is filled from three routes, and each has a
# way to be present and empty that a 200 alone would wave through: a reports
# object computed on nothing, a GARCH study that never finishes, a stress
# suite that forked no scenarios. So each is read for its substance. GARCH is
# polled, because it runs on a second domain after listen and a fresh container
# is honestly still computing; it gets two minutes.
# ---------------------------------------------------------------------------
if command -v python3 >/dev/null 2>&1; then
	rep=$(curl -sS --compressed --max-time 20 "$BASE/api/reports" 2>/dev/null | python3 -c '
import json, sys
try:
    r = json.load(sys.stdin)
except Exception as e:
    print("NOTJSON %s" % e); raise SystemExit
v = r.get("validation") or {}
n = len((v.get("synthetic") or {}).get("rows") or []) + len((v.get("crisis") or {}).get("rows") or [])
s = len((r.get("scaling") or {}).get("rows") or [])
o = len((r.get("options") or {}).get("states") or [])
if n != 18 or s != 3 or o != 4:
    print("SHAPE validation rows %d (18), scaling rows %d (3), option states %d (4)" % (n, s, o)); raise SystemExit
print("OK %d %s" % (n, r.get("computed_in_ms")))
' 2>/dev/null)
	case "$rep" in
	OK*) read -r _ rep_rows rep_ms <<<"$rep"; ok "GET /api/reports            $rep_rows validation rows, 3 scaling, options; computed in ${rep_ms%.*} ms" ;;
	*)   no "GET /api/reports            malformed" "${rep:-no response}" ;;
	esac

	stress=$(curl -sS --max-time 20 "$BASE/api/stress" 2>/dev/null | python3 -c '
import json, sys
try:
    x = json.load(sys.stdin)
except Exception as e:
    print("NOTJSON %s" % e); raise SystemExit
sc = x.get("scenarios") or []
if len(sc) != 12 or not x.get("worst"):
    print("SHAPE %d scenarios, worst=%r" % (len(sc), x.get("worst"))); raise SystemExit
print("OK %s %s" % (x["worst"], x.get("counter_cost")))
' 2>/dev/null)
	case "$stress" in
	OK*) read -r _ worst cost <<<"$stress"; ok "GET /api/stress             12 scenarios on forks, worst $worst, counter cost $cost" ;;
	*)   no "GET /api/stress             malformed" "${stress:-no response}" ;;
	esac

	garch_state=""
	for _ in $(seq 1 24); do
		garch_state=$(curl -sS --max-time 10 "$BASE/api/reports/garch" 2>/dev/null | python3 -c '
import json, sys
try:
    g = json.load(sys.stdin)
except Exception:
    print("NOTJSON"); raise SystemExit
if g.get("status") == "done":
    print("DONE %d %s" % (len(g.get("rows") or []), g.get("computed_in_ms")))
else:
    print("%s %s/%s" % (str(g.get("status")).upper(), g.get("done"), g.get("of")))
' 2>/dev/null)
		case "$garch_state" in DONE*|FAILED*|ABSENT*) break ;; esac
		/bin/sleep 5
	done
	case "$garch_state" in
	"DONE 6 "*) read -r _ _ garch_ms <<<"$garch_state"; ok "GET /api/reports/garch      done, 6 rows, ${garch_ms%.*} ms on a second domain" ;;
	*)          no "GET /api/reports/garch      not done within 120 s" "${garch_state:-no response}" ;;
	esac
else
	meh "GET /api/reports            python3 unavailable; the report routes not checked"
fi

# ---------------------------------------------------------------------------
# 5. TLS, and the redirect onto it (production only)
# ---------------------------------------------------------------------------
case "$BASE" in
https://*)
	host="${BASE#https://}"; host="${host%%/*}"
	code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 15 "http://$host/" 2>/dev/null)
	case "$code" in
	30*) ok "http://$host              redirects ($code)" ;;
	*)   no "http://$host              $code, expected a 3xx redirect" ;;
	esac

	if curl -sS -o /dev/null --max-time 15 "https://$host/" 2>/dev/null; then
		ok "TLS certificate            valid for $host"
	else
		no "TLS certificate            rejected for $host" "curl refused the chain"
	fi

	# The engines must not be reachable except through the proxy. If they are,
	# the basic-auth on the live host is decoration -- anyone can ask the
	# droplet for :8081 and skip it.
	for port in 8080 8081; do
		if curl -sS -o /dev/null --max-time 5 "http://$host:$port/api/health" 2>/dev/null; then
			no "port $port                  REACHABLE from outside" \
				"it must be published only to caddy, never to the host"
		else
			ok "port $port                  not reachable from outside"
		fi
	done
	;;
*)
	meh "TLS, redirect, port exposure -- localhost harness, not applicable"
	;;
esac

# ---------------------------------------------------------------------------
# 6. The live host refuses anonymous callers
# ---------------------------------------------------------------------------
if [ -n "$LIVE" ]; then
	# Five paths, not one. The gate is Caddy's basic_auth on the whole host,
	# and the tempting way to fill the ops page's peer column from the public
	# origin is a matcher that exempts /api/ops from it. That hole would show
	# up here as a 200 on one path while / still said 401. The page fills its
	# peer column the other way round -- the live origin reads the demo, over
	# the demo engine's own CORS header -- so the live host never needs one.
	for path in / /ops /api/ops /api/snapshot /api/health; do
		code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 15 "$LIVE$path" 2>/dev/null)
		[ "$code" = "401" ] && ok "GET $LIVE$path  401 without credentials" \
			|| no "GET $LIVE$path  $code, expected 401" "the live host is not gated on $path"
	done
else
	meh "live host                  not given (--live URL), skipping"
fi

# ---------------------------------------------------------------------------
printf '\n  %d passed, %d failed, %d skipped\n\n' "$pass" "$fail" "$skip"
[ "$fail" -eq 0 ] || exit 1
