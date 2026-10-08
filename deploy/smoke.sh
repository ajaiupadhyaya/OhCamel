#!/usr/bin/env bash
#
# Post-deploy verification for OhCamel.
#
#   ./smoke.sh                              # the localhost harness: Quant on :8000
#   ./smoke.sh http://localhost:8000 --engine http://localhost:8001
#                                           # + the synthetic engine (make deploy-verify)
#   ./smoke.sh https://ohcamel.example.com  # production, public host only
#   ./smoke.sh https://ohcamel.example.com --live https://live.ohcamel.example.com \
#              --expect-sha "$(git rev-parse HEAD)"      # what deploy.sh runs
#   ./smoke.sh https://ohcamel.example.com --live-container --expect-sha SHA
#                                           # + the live desk, read from inside
#                                           # its own container (section 7)
#
# BASE (the first bare argument) is the PUBLIC host, which serves OhCamel
# Quant: the FastAPI + React app on real market data. Section Q below asserts
# that it answers with real numbers -- rows, a yield curve, a VaR -- not only
# that it answers.
#
# --engine URL runs the OCaml engine suite (sections 1-4b) against an engine
# that is reachable without a password: the localhost harness's synthetic
# demo engine on :8001. The public host no longer serves an engine, and the
# live engine sits behind basic auth, so in production the engine is checked
# through the Quant app's read-only bridge (section Q, /api/engine/*) and by
# the anonymous-caller refusals of section 6.
#
# --expect-sha SHA asserts that the containers answering were built from SHA
# and started by this deploy. For the Quant app (and, with --live, the live
# engine) that is read from the running container's image label via the
# docker CLI, so it is meaningful where deploy.sh runs it: on the droplet.
#
# --live-container checks the live host from INSIDE its container, through
# `docker compose -f deploy/docker-compose.yml --profile live exec -T
# ohcamel-live curl http://localhost:8081/...` -- the runtime image ships curl
# for its healthcheck -- so the suite needs no password and no credential
# enters it. Run it on the droplet, where the containers are; section 7 says
# what it asserts. deploy/test/smoke_live_test.sh drives it against shims.
#
# Deployment has no unit tests worth writing. What it has is a handful of
# assertions run against the real thing after every deploy, and for the
# engine one of them carries all the weight -- see STREAM below.
#
# Exits non-zero if any assertion failed, naming the assertion rather than
# printing a stack trace.

set -uo pipefail

BASE="http://localhost:8000"
LIVE=""
ENGINE=""
SSE_WINDOW=20
EXPECT_SHA=""
LIVE_CONTAINER=0

# The routes, as this suite knows them. The 404 body lists lib/server.ml's
# `routes` table, the one the dispatcher is generated from, and then the
# extensions the process was created with, in the order given -- the desk's
# ten routes, on both hosts: /api/desk, then tca, sessions, preview, orders,
# cancel, kill and kill/reset under it (the last four answer 405 on the demo
# host, and are listed there all the same), then /api/research, the signal
# intake's strategies and judgements, and /api/research/evidence, EXP-A01's
# committed manifests. The table itself carries the six pages, /research
# after /execution. This string is that list's shadow:
# a shell script cannot read OCaml, so it carries the list and asserts the 404
# body equals it, in order. Adding a route or an extension means adding it here
# in the same commit -- the assertion fails until you do, which is the point of
# having it.
EXPECTED_ROUTES="/ /ops /argument /risk /execution /research /api/snapshot /api/health /api/stream /api/history /api/stress /api/graph /api/heat /api/reports /api/reports/garch /api/ops /api/desk /api/desk/tca /api/desk/sessions /api/desk/preview /api/desk/orders /api/desk/cancel /api/desk/kill /api/desk/kill/reset /api/research /api/research/evidence"

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
	--engine) [ $# -ge 2 ] || { echo "smoke: --engine requires a value" >&2; exit 2; }; ENGINE="$2"; shift 2 ;;
	--sse-window) [ $# -ge 2 ] || { echo "smoke: --sse-window requires a value" >&2; exit 2; }; SSE_WINDOW="$2"; shift 2 ;;
	--expect-sha) [ $# -ge 2 ] || { echo "smoke: --expect-sha requires a value" >&2; exit 2; }; EXPECT_SHA="$2"; shift 2 ;;
	--live-container) LIVE_CONTAINER=1; shift ;;
	*) echo "smoke: unknown argument $1" >&2; exit 2 ;;
	esac
done

BASE="${BASE%/}"
LIVE="${LIVE%/}"
ENGINE="${ENGINE%/}"

pass=0; fail=0; skip=0
ok()   { printf '  \033[32mPASS\033[0m  %s\n' "$1"; pass=$((pass + 1)); }
no()   { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; [ -n "${2:-}" ] && printf '        %s\n' "$2"; fail=$((fail + 1)); }
meh()  { printf '  \033[33mSKIP\033[0m  %s\n' "$1"; skip=$((skip + 1)); }

have_py=0
command -v python3 >/dev/null 2>&1 && have_py=1

# qjson LABEL METHOD PATH BODY PYCHECK
#
# One request to the Quant app, its body and status piped to a python check.
# PYCHECK sees `body` (parsed JSON) and `code` (the status, a string) and
# prints "OK <summary>" or a reason; anything but OK is a FAIL naming it. A
# 503 carries the app's own `detail` -- why a data source was unavailable --
# and that detail is what gets printed, because "503" alone sends the reader
# to the wrong log.
qjson() {
	local label="$1" method="$2" path="$3" data="$4" check="$5" out
	if [ "$have_py" != 1 ]; then
		meh "$label python3 unavailable for a real parse; not checked"
		return
	fi
	if [ "$method" = POST ]; then
		out=$(curl -sS --compressed --max-time 90 -w '\n%{http_code}' -X POST \
			-H 'Content-Type: application/json' -d "$data" "$BASE$path" 2>/dev/null)
	else
		out=$(curl -sS --compressed --max-time 90 -w '\n%{http_code}' "$BASE$path" 2>/dev/null)
	fi
	out=$(printf '%s' "$out" | QCHECK="$check" python3 -c '
import json, os, sys
raw = sys.stdin.read().rsplit("\n", 1)
code = raw[-1] if len(raw) == 2 else "000"
try:
    body = json.loads(raw[0])
except Exception as e:
    print("NOTJSON HTTP %s: %s" % (code, e)); raise SystemExit
if code == "503":
    print("UNAVAILABLE %s" % (body.get("detail") if isinstance(body, dict) else body)); raise SystemExit
if code != "200":
    print("CODE %s %s" % (code, str(body)[:200])); raise SystemExit
def find(o, pred, depth=0):
    """Every (key, value) under o, depth-first, where pred(key, value)."""
    if depth > 8: return
    if isinstance(o, dict):
        for k, v in o.items():
            if pred(k, v): yield k, v
            yield from find(v, pred, depth + 1)
    elif isinstance(o, list):
        for v in o[:200]:
            yield from find(v, pred, depth + 1)
exec(os.environ["QCHECK"])
' 2>&1)
	case "$BASE:$out" in
	*:OK*) ok "$label ${out#OK }" ;;
	# On the localhost harness (CI's image job) a vendor that does not answer
	# the runner -- FRED times out from GitHub's network -- is the vendor's
	# state, not the image's, so it warns; on a deployed host it still fails.
	http://localhost:*:UNAVAILABLE*|http://127.0.0.1:*:UNAVAILABLE*) meh "$label vendor unavailable from this network: ${out#UNAVAILABLE }" ;;
	*) no "$label" "${out:-no response}" ;;
	esac
}

