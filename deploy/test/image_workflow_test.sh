#!/usr/bin/env bash
#
# Pins, as text, the workflow that builds and publishes the images the
# droplet pulls:
#
#   .github/workflows/image.yml   -- who may trigger it, what it may write,
#                                    which images it builds from which
#                                    Dockerfile, and the one gate a push to
#                                    ghcr.io sits behind
#   deploy/Dockerfile, deploy/research.Dockerfile, quant/Dockerfile
#                                 -- that the stage which SHIPS declares the
#                                    build stamp the workflow asserts
#
#   deploy/test/image_workflow_test.sh
#
# Exits non-zero, naming the file and the line, if any assertion below fails.
# grep and awk only, no yq and no python; runs from any directory; picked up
# by the lint job's deploy/test/*.sh step with no change to ci.yml.
#
# Why this exists. The image workflow is the only file in the repository that
# holds a write scope, and the only one whose failure mode is a wrong image
# on a public registry under this project's name. A green run does not report
# what was NOT gated: each of the following edits leaves actionlint, shellcheck
# and every other check green, and each is refused here --
#   - `packages: write` moved to the top of the file, or onto the job that
#     runs the harness, so code from a branch runs beside a token that can
#     publish;
#   - the publish job's condition losing `head_branch == 'main'`, or its
#     `conclusion == 'success'`, so a red main or a stage branch publishes;
#   - `main` added to `on.push.branches`, so an image is built and smoked for
#     main BEFORE ci has passed on it, and the run people look at is not the
#     one that published;
#   - an image name built from `github.repository`, which is
#     `ajaiupadhyaya/OhCamel` and is not a valid image reference;
#   - a matrix entry pointed at another Dockerfile, or dropped;
#   - the harness brought up on the local override, whose build blocks would
#     rebuild the images instead of running the ones that were loaded;
#   - the ENV that carries the stamp removed from a Dockerfile's last stage,
#     where a build still succeeds and only a `docker run` would say so.
#
# The assertions, in the order they run:
#   1. The top-level `permissions:` block is exactly `contents: read`.
#   2. `packages: write` is granted at exactly one job, `publish`, whose block
#      is exactly `contents: read` + `packages: write`; nothing else in the
#      file is granted `write`.
#   3. The triggers: `workflow_run` of `ci`, `completed`, on `main`;
#      `push` to `desk/**` and `final/**` and NOT to `main`; `pull_request`;
#      `workflow_dispatch`.
#   4. The sha every job builds, checks out and stamps is the triggering
#      run's `head_sha` when there is one, and every checkout names it.
#   5. The build matrix holds the three images, each on its own Dockerfile,
#      in lowercase, each with a `stamp` of env, label or none. Further
#      entries are allowed -- ohcamel-hostd (compute task 0.4) lands as a
#      fourth.
#   6. Every ghcr.io reference is under the literal, lowercase
#      `ghcr.io/ajaiupadhyaya/`, and none is built from `github.repository`.
#   7. The build: linux/amd64, loaded and never pushed, both build args, both
#      OCI labels, the gha cache at mode=max.
#   8. The stamp step reads the revision label and `printenv
#      OHCAMEL_GIT_SHA`.
#   9. The harness: base + the CI override and never the local one,
#      `--no-build`, smoke.sh on :8000 with --engine :8001 and --expect-sha,
#      and a teardown that runs whatever happened.
#  10. `docker login` and `docker push` appear in the publish job and nowhere
#      else.
#  11. The publish job's gate: all five conjuncts, no disjunction, and it
#      needs both the build and the smoke.
#  12. The publish job writes the digests to the job summary.
#  13. The Dockerfiles: each matrix Dockerfile's last stage declares exactly
#      what its entry's `stamp` says -- env (ARG + ENV + the revision LABEL),
#      label (ARG + LABEL, no ENV) or none (no ARG at all) -- in both
#      directions, so the stamp step asserts everything an image can answer
#      and nothing it cannot.
#  14. Every image the matrix builds is named in the publish job's push.
#
# The YAML reader is deploy/test/ci_hygiene_test.sh's, unchanged: block
# mappings, block sequences, scalars, comments, with every line given its key
# path. image.yml is written in that subset on purpose -- lists as `- item`
# lines, never `[a, b]` -- so that this test can read it.

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."

