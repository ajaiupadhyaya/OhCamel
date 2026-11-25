#!/usr/bin/env bash
#
# "What finished means" (docs/superpowers/specs/2026-11-15-the-completion.md), as a script that checks it
# (completion Task 5.3). Seven lines, one per item, each `ok`, `FAIL: <why>` or `not checked: <why>`:
#
#   1. the desk can be left alone    the sim soak (test/desk_async "soak") shows trip -> reduce -> restart still tripped
#   2. the record is true            Subledger.check on a journal copy: no break, strategy P&L sums to the account in $1
#   3. verdicts are about the trade  the farm artifact: fill open, max_gross 1, capacity_usd, trial count == the ledger's
#   4. the experiment can reject     INC-02's Approved-by line is filled in and the pooled_test product exists
#   5. numbers regenerate            tools/regenerate.py on the atlas, farm and surface artifacts
#   6. formulas fit the instrument   the committed SPY chain: densities (Task 4.1's condition), put/call IV, vol target
#   7. the site says what is true    the ledger's as_of is today (UTC)
#
# Usage (from the repository root or anywhere):
#
#   scripts/check-done.sh [--journal FILE] [--farm DIR] [--trial-ledger FILE] [--atlas DIR] [--surface DIR]
#                         [--warehouse DIR] [--ledger FILE|URL] [--soak-log FILE] [--strict]
#
#   --journal        a COPY of last night's journal (a backup: ohcamel journal-backup writes them)      -> check 2
#   --farm           the newest farm.sweep artifact directory                                             -> checks 3, 5
#   --trial-ledger   the farm's trial_ledger.jsonl (default: two levels above --farm, the artifact root)  -> check 3
#   --atlas, --surface, --warehouse   the newest risk.mc_atlas and options.surface_league artifact
#                    directories and the warehouse export they regenerate from                            -> check 5
#   --ledger         the /api/ledger answer, as a saved file or an http(s) URL (one GET)                  -> check 7
#   --soak-log       a soak case's output file instead of running the suite (alcotest's soak.000.output)  -> check 1
#   --strict         exit non-zero for a "not checked" line too (finished means all seven are ok)
#
# Honest by construction: a check that was not given what it needs says `not checked: <why>`, never `ok`; the exit
# status is 1 if any line is FAIL (and, with --strict, 2 if any is not checked). READ-ONLY: a journal is opened
# immutable, artifacts, ledgers and the export are read, nothing is written to any of them -- the only files made are
# under one mktemp scratch directory this script removes on exit (the soak suite's output, regenerate's re-run).
# It never touches the droplet: running it against the droplet's data is the owner's step, docs/runbooks/desk.md.
#
# Environment (tests set these; the defaults are right for a checkout): CHECK_DONE_PY (the quant interpreter,
# default quant/.venv/bin/python), CHECK_DONE_SOAK_EXE (the built test_desk_async.exe), CHECK_DONE_TODAY (YYYY-MM-DD).

set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${CHECK_DONE_PY:-$ROOT/quant/.venv/bin/python}"
SOAK_EXE="${CHECK_DONE_SOAK_EXE:-$ROOT/_build/default/test/desk_async/test_desk_async.exe}"
PROBE="$ROOT/scripts/check_done_probe.py"

journal='' farm='' tledger='' atlas='' surface='' warehouse='' ledger='' soaklog='' strict=0
while [ $# -gt 0 ]; do
  case "$1" in
    --journal) journal="${2:-}"; shift 2 ;;
    --farm) farm="${2:-}"; shift 2 ;;
    --trial-ledger) tledger="${2:-}"; shift 2 ;;
    --atlas) atlas="${2:-}"; shift 2 ;;
    --surface) surface="${2:-}"; shift 2 ;;
    --warehouse) warehouse="${2:-}"; shift 2 ;;
    --ledger) ledger="${2:-}"; shift 2 ;;
    --soak-log) soaklog="${2:-}"; shift 2 ;;
    --strict) strict=1; shift ;;
    -h | --help) sed -n '2,32p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "check-done: unknown argument: $1" >&2; exit 64 ;;
  esac
done

scratch="$(mktemp -d "${TMPDIR:-/tmp}/check-done.XXXXXX")" || exit 70
trap 'rm -rf "$scratch"' EXIT

fails=0 unchecked=0
# report N NAME RESULT: the one line a check prints; RESULT is ok, FAIL: ... or not checked: ...
report() {
  case "$3" in
    ok) ;;
    FAIL:*) fails=$((fails + 1)) ;;
    "not checked:"*) unchecked=$((unchecked + 1)) ;;
    *) set -- "$1" "$2" "FAIL: the check printed no result: $3"; fails=$((fails + 1)) ;;
  esac
  printf '%s. %s: %s\n' "$1" "$2" "$3"
}

# The probe's one line, or why it could not run.
probe() {
  if [ ! -x "$PY" ]; then
    echo "not checked: no quant environment at $PY (run quant's uv sync, or set CHECK_DONE_PY)"
    return
  fi
  "$PY" "$PROBE" "$@" 2>&1 | tail -n 1
}