# ---------------------------------------------------------------------------
# Q. OhCamel Quant, the public site
#
# Real data only, so every data check asserts SUBSTANCE: a sector table with
# rows, a yield curve with points, a VaR that is a number. A 503 here is the
# app honestly saying a vendor did not answer; on a deployed host it is still a
# failure of the deploy, and its `detail` (printed) says which vendor and why.
# On the localhost harness it is a warning (see qjson).
# ---------------------------------------------------------------------------
printf '\nOhCamel smoke -- %s\n\n' "$BASE"

index=$(curl -sS --compressed --max-time 15 -D - "$BASE/" 2>/dev/null | tr -d '\r')
index_code=$(printf '%s\n' "$index" | sed -n '1s/^HTTP[^ ]* \([0-9]*\).*/\1/p')
index_ctype=$(printf '%s\n' "$index" | sed -n 's/^[Cc]ontent-[Tt]ype: *//p' | head -1)
case "$index_code:$index_ctype:$index" in
200:text/html*:*'id="root"'*) ok "GET /                       200, text/html, the SPA shell" ;;
200:text/html*:*) no "GET /                       200, text/html, but no #root" "the built web app (quant/web/dist) is missing from the image" ;;
*) no "GET /                       ${index_code:-no response} ${index_ctype:-}" "the Quant app did not render" ;;
esac

# A client-side route must come back as the same shell, or a reload on
# /portfolio is a 404.
code=$(curl -sS -o /dev/null -w '%{http_code}:%{content_type}' --max-time 15 "$BASE/portfolio" 2>/dev/null)
case "$code" in
200:text/html*) ok "GET /portfolio              200, text/html (SPA fallback)" ;;
*) no "GET /portfolio              ${code:-no response}, expected 200 text/html" "client-side routes are not falling back to index.html" ;;
esac

# The bundle the shell names is served, and immutable-cached.
asset=$(printf '%s\n' "$index" | grep -o '/assets/[A-Za-z0-9._-]*\.js' | head -1)
if [ -n "$asset" ]; then
	hdrs=$(curl -sS -o /dev/null -D - --max-time 15 "$BASE$asset" 2>/dev/null | tr -d '\r')
	acode=$(printf '%s\n' "$hdrs" | sed -n '1s/^HTTP[^ ]* \([0-9]*\).*/\1/p')
	case "$acode:$hdrs" in
	200:*immutable*) ok "GET $asset  200, immutable" ;;
	200:*) no "GET $asset  200, but not Cache-Control immutable" "check the ohcamel_quant snippet in deploy/Caddyfile.snippets" ;;
	*) no "GET $asset  ${acode:-no response}" "the shell names a bundle the server does not have" ;;
	esac
else
	no "GET /assets/*.js            the shell names no /assets/*.js bundle"
fi

# The CSP must admit every inline script the shell carries, or the page
# breaks in a browser while every curl above passes. Compared here, served
# page against served header, so a changed snippet in quant/web/index.html is
# caught by the deploy that ships it (deploy/test/csp_hash_test.sh catches it
# earlier, in CI).
if [ "$have_py" = 1 ]; then
	csp=$(printf '%s' "$index" | python3 -c '
import base64, hashlib, re, sys
raw = sys.stdin.read()
head, _, body = raw.partition("\n\n")
csp = ""
for line in head.splitlines():
    if line.lower().startswith("content-security-policy:"):
        csp = line.split(":", 1)[1].strip()
if not csp:
    print("NOCSP no Content-Security-Policy header"); raise SystemExit
need = []
for m in re.finditer(r"<script\b([^>]*)>(.*?)</script>", body, re.S):
    attrs, code = m.group(1), m.group(2)
    if "src=" in attrs or re.search(r"type=\"?application/(ld\+)?json", attrs):
        continue
    need.append("sha256-" + base64.b64encode(hashlib.sha256(code.encode()).digest()).decode())
missing = [h for h in need if ("'"'"'%s'"'"'" % h) not in csp]
if missing:
    print("MISSING inline script(s) not allowed by the CSP: " + " ".join(missing)); raise SystemExit
print("OK %d inline script(s) allowed by hash" % len(need))
' 2>&1)
	case "$csp" in
	OK*) ok "CSP                         ${csp#OK }" ;;
	*) no "CSP                         the page would break in a browser" "$csp" ;;
	esac
else
	meh "CSP                         python3 unavailable; not checked"
fi

qjson "GET /api/health            " GET /api/health "" '
if body.get("status") != "ok":
    print("STATUS %r" % body.get("status")); raise SystemExit
if body.get("offline") is not False:
    print("OFFLINE the public site is in offline (fixtures-only) mode"); raise SystemExit
print("OK ok, version %s, online" % body.get("version"))
'

qjson "GET /api/market/universes  " GET /api/market/universes "" '
names = list(body.keys()) if isinstance(body, dict) else []
if isinstance(body, dict) and isinstance(body.get("universes"), (list, dict)):
    u = body["universes"]
    names = list(u.keys()) if isinstance(u, dict) else [x.get("id") or x.get("name") if isinstance(x, dict) else x for x in u]
if "sectors" not in json.dumps(body):
    print("SHAPE no \"sectors\" universe in %s" % str(body)[:200]); raise SystemExit
print("OK %d universes, sectors among them" % len(names))
'

qjson "GET /api/market/overview   " GET "/api/market/overview?universe=sectors" "" '
rows = body.get("rows") if isinstance(body, dict) else None
if not isinstance(rows, list):
    rows = next((v for k, v in find(body, lambda k, v: isinstance(v, list) and v and isinstance(v[0], dict))), None)
if not rows:
    print("EMPTY no rows in %s" % str(body)[:200]); raise SystemExit
priced = [r for r in rows if not r.get("error")]
if not priced:
    print("ERRORS every row carries an error, e.g. %r" % rows[0].get("error")); raise SystemExit
