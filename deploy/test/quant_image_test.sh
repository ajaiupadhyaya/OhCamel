#!/usr/bin/env bash
#
# The Quant image carries the signal contract's schemas and the example book
# (autonomous-paper-trading plan, Task 16).
#
#   deploy/test/quant_image_test.sh
#
# Reads, as text:
#
#   quant/Dockerfile                  -- the image ohcamel-quant and
#                                        ohcamel-worker run
#   quant/Dockerfile.dockerignore     -- its allow-list (deny all, then allow
#                                        exactly what the Dockerfile copies)
#   quant/src/ohcamel_quant/config.py -- REPO_ROOT, the anchor every repo path
#                                        below hangs from
#   quant/src/ohcamel_quant/signals/contract.py, signals/daily.py
#                                     -- the two readers
#
# Why this exists. The signal producers the worker runs (signals.daily,
# signals.overlay) validate every document against interface/'s JSON schemas
# before it is written (signals/contract.py: REPO_ROOT / "interface"), and
# signals.daily refuses a cell naming a symbol outside book.example.sexp's
# positions (signals/daily.py: REPO_ROOT / "book.example.sexp"). REPO_ROOT is
# config.py's parents[3], which the Dockerfile asserts is /app. Neither file
# was in the image: every run on the droplet would have failed on a missing
# schema. Each edit below leaves the image building and is refused here:
#   - the COPY of interface/ or of book.example.sexp dropped from the runtime
#     stage, or pointed anywhere but /app/<the same path>;
#   - the allow-list line dropped, so the COPY fails for want of a context
#     file (or, worse, an allow line added for a path the Dockerfile does not
#     copy);
#   - the build's own import check no longer asserting both are present;
#   - REPO_ROOT, or either reader's path under it, moved;
#   - the worker's /signals mount point not created owned by 10001, so a
#     worker that mounts a fresh `signals` volume before ohcamel-research
#     seeds it root-owned (the race research_image_test.sh removed).
#
# Exits non-zero, naming the file, if any assertion fails. grep and awk only;
# picked up by ci.yml's deploy/test/*.sh step with no change there.

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."

df=quant/Dockerfile
ign=quant/Dockerfile.dockerignore
cfg=quant/src/ohcamel_quant/config.py
contract=quant/src/ohcamel_quant/signals/contract.py
daily=quant/src/ohcamel_quant/signals/daily.py

pass=0
fail=0
ok() { printf '  ok    %s\n' "$1"; pass=$((pass + 1)); }
no() { printf '  FAIL  %s -- %s\n' "$1" "$2"; fail=$((fail + 1)); }

for f in "$df" "$ign" "$cfg" "$contract" "$daily"; do
	[ -f "$f" ] || no "$f" "missing"
done
[ "$fail" -eq 0 ] || {
	printf '\n  %d passed, %d failed\n\n' "$pass" "$fail"
	exit 1
}

# The runtime stage: every non-comment line after the last FROM.
runtime=$(awk '/^[ \t]*#/ { next } toupper($1) == "FROM" { buf = ""; next } { buf = buf NR "\t" $0 "\n" } END { printf "%s", buf }' "$df")

# --- 1. The two COPYs, in the runtime stage, to /app/<path>. ---------------
for pair in 'interface/ /app/interface/' 'book.example.sexp /app/book.example.sexp'; do
	hit=$(awk -F '\t' -v want="COPY $pair" '{ line = $2; sub(/[ \t]+$/, "", line) } line == want { print $1; exit }' <<<"$runtime")
	if [ -n "$hit" ]; then
		ok "$df:$hit: the runtime stage has 'COPY $pair'"
	else
		no "$df: COPY $pair" "not in the runtime stage -- REPO_ROOT is /app in the image, so the producers read /app/${pair%% *}"
	fi
done

# --- 2. The build's import check asserts both are there. ------------------
if grep -qF "/app/interface/signal.schema.json" "$df" && grep -qF "/app/book.example.sexp" <<<"$(grep -F 'is_file()' "$df")"; then
	ok "$df: the build's import check asserts the schema and the example book are files"
else
	no "$df: import check" "does not assert /app/interface/signal.schema.json and /app/book.example.sexp are files -- a missing file would fail the first signals run, not the build"
fi

# --- 3. The allow-list lets both through, and nothing after re-denies them. -
for path in interface/ book.example.sexp; do
	line=$(grep -nxF "!$path" "$ign" | head -n 1 | cut -d: -f1 || true)
	if [ -n "$line" ]; then
		ok "$ign:$line: allows $path"
	else
		no "$ign: !$path" "not allowed -- the file denies everything first, so the COPY would find no $path in the context"
	fi
	# A later bare pattern that matches the path would deny it again.
	if [ -n "$line" ]; then
		redeny=$(awk -v from="$line" -v p="$path" 'NR > from && !/^[ \t]*(#|$|!)/ { q = $0; sub(/\/$/, "", q); pp = p; sub(/\/$/, "", pp); if (q == pp || q == "*" || q == "**") print NR ": " $0 }' "$ign")
		if [ -n "$redeny" ]; then
			no "$ign: $path" "denied again after its allow line: $redeny"
		else
			ok "$ign: nothing after line $line denies $path again"
		fi
	fi
done

# --- 3b. The worker's mount point seeds the volume as 10001. --------------
seed=$(awk -F '\t' '$2 ~ /mkdir[^&;]*\/signals([ \t;&\\]|$)/ { m = $1 } $2 ~ /chown[ \t]+10001(:10001)?[ \t]+\/signals([ \t;&\\]|$)/ { c = $1 } END { if (m && c) print m }' <<<"$runtime")
if [ -n "$seed" ]; then
	ok "$df:$seed: the runtime stage creates /signals owned by 10001"
else
	no "$df: /signals" "the runtime stage does not create /signals and chown it to 10001 -- the worker mounts the signals volume there, and a worker that mounts a fresh volume first would seed it owned by root, so ohcamel-research (uid 10001) could not write"
fi

# --- 4. The paths line up: REPO_ROOT is /app, the readers hang from it. ----
if grep -qE '^REPO_ROOT = Path\(__file__\)\.resolve\(\)\.parents\[3\]$' "$cfg" &&
	grep -qF "assert str(c.REPO_ROOT) == '/app'" "$df"; then
	ok "$cfg: REPO_ROOT is parents[3], and $df asserts it is /app"
else
	no "$cfg: REPO_ROOT" "is not parents[3], or the Dockerfile no longer asserts it is /app -- the COPY destinations above assume both"
fi
if grep -qE '^INTERFACE = REPO_ROOT / "interface"$' "$contract"; then
	ok "$contract: INTERFACE = REPO_ROOT / \"interface\""
else
	no "$contract: INTERFACE" "is not REPO_ROOT / \"interface\" -- the image puts the schemas at /app/interface"
fi
if grep -qE '^BOOK_PATH = REPO_ROOT / "book\.example\.sexp"$' "$daily"; then
	ok "$daily: BOOK_PATH = REPO_ROOT / \"book.example.sexp\""
else
	no "$daily: BOOK_PATH" "is not REPO_ROOT / \"book.example.sexp\" -- the image puts the book at /app/book.example.sexp"
fi

printf '\n  %d passed, %d failed\n\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