# 1 -- the soak -------------------------------------------------------------------------------------------------
check_soak() {
  local log="$soaklog" out
  if [ -z "$log" ]; then
    if [ ! -x "$SOAK_EXE" ]; then
      echo "not checked: the soak suite is not built ($SOAK_EXE); run make test, or give --soak-log"
      return
    fi
    mkdir -p "$scratch/soak" || return
    out="$(cd "$scratch/soak" && "$SOAK_EXE" test soak 2>&1)"
    local rc=$?
    if [ "$rc" -ne 0 ]; then
      echo "FAIL: the soak suite exited $rc: $(printf '%s' "$out" | grep -E '\[FAIL\]|Error|failed' | head -n 1)"
      return
    fi
    log="$(find "$scratch/soak" -name 'soak.000.output' | head -n 1)"
    if [ -z "$log" ]; then
      echo "FAIL: the soak suite ran but wrote no soak.000.output"
      return
    fi
  elif [ ! -f "$log" ]; then
    echo "not checked: no soak log at $log"
    return
  fi
  # The four events, in this order: the kernel's trip, a reduction sent through it, the restart, the switch
  # still tripped after it. Each is a line the soak asserts or traces (test/desk_async/test_desk_async.ml).
  local trip reduce restart after
  trip="$(grep -n 'tripped on dd-cap' "$log" | head -n 1 | cut -d: -f1)"
  reduce="$(grep -n "governor cut" "$log" | head -n 1 | cut -d: -f1)"
  restart="$(grep -n 'RESTART' "$log" | head -n 1 | cut -d: -f1)"
  after="$(grep -n 'after the restart: switch tripped' "$log" | head -n 1 | cut -d: -f1)"
  local missing=''
  [ -n "$trip" ] || missing="$missing trip"
  [ -n "$reduce" ] || missing="$missing reduce"
  [ -n "$restart" ] || missing="$missing restart"
  [ -n "$after" ] || missing="$missing restart-tripped"
  if [ -n "$missing" ]; then
    echo "FAIL: the soak log lacks:$missing"
  elif [ "$trip" -lt "$reduce" ] && [ "$reduce" -lt "$restart" ] && [ "$restart" -lt "$after" ]; then
    echo "ok"
  else
    echo "FAIL: the soak's events are out of order (trip $trip, reduce $reduce, restart $restart, tripped $after)"
  fi
}

# 5 -- regenerate -----------------------------------------------------------------------------------------------
check_regenerate() {
  local missing='' d
  [ -n "$atlas" ] || missing="$missing --atlas"
  [ -n "$farm" ] || missing="$missing --farm"
  [ -n "$surface" ] || missing="$missing --surface"
  [ -n "$warehouse" ] || missing="$missing --warehouse"
  if [ -n "$missing" ]; then
    echo "not checked: no artifacts given (missing$missing)"
    return
  fi
  for d in "$atlas" "$farm" "$surface" "$warehouse"; do
    if [ ! -d "$d" ]; then
      echo "not checked: no such directory: $d"
      return
    fi
  done
  if [ ! -x "$PY" ]; then
    echo "not checked: no quant environment at $PY"
    return
  fi
  local bad='' out rc n=0
  for d in "$atlas" "$farm" "$surface"; do
    n=$((n + 1))
    out="$("$PY" "$ROOT/tools/regenerate.py" "$d" --warehouse "$warehouse" --scratch "$scratch/regen$n" 2>&1)"
    rc=$?
    [ "$rc" -eq 0 ] || bad="$bad $(basename "$d") (exit $rc: $(printf '%s' "$out" | tail -n 1))"
  done
  if [ -z "$bad" ]; then echo "ok"; else echo "FAIL: regenerate refused or differed:$bad"; fi
}

# 7 -- the ledger's as_of ---------------------------------------------------------------------------------------
check_ledger() {
  local today="${CHECK_DONE_TODAY:-$(date -u +%Y-%m-%d)}" body="$scratch/ledger.json"
  if [ -z "$ledger" ]; then
    echo "not checked: no ledger given (--ledger FILE or URL: the /api/ledger answer)"
    return
  fi
  case "$ledger" in
    http://* | https://*)
      if ! curl -fsS --max-time 20 -o "$body" "$ledger" 2>"$scratch/curl.err"; then
        echo "not checked: could not read $ledger: $(tail -n 1 "$scratch/curl.err")"
        return
      fi ;;
    *)
      if [ ! -f "$ledger" ]; then
        echo "not checked: no ledger file at $ledger"
        return
      fi
      body="$ledger" ;;
  esac
  local as_of
  as_of="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["as_of"])' "$body" 2>/dev/null)"
  if [ -z "$as_of" ]; then
    echo "FAIL: the ledger has no as_of"
  elif [ "${as_of%%T*}" = "$today" ]; then
    echo "ok"
  else
    echo "FAIL: the ledger's as_of is $as_of, not $today"
  fi
}

report 1 "the desk can be left alone (soak: trip, reduce, restart tripped)" "$(check_soak)"

if [ -z "$journal" ]; then
  r2="not checked: no journal copy given"
else
  r2="$(probe ledger "$journal")"
fi
report 2 "the record is true (sub-ledger, strategy P&L = account)" "$r2"

if [ -z "$farm" ]; then
  r3="not checked: no farm artifact given"
else
  if [ -n "$tledger" ]; then r3="$(probe farm "$farm" "$tledger")"; else r3="$(probe farm "$farm")"; fi
fi
report 3 "the farm is scored as traded (fill open, gross 1, capacity, trials)" "$r3"

report 4 "the live experiment can reject something (INC-02 approved, pooled_test)" "$(probe inc02)"
report 5 "every published number regenerates (atlas, farm, surface)" "$(check_regenerate)"
report 6 "every formula fits its instrument (SPY densities, put/call IV, vol target)" "$(probe chain)"
report 7 "the site says what is true today (ledger as_of)" "$(check_ledger)"

[ "$fails" -eq 0 ] || exit 1
[ "$strict" -eq 0 ] || [ "$unchecked" -eq 0 ] || exit 2
exit 0