if not body.get("provenance"):
    print("NOPROVENANCE rows without provenance"); raise SystemExit
srcs = sorted({p.get("source", "?") for p in body["provenance"]})
stale = " -- ONLY committed fixtures: live vendors unreachable?" if all(x.startswith("fixture") for x in srcs) else ""
print("OK sectors, %d/%d rows priced, from %s%s" % (len(priced), len(rows), ", ".join(srcs), stale))
'

qjson "GET /api/macro/curve       " GET /api/macro/curve "" '
curve = body.get("curve") if isinstance(body, dict) else None
pts = 0
if isinstance(curve, list):
    pts = len(curve)
elif isinstance(curve, dict):
    pts = max((len(v) for v in curve.values() if isinstance(v, list)), default=len(curve))
if pts == 0:
    got = [v for k, v in find(body, lambda k, v: "yield" in str(k).lower() and isinstance(v, (list, dict)) and v)]
    pts = len(got[0]) if got else 0
if pts < 3:
    print("EMPTY no yield curve (%d points) in %s" % (pts, str(body)[:200])); raise SystemExit
print("OK %d tenors" % pts)
'

qjson "POST /api/risk/summary     " POST /api/risk/summary \
	'{"holdings":[{"ticker":"SPY","weight":0.6},{"ticker":"TLT","weight":0.4}]}' '
vars_ = [(k, v) for k, v in find(body, lambda k, v: ("var" in str(k).lower() or str(k).lower() in ("es", "cvar")) and isinstance(v, (int, float)) and not isinstance(v, bool))]
if not vars_:
    print("NOVAR no numeric VaR field in %s" % str(body)[:300]); raise SystemExit
k, v = vars_[0]
print("OK 60/40 SPY/TLT, %d VaR/ES figures (%s = %.4g)" % (len(vars_), k, v))
'

# The Flight Deck: a live reading of the reference book (marks, limits and the
# feeds behind them), and the flight recorder's status. The recorder being
# enabled is required on the public host (compose turns it on); how many rows
# it holds depends on how long the session has been open, so it is reported.
qjson "GET /api/deck/books        " GET /api/deck/books "" '
rec = body.get("recorder") or {}
if not any(b.get("key") == "core" for b in body.get("books", [])):
    print("NOBOOK no core reference book in %s" % str(body)[:200]); raise SystemExit
if not rec.get("enabled"):
    print("RECORDER the flight recorder is disabled (OHCAMEL_QUANT_RECORDER)"); raise SystemExit
print("OK core book, recorder %s, %s rows over %s sessions%s" % (
    "running" if rec.get("running") else "NOT RUNNING", rec.get("rows"), rec.get("sessions"),
    ", last error: " + rec["last_error"] if rec.get("last_error") else ""))
'

qjson "POST /api/deck/reading     " POST /api/deck/reading \
	'{"holdings":[{"ticker":"SPY","weight":0.6},{"ticker":"TLT","weight":0.4}]}' '
lims = body.get("limits") or []
feeds = {f.get("key"): f.get("state") for f in body.get("feeds", [])}
if len(lims) + len(body.get("unevaluated", [])) != 7:
    print("LIMITS expected the 7 default limits, got %s" % [x.get("name") for x in lims]); raise SystemExit
if feeds.get("quotes") not in ("ok", "stale"):
    print("QUOTES the quotes lamp is %r: %s" % (feeds.get("quotes"), str(body.get("feeds"))[:200])); raise SystemExit
srcs = sorted({m.get("source") for m in body.get("marks", []) if m.get("source")})
print("OK 60/40, %d limits evaluated, quotes %s from %s, session %s" % (
    len(lims), feeds.get("quotes"), ", ".join(srcs) or "?", "open" if body.get("clock", {}).get("is_open") else "closed"))
'

# Host telemetry (compute plan Task 0.4): ohcamel-hostd through the Quant
# app's proxy. Required on every host -- compose runs hostd in the default
# profile and points the app at it. hostd needs one sampling interval (5 s)
# before `latest` is non-null, so a stack that has only just started is given
# up to 15 s to produce its first sample before the check runs. Only an
# answer with a null `latest` waits: no answer, or a 503, goes straight to the
# check below, which reports it.
if [ "$have_py" = 1 ]; then
	for _ in 1 2 3; do
		rc=0
		curl -sS --max-time 5 "$BASE/api/ops/host" 2>/dev/null | python3 -c '
import json, sys
try:
    body = json.load(sys.stdin)
except Exception:
    sys.exit(0)
sys.exit(2 if isinstance(body, dict) and "latest" in body and body["latest"] is None else 0)' 2>/dev/null || rc=$?
		[ "$rc" = 2 ] || break
		sleep 5
	done
fi
qjson "GET /api/ops/host          " GET /api/ops/host "" '
latest = body.get("latest") or {}
if not (isinstance(latest.get("mem_available"), (int, float)) and latest["mem_available"] > 0):
    print("NOSAMPLE latest.mem_available is %r: %s" % (latest.get("mem_available"), str(body)[:200])); raise SystemExit
if (body.get("provenance") or [{}])[0].get("source") != "ohcamel-hostd":
    print("PROVENANCE %s" % str(body.get("provenance"))[:200]); raise SystemExit
