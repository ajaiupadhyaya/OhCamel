#!/usr/bin/env bash
#
# deploy/smoke.sh --expect-sha, on any compose project and on any host.
#
#   deploy/test/smoke_expect_sha_test.sh
#
# Drives the real smoke.sh against shim `docker` and `curl` binaries put first
# on PATH, and reads the two lines --expect-sha prints for the Quant
# container: its build sha (the image's org.opencontainers.image.revision
# label) and its age (the container's StartedAt). No daemon, no network, no
# container: the shims answer from environment variables this test sets.
#
# Why this exists. The image job (.github/workflows/image.yml) brings the
# harness up under its OWN compose project and runs smoke.sh --expect-sha
# against it, and two things in smoke.sh made that impossible:
#
#   - it looked for the container under the project name `ohcamel`, written
#     out, so a harness under any other name -- the image job's, or one an
#     engineer starts beside a running stack -- read "no running ohcamel-quant
#     container found" however healthy it was;
#   - it turned StartedAt into seconds with `date -d`, which is GNU's. On a
#     Mac that fails, the age came out as the whole epoch, and a container
#     started a second ago read "expected under 300 s ago".
#
# smoke.sh now reads COMPOSE_PROJECT_NAME, which is compose's own variable for
# exactly this, and falls back to BSD date's spelling. Every other line of the
# suite fails here, by design -- the curl shim refuses every request -- and
# this test reads none of them.

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."

smoke=deploy/smoke.sh

pass=0
fail=0
ok() { printf '  ok    %s\n' "$1"; pass=$((pass + 1)); }
no() { printf '  FAIL  %s -- %s\n' "$1" "$2"; fail=$((fail + 1)); }

work=$(mktemp -d "${TMPDIR:-/tmp}/smoke-expect-sha-test.XXXXXX")
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/bin"

# curl: every request is refused, at once. 7 is curl's own "failed to
# connect".
cat >"$work/bin/curl" <<'EOF'
#!/usr/bin/env bash
exit 7
EOF

# docker: `ps` answers with one container id when both filters name the
# project and the service the test planted, and with nothing otherwise;
# `inspect` prints "<revision> <StartedAt>", the two fields smoke.sh's format
# string asks for, in that order.
cat >"$work/bin/docker" <<'EOF'
#!/usr/bin/env bash
case "${1:-}" in
ps)
	project="" service=""
	for a in "$@"; do
		case "$a" in
		label=com.docker.compose.project=*) project=${a#label=com.docker.compose.project=} ;;
		label=com.docker.compose.service=*) service=${a#label=com.docker.compose.service=} ;;
		esac
	done
	if [ "$project" = "$SHIM_PROJECT" ] && [ "$service" = "ohcamel-quant" ]; then
		echo "c0ffee000001"
	fi
	;;
inspect)
	echo "$SHIM_REVISION $SHIM_STARTED"
	;;
*)
	echo "docker shim: unexpected: $*" >&2
	exit 64
	;;
esac
EOF
chmod +x "$work/bin/curl" "$work/bin/docker"

# stamp_ago SECONDS: the RFC 3339 stamp docker prints for a container started
# that many seconds ago -- UTC, nine fractional digits, a trailing Z.
stamp_ago() {
	local at=$(($(date +%s) - $1))
	date -u -d "@$at" '+%Y-%m-%dT%H:%M:%S.123456789Z' 2>/dev/null ||
		date -u -r "$at" '+%Y-%m-%dT%H:%M:%S.123456789Z'
}

# run_smoke PROJECT-ENV SHIM-PROJECT REVISION AGE EXPECT: the suite's output,
# colours stripped. PROJECT-ENV empty means COMPOSE_PROJECT_NAME is unset.
run_smoke() {
	local project_env="$1" shim_project="$2" revision="$3" age="$4" expect="$5"
	(
		if [ -n "$project_env" ]; then
			export COMPOSE_PROJECT_NAME="$project_env"
		else
			unset COMPOSE_PROJECT_NAME
		fi
		export SHIM_PROJECT="$shim_project" SHIM_REVISION="$revision"
		SHIM_STARTED=$(stamp_ago "$age")
		export SHIM_STARTED
		PATH="$work/bin:$PATH" bash "$smoke" http://127.0.0.1:9 --expect-sha "$expect" 2>&1 || true
	) | sed $'s/\033\\[[0-9;]*m//g'
}