wf=.github/workflows/image.yml

pass=0
fail=0
ok() { printf '  ok    %s\n' "$1"; pass=$((pass + 1)); }
no() { printf '  FAIL  %s -- %s\n' "$1" "$2"; fail=$((fail + 1)); }
finish() {
	printf '\n  %d passed, %d failed\n\n' "$pass" "$fail"
	[ "$fail" -eq 0 ] || exit 1
	exit 0
}

if [ ! -f "$wf" ]; then
	no "$wf" "missing -- the image workflow is expected at exactly this path; deploy/docker-compose.yml and deploy/docker-compose.ci.yml both name it"
	finish
fi

# walk FILE: one tab-separated record per line that carries structure.
#   KEY   ITEM PATH LINE VALUE   a `key:` or `key: value` line; VALUE is what
#                                follows the colon, unquoted, or empty
#   LIST  ITEM PATH LINE VALUE   a `- value` line whose parent key is PATH
#   ITEM  ITEM PATH LINE         every `- ` line, whatever it holds
# PATH is the slash-joined chain of ancestor keys (`on/push/branches`); ITEM
# counts `- ` lines from the top of the file, so every KEY of one sequence
# entry carries that entry's number.
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

# code FILE: "LINE<TAB>text" for every line that is not a comment, with a
# trailing ` # comment` dropped. What the greps below read, so that a comment
# which MENTIONS `docker push` is never mistaken for a step that runs it.
code() {
	awk '
		/^[ \t]*(#|$)/ { next }
		{ line = $0; sub(/[ \t]+#.*$/, "", line); printf "%d\t%s\n", NR, line }
	' "$1"
}

# job NAME: the `code` lines of jobs.NAME -- from `  NAME:` to the next key at
# the same two-column indent, or the end of the file.
job() {
	code "$wf" | awk -F '\t' -v name="$1" '
		$2 ~ /^[^ ]/ { injobs = ($2 ~ /^jobs:/); inside = 0; next }
		injobs && $2 ~ /^  [A-Za-z0-9_-]+:/ {
			key = $2; sub(/^  /, "", key); sub(/:.*$/, "", key)
			inside = (key == name)
		}
		inside { print }
	'
}

# value_of JOB KEY: the text of jobs.JOB.KEY as one line -- what follows the
# colon, and every deeper-indented line under it (a folded `>-` scalar, or a
# list), joined with single spaces.
value_of() {
	job "$1" | awk -F '\t' -v key="$2" '
		{
			text = $2
			if (text ~ ("^    " key ":")) {
				inside = 1
				sub(/^[^:]*:[ \t]*/, "", text)
				if (text !~ /^[>|][+-]?$/) out = text
				next
			}
			if (inside && text ~ /^     /) {
				sub(/^[ \t]+/, "", text)
				out = (out == "" ? text : out " " text)
				next
			}
			inside = 0
		}
		END { print out }
	'
}

wf_walk=$(walk "$wf")
wf_code=$(code "$wf")

# has_list PATH VALUE: a `- VALUE` line sits under PATH.
has_list() {
	awk -F '\t' -v p="$1" -v v="$2" '$1 == "LIST" && $3 == p && $5 == v { hit = 1 } END { exit hit ? 0 : 1 }' <<<"$wf_walk"
}
# has_key PATH: a key with exactly this path exists.
has_key() {
	awk -F '\t' -v p="$1" '$1 == "KEY" && $3 == p { hit = 1 } END { exit hit ? 0 : 1 }' <<<"$wf_walk"
}
# line_of PATH: the line of the first key with this path, or "?".
line_of() {
	awk -F '\t' -v p="$1" '$1 == "KEY" && $3 == p { print $4; hit = 1; exit } END { if (!hit) print "?" }' <<<"$wf_walk"
}

# --- 1. The top-level token scope. ---------------------------------------
top_count=$(awk -F '\t' '$1 == "KEY" && $3 == "permissions"' <<<"$wf_walk" | wc -l | tr -d ' ')
top_inline=$(awk -F '\t' '$1 == "KEY" && $3 == "permissions" { print $5 }' <<<"$wf_walk")
top_entries=$(awk -F '\t' '$1 == "KEY" && $3 ~ /^permissions\// { k = $3; sub(/^permissions\//, "", k); print k ": " $5 }' <<<"$wf_walk")
if [ "$top_count" -ne 1 ]; then
	no "$wf: top-level permissions" "$top_count column-zero 'permissions:' keys -- expected exactly one, 'permissions:' followed by '  contents: read'; a job that names no block inherits whatever the repository's default is"
elif [ -n "$top_inline" ]; then
	no "$wf:$(line_of permissions): 'permissions: $top_inline'" "expected a block whose only entry is 'contents: read', not an inline value"
elif [ "$top_entries" != "contents: read" ]; then
	no "$wf:$(line_of permissions): top-level permissions" "the block reads '$(tr '\n' ',' <<<"$top_entries" | sed 's/,$//')' -- expected exactly 'contents: read'; the one job that pushes takes 'packages: write' itself"
else
	ok "$wf:$(line_of permissions): the top-level 'permissions:' block is exactly 'contents: read'"
fi

# --- 2. The one write scope, and where it sits. ---------------------------
writes=$(awk -F '\t' '$1 == "KEY" && $5 ~ /write/ { print $3 "\t" $4 "\t" $5 }' <<<"$wf_walk")
inline_job_perms=$(awk -F '\t' '$1 == "KEY" && $3 ~ /^jobs\/[^\/]+\/permissions$/ && $5 != "" { print $3 ":" $4 ": " $5 }' <<<"$wf_walk")
if [ -n "$inline_job_perms" ]; then
	no "$wf: job permissions" "an inline value ($inline_job_perms) -- expected a block, so that what a job may write is listed key by key"
fi
if [ "$(awk -F '\t' '{ print $1 }' <<<"$writes")" = "jobs/publish/permissions/packages" ] &&
	[ "$(awk -F '\t' '{ print $3 }' <<<"$writes")" = "write" ]; then
	ok "$wf:$(awk -F '\t' '{ print $2 }' <<<"$writes"): 'packages: write' is granted once, at jobs.publish"
else
	no "$wf: write scopes" "found:$(awk -F '\t' 'NF { printf " %s=%s (line %s)", $1, $3, $2 }' <<<"$writes") -- expected exactly one, jobs/publish/permissions/packages=write; the jobs that build and run branch code hold no token that can publish"
fi
publish_perms=$(awk -F '\t' '$1 == "KEY" && $3 ~ /^jobs\/publish\/permissions\// { k = $3; sub(/^.*\//, "", k); print k ": " $5 }' <<<"$wf_walk" | sort | tr '\n' ',' | sed 's/,$//')
if [ "$publish_perms" = "contents: read,packages: write" ]; then
	ok "$wf:$(line_of jobs/publish/permissions): jobs.publish is granted exactly contents: read + packages: write"
else
	no "$wf: jobs.publish.permissions" "reads '$publish_perms' -- expected exactly 'contents: read' and 'packages: write'; GITHUB_TOKEN with that scope is the only credential this workflow uses"
fi

# --- 3. The triggers. ------------------------------------------------------
want_list() {
	if has_list "$1" "$2"; then
		ok "$wf:$(line_of "$1"): ${1//\//.} lists $2"
	else
		no "$wf: ${1//\//.}" "does not list '$2' -- $3"
	fi
}
want_list on/workflow_run/workflows ci "the publish run is the one that FOLLOWS a finished ci run; without this trigger nothing publishes"
want_list on/workflow_run/types completed "a 'requested' run has no conclusion to gate on"
want_list on/workflow_run/branches main "only main's ci run may lead to a publish"
want_list on/push/branches 'desk/**' "a stage branch must build and smoke its images before its merge"
want_list on/push/branches 'final/**' "a stage branch must build and smoke its images before its merge"
if has_list on/push/branches main; then
	no "$wf:$(line_of on/push/branches): on.push.branches" "lists 'main' -- main is built by the workflow_run trigger, AFTER ci is green on it; a push trigger would build it before, twice, and put a second run beside the one that publishes"
else
	ok "$wf:$(line_of on/push/branches): on.push.branches does not list main (workflow_run builds main, after ci)"
fi
for t in pull_request workflow_dispatch; do
	if has_key "on/$t"; then
		ok "$wf:$(line_of "on/$t"): on.$t"
	else
		no "$wf: on.$t" "missing -- expected the key, with nothing under it"
	fi
done

# --- 4. One sha, and it is the triggering run's. ---------------------------
sha_val=$(awk -F '\t' '$1 == "KEY" && $3 == "env/SHA" { print $5 }' <<<"$wf_walk")
case "$sha_val" in
*"github.event.workflow_run.head_sha || github.sha"*)
	ok "$wf:$(line_of env/SHA): env.SHA is the triggering run's head_sha, else github.sha"
	;;
*)
	no "$wf: env.SHA" "reads '${sha_val:-unset}' -- expected '\${{ github.event.workflow_run.head_sha || github.sha }}': on workflow_run, github.sha is the default branch's tip when the trigger fired, which need not be the commit ci passed on"
	;;
esac
checkouts=$(awk -F '\t' '$1 == "KEY" && $3 ~ /\/steps\/uses$/ && $5 ~ /^actions\/checkout@/ { print $2 "\t" $4 }' <<<"$wf_walk")
if [ -z "$checkouts" ]; then
	no "$wf: checkout" "no actions/checkout step found -- the build and the harness both read the tree"
else
	while IFS=$'\t' read -r item line; do
		ref=$(awk -F '\t' -v it="$item" '$1 == "KEY" && $2 == it && $3 ~ /\/steps\/with\/ref$/ { print $5 }' <<<"$wf_walk")
		case "$ref" in
		*"env.SHA"*) ok "$wf:$line: checkout at env.SHA" ;;
		*) no "$wf:$line: checkout" "its 'with: ref:' reads '${ref:-unset}' -- expected '\${{ env.SHA }}'; a default checkout on workflow_run reads the default branch's tip and the image would be stamped with one sha and built from another" ;;
		esac
	done <<<"$checkouts"