print("OK cpu %.2f, steal %.2f, swap_used %d MiB, %d MiB available" % (
    latest.get("cpu") or 0.0, latest.get("steal") or 0.0,
    (latest.get("swap_used") or 0) // 2**20, latest["mem_available"] // 2**20))
'

# The engine bridge. Reachable is REQUIRED only under --engine (the local
# harness wires the bridge to its engine). In production the bridge is off
# unless the owner opts in (OHCAMEL_QUANT_ENGINE_URL in deploy/.env), so a 503
# there is the documented, honest answer and is only reported.
if [ "$have_py" = 1 ]; then
	bridge=$(curl -sS --max-time 15 -w '\n%{http_code}' "$BASE/api/engine/status" 2>/dev/null | python3 -c '
import json, sys
raw = sys.stdin.read().rsplit("\n", 1)
try:
    body = json.loads(raw[0]); code = raw[1]
except Exception as e:
    print("NOTJSON %s" % e); raise SystemExit
if code == "200" and body.get("reachable") is True:
    ops = body.get("ops") or {}
    print("OK %s %s" % (ops.get("mode") or "unknown", "healthy" if (body.get("health") or {}).get("healthy") else "feed-stale"))
elif code == "503":
    print("DOWN %s" % body.get("detail"))
else:
    print("CODE %s" % code)
' 2>&1)
	case "$bridge" in
	OK*) read -r _ bmode bfeed <<<"$bridge"; ok "GET /api/engine/status     reachable, engine mode $bmode, feed $bfeed" ;;
	DOWN*)
		if [ -n "$ENGINE" ]; then
			no "GET /api/engine/status     503, but an engine should be running" "${bridge#DOWN }"
		else
			meh "GET /api/engine/status     503 (${bridge#DOWN }) -- no engine expected"
		fi
		;;
	*) no "GET /api/engine/status     malformed" "${bridge:-no response}" ;;
	esac
	# The snapshot is asked only of a bridge that answered: since the Quant
	# cutover the public host serves no engine, and a --live run against it
	# must not fail on a bridge the status check already called absent.
	if [ -n "$ENGINE" ] || [ "${bridge#OK}" != "$bridge" ]; then
		esnap=$(curl -sS --compressed --max-time 15 "$BASE/api/engine/snapshot" 2>/dev/null | python3 -c '
import json, sys
try:
    b = json.load(sys.stdin)
except Exception as e:
    print("NOTJSON %s" % e); raise SystemExit
s = b.get("snapshot") or {}
if not isinstance(s.get("gross_exposure"), (int, float)) or not isinstance(s.get("nodes_recomputed"), int):
    print("SHAPE %s" % str(b)[:200]); raise SystemExit
print("OK %d positions, gross %.0f" % (len(s.get("positions") or []), s["gross_exposure"]))
' 2>&1)
		case "$esnap" in
		OK*) ok "GET /api/engine/snapshot   ${esnap#OK }, through the read-only bridge" ;;
		*) no "GET /api/engine/snapshot   malformed" "${esnap:-no response}" ;;
		esac
	fi
	# The bridge is GET-only by construction; a POST must never reach an
	# engine route through it.
	code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 15 -X POST -H 'Content-Type: application/json' -d '{}' "$BASE/api/engine/snapshot" 2>/dev/null)
	case "$code" in
	404 | 405) ok "POST /api/engine/snapshot  $code: the bridge is read-only" ;;
	*) no "POST /api/engine/snapshot  $code, expected 405" "the engine bridge accepted a POST" ;;
	esac
else
	meh "GET /api/engine/*           python3 unavailable; the bridge not checked"
fi

# container_is_this_deploy SERVICE
#
# The image label org.opencontainers.image.revision (quant/Dockerfile and
# deploy/Dockerfile both stamp it from the build's OHCAMEL_GIT_SHA) against
# --expect-sha, and the container's start time against five minutes ago. The
# same pair of claims /api/ops used to make for the public demo engine: the
# commit just pulled is the one answering, and this deploy replaced it.
container_is_this_deploy() {
	local svc="$1" id rev started bsd_started started_s age
	if ! command -v docker >/dev/null 2>&1; then
		no "$svc build sha     docker CLI unavailable, cannot verify" "--expect-sha was given; run the suite where the containers are"
		return
	fi
	# The compose project the containers belong to: COMPOSE_PROJECT_NAME when
	# the caller set one (the image job's harness, or one started with -p
	# beside a running stack), else `ohcamel`, docker-compose.yml's own
	# `name:`. deploy.sh sets neither and gets the droplet's.
	id=$(docker ps --filter "label=com.docker.compose.project=${COMPOSE_PROJECT_NAME:-ohcamel}" \
		--filter "label=com.docker.compose.service=$svc" --quiet 2>/dev/null | head -1)
	if [ -z "$id" ]; then
		no "$svc build sha     no running $svc container found"
		return
	fi
	read -r rev started <<<"$(docker inspect -f '{{index .Config.Labels "org.opencontainers.image.revision"}} {{.State.StartedAt}}' "$id" 2>/dev/null)"
	if [ "$rev" = "$EXPECT_SHA" ]; then
		ok "$svc build sha     matches ${EXPECT_SHA:0:7}"
	else
		no "$svc build sha     ${rev:-unlabelled}, expected ${EXPECT_SHA:0:7}" \
			"the image answering was not built from this checkout: the build failed, or up -d kept the old image"
	fi
	# StartedAt is RFC 3339 with nanoseconds, in UTC. GNU date reads it whole;
	# BSD date (a Mac running the harness) needs the format spelled out, the
	# fraction and the Z dropped. Unparseable reads as epoch 0, so the age
	# below is the whole epoch and the check fails rather than passes.
	bsd_started="${started%%.*}"
	started_s=$(date -d "$started" +%s 2>/dev/null ||
		date -j -u -f '%Y-%m-%dT%H:%M:%S' "${bsd_started%Z}" +%s 2>/dev/null ||
		echo 0)
	age=$(( $(date +%s) - started_s ))
	if [ "$age" -ge 0 ] && [ "$age" -lt 300 ]; then
		ok "$svc started       ${age} s ago: this deploy's container"
	else
		no "$svc started       ${started:-unknown}, expected under 300 s ago" \
			"a container this old was not replaced by this deploy"
	fi
}

if [ -n "$EXPECT_SHA" ]; then
	container_is_this_deploy ohcamel-quant
	[ -n "$LIVE" ] && container_is_this_deploy ohcamel-live
else
	meh "build sha                  not given (--expect-sha SHA), skipping"
fi

# ---------------------------------------------------------------------------
# The OCaml engine suite, against --engine (the harness's synthetic engine).
# Every probe below was written for the engine's own origin and is unchanged
# but for which URL it reads.
# ---------------------------------------------------------------------------
if [ -n "$ENGINE" ]; then
printf '\nOhCamel engine -- %s\n\n' "$ENGINE"

# ---------------------------------------------------------------------------
# 1. The Desk itself, at /
# ---------------------------------------------------------------------------
code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 15 "$ENGINE/" 2>/dev/null)
ctype=$(curl -sS -o /dev/null -w '%{content_type}' --max-time 15 "$ENGINE/" 2>/dev/null)
case "$code:$ctype" in
200:text/html*) ok "GET /                       200, text/html" ;;
*)             no "GET /                       ${code:-no response} ${ctype:-}" "the Desk did not render" ;;
esac

# ---------------------------------------------------------------------------
# 2. Health
# ---------------------------------------------------------------------------
code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 15 "$ENGINE/api/health" 2>/dev/null)
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
read_snapshot() { curl -sS --max-time 15 "$ENGINE/api/snapshot" 2>/dev/null; }

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
	"$ENGINE/api/stream" 2>/dev/null |
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
	ops=$(curl -sS --max-time 15 "$ENGINE/api/ops" 2>/dev/null | python3 -c '
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
page=$(curl -sS --max-time 15 -w '\n%{http_code}' "$ENGINE/ops" 2>/dev/null)
page_code="${page##*$'\n'}"
ops_ctype=$(curl -sS -o /dev/null -w '%{content_type}' --max-time 15 "$ENGINE/ops" 2>/dev/null)
case "$page_code:$ops_ctype:$page" in
200:text/html*:*'id="this-host"'*'id="peer"'*) ok "GET /ops                    200, text/html, both host columns present" ;;
200:*:*'id="this-host"'*'id="peer"'*) no "GET /ops                    200, but not text/html ($ops_ctype)" ;;
200:*:*) no "GET /ops                    200, but not the ops page" "the body has no #this-host / #peer" ;;
*)       no "GET /ops                    ${page_code:-no response}" ;;
esac

