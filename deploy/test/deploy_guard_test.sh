#!/usr/bin/env bash
#
# Hand cases for deploy.sh's market-hours guard, exercised without running a
# deploy. Sourcing deploy.sh only defines its functions -- in_market_window,
# ny_wall_clock, should_refuse_live_deploy, say, main -- and never calls
# main, because deploy.sh guards that call with
# `[ "${BASH_SOURCE[0]}" = "${0}" ]`, which is false while it is being
# sourced from here.
#
#   deploy/test/deploy_guard_test.sh
#
# Exits non-zero if any case disagrees with the hand-derived expectation.
#
# Every case below is a Unix epoch (seconds since 1970-01-01 UTC) -- the same
# format DEPLOY_NOW takes -- fed through ny_wall_clock, which is deploy.sh's
# own zone-database read, into in_market_window. That means these tests
# exercise the DST conversion itself, not just the window arithmetic: a case
# is checked against BOTH the New York weekday/time ny_wall_clock is expected
# to produce and the final allowed/refused verdict, so a wrong offset shows
# up even on a case (like the two DST-transition ones) where the final
# verdict alone -- both are Sundays -- would not change either way.
#
# US DST in 2026 (used only to derive these expectations by hand; deploy.sh
# hardcodes neither): starts Sunday 2026-03-08 at 02:00 EST local, which
# becomes 03:00 EDT (clocks skip an hour); ends Sunday 2026-11-01 at 02:00
# EDT local, which becomes 01:00 EST (the 1 a.m. hour repeats). EST is
# UTC-5; EDT is UTC-4.

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/../.."
# shellcheck source=deploy/deploy.sh
source deploy/deploy.sh

pass=0
fail=0
ok() { printf '  ok    %s\n' "$1"; pass=$((pass + 1)); }
no() { printf '  FAIL  %s -- %s\n' "$1" "$2"; fail=$((fail + 1)); }

# check LABEL EPOCH EXPECT_DOW EXPECT_HHMM EXPECT_VERDICT
#
# Verifies ny_wall_clock(EPOCH) reads exactly EXPECT_DOW/EXPECT_HHMM (New
# York wall clock, via the zone database) before checking in_market_window's
# verdict against EXPECT_VERDICT (allowed | refused) -- so a DST conversion
# that drifted would fail here even on a case whose final verdict happens not
# to move.
check() {
	local label="$1" epoch="$2" expect_dow="$3" expect_hhmm="$4" expect="$5"
	local wall dow hhmm got
	wall=$(ny_wall_clock "$epoch")
	read -r dow hhmm <<<"$wall"
	if [ "$dow" != "$expect_dow" ] || [ "$hhmm" != "$expect_hhmm" ]; then
		no "$label -- epoch $epoch" "ny_wall_clock read weekday $dow $hhmm, expected weekday $expect_dow $expect_hhmm"
		return
	fi
	if in_market_window "$dow" "$hhmm"; then got=refused; else got=allowed; fi
	if [ "$got" = "$expect" ]; then
		ok "$label -- weekday $dow, $hhmm NY -- $got"
	else
		no "$label -- weekday $dow, $hhmm NY" "expected $expect, got $got"
	fi
}

# check_override LABEL EPOCH EXPECT_WITHOUT
#
# should_refuse_live_deploy at the same wall-clock reading, once with no
# override (must match EXPECT_WITHOUT) and once with --during-market's flag
# set (must always come back allowed) -- proving the override actually flips
# the decision rather than the test re-implementing the combination itself.
check_override() {
	local label="$1" epoch="$2" expect_without="$3"
	local wall dow hhmm without withv
	wall=$(ny_wall_clock "$epoch")
	read -r dow hhmm <<<"$wall"
	if should_refuse_live_deploy 0 "$dow" "$hhmm"; then without=refused; else without=allowed; fi
	if should_refuse_live_deploy 1 "$dow" "$hhmm"; then withv=refused; else withv=allowed; fi
	if [ "$without" = "$expect_without" ] && [ "$withv" = allowed ]; then
		ok "$label -- weekday $dow, $hhmm NY -- without: $without, with --during-market: $withv"
	else
		no "$label -- weekday $dow, $hhmm NY" "without: $without (expected $expect_without), with --during-market: $withv (expected allowed)"
	fi
}