fi

# --- 5. The matrix: three images, each on its own Dockerfile. --------------
# Each entry carries `stamp`, what its Dockerfile's LAST stage declares:
#   env    ARG OHCAMEL_GIT_SHA, ENV OHCAMEL_GIT_SHA=$OHCAMEL_GIT_SHA and the
#          revision LABEL -- the stamp step runs printenv as well
#   label  ARG and the revision LABEL, no ENV
#   none   no ARG at all (no entry today; research.Dockerfile was one until Task 11)
# Assertion 13 holds each entry exactly equal to its Dockerfile, both ways.
matrix=$(awk -F '\t' '
	$1 == "KEY" && $3 == "jobs/build/strategy/matrix/include/image" { image[$2] = $5; line[$2] = $4; order[++n] = $2 }
	$1 == "KEY" && $3 == "jobs/build/strategy/matrix/include/dockerfile" { file[$2] = $5 }
	$1 == "KEY" && $3 == "jobs/build/strategy/matrix/include/stamp" { stamp[$2] = $5 }
	END { for (i = 1; i <= n; i++) print line[order[i]] "\t" image[order[i]] "\t" file[order[i]] "\t" stamp[order[i]] }
' <<<"$wf_walk")
want_image() {
	local image="$1" file="$2" line i f s hit=0
	while IFS=$'\t' read -r line i f s; do
		[ -n "$line" ] || continue
		if [ "$i" = "$image" ]; then
			hit=1
			if [ "$f" = "$file" ]; then
				ok "$wf:$line: $image is built from $file"
			else
				no "$wf:$line: $image" "is built from '${f:-unset}' -- expected $file"
			fi
		fi
	done <<<"$matrix"
	if [ "$hit" -eq 0 ]; then
		no "$wf: jobs.build.strategy.matrix.include" "no entry with 'image: $image' (entries:$(awk -F '\t' 'NF { printf " %s", $2 }' <<<"$matrix")) -- deploy/docker-compose.yml pulls it, so it must be built and published here"
	fi
}
want_image ohcamel deploy/Dockerfile
want_image ohcamel-research deploy/research.Dockerfile
want_image ohcamel-quant quant/Dockerfile
while IFS=$'\t' read -r line i f s; do
	[ -n "$line" ] || continue
	case "$i" in
	*[!a-z0-9-]* | "") no "$wf:$line: image '$i'" "an image name is lowercase letters, digits and hyphens; ghcr.io refuses anything else" ;;
	esac
	case "$s" in
	env | label | none) ;;
	*) no "$wf:$line: image '$i'" "stamp is '${s:-unset}' -- expected env, label or none: what the Dockerfile's last stage declares (ENV + LABEL, LABEL only, or no ARG at all)" ;;
	esac
	if [ ! -f "$f" ]; then
		no "$wf:$line: image '$i'" "its dockerfile '$f' does not exist"
	fi