# The three pages W2 added, and /research, A3's. Each is a document rather
# than an API endpoint, and every page answers 200 text/html, so status and
# Content-Type alone would pass two handlers swapped in the routes table. Each
# body is therefore matched on an id only its own page carries -- the essay's
# <article>, the ledger, the open orders, the evidence -- as the /ops probe
# above matches its two columns. The ledger is the one section that moved to
# /risk; the open orders are one of the new renderings /execution adds from
# the journal; the evidence is where /research draws EXP-A01's manifests.
page_probe() {
	local path="$1" marker="$2" what="$3" body code ctype label
	label=$(printf 'GET %-23s' "$path")
	body=$(curl -sS --max-time 15 -w '\n%{http_code}' "$ENGINE$path" 2>/dev/null)
	code="${body##*$'\n'}"
	ctype=$(curl -sS -o /dev/null -w '%{content_type}' --max-time 15 "$ENGINE$path" 2>/dev/null)
	case "$code:$ctype:$body" in
	200:text/html*:*"$marker"*) ok "$label 200, text/html, $what present" ;;
	200:text/html*:*) no "$label 200, text/html, but not its page" "the body has no $marker" ;;
	*) no "$label ${code:-no response} ${ctype:-}" "expected 200, text/html" ;;
	esac
}
page_probe /argument '<article id="argument"' "the essay"
page_probe /risk 'id="ledger"' "the ledger"
page_probe /execution 'id="openorders"' "the open orders"
page_probe /research 'id="evidence"' "the evidence"

# The evidence /research draws: EXP-A01's two committed manifests, served the
# same on both hosts. 200 and JSON would pass an empty object, so the answer
# is parsed and each manifest's slug and verdict are read out of it -- both
# say fail, and a probe that printed them is how a deploy shows it.
if command -v python3 >/dev/null 2>&1; then
	evidence=$(curl -sS --max-time 15 -w '\n%{http_code}' "$ENGINE/api/research/evidence" 2>/dev/null | python3 -c '
import json, sys
raw = sys.stdin.read().rsplit("\n", 1)
try:
    body = json.loads(raw[0]); code = raw[1]
except Exception as e:
    print("NOTJSON %s" % e); raise SystemExit
if code != "200":
    print("CODE %s, expected 200" % code); raise SystemExit
ms = [m for m in (body.get("manifests") or []) if isinstance(m, dict)]
if body.get("experiment") != "EXP-A01" or len(ms) != 2:
    print("SHAPE experiment=%r, %d manifests" % (body.get("experiment"), len(ms))); raise SystemExit
print("OK EXP-A01, " + ", ".join("%s %s" % (m.get("slug"), m.get("verdict")) for m in ms))
' 2>/dev/null)
	case "$evidence" in
	OK*) ok "GET /api/research/evidence 200, ${evidence#OK }" ;;
	*)   no "GET /api/research/evidence malformed" "${evidence:-no response}" ;;
	esac
else
	meh "GET /api/research/evidence python3 unavailable for a real parse; not checked"
fi

# The 404 body is generated from the same table `handle` dispatches on, and
# EXPECTED_ROUTES is that table's shadow. Equality in order, not membership: a
# route that is served and not listed is the exact drift the table was
# introduced to make impossible, and a suite that only asked "is /api/ops in
# there" would wave it through.
if command -v python3 >/dev/null 2>&1; then
	listed=$(curl -sS --max-time 15 -w '\n%{http_code}' "$ENGINE/api/nope" 2>/dev/null | python3 -c '
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
# ---------------------------------------------------------------------------
# 4a'. The desk
#
# Both hosts attach a desk -- the simulated venue on the demo, Alpaca paper on
# the live host -- but this block reads $ENGINE only, which is the demo host when
# deploy.sh runs the suite; the live host stays behind its password, and
# section 6 asks of its /api/desk only that it refuses an anonymous caller. A
# status and a venue name are asserted, not an equity: a paper account can be
# empty, and "the desk said what it is" is the claim.
# ---------------------------------------------------------------------------
if command -v python3 >/dev/null 2>&1; then
	desk=$(curl -sS --max-time 15 "$ENGINE/api/desk" 2>/dev/null | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception as e:
    print("NOTJSON %s" % e); raise SystemExit
if d.get("status") not in ("enabled", "disabled") or not d.get("venue"):
    print("SHAPE status=%r venue=%r" % (d.get("status"), d.get("venue"))); raise SystemExit
print("OK %s %s %s" % (d["venue"], d["status"], d.get("sessions")))
' 2>/dev/null)
	case "$desk" in
	OK*) read -r _ dvenue dstatus dsessions <<<"$desk"; ok "GET /api/desk               venue $dvenue, $dstatus, $dsessions sessions recorded" ;;
	*)   no "GET /api/desk               malformed" "${desk:-no response}" ;;
	esac
else
	meh "GET /api/desk               python3 unavailable; the desk not checked"
fi

