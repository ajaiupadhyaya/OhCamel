#!/usr/bin/env bash
#
# The worker writes the signals volume, the live engine only reads it, and
# the worker's INC-01 kinds are registered and scheduled (autonomous-paper-
# trading plan, Task 16; design §4.2 and §9).
#
#   deploy/test/worker_signals_test.sh
#
# Reads, as text:
#
#   deploy/docker-compose.yml                    -- the three mounts of the
#                                                   `signals` volume, and the
#                                                   worker's environment
#   quant/src/ohcamel_quant/jobs/kinds.py        -- the kinds the worker runs
#   quant/src/ohcamel_quant/jobs/schedules.yaml  -- when it runs them
#   deploy/Dockerfile, deploy/research.Dockerfile, quant/Dockerfile
#                                                -- the `signals` group and
#                                                   each mount point's mode
#   deploy/docker-compose.{ci,local}.yml         -- signals-init's image
#
# Why this exists. signals.daily and signals.overlay refuse to run without
# OHCAMEL_QUANT_SIGNALS_DIR, and desk.report reads the live engine at
# OHCAMEL_QUANT_LIVE_ENGINE_URL; on the droplet both come from this compose
# file and nowhere else. Each edit below renders and starts cleanly, and is
# refused here:
#   - the worker's `signals:/signals` mount dropped, or made `:ro`, so every
#     producer run fails on its first write;
#   - OHCAMEL_QUANT_SIGNALS_DIR unset or pointed anywhere but the mount;
#   - OHCAMEL_QUANT_LIVE_ENGINE_URL pointed anywhere but the live engine's
#     in-network address (the public host's bridge, say);
#   - the live engine's mount losing `:ro` -- it only ever reads, and the two
#     writers (the worker and ohcamel-research) are the only ones;
#   - ohcamel-research's read-write mount dropped;
#   - a `ports:` block on the worker (none of the app containers publishes a
#     port);
#   - a producer kind registered without a schedule or a max age, or
#     desk.report's 16:30 weekday entry moved;
#   - the `signals` group (gid 10001) missing from any of the three images,
#     its service user not a member, or a mount point not 10001:10001 2775
#     -- the worker, uid 10002, writes only through that group;
#   - signals-init (the root one-shot that sets the volume's owner and mode
#     on every `up`, fixing a volume seeded before the group) dropped, moved
#     out of the default profile, or not waited for by the worker, research
#     or the live engine.
#
# Exits non-zero, naming the file, if any assertion fails. grep and awk only;
# picked up by ci.yml's deploy/test/*.sh step with no change there.

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."

compose=deploy/docker-compose.yml
kinds=quant/src/ohcamel_quant/jobs/kinds.py
sched=quant/src/ohcamel_quant/jobs/schedules.yaml

pass=0
fail=0
ok() { printf '  ok    %s\n' "$1"; pass=$((pass + 1)); }
no() { printf '  FAIL  %s -- %s\n' "$1" "$2"; fail=$((fail + 1)); }

for f in "$compose" "$kinds" "$sched"; do
	[ -f "$f" ] || no "$f" "missing"
done
[ "$fail" -eq 0 ] || {
	printf '\n  %d passed, %d failed\n\n' "$pass" "$fail"
	exit 1
}

