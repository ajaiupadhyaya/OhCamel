#!/usr/bin/env bash
#
# Pins, as text, the two files in this repository that no other check reads
# and no green GitHub run reports on:
#
#   .github/workflows/ci.yml   -- its top-level token scope and push triggers
#   .github/dependabot.yml     -- the two weekly entries the digest pins rely on
#
#   deploy/test/ci_hygiene_test.sh
#
# Exits non-zero, naming the file and the line, if any of the three
# assertions below fails or either file is missing. grep and awk only, no
# yq and no python; runs from any directory; picked up by the lint job's
# deploy/test/*.sh step with no change to the workflow.
#
# Why this exists. A run that goes green does not report what was NOT
# granted, NOT triggered or NOT bumped, and nothing under deploy/, scripts/
# or the Makefile read either file, so each of these edits used to leave
# every check green:
#   - deleting the top-level `permissions: contents: read` -- every job
#     without a block of its own silently inherits the repository's Actions
#     default, which for a repository created before GitHub changed that
#     default is read/write;
#   - dropping `final/**` (or `desk/**`) from `on.push.branches` -- a stage
#     branch silently stops running CI before its merge, and ruling 1's
#     "green before the merge" becomes a claim about a run that never
#     happened;
#   - deleting dependabot's `docker` entry -- the digest pins under deploy/
#     stop moving for ever, which deploy/Dockerfile's own FROM comment calls
#     a worse failure than a floating tag.
#
# Assertion 1: ci.yml has exactly one column-zero `permissions:` block, and
#   its only entry is `contents: read`. A job-level block does not satisfy
#   it (the job-level blocks are copies of this one for the jobs that name
#   it, not substitutes for it: a job that names none inherits the default),
#   and anything else granted at the top fails it. A job that genuinely
#   needs more -- Task 10's image job needs `packages: write` for ghcr --
#   takes it at the job, where the exception is visible at the job taking it.
# Assertion 2: `on.push.branches` lists `main`, `desk/**` and `final/**`,
#   one `- <pattern>` per line. Further patterns are allowed.
# Assertion 3: dependabot.yml is `version: 2` and carries a `github-actions`
#   entry at directory "/" and a `docker` entry at directory "/deploy", each
#   with `interval: "weekly"`. Further entries are allowed -- the compute
#   program expects a `docker` entry at "/quant" -- so this does not forbid
#   one.
#
# The YAML reader below covers the subset these two files use: block
# mappings, block sequences, scalars, comments. It tracks indentation to
# give every line a key path, so `permissions:` at column zero and a job's
# `permissions:` are different paths, and `pull_request: branches:` is never
# mistaken for `push: branches:`. Lines inside a block scalar (`run: |`)
# are walked too, harmlessly: nothing inside one can sit at the paths this
# test reads, because a block scalar's lines are indented past its key.

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."

ci=.github/workflows/ci.yml
dep=.github/dependabot.yml

pass=0
fail=0
ok() { printf '  ok    %s\n' "$1"; pass=$((pass + 1)); }
no() { printf '  FAIL  %s -- %s\n' "$1" "$2"; fail=$((fail + 1)); }
finish() {
	printf '\n  %d passed, %d failed\n\n' "$pass" "$fail"
	[ "$fail" -eq 0 ] || exit 1
}

for f in "$ci" "$dep"; do
	if [ ! -f "$f" ]; then
		no "$f" "missing -- expected at exactly this path; if it moved, this test's paths move with it"
	fi
done
# Nothing below can be read without both files.
[ "$fail" -eq 0 ] || finish