# ---------------------------------------------------------------------------
# 4a''. The desk's routes, as each host allows them
#
# The preview answers on the demo and creates nothing; the four routes that
# change a desk -- orders, cancel, kill and kill/reset -- are each refused
# there with a 405 and a JSON `error` sentence. Desk_routes.Protection
# refuses every one on the demo host before its handler runs, so POSTing
# kill here halts nothing; on the live host this suite never POSTs kill at
# all (section 7). The demo's own /api/desk says what it is: the simulated
# venue, an in-memory journal, and a clock block read from that venue. On the
# live host this suite has no password and sends no order: section 6 asserts
# the host refuses anonymous callers, and section 7 reads it from inside its
# container.
# ---------------------------------------------------------------------------
if command -v python3 >/dev/null 2>&1 && [ "${ops_mode:-}" = "demo" ]; then
	ticket='{"symbol":"AAPL","side":"buy","qty":1}'
	for route in orders cancel kill kill/reset; do
		case "$route" in
		orders) body="$ticket" ;;
		cancel) body='{"client_order_id":"smoke-no-such-order"}' ;;
		kill) body='{"why":"smoke: the demo must refuse this"}' ;;
		kill/reset) body='{"confirm":"reset"}' ;;
		esac
		refused=$(curl -sS --max-time 15 -w '\n%{http_code}' -X POST -H 'Content-Type: application/json' -d "$body" "$ENGINE/api/desk/$route" 2>/dev/null | python3 -c '
import json, sys
raw = sys.stdin.read().rsplit("\n", 1)
code = raw[-1] if len(raw) == 2 else "000"
try:
    b = json.loads(raw[0])
except Exception as e:
    print("NOTJSON HTTP %s: %s" % (code, e)); raise SystemExit
if code != "405":
    print("CODE %s, expected 405" % code); raise SystemExit
e = b.get("error") if isinstance(b, dict) else None
if not isinstance(e, str) or not e:
    print("NOERROR 405 without a JSON error sentence: %s" % str(b)[:200]); raise SystemExit
print("OK")
' 2>/dev/null)
		label=$(printf 'POST %-23s' "/api/desk/$route")
		case "$refused" in
		OK*) ok "$label 405 on the demo host, with an error sentence" ;;
		*) no "$label ${refused:-no response}" "the public demo did not refuse $route" ;;
		esac
	done
	demodesk=$(curl -sS --max-time 15 "$ENGINE/api/desk" 2>/dev/null | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception as e:
    print("NOTJSON %s" % e); raise SystemExit
c = d.get("clock")
bad = []
if d.get("venue") != "simulated": bad.append("venue %r, expected simulated" % (d.get("venue"),))
if d.get("journal") != "memory": bad.append("journal %r, expected memory" % (d.get("journal"),))
if not isinstance(c, dict): bad.append("no clock block")
elif c.get("source") != "venue-clock": bad.append("clock.source %r, expected venue-clock" % (c.get("source"),))
if bad:
    print("BAD " + "; ".join(bad)); raise SystemExit
print("OK")
' 2>/dev/null)
	case "$demodesk" in
	OK*) ok "GET /api/desk               demo: venue simulated, journal memory, clock from the venue" ;;
	*) no "GET /api/desk               demo desk is not what the demo should be" "${demodesk:-no response}" ;;
	esac
	preview=$(curl -sS --max-time 15 -X POST -H 'Content-Type: application/json' -d "$ticket" "$ENGINE/api/desk/preview" 2>/dev/null | python3 -c '
import json, sys
try:
    p = json.load(sys.stdin)
except Exception as e:
    print("NOTJSON %s" % e); raise SystemExit
if not isinstance(p.get("passed"), bool) or not isinstance(p.get("rules"), list) or "gate" not in p:
    print("SHAPE keys=%r" % sorted(p.keys())); raise SystemExit
print("OK %s %d" % ("passes" if p["passed"] else "refused", len(p.get("reasons") or [])))
' 2>/dev/null)
	case "$preview" in
	OK*) read -r _ pverdict preasons <<<"$preview"; ok "POST /api/desk/preview       $pverdict, $preasons reasons, nothing created" ;;
	*)   no "POST /api/desk/preview       malformed" "${preview:-no response}" ;;
	esac
elif [ "${ops_mode:-}" = "live" ]; then
	meh "POST /api/desk/*             the live host's desk routes sit behind its password; section 6 covers them"
else
	meh "POST /api/desk/*             python3 unavailable or the mode unknown; the desk's routes not checked"
fi

# ---------------------------------------------------------------------------
# 4a'''. Research (Task 16)
#
# The demo registers no strategies (its book has no signals block), so this
# only asserts the route answers and has the shape desk/intake.ml's
# research_json always produces -- intake off-or-running, a strategies list
# (empty here), and the fixed R8 sentence -- not that any strategy or signal
# is present. The live host's own /api/research, gated behind its password
# like every other route there, is covered by section 6 below.
# ---------------------------------------------------------------------------
if command -v python3 >/dev/null 2>&1; then
	research=$(curl -sS --max-time 15 "$ENGINE/api/research" 2>/dev/null | python3 -c '
import json, sys
try:
    r = json.load(sys.stdin)
except Exception as e:
    print("NOTJSON %s" % e); raise SystemExit
if r.get("intake") not in ("running", "off") or not isinstance(r.get("strategies"), list) \
        or not r.get("r8"):
    print("SHAPE keys=%r" % sorted(r.keys())); raise SystemExit
print("OK %s %d" % (r["intake"], len(r["strategies"])))
' 2>/dev/null)
	case "$research" in
	OK*) read -r _ rintake rstrategies <<<"$research"; ok "GET /api/research           intake $rintake, $rstrategies strategies registered" ;;
	*)   no "GET /api/research           malformed" "${research:-no response}" ;;
	esac
else
	meh "GET /api/research           python3 unavailable; research not checked"
fi

