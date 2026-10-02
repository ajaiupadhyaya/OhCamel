#!/usr/bin/env bash
#
# Drives deploy/pull-backups.sh through a shim `rsync` on PATH and reads the
# launchd plist that runs it, pinning the off-box copy the finish plan's
# ruling 11 names ("the owner's free pull"):
#
#   deploy/test/pull_backups_test.sh
#
# Exits non-zero, naming the assertion, if any case disagrees. Needs bash and
# coreutils; `plutil` when present (macOS; CI's build-and-test macOS leg runs
# `plutil -lint` on the plist too, and this test says so when it skips it).
# No ssh, no network, no daemon: the shim records the one rsync call the
# script makes and answers with whatever exit status a case asks for.
#
#   P. one rsync a run, from ohcamel:/var/backups/ohcamel/ into
#      $HOME/Backups/ohcamel/ (created first), archive mode, in-progress
#      `.part` copies excluded, ssh in BatchMode so a missing key fails
#      instead of prompting a launchd job that has no terminal; a failing
#      rsync fails the run; OHCAMEL_SSH_HOST re-points the host alias.
#   L. the plist: lints, carries the label, runs this script by its path
#      under $HOME through a shell (launchd expands no `~`), on a calendar
#      interval (launchd runs a missed one on wake, which is the "while the
#      laptop is awake" of the plan), and never at load.

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."

script=deploy/pull-backups.sh
plist=deploy/launchd/com.ohcamel.pull-backups.plist

pass=0
fail=0
ok() { printf '  ok    %s\n' "$1"; pass=$((pass + 1)); }
no() { printf '  FAIL  %s -- %s\n' "$1" "$2"; fail=$((fail + 1)); }
finish() {
	printf '\n  %d passed, %d failed\n\n' "$pass" "$fail"
	[ "$fail" -eq 0 ] || exit 1
}

missing=0
for f in "$script" "$plist"; do
	if [ ! -f "$f" ]; then
		no "$f" "missing"
		missing=1
	fi
done
if [ "$missing" = 1 ]; then finish; fi

scratch=$(mktemp -d "${TMPDIR:-/tmp}/pull-backups-test.XXXXXX")
trap 'rm -rf "$scratch"' EXIT
log="$scratch/rsync.log"
home="$scratch/home"

mkdir -p "$scratch/bin"
cat >"$scratch/bin/rsync" <<'SHIM'
#!/usr/bin/env bash
{ printf 'rsync'; printf ' %q' "$@"; printf '\n'; } >>"$SHIM_LOG"
exit "${SHIM_EXIT:-0}"
SHIM
chmod +x "$scratch/bin/rsync"

rc=0
run_pull() { # [VAR=value ...]
	rm -rf "$home"
	mkdir -p "$home"
	: >"$log"
	rc=0
	env -u OHCAMEL_SSH_HOST -u OHCAMEL_PULL_DIR HOME="$home" PATH="$scratch/bin:$PATH" SHIM_LOG="$log" "$@" \
		bash "$script" >"$scratch/out" 2>"$scratch/err" || rc=$?
}
expect_rc() {
	if [ "$rc" -eq "$2" ]; then ok "$1"; else no "$1" "exit $rc, expected $2; stderr: $(head -c 300 "$scratch/err" | tr '\n' ' ')"; fi
}
expect_has() { # LABEL FILE FIXED-STRING
	if grep -qF -- "$3" "$2"; then ok "$1"; else no "$1" "'$3' not found in: $(head -c 300 "$2" | tr '\n' ' ')"; fi
}

# --- P. the pull --------------------------------------------------------------------
run_pull
expect_rc "P1 a pull whose rsync succeeds" 0
n=$(grep -c . "$log" || true)
if [ "$n" = 1 ]; then ok "P1 exactly one rsync call"; else no "P1 exactly one rsync call" "$n calls"; fi
call=$(head -n1 "$log")
for word in " -a" " ohcamel:/var/backups/ohcamel/ " " $home/Backups/ohcamel/" "BatchMode=yes" ".part"; do
	case "$call" in
	*"$word"*) ok "P1 the call carries '$word'" ;;
	*) no "P1 the call carries '$word'" "call was: $(printf '%s' "$call" | cut -c1-300)" ;;
	esac
done
case "$call" in
*--delete*) no "P1 the pull never deletes on the laptop: the host prunes, the laptop keeps" "--delete is in the call" ;;
*) ok "P1 the pull never deletes on the laptop: the host prunes, the laptop keeps" ;;
esac
if [ -d "$home/Backups/ohcamel" ]; then ok "P1 \$HOME/Backups/ohcamel is created before the pull"; else no "P1 \$HOME/Backups/ohcamel is created before the pull" "missing"; fi
expect_has "P1 the run reports where it pulled to" "$scratch/out" "$home/Backups/ohcamel"

run_pull SHIM_EXIT=23
expect_rc "P2 rsync exiting 23 (partial transfer) fails the run" 1
expect_has "P2 and the failure names rsync's status" "$scratch/err" "23"

run_pull OHCAMEL_SSH_HOST=droplet-two
call=$(head -n1 "$log")
case "$call" in
*" droplet-two:/var/backups/ohcamel/ "*) ok "P3 OHCAMEL_SSH_HOST re-points the host alias" ;;
*) no "P3 OHCAMEL_SSH_HOST re-points the host alias" "call was: $(printf '%s' "$call" | cut -c1-300)" ;;
esac

run_pull OHCAMEL_PULL_DIR="$scratch/elsewhere"
call=$(head -n1 "$log")
case "$call" in
*" $scratch/elsewhere/") ok "P4 OHCAMEL_PULL_DIR re-points the destination" ;;
*) no "P4 OHCAMEL_PULL_DIR re-points the destination" "call was: $(printf '%s' "$call" | cut -c1-300)" ;;
esac

# --- L. the plist -------------------------------------------------------------------
if command -v plutil >/dev/null 2>&1; then
	if plutil -lint "$plist" >"$scratch/lint" 2>&1; then
		ok "L1 plutil -lint $plist"
	else
		no "L1 plutil -lint $plist" "$(tr '\n' ' ' <"$scratch/lint")"
	fi
else
	echo "  skip  L1 plutil is not on this machine (macOS only; CI's macOS leg runs it)"
fi
expect_has "L2 the label is com.ohcamel.pull-backups" "$plist" "<string>com.ohcamel.pull-backups</string>"
expect_has "L2 it runs deploy/pull-backups.sh" "$plist" "deploy/pull-backups.sh"
expect_has "L2 the script path is under \$HOME, expanded by a shell" "$plist" '$HOME/'
expect_has "L2 on a calendar interval" "$plist" "<key>StartCalendarInterval</key>"
if grep -E '<string>[^<]*~' "$plist" >/dev/null; then
	no "L3 no literal ~ in a launchd string (it expands none)" "$(grep -nE '<string>[^<]*~' "$plist" | head -2 | tr '\n' ' ')"
else
	ok "L3 no literal ~ in a launchd string (it expands none)"
fi
if grep -A1 '<key>RunAtLoad</key>' "$plist" | grep -q '<false/>'; then
	ok "L3 RunAtLoad is false: loading the agent does not start a pull"
else
	no "L3 RunAtLoad is false" "RunAtLoad is not <false/>"
fi

finish