# walk FILE: one tab-separated record per line that carries structure.
#   KEY   ITEM PATH LINE VALUE   a `key:` or `key: value` line; VALUE is what
#                                follows the colon, unquoted, or empty
#   LIST  ITEM PATH LINE VALUE   a `- value` line whose parent key is PATH
#   ITEM  ITEM PATH LINE         every `- ` line, whatever it holds
# PATH is the slash-joined chain of ancestor keys (`on/push/branches`); a
# column-zero key's PATH is the key alone. ITEM counts `- ` lines from the
# top of the file, so every KEY of one sequence entry carries that entry's
# number. Trailing comments are dropped; comment-only and blank lines are
# skipped; a `- key: value` entry pushes its key two columns in, where YAML
# places it.
walk() {
	awk -v sq="'" '
		function trim(s) { sub(/^[ \t]+/, "", s); sub(/[ \t]+$/, "", s); return s }
		function unquote(s,   n) {
			s = trim(s); n = length(s)
			if (n >= 2 && substr(s, 1, 1) == "\"" && substr(s, n, 1) == "\"") return substr(s, 2, n - 2)
			if (n >= 2 && substr(s, 1, 1) == sq && substr(s, n, 1) == sq) return substr(s, 2, n - 2)
			return s
		}
		function pop_from(ind) { while (depth > 0 && indents[depth] >= ind) depth-- }
		function push(ind, key) { depth++; indents[depth] = ind; keys[depth] = key }
		function path(   i, p) {
			p = ""
			for (i = 1; i <= depth; i++) p = (p == "" ? keys[i] : p "/" keys[i])
			return p
		}
		function keyof(s) { sub(/:.*$/, "", s); return unquote(s) }
		function valof(s) { sub(/^[^:]*:[ \t]*/, "", s); return unquote(s) }
		BEGIN { depth = 0; item = 0 }
		{
			line = $0
			sub(/\r$/, "", line)
			if (line ~ /^[ \t]*(#|$)/) next
			sub(/[ \t]+#.*$/, "", line)
			match(line, /^[ \t]*/); ind = RLENGTH
			body = substr(line, ind + 1)
			if (body == "---" || body == "...") next
			if (body ~ /^-([ \t]|$)/) {
				rest = trim(substr(body, 2))
				pop_from(ind + 1)
				p = path()
				item++
				print "ITEM\t" item "\t" p "\t" NR
				if (rest ~ /^[^:]+:([ \t]|$)/) {
					push(ind + 2, keyof(rest))
					print "KEY\t" item "\t" path() "\t" NR "\t" valof(rest)
				} else {
					print "LIST\t" item "\t" p "\t" NR "\t" unquote(rest)
				}
				next
			}
			if (body ~ /^[^:]+:([ \t]|$)/) {
				pop_from(ind)
				push(ind, keyof(body))
				print "KEY\t" item "\t" path() "\t" NR "\t" valof(body)
			}
		}
	' "$1"
}

# --- 1. The top-level token scope: `permissions:` at column zero, and only
# `contents: read` under it. --------------------------------------------
ci_walk=$(walk "$ci")

top_count=0
top_line=""
top_val=""
entries="" # LINE<TAB>KEY<TAB>VALUE per entry under the top-level block
job_blocks=""
while IFS=$'\t' read -r kind _ p line val; do
	[ -n "$kind" ] || continue
	case "$kind:$p" in
	KEY:permissions)
		top_count=$((top_count + 1))
		top_line=$line
		top_val=$val
		;;
	KEY:permissions/*)
		entries="${entries}${line}"$'\t'"${p#permissions/}"$'\t'"${val}"$'\n'
		;;
	LIST:permissions | ITEM:permissions)
		# A sequence under permissions: is not a grant of anything.
		entries="${entries}${line}"$'\t'"- ${val}"$'\t'""$'\n'
		;;
	KEY:*/permissions)
		job_blocks="${job_blocks:+$job_blocks, }$line"
		;;
	esac
done <<<"$ci_walk"

if [ "$top_count" -eq 0 ]; then
	no "$ci: top-level permissions" "no column-zero 'permissions:' block -- expected 'permissions:' followed by '  contents: read', above 'jobs:'${job_blocks:+; the job-level block(s) at line(s) $job_blocks do not satisfy this, because a job that names none inherits the repository default}"
elif [ "$top_count" -gt 1 ]; then
	no "$ci:$top_line: top-level permissions" "$top_count column-zero 'permissions:' keys -- expected exactly one"
elif [ -n "$top_val" ]; then
	no "$ci:$top_line: 'permissions: $top_val'" "expected a block whose only entry is 'contents: read', not an inline value"
elif [ -z "$entries" ]; then
	no "$ci:$top_line: 'permissions:'" "the block is empty -- expected exactly one entry, 'contents: read'"
else
	bad=0
	while IFS=$'\t' read -r line key val; do
		[ -n "$line" ] || continue
		if [ "$key" != contents ] || [ "$val" != read ]; then
			no "$ci:$line: '$key: $val'" "the top-level block grants exactly 'contents: read' and nothing else; a job that needs more (Task 10's image job, 'packages: write') takes it at the job, where the exception is visible"
			bad=1
		fi
	done <<<"$entries"
	[ "$bad" -eq 1 ] || ok "$ci:$top_line: the top-level 'permissions:' block is exactly 'contents: read'"
fi

# --- 2. The push triggers: on.push.branches lists the three. -----------
branches_line=""
branches="" # one pattern per line, unquoted
while IFS=$'\t' read -r kind _ p line val; do
	[ -n "$kind" ] || continue
	case "$kind:$p" in
	KEY:on/push/branches) branches_line=$line ;;
	LIST:on/push/branches) branches="${branches}${val}"$'\n' ;;
	esac