# ---------------------------------------------------------------------------
# 4b. The reports the page reads
#
# The argument on /argument is filled from three routes, and each has a
# way to be present and empty that a 200 alone would wave through: a reports
# object computed on nothing, a GARCH study that never finishes, a stress
# suite that forked no scenarios. So each is read for its substance. GARCH is
# polled, because it runs on a second domain after listen and a fresh container
# is honestly still computing; it gets two minutes.
# ---------------------------------------------------------------------------
if command -v python3 >/dev/null 2>&1; then
	rep=$(curl -sS --compressed --max-time 20 "$ENGINE/api/reports" 2>/dev/null | python3 -c '
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

	stress=$(curl -sS --max-time 20 "$ENGINE/api/stress" 2>/dev/null | python3 -c '
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
		garch_state=$(curl -sS --max-time 10 "$ENGINE/api/reports/garch" 2>/dev/null | python3 -c '
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

else
	meh "engine suite               no --engine URL (the public host serves no engine); see /api/engine/* above"
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

	# The apps must not be reachable except through the proxy. If they are,
	# the basic-auth on the live host is decoration -- anyone can ask the
	# droplet for :8081 and skip it.
	for port in 8080 8081 8090; do
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
	# Every path below, not just one. The gate is Caddy's basic_auth on the whole host,
	# and the tempting way to fill the ops page's peer column from the public
	# origin is a matcher that exempts /api/ops from it. That hole would show
	# up here as a 200 on one path while / still said 401. The page fills its
	# peer column the other way round -- the live origin reads the demo, over
	# the demo engine's own CORS header -- so the live host never needs one.
	for path in / /ops /argument /risk /execution /research /api/ops /api/snapshot /api/health /api/desk /api/desk/orders /api/research /api/research/evidence; do
		code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 15 "$LIVE$path" 2>/dev/null)
		[ "$code" = "401" ] && ok "GET $LIVE$path  401 without credentials" \
			|| no "GET $LIVE$path  $code, expected 401" "the live host is not gated on $path"
	done
else
	meh "live host                  not given (--live URL), skipping"
fi

# ---------------------------------------------------------------------------
# 7. The live desk, from inside its container (--live-container)
#
# Every request below runs as
#
#   docker compose -f deploy/docker-compose.yml --profile live \
#     exec -T ohcamel-live curl ... http://localhost:8081/PATH
#
# so it reaches the engine on its own loopback, behind Caddy's basic_auth:
# the suite holds no password and no credential ever enters it. The runtime
# image ships curl for its healthcheck; nothing was added to it for this.
# /api/health stays liveness-only (ruling 13), so readiness is read here, from
# /api/desk and /api/ops. Every line this block prints begins "live:", which
# is what deploy/test/smoke_live_test.sh reads.
# ---------------------------------------------------------------------------
if [ "$LIVE_CONTAINER" = 1 ]; then
printf '\nOhCamel live, inside ohcamel-live -- http://localhost:8081\n\n'

SMOKE_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# OHCAMEL_TAG: docker-compose.yml's ohcamel-research image is written
# ${OHCAMEL_TAG:?...}, and compose interpolates the whole file before an
# `exec`, so an unset tag would refuse the call. exec acts on the running
# container and pulls nothing, so the value only has to be present: the tag
# deploy.sh exported, else the sha this suite was told to expect.
LIVE_COMPOSE=(env "OHCAMEL_TAG=${OHCAMEL_TAG:-${EXPECT_SHA:-unset}}"
	docker compose -f "$SMOKE_DIR/docker-compose.yml" --profile live)

# lc METHOD PATH [CURL-ARGS...]: one request inside ohcamel-live; prints the
# body, a newline, and the status.
lc() {
	local method="$1" path="$2"
	shift 2
	"${LIVE_COMPOSE[@]}" exec -T ohcamel-live \
		curl -sS --max-time 15 -w '\n%{http_code}' -X "$method" "$@" "http://localhost:8081$path" 2>/dev/null
}

# report: reads "OK<TAB>label<TAB>detail" / "NO<TAB>label<TAB>detail" lines,
# one per assertion, and passes or fails each, so one regression is named on
# its own line instead of hiding behind the first.
report() {
	local verdict label detail
	while IFS=$'\t' read -r verdict label detail; do
		case "$verdict" in
		OK) ok "live: $label  $detail" ;;
		NO) no "live: $label  $detail" ;;
		*) [ -n "$verdict" ] && no "live: $verdict" "$label $detail" ;;
		esac
	done
}

if [ "$have_py" != 1 ]; then
	no "live:                        python3 unavailable" "--live-container was asked for and cannot parse an answer here"
else

# 7a. /api/desk. last_sync is polled: the first sync follows startup and the
# backfill, so a container this deploy just started honestly has none yet.
# SMOKE_SYNC_WAIT_S (default 120) bounds the wait; the test sets it to 0.
sync_wait="${SMOKE_SYNC_WAIT_S:-120}"
deadline=$(($(date +%s) + sync_wait))
while :; do
	desk_raw=$(lc GET /api/desk)
	fresh=$(printf '%s' "$desk_raw" | python3 -c '
import datetime, json, sys
raw = sys.stdin.read().rsplit("\n", 1)
try:
    d = json.loads(raw[0])
    s = d["last_sync"]
    head, _, frac = s.rstrip("Z").partition(".")
    at = datetime.datetime.strptime(head, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=datetime.timezone.utc)
    age = (datetime.datetime.now(datetime.timezone.utc) - at).total_seconds()
    print("FRESH" if age < 180 else "OLD")
except Exception:
    print("NONE")
' 2>/dev/null)
	[ "$fresh" = FRESH ] && break
	[ "$(date +%s)" -ge "$deadline" ] && break
	/bin/sleep 5
done
printf '%s' "$desk_raw" | python3 -c '
import datetime, json, sys
raw = sys.stdin.read().rsplit("\n", 1)
code = raw[-1] if len(raw) == 2 else "000"
def say(ok, label, detail):
    print("%s\t%-35s\t%s" % ("OK" if ok else "NO", label, detail))
try:
    d = json.loads(raw[0])
except Exception as e:
    say(False, "/api/desk", "HTTP %s, not JSON: %s" % (code, e)); raise SystemExit
if code != "200" or not isinstance(d, dict):
    say(False, "/api/desk", "HTTP %s, expected 200: %s" % (code, str(d)[:200])); raise SystemExit
say(d.get("status") == "enabled", "/api/desk status", "%s%s" % (d.get("status"), "" if d.get("status") == "enabled" else ", expected enabled: %s" % d.get("reason")))
say(d.get("venue") == "alpaca-paper", "/api/desk venue", "%s%s" % (d.get("venue"), "" if d.get("venue") == "alpaca-paper" else ", expected alpaca-paper"))
say(d.get("journal") == "file", "/api/desk journal", "%s%s" % (d.get("journal"), "" if d.get("journal") == "file" else ", expected file: the live desk would forget its orders on restart"))
k = d.get("kill_switch")
say(isinstance(k, str) and bool(k), "/api/desk kill_switch", "%s%s" % (k, "" if isinstance(k, str) and k else ", expected present: the desk has no order manager"))
c = d.get("clock") if isinstance(d.get("clock"), dict) else {}
src = c.get("source")
say(isinstance(src, str) and src != "unknown", "/api/desk clock.source", "%s%s" % (json.dumps(src), "" if isinstance(src, str) and src != "unknown" else ", expected a reading: the market clock never answered"))
s = d.get("last_sync")
if s is None:
    say(False, "/api/desk last_sync", "null, expected a sync under 180 s old: none within the wait")
else:
    try:
        head, _, frac = s.rstrip("Z").partition(".")
        at = datetime.datetime.strptime(head, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=datetime.timezone.utc)
        age = int((datetime.datetime.now(datetime.timezone.utc) - at).total_seconds())
        if age < -5:
            say(False, "/api/desk last_sync", "%d s in the future: the host clock stepped back" % -age)
        else:
            say(age < 180, "/api/desk last_sync", "%d s ago%s" % (age, "" if age < 180 else ", expected under 180 s: the sync loop has stopped"))
    except Exception:
        say(False, "/api/desk last_sync", "%r, unreadable" % (s,))
e = d.get("last_error")
say(e is None, "/api/desk last_error", "null" if e is None else "%r, expected null" % (e,))
a = d.get("account") if isinstance(d.get("account"), dict) else {}
say(a.get("status") == "ACTIVE", "/api/desk account.status", "%s%s" % (a.get("status"), "" if a.get("status") == "ACTIVE" else ", expected ACTIVE"))
say(a.get("trading_blocked") is False, "/api/desk account.trading_blocked", "%s%s" % (json.dumps(a.get("trading_blocked")), "" if a.get("trading_blocked") is False else ", expected false"))
' 2>&1 | report

# 7b. The mutating routes refuse a request that is not the page's own: first
# without X-OhCamel-Desk, then with it but with neither Origin nor
# Sec-Fetch-Site (curl sends neither). Each body is one that would do
# nothing even if it got through: an empty ticket, a cancel of an id that
# does not exist, a reset with no confirm field.
#
# /api/desk/kill is NEVER POSTed here, on purpose (ruling 13): a regression
# in its guard would halt the live desk, and the smoke suite must not be the
# thing that finds out that way. Its refusal is covered by the hermetic
# desk-route tests (test/test_desk_routes.ml) instead. §3.15's acceptance asks
# for all four mutating routes, so this gap is recorded as a §8 departure of
# docs/superpowers/specs/2026-09-12-the-desk-design.md -- "the live smoke
# suite never POSTs /api/desk/kill" -- which Task 71 keeps.
for route in orders cancel kill/reset; do
	case "$route" in
	orders) body='{}' ;;
	cancel) body='{"client_order_id":"smoke-no-such-order-0000"}' ;;
	kill/reset) body='{}' ;;
	esac
	for variant in noheader noorigin; do
		if [ "$variant" = noheader ]; then
			raw=$(lc POST "/api/desk/$route" -H 'Content-Type: application/json' -d "$body")
			want="without X-OhCamel-Desk"
		else
			raw=$(lc POST "/api/desk/$route" -H 'Content-Type: application/json' -H 'X-OhCamel-Desk: 1' -d "$body")
			want="with the header but no Origin"
		fi
		code=""
		case "$raw" in *$'\n'*) code="${raw##*$'\n'}" ;; esac
		label=$(printf 'POST %-24s' "/api/desk/$route")
		if [ "$code" = 403 ]; then
			ok "live: $label 403 $want"
		elif [ -z "$code" ] || [ "$code" = 000 ]; then
			no "live: $label no response, expected 403 $want" \
				"the request never reached ohcamel-live: is it running, and is this the host it runs on?"
		else
			no "live: $label $code, expected 403 $want" \
				"a request that is not the page's own was not refused: ${raw%$'\n'*}"
		fi
	done
