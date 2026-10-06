#!/usr/bin/env bash
#
# The research image runs as uid 10001, owns the signals volume it writes,
# and declares its build stamp (finish plan Task 11).
#
#   deploy/test/research_image_test.sh
#
# Reads, as text:
#
#   deploy/research.Dockerfile   -- the image that writes /signals
#   deploy/Dockerfile            -- the engine, which reads the same volume
#                                   read-only at /data/signals
#   deploy/docker-compose.yml    -- the two mounts of the `signals` volume
#   .github/workflows/image.yml  -- the step that proves all of it on a real
#                                   daemon, on a fresh named volume
#   docs/status.md               -- the departure this task removes
#
# Why this exists. The research image ran as root on a guess: that whichever
# of the two containers mounted the still-empty `signals` volume first would
# seed its ownership, and that a non-root writer could lose that race to the
# engine. No build ever tested the guess. The fix removes the race instead of
# sidestepping it: both images carry the volume's mount point, owned by the
# same uid (10001, the engine's), so the volume is seeded writable for the
# writer whichever container Docker creates first. Each edit below leaves the
# image building and every other check green, and each is refused here:
#   - USER dropped from research.Dockerfile, or a name instead of the uid,
#     so the process is root again or a uid nobody can check from the text;
#   - /signals not created, or created and left root-owned, so the first
#     write of the first 19:15 run fails with EACCES;
#   - the chown placed AFTER the USER line, where it runs as 10001 and fails;
#   - the engine's /data/signals dropped, so an engine that starts first
#     seeds the volume root-owned (the race this task removes);
#   - the engine's mount losing `:ro`, or the research mount gaining it;
#   - the stamp's OHCAMEL_BUILT_AT ARG or ENV dropped (image_workflow_test.sh
#     holds OHCAMEL_GIT_SHA; nothing else holds BUILT_AT);
#   - the image job's volume step losing any of its probes;
#   - the root departure left standing in docs/status.md.
#
# Exits non-zero, naming the file, if any assertion fails. grep and awk only;
# picked up by ci.yml's deploy/test/*.sh step with no change there.

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."

research=deploy/research.Dockerfile
engine=deploy/Dockerfile
compose=deploy/docker-compose.yml
wf=.github/workflows/image.yml
status=docs/status.md

pass=0
fail=0
ok() { printf '  ok    %s\n' "$1"; pass=$((pass + 1)); }
no() { printf '  FAIL  %s -- %s\n' "$1" "$2"; fail=$((fail + 1)); }

for f in "$research" "$engine" "$compose" "$wf" "$status"; do
	if [ ! -f "$f" ]; then
		no "$f" "missing"
	fi
done
[ "$fail" -eq 0 ] || {
	printf '\n  %d passed, %d failed\n\n' "$pass" "$fail"
	exit 1
}

# last_stage FILE: "LINE<TAB>instruction" for every instruction after the
# file's last FROM -- comments and heredoc bodies dropped, continuation lines
# joined onto the line that starts them.
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
		/^[ \t]*$/ { next }
		{
			if (held == "") start = NR
			line = held $0
			if (line ~ /\\[ \t]*$/) { sub(/\\[ \t]*$/, " ", line); held = line; next }
			held = ""
			out[++n] = start "\t" line
		}
		END { for (i = 1; i <= n; i++) print out[i] }
	' "$1"
}