done <<<"$matrix"

# --- 6. Every registry reference is the literal lowercase namespace. -------
refs=$(grep -nE 'ghcr\.io' <<<"$wf_code" | sed 's/^[0-9]*://' || true)
if [ -z "$refs" ]; then
	no "$wf: image references" "no ghcr.io reference found -- the images are named ghcr.io/ajaiupadhyaya/<name>:<sha>"
else
	bad=0
	while IFS=$'\t' read -r line text; do
		[ -n "$line" ] || continue
		# Every ghcr.io on the line, not only the first.
		rest=$text
		while [ "${rest#*ghcr.io}" != "$rest" ]; do
			rest=${rest#*ghcr.io}
			case "$rest" in
			/ajaiupadhyaya/* | " -u "* | '"'* | "'"*) ;;
			"") ;;
			*)
				no "$wf:$line: '$(sed 's/^[ \t]*//' <<<"$text")'" "a ghcr.io reference that is not under the literal 'ghcr.io/ajaiupadhyaya/' -- github.repository is 'ajaiupadhyaya/OhCamel', which is not a valid image name, so the namespace is written out in lowercase"
				bad=1
				;;
			esac
		done
		case "$text" in
		*github.repository*)
			no "$wf:$line: '$(sed 's/^[ \t]*//' <<<"$text")'" "builds an image reference from github.repository ('ajaiupadhyaya/OhCamel': uppercase, refused by the registry) -- write the name out"
			bad=1
			;;
		esac
	done <<<"$refs"
	[ "$bad" -eq 1 ] || ok "$wf: every ghcr.io reference is under the literal ghcr.io/ajaiupadhyaya/ ($(wc -l <<<"$refs" | tr -d ' ') lines)"