done

# 7c. /api/ops: this deploy's build, in a container this deploy started.
lc GET /api/ops | EXPECT="$EXPECT_SHA" python3 -c '
import json, os, sys
raw = sys.stdin.read().rsplit("\n", 1)
code = raw[-1] if len(raw) == 2 else "000"
def say(ok, label, detail):
    print("%s\t%-35s\t%s" % ("OK" if ok else "NO", label, detail))
try:
    o = json.loads(raw[0])
except Exception as e:
    say(False, "/api/ops", "HTTP %s, not JSON: %s" % (code, e)); raise SystemExit
want = os.environ.get("EXPECT", "")
say(o.get("mode") == "live", "/api/ops mode", "%s%s" % (o.get("mode"), "" if o.get("mode") == "live" else ", expected live"))
sha = (o.get("build") or {}).get("git_sha")
if not want:
    say(False, "/api/ops build.git_sha", "%s, and no --expect-sha to compare it with" % sha)
else:
    say(sha == want, "/api/ops build.git_sha", ("matches %s" % want[:7]) if sha == want else "%s, expected %s" % (sha, want[:7]))
up = o.get("uptime_s")
fine = isinstance(up, (int, float)) and not isinstance(up, bool) and up < 300
say(fine, "/api/ops uptime_s", "%s s%s" % (up, "" if fine else ", expected under 300: this deploy did not replace it"))
' 2>&1 | report

# 7d. ohcamel-research: running, built from this sha, and stamped with it.
rid=$(docker ps --filter "label=com.docker.compose.project=${COMPOSE_PROJECT_NAME:-ohcamel}" \
	--filter "label=com.docker.compose.service=ohcamel-research" --quiet 2>/dev/null | head -1)
if [ -z "$rid" ]; then
	no "live: ohcamel-research running        no running ohcamel-research container found"
else
	read -r rrunning rrev <<<"$(docker inspect -f '{{.State.Running}} {{index .Config.Labels "org.opencontainers.image.revision"}}' "$rid" 2>/dev/null)"
	if [ "$rrunning" = true ]; then
		ok "live: ohcamel-research running        yes"
	else
		no "live: ohcamel-research running        ${rrunning:-unknown}, expected true"
	fi
	if [ -n "$EXPECT_SHA" ] && [ "$rrev" = "$EXPECT_SHA" ]; then
		ok "live: ohcamel-research image revision matches ${EXPECT_SHA:0:7}"
	else
		no "live: ohcamel-research image revision ${rrev:-unlabelled}, expected ${EXPECT_SHA:-a --expect-sha}"
	fi
	renv=$(docker exec "$rid" printenv OHCAMEL_GIT_SHA 2>/dev/null)
	if [ -n "$EXPECT_SHA" ] && [ "$renv" = "$EXPECT_SHA" ]; then
		ok "live: ohcamel-research OHCAMEL_GIT_SHA matches ${EXPECT_SHA:0:7}"
	else
		no "live: ohcamel-research OHCAMEL_GIT_SHA ${renv:-unset}, expected ${EXPECT_SHA:-a --expect-sha}"
	fi
fi

# 7e. /api/research: the intake runs, or is off because the owner's book has
# no signals block (a skip, not a failure). How many strategies it holds is
# reported, not asserted.
research=$(lc GET /api/research | python3 -c '
import json, sys
raw = sys.stdin.read().rsplit("\n", 1)
code = raw[-1] if len(raw) == 2 else "000"
try:
    r = json.loads(raw[0])
except Exception as e:
    print("NOTJSON HTTP %s: %s" % (code, e)); raise SystemExit
if code != "200":
    print("CODE %s, expected 200" % code); raise SystemExit
if r.get("intake") == "off" and "no signals block" in str(r.get("reason") or ""):
    print("BOOKOFF"); raise SystemExit
if r.get("intake") != "running":
    print("INTAKE %r, expected running" % (r.get("intake"),)); raise SystemExit
print("OK %d" % len(r.get("strategies") or []))
' 2>&1)
case "$research" in
OK*) ok "live: /api/research                   intake running, ${research#OK } strategies registered" ;;
# Off by the owner's book (no signals block): the configured state, not a fault.
BOOKOFF) meh "live: /api/research                   intake off -- the book has no signals block (owner's)" ;;
*) no "live: /api/research                   ${research:-no response}" ;;
esac

fi
else
	meh "live container             not asked for (--live-container), skipping"
fi

# ---------------------------------------------------------------------------
printf '\n  %d passed, %d failed, %d skipped\n\n' "$pass" "$fail" "$skip"
[ "$fail" -eq 0 ] || exit 1