# expect NAME OUTPUT VERDICT PATTERN: exactly one line of OUTPUT matches the
# extended regex PATTERN and it begins with VERDICT (PASS or FAIL).
expect() {
	local name="$1" out="$2" verdict="$3" pattern="$4" lines
	lines=$(grep -E -- "$pattern" <<<"$out" || true)
	if [ -z "$lines" ]; then
		no "$name" "no line matching /$pattern/; the ohcamel-quant lines were: $(grep -F 'ohcamel-quant ' <<<"$out" | tr -s ' ' | tr '\n' '|')"
	elif [ "$(wc -l <<<"$lines" | tr -d ' ')" -ne 1 ]; then
		no "$name" "more than one line matching /$pattern/: $(tr '\n' '|' <<<"$lines")"
	elif ! grep -qE "^ +$verdict " <<<"$lines"; then
		no "$name" "expected $verdict, got: $(tr -s ' ' <<<"$lines")"
	else
		ok "$name"
	fi
}

sha=0123456789abcdef0123456789abcdef01234567
other=fedcba9876543210fedcba9876543210fedcba98

# The age rule is smoke.sh's `[ "$age" -ge 0 ] && [ "$age" -lt 300 ]`: under
# five minutes. The shim stamps the start from this test's clock and smoke.sh
# reads its own a moment later, so an age planted as N is read as N, N+1 or
# N+2; every case below keeps that slack clear of the 300 s line.

# 1. The image job's case: a project that is not `ohcamel`.
#    Planted 100 s ago -> read as 100..102, and 102 < 300 -> PASS.
out=$(run_smoke harness-x harness-x "$sha" 100 "$sha")
expect "a harness under COMPOSE_PROJECT_NAME=harness-x is found, and its sha matches" \
	"$out" PASS 'ohcamel-quant build sha +matches 0123456'
expect "started 100 s ago reads as 100-102 s, under 300" \
	"$out" PASS 'ohcamel-quant started +10[0-2] s ago'

# 2. The droplet's case, unchanged: no COMPOSE_PROJECT_NAME, project `ohcamel`
#    (deploy/docker-compose.yml's `name:`).
#    Planted 290 s ago -> read as 290..292, and 292 < 300 -> PASS.
out=$(run_smoke "" ohcamel "$sha" 290 "$sha")
expect "with COMPOSE_PROJECT_NAME unset the project is ohcamel, as before" \
	"$out" PASS 'ohcamel-quant build sha +matches 0123456'
expect "started 290 s ago reads as 290-292 s, under 300" \
	"$out" PASS 'ohcamel-quant started +29[0-2] s ago'

# 3. The variable is read, not ignored: the harness runs under harness-x and
#    the suite is told to look in another project.
out=$(run_smoke elsewhere harness-x "$sha" 100 "$sha")
expect "a container in another project is not this deploy's" \
	"$out" FAIL 'ohcamel-quant build sha +no running ohcamel-quant container found'

# 4. The wrong image answering.
out=$(run_smoke harness-x harness-x "$other" 100 "$sha")
expect "a revision label that is not --expect-sha fails, naming both" \
	"$out" FAIL 'ohcamel-quant build sha +fedcba9876543210fedcba9876543210fedcba98, expected 0123456'

# 5. The right image in a container this deploy did not start.
#    Planted 310 s ago -> read as 310..312, and 310 >= 300 -> FAIL.
out=$(run_smoke harness-x harness-x "$sha" 310 "$sha")
expect "started 310 s ago is not under 300" \
	"$out" FAIL 'ohcamel-quant started +.*expected under 300 s ago'

printf '\n  %d passed, %d failed\n\n' "$pass" "$fail"
[ "$fail" -eq 0 ] || exit 1
