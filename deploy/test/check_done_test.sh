#!/usr/bin/env bash
#
# scripts/check-done.sh (completion Task 5.3), against a shim interpreter and canned logs.
#
#   deploy/test/check_done_test.sh
#
# The script's own plumbing is what this pins; the checks' content is pinned where the code lives
# (quant/tests/test_check_done_probe.py runs the probe on a fixture journal, a fixture artifact, INC-02 and the
# committed SPY chain). Here CHECK_DONE_PY is a shim that answers the probe's subcommands from environment
# variables, so the test needs no quant environment and takes a second or two.
#
# What it pins:
#   - seven lines, numbered 1 to 7, each ending `ok`, `FAIL: <why>` or `not checked: <why>`;
#   - given nothing, every check that needs data says `not checked` (never `ok`) and the exit status is 0;
#   - --strict turns a `not checked` into exit status 2; a FAIL is exit status 1, strict or not;
#   - check 1 reads a soak log: ok when trip, reduce, restart and restart-tripped come in that order; FAIL when one
#     is missing or they are out of order; not checked with no log and no built suite;
#   - check 7 compares the ledger's as_of with today (CHECK_DONE_TODAY) and reads a file; a missing file is not
#     checked, a stale as_of FAIL;
#   - check 5 needs all four arguments and names the missing ones; a regenerate exit code is a FAIL naming the
#     artifact;
#   - the probe's own FAIL and not-checked lines pass through verbatim, and a probe that prints nothing the script
#     can read is a FAIL, never an ok;
#   - read-only: every input file and directory is byte-identical afterwards, and the scratch directory is gone.
#
# Exits non-zero, naming the case, if any assertion fails.

set -u
cd "$(dirname "${BASH_SOURCE[0]}")/../.." || exit 1

script=scripts/check-done.sh
pass=0
fail=0
ok() { printf '  ok    %s\n' "$1"; pass=$((pass + 1)); }
no() { printf '  FAIL  %s -- %s\n' "$1" "$2"; fail=$((fail + 1)); }

work=$(mktemp -d "${TMPDIR:-/tmp}/check-done-test.XXXXXX")
trap 'rm -rf "$work"' EXIT
export TMPDIR="$work/tmp"
mkdir -p "$TMPDIR" "$work/in"

# The shim interpreter: `py <probe> <sub> ...` prints SHIM_<SUB>, and `py tools/regenerate.py DIR ...` exits with
# SHIM_REGEN_<basename of DIR> (default 0).
cat >"$work/py" <<'EOF'
#!/usr/bin/env bash
case "$1" in
*check_done_probe.py)
	sub=$2
	var="SHIM_$(printf '%s' "$sub" | tr '[:lower:]' '[:upper:]')"
	printf '%s\n' "${!var-ok}"
	;;
*regenerate.py)
	dir=$(basename "$2")
	var="SHIM_REGEN_$dir"
	echo "regenerate: $dir"
	exit "${!var-0}"
	;;
esac
EOF
chmod +x "$work/py"
export CHECK_DONE_PY="$work/py"
export CHECK_DONE_TODAY=2026-11-25
export CHECK_DONE_SOAK_EXE="$work/no-such-suite"

good="$work/in/soak-good.output"
cat >"$good" <<'EOF'
  SOAK  reduce_through_trip true
ASSERT three observations over 30 s: tripped on dd-cap
ASSERT R2 on: soak_gov's governor cut (governor 0) filled through the trip
  [2026-09-17 14:00:00Z] soak      RESTART: the process is rebuilt from the journal file
  [2026-09-17 14:00:00Z] soak      after the restart: switch tripped, kernel armed, drawdown 0.0098
EOF
grep -v 'RESTART' "$good" >"$work/in/soak-no-restart.output"
{ sed -n 1,2p "$good"; sed -n 4,5p "$good"; sed -n 3p "$good"; } >"$work/in/soak-disorder.output"
printf '{"as_of": "2026-11-25T06:30:00+00:00", "rows": []}\n' >"$work/in/ledger-today.json"
printf '{"as_of": "2026-11-24T06:30:00+00:00", "rows": []}\n' >"$work/in/ledger-stale.json"
printf '{"rows": []}\n' >"$work/in/ledger-none.json"
mkdir -p "$work/in/atlas" "$work/in/farm" "$work/in/surface" "$work/in/wh"
echo x >"$work/in/atlas/manifest.json"
echo db >"$work/in/journal.db"

