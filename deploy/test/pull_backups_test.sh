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
#   W. the warehouse exports (Task 3.3 fix round 1): the main rsync excludes
#      warehouse/; a listing of the host's exports picks the NEWEST and each
#      month's newest; each is pulled into DATE.part, checked (export.json,
#      load.sql and every Parquet file it names) and only then renamed to
#      DATE; one already on the laptop is not pulled again. Then the laptop
#      keeps its 3 newest exports and each month's newest -- and prunes
#      nothing at all when the newest did not arrive whole, so the only
#      off-box copy is never the one deleted.
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
dest=${!#}
case " $* " in
*" --list-only "*)
	[ "${SHIM_LIST_EXIT:-0}" = 0 ] || exit "$SHIM_LIST_EXIT"
	printf 'drwxrwx---          4,096 2026/11/17 06:31:00 .\n'
	for d in ${SHIM_REMOTE_EXPORTS-}; do
		printf 'drwxrwx---          4,096 2026/11/17 06:31:00 %s\n' "$d"
	done
	printf 'drwxrwx---          4,096 2026/11/17 06:31:00 2026-11-18.part\n'
	exit 0
	;;
*"/warehouse/20"[0-9][0-9]-*)
	d=$(basename "$dest" .part)
	mkdir -p "$dest"
	printf "COPY bars_daily FROM '/backups/warehouse/%s.part/bars_daily.parquet' (FORMAT 'parquet');\n" "$d" >"$dest/load.sql"
	printf 'PAR1' >"$dest/bars_daily.parquet"
	case " ${SHIM_BAD_EXPORTS-} " in
	*" $d "*) ;;
	*) printf '{"day": "%s"}\n' "$d" >"$dest/export.json" ;;
	esac
	exit "${SHIM_EXPORT_EXIT:-0}"
	;;
esac
exit "${SHIM_EXIT:-0}"
SHIM
chmod +x "$scratch/bin/rsync"

rc=0
run_pull() { # [VAR=value ...]
	run_pull_keep "$@"
}
run_pull_keep() { # like run_pull, but $home is the caller's (a laptop with exports on it)
	[ "${keep_home:-0}" = 1 ] || { rm -rf "$home"; mkdir -p "$home"; }
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
if [ "$n" = 2 ]; then ok "P1 two rsync calls with no export on the host: the pull and the export listing"; else no "P1 two rsync calls" "$n calls"; fi
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


# --- W. the warehouse exports ---------------------------------------------------------
exports() { (cd "$home/Backups/ohcamel/warehouse" 2>/dev/null && find . -mindepth 1 -maxdepth 1 | sed 's|^\./||' | sort | tr '\n' ' ' | sed 's/ $//'); }
plant() { # DATE...: verified exports already on the laptop
	local d
	for d in "$@"; do
		mkdir -p "$home/Backups/ohcamel/warehouse/$d"
		printf "COPY bars_daily FROM '/x/%s/bars_daily.parquet' (FORMAT 'parquet');\n" "$d" >"$home/Backups/ohcamel/warehouse/$d/load.sql"
		printf 'PAR1' >"$home/Backups/ohcamel/warehouse/$d/bars_daily.parquet"
		: >"$home/Backups/ohcamel/warehouse/$d/export.json"
	done
}

run_pull
case "$(head -n1 "$log")" in
*"--exclude=warehouse/"*) ok "W1 the main pull excludes warehouse/ (the exports are pulled apart)" ;;
*) no "W1 the main pull excludes warehouse/" "$(head -n1 "$log" | cut -c1-300)" ;;
esac
# completion task 3.6 fix round 1: the backup's lock and last-run record live in .state/ -- the host's, not a copy
case "$(head -n1 "$log")" in
*"--exclude=.state/"*) ok "W1b the main pull leaves out .state/ (backup.sh's lock and record)" ;;
*) no "W1b the main pull leaves out .state/" "$(head -n1 "$log" | cut -c1-300)" ;;
esac

run_pull SHIM_REMOTE_EXPORTS="2026-09-29 2026-09-30 2026-10-31 2026-11-15 2026-11-16 2026-11-17"
expect_rc "W2 a first pull of the exports" 0
if [ "$(exports)" = "2026-09-30 2026-10-31 2026-11-17" ]; then
	ok "W2 the newest export and each month's newest arrive, nothing else"
else
	no "W2 the newest export and each month's newest arrive, nothing else" "laptop has: $(exports)"
fi
if [ -f "$home/Backups/ohcamel/warehouse/2026-11-17/export.json" ]; then ok "W2 each arrives whole, under its date"; else no "W2 each arrives whole" "$(exports)"; fi
grep -q -- "--list-only" "$log" && ok "W2 the host's exports are listed with rsync, not a shell" || no "W2 listed with rsync" "no --list-only call"

rm -rf "$home"; mkdir -p "$home"
plant 2026-10-31 2026-11-12 2026-11-13 2026-11-14 2026-11-15 2026-11-16
keep_home=1 run_pull SHIM_REMOTE_EXPORTS="2026-10-31 2026-11-16 2026-11-17"
expect_rc "W3 a nightly pull over a laptop holding six exports" 0
if [ "$(exports)" = "2026-10-31 2026-11-15 2026-11-16 2026-11-17" ]; then
	ok "W3 the laptop keeps its 3 newest exports and each month's newest (11-12, 11-13, 11-14 pruned)"
else
	no "W3 the laptop keeps its 3 newest and each month's newest" "laptop has: $(exports)"
fi
if grep -q "warehouse/2026-10-31/" "$log"; then no "W3 an export already on the laptop is not pulled again" "10-31 was pulled"; else ok "W3 an export already on the laptop is not pulled again"; fi

rm -rf "$home"; mkdir -p "$home"
plant 2026-10-31 2026-11-12 2026-11-13 2026-11-14 2026-11-15 2026-11-16
keep_home=1 run_pull SHIM_REMOTE_EXPORTS="2026-10-31 2026-11-16 2026-11-17" SHIM_BAD_EXPORTS=2026-11-17
expect_rc "W4 a newest export that arrives incomplete fails the run" 1
if [ "$(exports)" = "2026-10-31 2026-11-12 2026-11-13 2026-11-14 2026-11-15 2026-11-16" ]; then
	ok "W4 and nothing is pruned, and no half copy is left"
else
	no "W4 and nothing is pruned, and no half copy is left" "laptop has: $(exports)"
fi
expect_has "W4 and the failure names the export" "$scratch/err" "2026-11-17"

rm -rf "$home"; mkdir -p "$home"
plant 2026-11-12 2026-11-13 2026-11-14 2026-11-15
keep_home=1 run_pull SHIM_REMOTE_EXPORTS="2026-11-16" SHIM_LIST_EXIT=12
expect_rc "W5 a listing that fails fails the run" 1
if [ "$(exports)" = "2026-11-12 2026-11-13 2026-11-14 2026-11-15" ]; then ok "W5 and prunes nothing"; else no "W5 and prunes nothing" "laptop has: $(exports)"; fi

rm -rf "$home"; mkdir -p "$home"
plant 2026-11-12 2026-11-13 2026-11-14 2026-11-15
keep_home=1 run_pull SHIM_REMOTE_EXPORTS="2026-11-16" SHIM_EXPORT_EXIT=23
expect_rc "W6 an rsync of the newest export that fails fails the run" 1
if [ "$(exports)" = "2026-11-12 2026-11-13 2026-11-14 2026-11-15" ]; then ok "W6 and prunes nothing, leaves no .part"; else no "W6 and prunes nothing, leaves no .part" "laptop has: $(exports)"; fi

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