# --- The original five, re-expressed as epochs (EDT: UTC-4 in September) ---
# 2026-09-14 is a Monday, 2026-09-16 a Wednesday, 2026-09-19 a Saturday.
check "Monday    13:24 UTC = 09:24 EDT, just before the window" 1789392240 1 0924 allowed
check "Monday    13:25 UTC = 09:25 EDT, the window opens"       1789392300 1 0925 refused
check "Wednesday 20:09 UTC = 16:09 EDT, still inside the window" 1789589340 3 1609 refused
check "Wednesday 20:10 UTC = 16:10 EDT, the window closes"       1789589400 3 1610 allowed
check "Saturday  15:00 UTC = 11:00 EDT, a weekend"                1789830000 6 1100 allowed

# --- Winter (EST: UTC-5). 2026-01-15 is a Thursday. ---
# 20:30 UTC - 5:00 = 15:30 EST, inside [09:25, 16:10) -> refused. This is the
# exact case a fixed-EDT UTC band got wrong: 20:30 UTC sits outside a band
# tuned to [13:25, 20:10) UTC, which would have let this one through.
check "Winter afternoon: 20:30 UTC = 15:30 EST, inside" 1768509000 4 1530 refused
# 13:45 UTC - 5:00 = 08:45 EST, before the window -> allowed.
check "Winter morning:   13:45 UTC = 08:45 EST, before" 1768484700 4 0845 allowed
# 14:45 UTC - 5:00 = 09:45 EST, inside -> refused.
check "Winter morning:   14:45 UTC = 09:45 EST, inside" 1768488300 4 0945 refused

# --- Summer (EDT: UTC-4). 2026-07-15 is a Wednesday. ---
# 20:30 UTC - 4:00 = 16:30 EDT, past the window's 16:10 close -> allowed.
check "Summer afternoon: 20:30 UTC = 16:30 EDT, outside" 1784147400 3 1630 allowed

# --- Spring forward, 2026-03-08 (a Sunday, so both sides are weekend and
# allowed regardless of clock time -- what this pins is that ny_wall_clock
# itself crosses the jump correctly). 02:00 EST becomes 03:00 EDT at 07:00
# UTC (02:00 + 5h EST offset).
# One minute before: 06:59 UTC - 5:00 (still EST) = 01:59 local.
check "Spring forward, one minute before: still EST" 1772953140 7 0159 allowed
# One minute after: 07:01 UTC - 4:00 (now EDT) = 03:01 local -- the clock
# jumped from 02:xx straight to 03:xx.
check "Spring forward, one minute after: now EDT"     1772953260 7 0301 allowed

# --- Fall back, 2026-11-01 (also a Sunday). 02:00 EDT becomes 01:00 EST at
# 06:00 UTC (02:00 + 4h EDT offset).
# One minute before: 05:59 UTC - 4:00 (still EDT) = 01:59 local.
check "Fall back, one minute before: still EDT" 1793512740 7 0159 allowed
# One minute after: 06:01 UTC - 5:00 (now EST) = 01:01 local -- the 1 a.m.
# hour repeats, one hour earlier than the reading just above.
check "Fall back, one minute after: now EST"    1793512860 7 0101 allowed

# --- --during-market overrides an otherwise-refused reading. ---
# 15:00 UTC in January - 5:00 EST = 10:00 EST, Thursday, inside the window.
check_override "During-market override at 10:00 EST, Thursday, inside" 1768489200 refused

printf '\n  %d passed, %d failed\n\n' "$pass" "$fail"
[ "$fail" -eq 0 ] || exit 1