tree_sum() { (cd "$work/in" && find . -type f | LC_ALL=C sort | while read -r f; do printf '%s ' "$f"; cksum <"$f"; done); }
before=$(tree_sum)

out=''
run() { out=$("$script" "$@" 2>&1); rc=$?; }
line() { printf '%s\n' "$out" | sed -n "${1}p"; }
has() { case "$out" in *"$1"*) return 0 ;; *) return 1 ;; esac; }
expect() { # NAME CONDITION-RESULT
  if [ "$2" = 0 ]; then ok "$1"; else no "$1" "got: $(printf '%s' "$out" | tr '\n' '|') rc=$rc"; fi
}

# ---- given nothing ----------------------------------------------------------------------------------------------
SHIM_INC02="FAIL: INC-02 is not approved" run
expect "seven lines" "$([ "$(printf '%s\n' "$out" | wc -l | tr -d ' ')" = 7 ] && echo 0 || echo 1)"
bad=0
for n in 1 2 3 4 5 6 7; do
	l=$(line "$n")
	case "$l" in
	"$n. "*": ok" | "$n. "*": FAIL: "* | "$n. "*": not checked: "*) ;;
	*) bad=1 ;;
	esac
done
expect "every line is numbered and ends ok, FAIL: or not checked:" "$bad"
case "$(line 1)" in *"not checked: the soak suite is not built"*) c=0 ;; *) c=1 ;; esac
expect "no log and no built suite: check 1 is not checked" "$c"
case "$(line 2)" in *"not checked: no journal copy given") c=0 ;; *) c=1 ;; esac
expect "no journal: check 2 says so" "$c"
case "$(line 3)" in *"not checked: no farm artifact given") c=0 ;; *) c=1 ;; esac
expect "no farm artifact: check 3 says so" "$c"
case "$(line 4)" in *": FAIL: INC-02 is not approved") c=0 ;; *) c=1 ;; esac
expect "an unapproved INC-02 is a FAIL line, never ok" "$c"
case "$(line 5)" in *"not checked: no artifacts given (missing --atlas --farm --surface --warehouse)") c=0 ;; *) c=1 ;; esac
expect "no artifacts: check 5 names what is missing" "$c"
case "$(line 7)" in *"not checked: no ledger given"*) c=0 ;; *) c=1 ;; esac
expect "no ledger: check 7 is not checked" "$c"
expect "a FAIL line makes the exit status 1" "$([ "$rc" = 1 ] && echo 0 || echo 1)"

# ---- exit statuses ----------------------------------------------------------------------------------------------
run
expect "nothing failed, some not checked: exit 0" "$([ "$rc" = 0 ] && echo 0 || echo 1)"
run --strict
expect "--strict: a not checked line is exit 2" "$([ "$rc" = 2 ] && echo 0 || echo 1)"
SHIM_INC02="FAIL: x" run --strict
expect "--strict: a FAIL is still exit 1" "$([ "$rc" = 1 ] && echo 0 || echo 1)"
run --no-such-flag
expect "an unknown flag is refused (64)" "$([ "$rc" = 64 ] && echo 0 || echo 1)"

# ---- check 1: the soak log --------------------------------------------------------------------------------------
run --soak-log "$good"
case "$(line 1)" in *": ok") c=0 ;; *) c=1 ;; esac
expect "check 1: trip, reduce, restart, restart-tripped in order is ok" "$c"
run --soak-log "$work/in/soak-no-restart.output"
case "$(line 1)" in *"FAIL: the soak log lacks: restart"*) c=0 ;; *) c=1 ;; esac
expect "check 1: a missing event is a FAIL naming it" "$c"
run --soak-log "$work/in/soak-disorder.output"
case "$(line 1)" in *"FAIL: the soak's events are out of order"*) c=0 ;; *) c=1 ;; esac
expect "check 1: events out of order are a FAIL" "$c"
run --soak-log "$work/in/absent.output"
case "$(line 1)" in *"not checked: no soak log at"*) c=0 ;; *) c=1 ;; esac
expect "check 1: a log that is not there is not checked" "$c"