fi

# --- 7. The build. ---------------------------------------------------------
build=$(job build)
want_in() { # want_in BLOCKNAME BLOCK FIXED-STRING WHY
	local hit
	hit=$(grep -F -- "$3" <<<"$2" | head -n 1 || true)
	if [ -n "$hit" ]; then
		ok "$wf:${hit%%$'\t'*}: jobs.$1 has '$3'"
	else
		no "$wf: jobs.$1" "no '$3' -- $4"
	fi
}
if [ -z "$build" ]; then
	no "$wf: jobs.build" "no such job -- expected the matrix job that builds the images"
else
	want_in build "$build" "platforms: linux/amd64" "the droplet is amd64; an image built for the runner's own platform by default is right only by luck"
	want_in build "$build" "load: true" "the stamp step and the harness run the image from the runner's own daemon"
	want_in build "$build" "push: false" "the build never pushes; jobs.publish does, after the smoke, on a green main"
	want_in build "$build" "OHCAMEL_GIT_SHA=" "the build arg the binary, the ENV and the revision label are all stamped from"
	want_in build "$build" "OHCAMEL_BUILT_AT=" "the build arg /api/ops reports as built_at"
	want_in build "$build" "org.opencontainers.image.source=https://github.com/ajaiupadhyaya/OhCamel" "the label that ties the package to this repository on ghcr.io"
	want_in build "$build" "org.opencontainers.image.revision=" "the label deploy/smoke.sh --expect-sha reads"
	want_in build "$build" "cache-from: type=gha" "without the cache every run pays the twenty-minute opam layer"
	want_in build "$build" "mode=max" "mode=min caches only the final stage, and the opam layer is in the builder stage"
