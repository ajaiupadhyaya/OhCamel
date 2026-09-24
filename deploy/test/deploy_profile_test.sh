#!/usr/bin/env bash
#
# Hand cases for deploy.sh's live-profile decision, exercised without a
# filesystem, a docker daemon, or a deploy. Sourcing deploy.sh only defines
# its functions -- see deploy_guard_test.sh beside this file for why that is
# safe -- so resolve_live_profile can be called directly, in isolation, for
# every combination of its three 1/0 inputs.
#
#   deploy/test/deploy_profile_test.sh
#
# Exits non-zero if any case disagrees with the hand-derived expectation.
#
# This is the fix for a real bug: a plain `deploy.sh` (no --live) could be
# refused outright. The auto-add heuristic (an ohcamel-live container already
# exists, or /etc/ohcamel/live.env is readable) used to set the live profile
# in one `if`, and a second, separate `if` then exited 1 the moment that
# profile was set and the file was not actually readable -- with no way to
# tell "the operator asked for --live" apart from "the heuristic guessed
# wrong". The eight cases below are every combination of
# (--live given, live.env readable, ohcamel-live container exists); the two
# that matter most are marked below.

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/../.."
# shellcheck source=deploy/deploy.sh
source deploy/deploy.sh

pass=0
fail=0
ok() { printf '  ok    %s\n' "$1"; pass=$((pass + 1)); }
no() { printf '  FAIL  %s -- %s\n' "$1" "$2"; fail=$((fail + 1)); }

# check LABEL LIVE_FLAG LIVE_ENV_READABLE HAS_LIVE_CONTAINER EXPECT_PROFILE EXPECT_AUTO EXPECT_REFUSE
#
# Calls resolve_live_profile with the three inputs and checks all three of
# its outputs at once, so a case that gets the right PROFILE for the wrong
# reason (say, REFUSE set when main() would already have exited on AUTO_ADDED
# alone) still fails.
check() {
	local label="$1" live_flag="$2" readable="$3" container="$4"
	local expect_profile="$5" expect_auto="$6" expect_refuse="$7"
	local profile auto refuse
	read -r profile auto refuse <<<"$(resolve_live_profile "$live_flag" "$readable" "$container")"
	if [ "$profile" = "$expect_profile" ] && [ "$auto" = "$expect_auto" ] && [ "$refuse" = "$expect_refuse" ]; then
		ok "$label -- profile=$profile auto=$auto refuse=$refuse"
	else
		no "$label" "got profile=$profile auto=$auto refuse=$refuse, expected profile=$expect_profile auto=$expect_auto refuse=$expect_refuse"
	fi
}

# --- No --live, live.env unreadable, no ohcamel-live container: the common
# case on a fresh host. Nothing to auto-add a reason for -- plain demo. ---
check "plain deploy, nothing live anywhere" 0 0 0 0 0 0

# --- No --live, live.env unreadable, but an ohcamel-live container already
# exists: THE BUG. The heuristic wants to add the profile so ohcamel-research
# gets rebuilt, but the credential it would need is not there. Before this
# fix, PROFILE was set here and the very next check exited 1 -- a plain
# `deploy.sh` refused outright. The fix: auto-added and refused disagree on
# purpose -- PROFILE falls back to off (a demo-only deploy proceeds) and
# nothing exits. ---
check "auto-add wants live, but live.env unreadable -- falls back to demo, does not exit" 0 0 1 0 1 0

# --- No --live, live.env readable: the ordinary auto-add path from the
# original comment at the top of deploy.sh -- profile comes on, and there is
# nothing for the readability check to catch, so no refusal either. ---
check "auto-add, live.env readable" 0 1 0 1 1 0
check "auto-add, live.env readable, container also exists" 0 1 1 1 1 0

# --- --live, live.env unreadable, no existing container: THE OTHER HALF.
# This was asked for explicitly, so it is not the auto-add heuristic's guess
# to walk back -- exiting 1 is the honest answer, same as before this fix. ---
check "explicit --live, live.env unreadable -- refuses, does not fall back" 1 0 0 1 0 1

# --live with an existing container present changes nothing: the container
# check is only ever consulted to justify auto-adding, and --live already
# decided the profile on its own.
check "explicit --live, live.env unreadable, container exists -- still refuses" 1 0 1 1 0 1

# --- --live, live.env readable: the ordinary explicit path, both with and
# without a pre-existing container -- always on, never auto, never refused. ---
check "explicit --live, live.env readable" 1 1 0 1 0 0
check "explicit --live, live.env readable, container exists" 1 1 1 1 0 0

# --- --public-only (the optional fourth input): ships the public Quant site
# during a trading session without touching the live profile. It must win
# over every reason the heuristic has to auto-add live -- a readable live.env
# and an existing ohcamel-live container are exactly the droplet's normal
# state, and auto-adding there is what would drag the market-hours guard (or
# worse, a live restart) into a public-site deploy. ---
check4() {
	local label="$1" live_flag="$2" readable="$3" container="$4" public_only="$5"
	local profile auto refuse
	read -r profile auto refuse <<<"$(resolve_live_profile "$live_flag" "$readable" "$container" "$public_only")"
	if [ "$profile" = 0 ] && [ "$auto" = 0 ] && [ "$refuse" = 0 ]; then
		ok "$label -- profile=$profile auto=$auto refuse=$refuse"
	else
		no "$label" "got profile=$profile auto=$auto refuse=$refuse, expected 0 0 0"
	fi
}
for readable in 0 1; do
	for container in 0 1; do
		check4 "--public-only, live.env readable=$readable, container=$container -- never live" 0 "$readable" "$container" 1
	done
done
# Not produced by main() (it refuses the pair first), but still inert.
check4 "--public-only with --live -- inert, main() refuses the pair" 1 1 1 1

# And an explicit 0 in the fourth slot is exactly the three-argument form.
read -r a <<<"$(resolve_live_profile 0 1 0 0)"
read -r b <<<"$(resolve_live_profile 0 1 0)"
[ "$a" = "$b" ] && ok "public_only=0 is the three-argument form" \
	|| no "public_only=0 is the three-argument form" "got '$a' vs '$b'"

printf '\n  %d passed, %d failed\n\n' "$pass" "$fail"
[ "$fail" -eq 0 ] || exit 1