done <<<"$ci_walk"

if [ -z "$branches_line" ]; then
	no "$ci: on.push.branches" "no 'branches:' list under 'on:' -> 'push:' -- expected one '- <pattern>' line each for main, desk/** and final/**"
else
	for want in main 'desk/**' 'final/**'; do
		if grep -qxF -- "$want" <<<"$branches"; then
			ok "$ci:$branches_line: on.push.branches lists $want"
		else
			no "$ci:$branches_line: on.push.branches" "does not list '$want' (it lists:$(awk 'NF { printf " %s", $0 }' <<<"$branches")) -- a branch that is not listed here never runs CI before its merge"
		fi
	done
fi

# --- 3. Dependabot: version 2, github-actions at "/", docker at "/deploy",
# both weekly. ------------------------------------------------------------
dep_walk=$(walk "$dep")

version_line=""
version_val=""
items="" # LINE<TAB>ECOSYSTEM<TAB>DIRECTORY<TAB>INTERVAL per updates entry
in_item=0
cur_line=""
cur_eco=""
cur_dir=""
cur_intv=""
flush_item() {
	if [ "$in_item" -eq 1 ]; then
		items="${items}${cur_line}"$'\t'"${cur_eco}"$'\t'"${cur_dir}"$'\t'"${cur_intv}"$'\n'
	fi
}
while IFS=$'\t' read -r kind _ p line val; do
	[ -n "$kind" ] || continue
	case "$kind:$p" in
	KEY:version)
		version_line=$line
		version_val=$val
		;;
	ITEM:updates)
		flush_item
		in_item=1
		cur_line=$line
		cur_eco=""
		cur_dir=""
		cur_intv=""
		;;
	KEY:updates/package-ecosystem) cur_eco=$val ;;
	KEY:updates/directory) cur_dir=$val ;;
	KEY:updates/schedule/interval) cur_intv=$val ;;
	esac
done <<<"$dep_walk"
flush_item

if [ -z "$version_line" ]; then
	no "$dep: version" "no top-level 'version:' -- expected 'version: 2'"
elif [ "$version_val" != 2 ]; then
	no "$dep:$version_line: 'version: $version_val'" "expected 'version: 2'"
else
	ok "$dep:$version_line: version 2"
fi

# want_entry ECOSYSTEM DIRECTORY: that entry exists and is weekly.
want_entry() {
	local eco="$1" dir="$2" line e d i hit=0
	while IFS=$'\t' read -r line e d i; do
		[ -n "$line" ] || continue
		if [ "$e" = "$eco" ] && [ "$d" = "$dir" ]; then
			hit=1
			if [ "$i" = weekly ]; then
				ok "$dep:$line: $eco at \"$dir\", weekly"
			else
				no "$dep:$line: $eco at \"$dir\"" "interval is '${i:-unset}' -- expected \"weekly\"; a bump that arrives every morning is a bump nobody reads"
			fi
		fi
	done <<<"$items"
	if [ "$hit" -eq 0 ]; then
		no "$dep: updates" "no 'package-ecosystem: \"$eco\"' entry at 'directory: \"$dir\"' (entries found:$(awk -F '\t' 'NF { printf " %s@%s", $2, $3 }' <<<"$items")) -- expected one, with 'interval: \"weekly\"'"
	fi
}
want_entry github-actions /
want_entry docker /deploy

finish