fi
if grep -qE $'\t'' *push: *true' <<<"$wf_code"; then
	no "$wf:$(grep -E $'\t'' *push: *true' <<<"$wf_code" | head -n 1 | cut -f 1): 'push: true'" "a build step that pushes -- the only push is jobs.publish's, of the images the smoke ran"
else
	ok "$wf: no build step has 'push: true'"
fi

# --- 8. The stamp step. ----------------------------------------------------
if [ -n "$build" ]; then
	want_in build "$build" 'index .Config.Labels "org.opencontainers.image.revision"' "the stamp step reads the label from the image that was loaded"
	want_in build "$build" "--entrypoint printenv" "the engine's entrypoint is the binary, so a bare 'docker run <image> printenv' runs 'ohcamel printenv' and prints the usage"
	want_in build "$build" "OHCAMEL_GIT_SHA" "the variable the stamp step reads"
fi

# --- 9. The harness. -------------------------------------------------------
smoke=$(job smoke)
if [ -z "$smoke" ]; then
	no "$wf: jobs.smoke" "no such job -- expected the job that brings the harness up on the loaded images"
else
	want_in smoke "$smoke" "-f deploy/docker-compose.yml -f deploy/docker-compose.ci.yml" "the harness is the base file plus the image job's own override"
	want_in smoke "$smoke" "--no-build" "the harness runs the images that were loaded; it builds nothing"
	want_in smoke "$smoke" "cp book.example.sexp book.sexp" "book.sexp is gitignored and the runner has none; both engines bind-mount ../book.sexp, and Docker turns a missing bind source into a directory, which fails the demo container with 'not a directory' (seen on the first local harness run of this job's steps)"
	want_in smoke "$smoke" "deploy/smoke.sh http://localhost:8000" "the suite, against the Quant site on the harness's port"
	want_in smoke "$smoke" "--engine http://localhost:8001" "the engine suite, against the demo engine"
	want_in smoke "$smoke" '--expect-sha "$SHA"' "without it the suite passes against any image that answers"
	want_in smoke "$smoke" "down -v" "the harness is torn down, volumes included"
	if grep -qF "docker-compose.local.yml" <<<"$smoke"; then
		no "$wf:$(grep -F "docker-compose.local.yml" <<<"$smoke" | head -n 1 | cut -f 1): jobs.smoke" "names deploy/docker-compose.local.yml -- that override carries the build blocks, and a harness that can rebuild may test something other than the image it loaded"
	else
		ok "$wf: jobs.smoke never names the local override"
	fi
	# The teardown runs whatever the steps before it did: the step that holds
	# `down -v` is an `if: always()` step.
	down_item=$(awk -F '\t' '$1 == "KEY" && $3 == "jobs/smoke/steps/if" && $5 == "always()" { print $2 }' <<<"$wf_walk")
	down_ok=0
	for it in $down_item; do
		first=$(awk -F '\t' -v it="$it" '$1 == "ITEM" && $2 == it { print $4 }' <<<"$wf_walk")
		next_item=$(awk -F '\t' -v it="$it" '$1 == "ITEM" && $2 > it && $3 == "jobs/smoke/steps" { print $4; exit }' <<<"$wf_walk")
		if awk -F '\t' -v a="$first" -v b="${next_item:-999999}" '$1 >= a && $1 < b' <<<"$smoke" | grep -qF "down -v"; then
			down_ok=1
		fi
	done
	if [ "$down_ok" -eq 1 ]; then
		ok "$wf: jobs.smoke tears the harness down in an 'if: always()' step"
	else
		no "$wf: jobs.smoke" "'down -v' is not inside a step with 'if: always()' -- a failed smoke would leave the harness up, and the step that prints its logs would never be followed by a teardown"
	fi