# ---- the probe's lines pass through -----------------------------------------------------------------------------
SHIM_LEDGER="FAIL: sub-ledger break at the close of 2026-11-24: SPY strategies 10 vs account 0" run --journal "$work/in/journal.db"
case "$(line 2)" in *"FAIL: sub-ledger break at the close of 2026-11-24: SPY strategies 10 vs account 0") c=0 ;; *) c=1 ;; esac
expect "check 2: the probe's FAIL passes through verbatim" "$c"
SHIM_LEDGER="" run --journal "$work/in/journal.db"
case "$(line 2)" in *"FAIL: the check printed no result"*) c=0 ;; *) c=1 ;; esac
expect "check 2: a probe that prints nothing is a FAIL, never ok" "$c"
SHIM_FARM="not checked: no farm artifact at x" run --farm "$work/in/farm"
case "$(line 3)" in *"not checked: no farm artifact at x") c=0 ;; *) c=1 ;; esac
expect "check 3: the probe's not checked passes through" "$c"
SHIM_INC02=ok SHIM_CHAIN="FAIL: 2026-12-18: density fails mean" run
case "$(line 6)" in *"FAIL: 2026-12-18: density fails mean") c=0 ;; *) c=1 ;; esac
expect "check 6: a failing slice is named" "$c"
SHIM_INC02=ok SHIM_CHAIN=ok SHIM_FARM=ok SHIM_LEDGER=ok run --journal "$work/in/journal.db" --farm "$work/in/farm" \
	--atlas "$work/in/atlas" --surface "$work/in/surface" --warehouse "$work/in/wh" --soak-log "$good" \
	--ledger "$work/in/ledger-today.json"
all_ok=0
for n in 1 2 3 4 5 6 7; do case "$(line "$n")" in "$n. "*": ok") ;; *) all_ok=1 ;; esac; done
expect "all seven given what they need and passing: seven ok lines" "$all_ok"
expect "and exit 0, strict or not" "$([ "$rc" = 0 ] && echo 0 || echo 1)"

# ---- check 5: regenerate ----------------------------------------------------------------------------------------
SHIM_REGEN_surface=4 run --atlas "$work/in/atlas" --farm "$work/in/farm" --surface "$work/in/surface" --warehouse "$work/in/wh"
case "$(line 5)" in *"FAIL: regenerate refused or differed: surface (exit 4:"*) c=0 ;; *) c=1 ;; esac
expect "check 5: a regenerate exit code is a FAIL naming the artifact" "$c"
run --atlas "$work/in/atlas" --farm "$work/in/farm" --surface "$work/in/surface" --warehouse "$work/in/missing"
case "$(line 5)" in *"not checked: no such directory:"*) c=0 ;; *) c=1 ;; esac
expect "check 5: an export that is not there is not checked" "$c"
run --atlas "$work/in/atlas" --farm "$work/in/farm"
case "$(line 5)" in *"missing --surface --warehouse"*) c=0 ;; *) c=1 ;; esac
expect "check 5: names exactly what is missing" "$c"

# ---- check 7: the ledger ----------------------------------------------------------------------------------------
run --ledger "$work/in/ledger-today.json"
case "$(line 7)" in *": ok") c=0 ;; *) c=1 ;; esac
expect "check 7: an as_of of today is ok" "$c"
run --ledger "$work/in/ledger-stale.json"
case "$(line 7)" in *"FAIL: the ledger's as_of is 2026-11-24T06:30:00+00:00, not 2026-11-25") c=0 ;; *) c=1 ;; esac
expect "check 7: yesterday's as_of is a FAIL naming both dates" "$c"
run --ledger "$work/in/ledger-none.json"
case "$(line 7)" in *"FAIL: the ledger has no as_of") c=0 ;; *) c=1 ;; esac
expect "check 7: a ledger with no as_of is a FAIL" "$c"
run --ledger "$work/in/absent.json"
case "$(line 7)" in *"not checked: no ledger file at"*) c=0 ;; *) c=1 ;; esac
expect "check 7: a file that is not there is not checked" "$c"

# ---- read-only --------------------------------------------------------------------------------------------------
after=$(tree_sum)
expect "read-only: every input file is byte-identical afterwards" "$([ "$before" = "$after" ] && echo 0 || echo 1)"
expect "the scratch directory is removed" "$([ -z "$(ls -A "$TMPDIR")" ] && echo 0 || echo 1)"

printf '\ncheck_done_test: %d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