# --- 1. research.Dockerfile: USER 10001, and it is the last USER. ----------
rstage=$(last_stage "$research")
users=$(awk -F '\t' '{ split($2, w, /[ \t]+/); if (toupper(w[1]) == "USER") print $1 "\t" w[2] }' <<<"$rstage")
last_user=$(tail -n 1 <<<"$users")
user_line=${last_user%%$'\t'*}
user_val=${last_user#*$'\t'}
if [ -z "$users" ]; then
	no "$research: USER" "no USER instruction in the last stage -- the service runs as root; expected 'USER 10001', the engine's uid"
elif [ "$user_val" = "10001" ] || [ "$user_val" = "10001:10001" ]; then
	ok "$research:$user_line: the last stage's last USER is $user_val"
else
	no "$research:$user_line: 'USER $user_val'" "expected 'USER 10001' -- the uid, written out, so that what owns /signals and what runs the process are checkably the same number"
fi

# --- 2. /signals is created and owned by 10001, as root, before USER. ------
chown_hit=$(awk -F '\t' '
	{ split($2, w, /[ \t]+/) }
	toupper(w[1]) == "RUN" && $2 ~ /mkdir[^&;]*\/signals([ \t;&]|$)/ && $2 ~ /chown[ \t]+(-[A-Za-z]+[ \t]+)*10001(:10001)?[ \t][^&;]*\/signals([ \t;&]|$)/ { print $1; exit }
' <<<"$rstage")
if [ -z "$chown_hit" ]; then
	no "$research: /signals" "no RUN that both creates /signals (mkdir) and chowns it to 10001 -- an empty named volume mounted there is seeded from the image, ownership included, so this line is what makes the volume writable for the service"
elif [ -n "$user_line" ] && [ "$chown_hit" -gt "$user_line" ]; then
	no "$research:$chown_hit: /signals" "is chowned after USER (line $user_line) -- it would run as 10001 and fail; create and chown it while the build is still root"
else
	ok "$research:$chown_hit: /signals is created and chowned to 10001 before USER"
fi

# --- 3. The stamp: both ARGs, both ENVs, the revision LABEL. ---------------
for var in OHCAMEL_GIT_SHA OHCAMEL_BUILT_AT; do
	a=$(awk -F '\t' -v v="$var" '{ split($2, w, /[ \t]+/) } toupper(w[1]) == "ARG" && (w[2] == v || index(w[2], v "=") == 1) { print $1; exit }' <<<"$rstage")
	e=$(awk -F '\t' -v v="$var" '{ split($2, w, /[ \t]+/) } toupper(w[1]) == "ENV" && $2 ~ (v "=\"?\\$\\{?" v) { print $1; exit }' <<<"$rstage")
	if [ -n "$a" ] && [ -n "$e" ]; then
		ok "$research:$a,$e: ARG $var and ENV $var=\$$var in the last stage"
	else
		no "$research: $var" "ARG at line ${a:-none}, ENV at line ${e:-none} -- expected both in the last stage, as $engine declares them: the image job passes the build arg, and only an ENV lets 'docker run --entrypoint printenv' answer"
	fi
done
lbl=$(awk -F '\t' '{ split($2, w, /[ \t]+/) } toupper(w[1]) == "LABEL" && $2 ~ /org\.opencontainers\.image\.revision="?\$\{?OHCAMEL_GIT_SHA/ { print $1; exit }' <<<"$rstage")
if [ -n "$lbl" ]; then
	ok "$research:$lbl: LABEL org.opencontainers.image.revision=\"\$OHCAMEL_GIT_SHA\""
else
	no "$research: LABEL" "no revision label from OHCAMEL_GIT_SHA -- deploy.sh's --build fallback passes no --label, so the Dockerfile's own LABEL is the only revision an image built on the droplet carries"
fi
if grep -qiE 'runs as root|no USER here' "$research"; then
	no "$research:$(grep -niE 'runs as root|no USER here' "$research" | head -n 1 | cut -d: -f1): comment" "still says the image runs as root"
else
	ok "$research: no comment says the image runs as root"
fi

# --- 4. The engine seeds the same mount point with the same uid. -----------
estage=$(last_stage "$engine")
eseed=$(awk -F '\t' '
	{ split($2, w, /[ \t]+/) }
	toupper(w[1]) == "RUN" && $2 ~ /mkdir[^&;]*\/data\/signals([ \t;&]|$)/ && $2 ~ /chown[ \t]+(-[A-Za-z]+[ \t]+)*(ohcamel|10001)(:(ohcamel|10001))?[ \t][^&;]*\/data\/signals([ \t;&]|$)/ { print $1; exit }
' <<<"$estage")
if [ -n "$eseed" ]; then
	ok "$engine:$eseed: the engine's runtime stage creates /data/signals owned by ohcamel (10001)"
else
	no "$engine: /data/signals" "the runtime stage does not create /data/signals owned by ohcamel (uid 10001) -- an engine that mounts the empty signals volume first would otherwise seed it root-owned, and the research service, running as 10001, could not write its first signal"
fi
if grep -qE 'useradd[^#]*--uid 10001[^#]* ohcamel' "$engine"; then
	ok "$engine: the engine's ohcamel user is uid 10001"
else
	no "$engine: useradd" "the engine's ohcamel user is not created with --uid 10001 -- the research image's USER 10001 is that number on purpose"
fi

# --- 5. The two mounts. ----------------------------------------------------
# svc_block NAME: the lines of services.NAME in the compose file.
svc_block() {
	awk -v name="$1" '
		/^[^ #]/ { inservices = ($0 ~ /^services:/); inside = 0; next }
		inservices && /^  [A-Za-z0-9_-]+:/ { key = $0; sub(/^  /, "", key); sub(/:.*$/, "", key); inside = (key == name); next }
		inside && !/^[ \t]*#/ { print }
	' "$compose"
}
rblock=$(svc_block ohcamel-research)
lblock=$(svc_block ohcamel-live)
if grep -qE '^[ \t]*-[ \t]*"?signals:/signals"?[ \t]*$' <<<"$rblock"; then
	ok "$compose: ohcamel-research mounts signals:/signals read-write"
else
	no "$compose: ohcamel-research" "does not mount 'signals:/signals' read-write -- it is the volume's only writer"
fi
if grep -qE '^[ \t]*-[ \t]*"?signals:/data/signals:ro"?[ \t]*$' <<<"$lblock"; then
	ok "$compose: ohcamel-live mounts signals:/data/signals:ro"
else
	no "$compose: ohcamel-live" "does not mount 'signals:/data/signals:ro' -- the engine only lists and opens what is there"
fi
if grep -qE '^[ \t]*user:' <<<"$rblock"; then
	no "$compose: ohcamel-research" "sets 'user:' -- the image's USER 10001 is the one place the uid is set; a compose override would let the two drift"
else
	ok "$compose: ohcamel-research does not override the image's USER"
fi

# --- 6. The image job proves it on a real daemon. --------------------------
# The smoke job is the one that holds all three images; the step that names
# the probe is read as one block.
step=$(awk '
	/^[ \t]*#/ { next }
	/^      - name:/ { if (hit) exit; buf = "" ; inside = 1 }
	inside { buf = buf $0 "\n"; if ($0 ~ /\/signals\/probe/) hit = 1 }
	END { if (hit) printf "%s", buf }
' "$wf")
if [ -z "$step" ]; then
	no "$wf: signals volume step" "no step touches /signals/probe -- expected a step that runs both images on a fresh named volume"
else
	for want in \
		'docker volume create' \
		'--entrypoint id' \
		'10001' \
		'--entrypoint touch' \
		' /signals/probe' \
		':/data/signals:ro' \
		'/data/signals/probe' \
		'ohcamel-research:$SHA' \
		'ohcamel:$SHA' \
		'docker volume rm'; do
		if grep -qF -- "$want" <<<"$step"; then
			ok "$wf: the signals volume step has '$want'"
		else
			no "$wf: the signals volume step" "has no '$want' -- the task's proof is: on a fresh named volume, research's 'id -u' is 10001, 'touch /signals/probe' succeeds, and the engine reads /data/signals/probe through a :ro mount"
		fi
	done
	# Both orders: the race this task removes is the engine mounting first, so
	# some engine run on the :ro mount comes before the first research run.
	first_engine=$(grep -nF ':/data/signals:ro' <<<"$step" | head -n 1 | cut -d: -f1 || true)
	first_id=$(grep -nF -- '--entrypoint id' <<<"$step" | head -n 1 | cut -d: -f1 || true)
	if [ -n "$first_engine" ] && [ -n "$first_id" ] && [ "$first_engine" -lt "$first_id" ] &&
		grep -qE 'for [a-z_]+ in .*first.*first' <<<"$step"; then
		ok "$wf: the signals volume step also runs with the engine mounting the fresh volume first"
	else
		no "$wf: the signals volume step" "does not run the engine against a fresh volume before research (a loop over both orders, the engine's run first in the step) -- the engine-mounts-first order is the one the old root departure was about"
	fi
	if grep -qE -- '--entrypoint touch[^#]*:/data/signals:ro[^#]* /data/signals/' <<<"$step"; then
		ok "$wf: the signals volume step proves the engine cannot write through its :ro mount"
	else
		no "$wf: the signals volume step" "never tries to write as the engine -- ':ro' is only a claim until a write through it is refused"
	fi
fi
smoke_line=$(grep -nE '^  smoke:' "$wf" | cut -d: -f1 || true)
publish_line=$(grep -nE '^  publish:' "$wf" | cut -d: -f1 || true)
probe_line=$(grep -nF ' /signals/probe' "$wf" | head -n 1 | cut -d: -f1 || true)
if [ -n "$probe_line" ] && [ -n "$smoke_line" ] && [ -n "$publish_line" ] &&
	[ "$probe_line" -gt "$smoke_line" ] && [ "$probe_line" -lt "$publish_line" ]; then
	ok "$wf:$probe_line: the signals volume step is in jobs.smoke, which holds both images"
else
	no "$wf: the signals volume step" "is not inside jobs.smoke (lines ${smoke_line:-?}-${publish_line:-?}) -- only that job has the engine and the research image loaded together, and it runs before publish"
fi

# --- 7. The departure is gone. ---------------------------------------------
if grep -nF 'research.Dockerfile`) runs its process as root' "$status" >/dev/null; then
	no "$status:$(grep -nF 'research.Dockerfile`) runs its process as root' "$status" | cut -d: -f1)" "still records the root departure -- it was a guess about volume seeding that no build tested, and the image now runs as 10001"
else
	ok "$status: the research image's root departure is no longer recorded"
fi

printf '\n  %d passed, %d failed\n\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
