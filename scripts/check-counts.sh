#!/usr/bin/env bash
#
# Checks that every <!-- count:KIND -->N<!-- /count --> marker in README.md,
# docs/overview.md and docs/status.md agrees with the source of truth for
# that KIND: lib/verified.ml's `tests` and `scheduler_tests`, and
# research/src/ohcamel_research/verified.py's `TESTS`.
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
# number out of, or on a source-of-truth constant this script cannot find.

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

if [ "$status" -eq 0 ]; then
  echo "check-counts.sh: ocaml-tests=$ocaml_tests scheduler-tests=$ocaml_scheduler_tests research-tests=$py_tests -- every marker agrees"
fi

exit "$status"