# svc_block NAME: the non-comment lines of services.NAME in the compose file.
svc_block() {
	awk -v name="$1" '
		/^[^ #]/ { inservices = ($0 ~ /^services:/); inside = 0; next }
		inservices && /^  [A-Za-z0-9_-]+:/ { key = $0; sub(/^  /, "", key); sub(/:.*$/, "", key); inside = (key == name); next }
		inside && !/^[ \t]*#/ { print }
	' "$compose"
}
wblock=$(svc_block ohcamel-worker)
lblock=$(svc_block ohcamel-live)
rblock=$(svc_block ohcamel-research)
[ -n "$wblock" ] || no "$compose: ohcamel-worker" "no such service"

# --- 1. The worker: the mount, read-write, and the two settings. ----------
if grep -qE '^[ \t]*-[ \t]*"?signals:/signals"?[ \t]*$' <<<"$wblock"; then
	ok "$compose: ohcamel-worker mounts signals:/signals read-write"
else
	no "$compose: ohcamel-worker" "does not mount 'signals:/signals' read-write -- signals.daily and signals.overlay write there"
fi
if grep -qE '^[ \t]*-[ \t]*"?signals:[^ ]*:ro' <<<"$wblock"; then
	no "$compose: ohcamel-worker" "mounts signals read-only -- it is a writer"
else
	ok "$compose: ohcamel-worker has no read-only signals mount"
fi
if grep -qE '^[ \t]+OHCAMEL_QUANT_SIGNALS_DIR:[ \t]+"?/signals"?[ \t]*$' <<<"$wblock"; then
	ok "$compose: ohcamel-worker sets OHCAMEL_QUANT_SIGNALS_DIR: /signals"
else
	no "$compose: ohcamel-worker" "does not set OHCAMEL_QUANT_SIGNALS_DIR: /signals -- unset, the producers refuse to run; elsewhere, they write where the desk never reads"
fi
if grep -qE '^[ \t]+OHCAMEL_QUANT_LIVE_ENGINE_URL:[ \t]+"?http://ohcamel-live:8081"?[ \t]*$' <<<"$wblock"; then
	ok "$compose: ohcamel-worker sets OHCAMEL_QUANT_LIVE_ENGINE_URL: http://ohcamel-live:8081"
else
	no "$compose: ohcamel-worker" "does not set OHCAMEL_QUANT_LIVE_ENGINE_URL: http://ohcamel-live:8081 -- desk.report reads the live engine on the internal network"
fi
if grep -qE '^    ports:' <<<"$wblock"; then
	no "$compose: ohcamel-worker" "publishes a port -- only caddy has one"
else
	ok "$compose: ohcamel-worker publishes no port"
fi

# --- 2. The other two mounts stand as they were. --------------------------
if grep -qE '^[ \t]*-[ \t]*"?signals:/data/signals:ro"?[ \t]*$' <<<"$lblock" &&
	! grep -qE '^[ \t]*-[ \t]*"?signals:/data/signals"?[ \t]*$' <<<"$lblock"; then
	ok "$compose: ohcamel-live mounts signals:/data/signals:ro, and only so"
else
	no "$compose: ohcamel-live" "does not mount 'signals:/data/signals:ro' alone -- the engine only lists and opens what is there"
fi
if grep -qE '^[ \t]*-[ \t]*"?signals:/signals"?[ \t]*$' <<<"$rblock"; then
	ok "$compose: ohcamel-research still mounts signals:/signals read-write"
else
	no "$compose: ohcamel-research" "does not mount 'signals:/signals' read-write -- it writes exp_a01_*"
fi

# --- 3. The comment says who writes what. ---------------------------------
if grep -qE 'both writers' "$compose" && grep -qF 'overlay-<n>.json' "$compose" && grep -qF '<slug>-<date>.json' "$compose"; then
	ok "$compose: a comment names the two writers and their distinct file names"
else
	no "$compose: comment" "no comment says the worker and ohcamel-research are both writers, to distinct names (<slug>-<date>.json, overlay-<n>.json, exp_a01_*)"
fi
if grep -qE 'ohcamel-research below is the only writer|research is the only writer' "$compose"; then
	no "$compose: comment" "still says ohcamel-research is the only writer -- the worker writes too"
else
	ok "$compose: no comment still calls ohcamel-research the only writer"
fi

# --- 4. The kinds the worker runs: registered, scheduled, aged. ------------
for kind in signals.daily signals.overlay desk.report; do
	if grep -qE "register\(KindSpec\(\"$kind\"" "$kinds" &&
		grep -qE "^  $kind: [0-9]+[hm]" "$sched" &&
		grep -qE "^[[:blank:]]+kind: ${kind}[[:blank:]]*$" "$sched"; then
		ok "$kinds, $sched: $kind is registered, aged and scheduled"
	else
		no "$kind" "not registered in $kinds, or without a max_age or a schedule entry in $sched"
	fi
done
report_entry=$(awk '/^  - name:/ { blk = "" } { blk = blk $0 "\n" } /^[ \t]+kind: desk\.report[ \t]*$/ { hit = 1 } hit && /^[ \t]+heavy:/ { printf "%s", blk; exit }' "$sched")
if grep -qF 'cron: "30 16 * * 1-5"' <<<"$report_entry" && grep -qE 'mem_class: S' <<<"$report_entry" &&
	grep -qE 'heavy: false' <<<"$report_entry"; then
	ok "$sched: desk.report runs at 16:30 New York on weekdays, class S, not heavy"
else
	no "$sched: desk.report" "the entry is not cron \"30 16 * * 1-5\", mem_class S, heavy false: $(tr '\n' ' ' <<<"$report_entry")"
fi
if grep -qE '^  desk\.report: 30h' "$sched"; then
	ok "$sched: desk.report's max_age is 30h"
else
	no "$sched: desk.report" "max_age is not 30h"
fi

# --- 5. One group across the three images. --------------------------------
# Each image: `groupadd --gid 10001 signals`, its service user a member, and
# its mount point chowned 10001:10001 and chmod 2775 -- all in the build's
# root part, before the image's USER line. A different number in any one of
# them and that image's user is in a group the volume does not have.
for spec in "deploy/Dockerfile:ohcamel:/data/signals" "deploy/research.Dockerfile:research:/signals" \
	"quant/Dockerfile:quant:/signals"; do
	df=${spec%%:*}
	rest=${spec#*:}
	user=${rest%%:*}
	mp=${rest#*:}
	# The Dockerfile's instructions with continuation lines joined, comments dropped.
	joined=$(awk '/^[ \t]*#/ { next } { if (held == "") start = NR; line = held $0; if (line ~ /\\[ \t]*$/) { sub(/\\[ \t]*$/, " ", line); held = line; next } held = ""; print start "\t" line }' "$df")
	last_user=$(awk -F '\t' '{ split($2, w, /[ \t]+/) } toupper(w[1]) == "USER" { n = $1 } END { print n + 0 }' <<<"$joined")
	gline=$(awk -F '\t' '$2 ~ /groupadd[ \t]+(--gid|-g)[ \t]+10001[ \t]+signals([ \t;&]|$)/ { print $1; exit }' <<<"$joined")
	uline=$(awk -F '\t' -v u="$user" '$2 ~ ("usermod[ \t]+-aG[ \t]+signals[ \t]+" u "([ \t;&]|$)") { print $1; exit }' <<<"$joined")
	mline=$(awk -F '\t' -v mp="$mp" '{ c = "chown[ \t]+10001:10001[ \t]+" mp "([ \t;&]|$)"; m = "chmod[ \t]+2775[ \t]+" mp "([ \t;&]|$)" } $2 ~ c && $2 ~ m { print $1; exit }' <<<"$joined")
	if [ -n "$gline" ] && [ -n "$uline" ]; then
		ok "$df:$gline: the signals group is gid 10001 and $user is a member"
	else
		no "$df: signals group" "no 'groupadd --gid 10001 signals' (line ${gline:-none}) or no 'usermod -aG signals $user' (line ${uline:-none}) -- the worker (uid 10002) writes the volume through gid 10001, and the number must be the same in all three images"
	fi
	if [ -n "$mline" ]; then
		ok "$df:$mline: $mp is chown 10001:10001 and chmod 2775"
	else
		no "$df: $mp" "not chowned 10001:10001 and chmod 2775 in one RUN -- a fresh volume is seeded from whichever image mounts it first, mode included"
	fi
	if [ -n "$mline" ] && [ "$last_user" -gt 0 ] && [ "$mline" -gt "$last_user" ]; then
		no "$df:$mline" "runs after USER (line $last_user) -- as the unprivileged user, the chown fails"
	fi
done

# --- 6. signals-init: a root one-shot on every `up`, and the three wait. ---
iblock=$(svc_block signals-init)
if [ -z "$iblock" ]; then
	no "$compose: signals-init" "no such service -- a volume seeded before the group existed (the droplet's) keeps 10001:root 0755, and the worker cannot write"
else
	ok "$compose: signals-init exists"
	for want in \
		'^    image: "\$\{OHCAMEL_IMAGE:-ghcr\.io/ajaiupadhyaya/ohcamel\}:' \
		'^    user: "0:0"$' \
		'^    restart: "no"$' \
		'chown 10001:10001 /signals && chmod 2775 /signals' \
		'^      - signals:/signals$' \
		'^    network_mode: none$' \
		'^    read_only: true$'; do
		if grep -qE -- "$want" <<<"$iblock"; then
			ok "$compose: signals-init has /$want/"
		else
			no "$compose: signals-init" "has no line matching /$want/"
		fi
	done
	for bad in '^    profiles:' '^    ports:' '^    healthcheck:'; do
		if grep -qE -- "$bad" <<<"$iblock"; then
			no "$compose: signals-init" "has /$bad/ -- it is a one-shot in the default profile, so every up (--public-only and --live) runs it, with no port"
		else
			ok "$compose: signals-init has no /$bad/"
		fi
	done
fi
for svc in ohcamel-worker ohcamel-research ohcamel-live; do
	blk=$(svc_block "$svc")
	if awk '/^    depends_on:/ { d = 1; next } d && /^    [a-z]/ { d = 0 } d && /^      signals-init:/ { s = 1; next } s && /condition: service_completed_successfully/ { ok = 1 } s && /^      [a-z]/ { s = 0 } END { exit !ok }' <<<"$blk"; then
		ok "$compose: $svc depends_on signals-init: service_completed_successfully"
	else
		no "$compose: $svc" "does not wait for signals-init (depends_on, condition: service_completed_successfully) -- it could mount the volume before its owner and mode are set"
	fi
done
for ov in deploy/docker-compose.ci.yml deploy/docker-compose.local.yml; do
	if grep -qE '^  signals-init:' "$ov"; then
		ok "$ov: names signals-init (the image it loaded, or the engine build)"
	else
		no "$ov: signals-init" "not named -- the ci override's pull_policy: never and the harness's build would miss the engine image it runs"
	fi
done

printf '\n  %d passed, %d failed\n\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
