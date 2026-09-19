#!/usr/bin/env bash
#
# Hand cases for deploy.sh's market-hours guard, exercised without running a
# deploy. Sourcing deploy.sh only defines its functions -- in_market_window,
# say, main -- and never calls main, because deploy.sh guards that call with
# `[ "${BASH_SOURCE[0]}" = "${0}" ]`, which is false while it is being
# sourced from here.
#
#   deploy/test/deploy_guard_test.sh
#
# Exits non-zero if any case disagrees with the hand-derived expectation.

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/../.."
# shellcheck source=deploy/deploy.sh
source deploy/deploy.sh

pass=0
fail=0
ok() { printf '  ok    %s\n' "$1"; pass=$((pass + 1)); }
no() { printf '  FAIL  %s -- %s\n' "$1" "$2"; fail=$((fail + 1)); }

# label, UTC timestamp, expected verdict (allowed | refused).
#
# 2026-09-14 is a Monday, 2026-09-16 a Wednesday and 2026-09-19 a Saturday on
# the Gregorian calendar (confirmed against `date`); in_market_window's own
# comment in deploy.sh says why the weekday is computed with Zeller's
# congruence rather than `date -d`/`date -j` (GNU vs. BSD). The window under
# test is [13:25, 20:10) UTC on a weekday.
check() {
	local label="$1" ts="$2" expect="$3" got
	if in_market_window "$ts"; then got=refused; else got=allowed; fi
	if [ "$got" = "$expect" ]; then
		ok "$label -- $ts -- $got"
	else
		no "$label -- $ts" "expected $expect, got $got"
	fi
}

check "Monday    13:24 UTC, just before the window" "2026-09-14T13:24:00Z" allowed
check "Monday    13:25 UTC, the window opens"       "2026-09-14T13:25:00Z" refused
check "Wednesday 20:09 UTC, still inside the window" "2026-09-16T20:09:00Z" refused
check "Wednesday 20:10 UTC, the window closes"       "2026-09-16T20:10:00Z" allowed
check "Saturday  15:00 UTC, a weekend"                "2026-09-19T15:00:00Z" allowed

printf '\n  %d passed, %d failed\n\n' "$pass" "$fail"
[ "$fail" -eq 0 ] || exit 1