fi

# --- 10. Login and push, in the publish job and nowhere else. --------------
publish=$(job publish)
if [ -z "$publish" ]; then
	no "$wf: jobs.publish" "no such job -- expected the one job that logs in to ghcr.io and pushes"
else
	first=$(head -n 1 <<<"$publish" | cut -f 1)
	last=$(tail -n 1 <<<"$publish" | cut -f 1)
	for verb in "docker login" "docker push"; do
		hits=$(grep -F -- "$verb" <<<"$wf_code" | cut -f 1 || true)
		if [ -z "$hits" ]; then
			no "$wf: '$verb'" "not found -- jobs.publish logs in with GITHUB_TOKEN and pushes :<sha> and :main"
			continue
		fi
		outside=""
		for l in $hits; do
			if [ "$l" -lt "$first" ] || [ "$l" -gt "$last" ]; then outside="$outside $l"; fi
		done
		if [ -n "$outside" ]; then
			no "$wf:${outside# }: '$verb'" "outside jobs.publish (lines $first-$last) -- nothing but that job may reach the registry"
		else
			ok "$wf: every '$verb' is inside jobs.publish (lines $first-$last)"
		fi
	done
	want_in publish "$publish" 'secrets.GITHUB_TOKEN' "the job's own token is the only credential; no new secret"
	want_in publish "$publish" '--password-stdin' "a token on the command line is a token in the process list"
	want_in publish "$publish" ':main' "the moving tag, beside the immutable :<sha>"

	# --- 11. The gate. ------------------------------------------------------
	gate=$(value_of publish if)
	for want in \
		"github.event_name == 'workflow_run'" \
		"github.event.workflow_run.conclusion == 'success'" \
		"github.event.workflow_run.event == 'push'" \
		"github.event.workflow_run.head_branch == 'main'" \
		"github.event.workflow_run.head_repository.full_name == github.repository"; do
		case "$gate" in
		*"$want"*) ok "$wf: jobs.publish.if requires $want" ;;
		*) no "$wf: jobs.publish.if" "does not require \"$want\" (it reads: ${gate:-nothing}) -- only a green ci run of a push to this repository's main may publish" ;;
		esac
	done
	case "$gate" in
	*"||"*) no "$wf: jobs.publish.if" "contains '||' (it reads: $gate) -- the gate is a conjunction; a disjunct is a second way in" ;;
	*) ok "$wf: jobs.publish.if is a conjunction (no '||')" ;;
	esac
	needs=$(value_of publish needs)
	for want in build smoke; do
		case " $(tr -d -- '-[],' <<<"$needs") " in
		*" $want "*) ok "$wf: jobs.publish needs $want" ;;
		*) no "$wf: jobs.publish.needs" "does not name '$want' (it reads: ${needs:-nothing}) -- an image is published only after it was built here and the harness smoke passed on it" ;;
		esac
	done

	# --- 12. The summary. ---------------------------------------------------
	want_in publish "$publish" 'GITHUB_STEP_SUMMARY' "the run's summary page lists what was published"
	want_in publish "$publish" 'RepoDigests' "the digest the registry assigned, read back from the daemon after the push"
fi

