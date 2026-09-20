#!/usr/bin/env bash
#
# Checks that every <!-- count:KIND -->N<!-- /count --> marker in README.md,
# docs/overview.md and docs/status.md agrees with the source of truth for
# that KIND: lib/verified.ml's `tests` and `scheduler_tests`, and
# research/src/ohcamel_research/verified.py's `TESTS`. Also sweeps the same
# three documents for a bare count -- a number quoting a test count with no
# marker around it at all -- and fails on any it finds outside docs/status.md's
# dated ledger rows. That second sweep exists because a marker check alone has
# a hole: stripping the <!-- count:KIND --> tags (or writing a brand-new count
# sentence and never wrapping it) leaves a plain "658 tests" that the marker
# comparison above has nothing to compare, so it reports every marker present
# agrees and exits 0 while the number is free to drift right next to it.
#
# grep and sed only -- no dune, no build, no opam switch -- because the
# `lint` job that runs this on every push has none of those, and a count
# check that itself needed a build would defeat the point of being cheap
# enough to run on every push. This is what makes Definition of Done #3
# (ruling 22: "a count is a CI fact") true: the OCaml suite already asserts
# its own count against lib/verified.ml (test/test_ohcamel.ml), but nothing
# before this script checked the three prose sentences that quote it, which
# is exactly how 355 survived in three documents after the research count
# moved to 388.
#
# Run from the repository root:
#
#   scripts/check-counts.sh
#
# Exits non-zero -- naming the file and line -- on the first marker whose
# number disagrees with its source of truth, on a marker sed cannot parse a
# number out of, on a source-of-truth constant this script cannot find, or
# on a bare (unmarked) count sentence outside the ledger allowlist below.

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

status=0

# --- Source of truth -------------------------------------------------------

ocaml_tests=$(grep -E '^let tests = [0-9]+$' lib/verified.ml | grep -oE '[0-9]+') || {
  echo "check-counts.sh: could not find 'let tests = N' in lib/verified.ml"
  exit 1
}

ocaml_scheduler_tests=$(grep -E '^let scheduler_tests = [0-9]+$' lib/verified.ml | grep -oE '[0-9]+') || {
  echo "check-counts.sh: could not find 'let scheduler_tests = N' in lib/verified.ml"
  exit 1
}

py_tests=$(grep -E '^TESTS = [0-9]+$' research/src/ohcamel_research/verified.py | grep -oE '[0-9]+') || {
  echo "check-counts.sh: could not find 'TESTS = N' in research/src/ohcamel_research/verified.py"
  exit 1
}

# --- Markers -----------------------------------------------------------

docs=(README.md docs/overview.md docs/status.md)

# check_marker KIND EXPECTED: every "<!-- count:KIND -->N<!-- /count -->" in
# each of $docs must read N == EXPECTED. Sets $status rather than exiting on
# the first mismatch, so one run reports every disagreement, not just the
# first file alphabetically.
check_marker() {
  kind="$1"
  expected="$2"
  for file in "${docs[@]}"; do
    while IFS=: read -r line_no content; do
      [ -z "$line_no" ] && continue
      found=$(printf '%s\n' "$content" | sed -E "s#.*<!-- count:${kind} -->([0-9]+)<!-- /count -->.*#\1#")
      if [ "$found" = "$content" ]; then
        echo "check-counts.sh: $file:$line_no: could not parse a number out of a count:${kind} marker"
        status=1
        continue
      fi
      if [ "$found" != "$expected" ]; then
        echo "check-counts.sh: $file:$line_no: count:${kind} says $found, but the source of truth says $expected"
        status=1
      fi
    done < <(grep -n -- "<!-- count:${kind} -->" "$file" || true)
  done
}

check_marker ocaml-tests "$ocaml_tests"
check_marker scheduler-tests "$ocaml_scheduler_tests"
check_marker research-tests "$py_tests"

# --- Bare counts -----------------------------------------------------------
#
# A marker breaks the very adjacency each pattern below needs to match: in
# "<!-- count:ocaml-tests -->658<!-- /count --> tests" the digits are
# followed by "<!-- /count -->", not by a space and the word, so nothing
# properly wrapped ever matches here. Anything that DOES match is therefore
# a count sentence with no marker at all -- a marker someone stripped, or a
# new sentence nobody wrapped -- and either way check_marker above has
# nothing to compare it to and would report success regardless.
#
# The four patterns are the ones actually in use across the three
# documents today (see the "count:KIND" sentences above and README.md's
# own "N in `test/desk_async`" phrasing for the scheduler count); a count
# sentence written with new wording would need a new pattern here too.
bare_count_patterns=(
  '[0-9]+ tests?\b'
  '[0-9]+ Python tests?\b'
  '[0-9]+ scheduler cases\b'
  '[0-9]+ hermetic tests\b'
  '[0-9]+ in `test/desk_async`'
)

# docs/status.md's dated ledger keeps one row per merged phase, each stating
# the counts that were true on the date it was written ("| 2026-09-19 |
# Phase A3 merged ... 600 tests plus 30 scheduler cases and 388 research
# tests ...") -- a historical record, not a live claim, and it is allowed
# to disagree with today's source of truth on purpose. Allowed by an
# explicit pattern matching only a dated ledger row's own line (a table row
# starting "| YYYY-MM-DD"), not by skipping docs/status.md outright: a new
# count sentence written anywhere else in that file, including right next
# to the ledger but outside one of its own dated rows, still fails.
status_ledger_row='^\| [0-9]{4}-[0-9]{2}-[0-9]{2}'

for file in "${docs[@]}"; do
  for pattern in "${bare_count_patterns[@]}"; do
    while IFS=: read -r line_no content; do
      [ -z "$line_no" ] && continue
      if [ "$file" = "docs/status.md" ] && printf '%s\n' "$content" | grep -qE "$status_ledger_row"; then
        continue
      fi
      match=$(printf '%s\n' "$content" | grep -oE "$pattern" | head -1)
      echo "check-counts.sh: $file:$line_no: bare count \"$match\" -- not inside a <!-- count:KIND --> marker"
      status=1
    done < <(grep -n -E "$pattern" "$file" || true)
  done
done

if [ "$status" -eq 0 ]; then
  echo "check-counts.sh: ocaml-tests=$ocaml_tests scheduler-tests=$ocaml_scheduler_tests research-tests=$py_tests -- every marker agrees, and no bare count found"
fi

exit "$status"