# --- 13. The Dockerfiles declare what the workflow asserts. ----------------
# last_stage FILE: the instructions after the file's last FROM, comments and
# heredoc bodies dropped, continuation lines joined.
last_stage() {
	awk '
		/^[ \t]*#/ { next }
		inheredoc { t = $0; sub(/^[ \t]+/, "", t); if (t == delim) inheredoc = 0; next }
		{
			for (k = 1; k <= NF; k++)
				if ($k ~ /^<<-?[\047"]?[A-Za-z0-9_]+[\047"]?$/) {
					delim = $k
					sub(/^<<-?[\047"]?/, "", delim); sub(/[\047"]$/, "", delim)
					inheredoc = 1
				}
		}
		toupper($1) == "FROM" { n = 0; held = ""; next }
		{
			line = held $0
			if (line ~ /\\[ \t]*$/) { sub(/\\[ \t]*$/, " ", line); held = line; next }
			held = ""
			out[++n] = line
		}
		END { for (i = 1; i <= n; i++) print out[i] }
	' "$1"
}
# What the last stage declares, read from the file: `env`, `label` or `none`,
# in the matrix's own vocabulary. The three facts are per stage on purpose --
# an ARG or an ENV set in the builder never reaches the image that ships
# (deploy/Dockerfile's builder has both and its runtime stage needed its own),
# and deploy.sh's --build fallback passes no --label, so the LABEL in the
# Dockerfile is the only revision an image built on the droplet carries.
declared_stamp() {
	local stage="$1" has_arg=0 has_label=0 has_env=0
	grep -qE '^[ \t]*ARG[ \t]+OHCAMEL_GIT_SHA' <<<"$stage" && has_arg=1
	grep -E '^[ \t]*LABEL[ \t]' <<<"$stage" | grep -qE 'org\.opencontainers\.image\.revision="?\$\{?OHCAMEL_GIT_SHA' && has_label=1
	grep -E '^[ \t]*ENV[ \t]' <<<"$stage" | grep -qE 'OHCAMEL_GIT_SHA="?\$\{?OHCAMEL_GIT_SHA' && has_env=1
	if [ "$has_arg" -eq 1 ] && [ "$has_label" -eq 1 ] && [ "$has_env" -eq 1 ]; then
		echo env
	elif [ "$has_arg" -eq 1 ] && [ "$has_label" -eq 1 ]; then
		echo label
	elif [ "$has_arg" -eq 0 ] && [ "$has_label" -eq 0 ] && [ "$has_env" -eq 0 ]; then
		echo none
	else
		echo "partial(arg=$has_arg label=$has_label env=$has_env)"
	fi
}
while IFS=$'\t' read -r line i f s; do
	[ -n "$line" ] || continue
	[ -f "$f" ] || continue
	declared=$(declared_stamp "$(last_stage "$f")")
	if [ "$declared" = "$s" ]; then
		case "$s" in
		env) ok "$f: the last stage declares ARG + ENV OHCAMEL_GIT_SHA + the revision LABEL (stamp: env)" ;;
		label) ok "$f: the last stage declares ARG + the revision LABEL and no ENV (stamp: label)" ;;
		none) ok "$f: the last stage declares no stamp, as the matrix says (stamp: none)" ;;
		esac
	else
		case "$declared" in
		partial*)
			no "$f: last stage" "declares $declared after the last FROM -- a stamp is 'ARG OHCAMEL_GIT_SHA' with 'LABEL org.opencontainers.image.revision=\"\$OHCAMEL_GIT_SHA\"', plus 'ENV OHCAMEL_GIT_SHA=\$OHCAMEL_GIT_SHA' for stamp: env; a label without its ARG is the literal string, and an ENV without the label leaves smoke.sh --expect-sha nothing to read"
			;;
		*)
			no "$f: last stage" "declares '$declared' but $wf:$line marks '$i' stamp: ${s:-unset} -- the entry must say exactly what the file does, so that the stamp step asserts everything the image can answer and nothing it cannot: set stamp: $declared, or change the Dockerfile"
			;;
		esac
	fi
done <<<"$matrix"

# --- 14. Every image the matrix builds is one the publish job pushes. -------
# The publish job cannot read another job's matrix, so it names the images
# itself; an entry added to the matrix (ohcamel-hostd, compute task 0.4) and
# not here would be built, smoked and quietly never published.
if [ -n "$publish" ]; then
	while IFS=$'\t' read -r line i f s; do
		[ -n "$line" ] || continue
		if grep -qE "(^|[^A-Za-z0-9-])$i([^A-Za-z0-9-]|\$)" <<<"$(grep -F 'docker push' -B 8 <<<"$publish" || true)"; then
			ok "$wf: jobs.publish pushes $i"
		else
			no "$wf: jobs.publish" "does not name '$i' within the eight lines before its 'docker push' -- the matrix builds it (line $line), so it must be pushed and in the digest summary"
		fi
	done <<<"$matrix"
fi

finish
