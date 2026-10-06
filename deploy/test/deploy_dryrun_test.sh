#!/usr/bin/env bash
#
# deploy.sh deploys a sha by pulling (finish plan Task 16), driven end to end
# against shims: a scratch copy of the deploy/ files it reads, a `git` and a
# `docker` on PATH that record every call into one log and answer from
# environment knobs, and a stub smoke.sh that records itself into the same
# log. DEPLOY_LOG points at that log too, so deploy.sh's `UTC sha result`
# line lands in sequence with the calls around it and the order of all of
# them is one file read top to bottom.
#
#   deploy/test/deploy_dryrun_test.sh
#
# What it pins:
#   O. a real (shimmed) run calls, in this order: git fetch, docker manifest
#      inspect once per image, the pre-deploy backup, check-book, compose
#      pull, compose up, the health wait, smoke, the deploys.log line -- and
#      the pull and up carry OHCAMEL_TAG=<sha> as a per-command prefix.
#   F. a failing check-book aborts before the pull, and before any up.
#   B. no journal yet (the backup container exits 3): skipped with a message,
#      the deploy goes on; any other backup failure aborts before check-book.
#   M. a sha CI never published: the manifest wait gives up, exits 1, names
#      the sha and says CI has not published it; nothing after it runs.
#   D. --dry-run --sha deadbeef prints the pull and up lines carrying
#      OHCAMEL_TAG=deadbeef and runs none of them.
#   U. --dry-run --build --sha deadbeef prints one build and one tag per
#      Dockerfile and no manifest wait.
#   G. DEPLOY_NOW=2026-09-21T15:00Z (a Monday, 11:00 in New York) prints the
#      market-hours refusal for a live deploy; --during-market overrides it.
#   S. a failing smoke exits 1, logs the failure, prunes nothing, and names
#      the previous good sha as the rollback.
#   P. after a good deploy, the last three logged shas' tags are kept and
#      every other tag of the three images is removed.
#   H. --help documents --sha, --live, --build, --during-market, --dry-run
#      and --public-only, and exits 0.
#
# Exits non-zero if any case fails.

set -uo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)

pass=0
fail=0
ok() { printf '  ok    %s\n' "$1"; pass=$((pass + 1)); }
no() { printf '  FAIL  %s -- %s\n' "$1" "$2"; fail=$((fail + 1)); }

for f in deploy/deploy.sh deploy/backup.sh deploy/docker-compose.yml book.example.sexp; do
	[ -f "$here/$f" ] || {
		no "setup" "$f is missing"
		echo "deploy_dryrun_test: $pass passed, $fail failed"
		exit 1
	}
done

scratch=$(mktemp -d "${TMPDIR:-/tmp}/deploy-dryrun.XXXXXX")
trap 'rm -rf "$scratch"' EXIT

SHA=1111111111111111111111111111111111111111
OLD=0000000000000000000000000000000000000000

# --- the shims ------------------------------------------------------------------

mkdir -p "$scratch/bin"

cat >"$scratch/bin/git" <<'EOF'
#!/usr/bin/env bash
# Records the call; answers rev-parse from SHIM_* knobs. -C DIR is dropped from
# the record so the log reads the same from any checkout.
args=("$@")
if [ "${args[0]:-}" = -C ]; then args=("${args[@]:2}"); fi
printf 'git %s\n' "${args[*]}" >>"$SHIM_LOG"
case "${args[0]:-}" in
rev-parse)
	case " ${args[*]} " in
	*" --short=12 "*) printf '%s\n' "${SHIM_OLD_SHA:0:12}" ;;
	*" HEAD "* | *" HEAD") printf '%s\n' "$SHIM_HEAD" ;;
	*) printf '%s\n' "$SHIM_TARGET_SHA" ;;
	esac
	;;
diff) exit 0 ;; # deploy.sh is the same at both shas: no re-exec
esac
exit 0
EOF

cat >"$scratch/bin/docker" <<'EOF'
#!/usr/bin/env bash
# Records the call with any OHCAMEL_* per-command prefix that reached it, then
# answers from SHIM_* knobs.
prefix=""
# OHCAMEL_BACKUP_DIR is in the harness's environment for the whole run, so it
# is recorded only beside OHCAMEL_BACKUP_VOLUME, which only a prefix sets.
for v in OHCAMEL_TAG OHCAMEL_BACKUP_VOLUME; do
	if [ -n "${!v:-}" ]; then prefix="$prefix$v=${!v} "; fi
done
if [ -n "${OHCAMEL_BACKUP_VOLUME:-}" ]; then prefix="${prefix}OHCAMEL_BACKUP_DIR=${OHCAMEL_BACKUP_DIR:-} "; fi
printf 'docker %s%s\n' "$prefix" "$*" >>"$SHIM_LOG"
all=" $* "
case "$all" in
*" manifest inspect "*) exit "${SHIM_MANIFEST_RC:-0}" ;;
*" journal-backup "*) exit "${SHIM_BACKUP_RC:-0}" ;;
*" check-book "*) exit "${SHIM_CHECKBOOK_RC:-0}" ;;
*" exec -T caddy "*) exit 1 ;; # nothing to compare: caddy is recreated
esac
case "${1:-}" in
ps)
	# wait_healthy's lookup, and resolve_live_profile's
	echo cafe0001
	;;
inspect) echo healthy ;;
image)
	if [ "${2:-}" = ls ]; then
		printf '%s\n' ${SHIM_IMAGE_TAGS:-}
	fi
	;;
esac
exit 0
EOF

cat >"$scratch/bin/sleep" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF

chmod +x "$scratch/bin/git" "$scratch/bin/docker" "$scratch/bin/sleep"

# A fresh checkout per case: the deploy/ files deploy.sh reads, a stub
# smoke.sh, a deploy/.env with the two hosts, and the example book.
fresh() {
	local repo="$scratch/repo-$1"
	mkdir -p "$repo/deploy" "$scratch/backups-$1"
	cp "$here/deploy/deploy.sh" "$here/deploy/backup.sh" "$here/deploy/docker-compose.yml" "$here/deploy/backup.list" "$repo/deploy/"
	cp "$here/book.example.sexp" "$repo/"
	cat >"$repo/deploy/smoke.sh" <<'EOF'
#!/usr/bin/env bash
printf 'smoke %s\n' "$*" >>"$SHIM_LOG"
exit "${SHIM_SMOKE_RC:-0}"
EOF
	chmod +x "$repo/deploy/smoke.sh"
	printf 'OHCAMEL_DEMO_HOST=demo.example\nOHCAMEL_LIVE_HOST=live.example\n' >"$repo/deploy/.env"
	: >"$scratch/live.env"
	: >"$scratch/log-$1"
	printf '%s\n' "$repo"
}

# run CASE ARGS...: deploy.sh from that case's checkout, shims first on PATH,
# a Saturday clock unless the case sets DEPLOY_NOW. Output in out-CASE, status
# in rc-CASE.
run() {
	local c="$1" repo
	shift
	repo="$scratch/repo-$c"
	(
		cd "$repo" || exit 99
		PATH="$scratch/bin:$PATH" \
			SHIM_LOG="$scratch/log-$c" \
			SHIM_HEAD="${SHIM_HEAD:-$OLD}" \
			SHIM_OLD_SHA="$OLD" \
			SHIM_TARGET_SHA="${SHIM_TARGET_SHA:-$SHA}" \
			DEPLOY_LOG="$scratch/log-$c" \
			DEPLOY_LIVE_ENV="$scratch/live.env" \
			DEPLOY_NOW="${DEPLOY_NOW:-2026-09-19T15:00Z}" \
			DEPLOY_MANIFEST_WAIT_S="${DEPLOY_MANIFEST_WAIT_S:-0}" \
			DEPLOY_POLL_S=0 \
			OHCAMEL_BACKUP_DIR="$scratch/backups-$c" \
			bash deploy/deploy.sh "$@"
	) >"$scratch/out-$c" 2>&1
	echo $? >"$scratch/rc-$c"
}

rc() { cat "$scratch/rc-$1"; }

# step LINE: the one-word name of a recorded call, or nothing for the calls
# the order assertion does not name (rev-parse, checkout, ps, rm, exec ...).
step() {
	case "$1" in
	"git fetch"*) echo fetch ;;
	*" manifest inspect "*) echo manifest ;;
	*" journal-backup "*) echo backup ;;
	*" check-book "*) echo check-book ;;
	*" compose "*" pull"*) echo pull ;;
	*" compose "*" up -d --remove-orphans"*) echo up ;;
	"docker inspect "*) echo health ;;
	smoke*) echo smoke ;;
	*" $SHA ok" | *" $SHA failed") echo log ;;
	esac
}

order() {
	local line s prev="" out=""
	while IFS= read -r line; do
		s=$(step "$line")
		[ -n "$s" ] || continue
		# health polls once per service: one word for the run of them
		if [ "$s" = health ] && [ "$prev" = health ]; then continue; fi
		out="$out $s"
		prev="$s"
	done <"$scratch/log-$1"
	printf '%s\n' "${out# }"
}

has() { grep -qF -- "$2" "$scratch/$1"; }

# --- O. the order -----------------------------------------------------------------

fresh O >/dev/null
run O --live --sha "$SHA"
if [ "$(rc O)" = 0 ]; then ok "O1 a live deploy of a published sha exits 0"; else no "O1 a live deploy of a published sha exits 0" "rc $(rc O): $(tail -5 "$scratch/out-O" | tr '\n' ' ')"; fi
want="fetch manifest manifest manifest backup check-book pull up health smoke log"
got=$(order O)
if [ "$got" = "$want" ]; then ok "O2 order: $want"; else no "O2 order" "got '$got', want '$want'"; fi
if grep -E " compose .* pull" "$scratch/log-O" | grep -q "^docker OHCAMEL_TAG=$SHA "; then ok "O3 the pull carries OHCAMEL_TAG=<sha> as a prefix"; else no "O3 the pull carries OHCAMEL_TAG=<sha> as a prefix" "$(grep ' pull' "$scratch/log-O")"; fi
if grep -F " up -d --remove-orphans" "$scratch/log-O" | grep -q "^docker OHCAMEL_TAG=$SHA .*--profile live"; then ok "O4 the up carries OHCAMEL_TAG=<sha> and --profile live"; else no "O4 the up carries OHCAMEL_TAG=<sha> and --profile live" "$(grep -F ' up -d' "$scratch/log-O")"; fi
for img in ohcamel ohcamel-research ohcamel-quant; do
	if has log-O "manifest inspect ghcr.io/ajaiupadhyaya/$img:$SHA"; then ok "O5 manifest inspect $img:<sha>"; else no "O5 manifest inspect $img:<sha>" "not called"; fi
done
if has log-O "git checkout --detach $SHA"; then ok "O6 the sha is checked out detached"; else no "O6 the sha is checked out detached" "$(grep checkout "$scratch/log-O")"; fi
if grep -F "journal-backup" "$scratch/log-O" | grep -qE "pre-deploy-[0-9]{8}T[0-9]{6}Z-${OLD:0:12}\.db"; then ok "O7 the backup is named pre-deploy-<UTC>-<old sha>.db"; else no "O7 the backup is named pre-deploy-<UTC>-<old sha>.db" "$(grep journal-backup "$scratch/log-O")"; fi
if grep -F "journal-backup" "$scratch/log-O" | grep -q -- "--profile backup run --rm -T"; then ok "O8 the backup goes through the backup service"; else no "O8 the backup goes through the backup service" "$(grep journal-backup "$scratch/log-O")"; fi
if grep -F smoke "$scratch/log-O" | grep -q -- "https://demo.example --expect-sha $SHA --live https://live.example --live-container"; then ok "O9 smoke: the public hosts, then --live-container"; else no "O9 smoke: the public hosts, then --live-container" "$(grep smoke "$scratch/log-O")"; fi
if grep -qE "^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z $SHA ok$" "$scratch/log-O"; then ok "O10 deploys.log gains 'UTC sha ok'"; else no "O10 deploys.log gains 'UTC sha ok'" "$(tail -3 "$scratch/log-O")"; fi
if grep -q "^docker build " "$scratch/log-O"; then no "O11 nothing is built without --build" "$(grep '^docker build ' "$scratch/log-O")"; else ok "O11 nothing is built without --build"; fi
if grep -qE "^(sleep|/bin/sleep) 15" "$scratch/log-O" || grep -q "sleep 15" "$here/deploy/deploy.sh"; then no "O12 the fixed sleep 15 is gone" "still in deploy.sh"; else ok "O12 the fixed sleep 15 is gone"; fi

# --- F. a failing check-book ------------------------------------------------------

fresh F >/dev/null
SHIM_CHECKBOOK_RC=1 run F --live --sha "$SHA"
if [ "$(rc F)" != 0 ]; then ok "F1 a failing check-book exits non-zero"; else no "F1 a failing check-book exits non-zero" "rc 0"; fi
got=$(order F)
if [ "$got" = "fetch manifest manifest manifest backup check-book" ]; then ok "F2 nothing after check-book: no pull, no up"; else no "F2 nothing after check-book: no pull, no up" "got '$got'"; fi

# --- B. the backup ----------------------------------------------------------------

fresh B3 >/dev/null
SHIM_BACKUP_RC=3 run B3 --live --sha "$SHA"
if [ "$(rc B3)" = 0 ] && [ "$(order B3)" = "$want" ]; then ok "B1 no journal yet: skipped, the deploy goes on"; else no "B1 no journal yet: skipped, the deploy goes on" "rc $(rc B3), order '$(order B3)'"; fi
if grep -qi "no journal" "$scratch/out-B3"; then ok "B2 the skip says there is no journal yet"; else no "B2 the skip says there is no journal yet" "$(tail -5 "$scratch/out-B3" | tr '\n' ' ')"; fi
fresh B1 >/dev/null
SHIM_BACKUP_RC=1 run B1 --live --sha "$SHA"
if [ "$(rc B1)" != 0 ] && [ "$(order B1)" = "fetch manifest manifest manifest backup" ]; then ok "B3 a failed backup aborts before check-book"; else no "B3 a failed backup aborts before check-book" "rc $(rc B1), order '$(order B1)'"; fi

# --- M. the manifest wait ---------------------------------------------------------

fresh M >/dev/null
SHIM_MANIFEST_RC=1 run M --sha "$SHA"
if [ "$(rc M)" = 1 ]; then ok "M1 an unpublished sha exits 1"; else no "M1 an unpublished sha exits 1" "rc $(rc M)"; fi
if has out-M "$SHA" && grep -qi "CI has not published" "$scratch/out-M"; then ok "M2 the refusal names the sha and says CI has not published it"; else no "M2 the refusal names the sha and says CI has not published it" "$(tail -3 "$scratch/out-M" | tr '\n' ' ')"; fi
got=$(order M)
case "$got" in *backup* | *pull* | *up*) no "M3 nothing after the wait" "got '$got'" ;; *) ok "M3 nothing after the wait" ;; esac

# --- D. the dry run ---------------------------------------------------------------

fresh D >/dev/null
SHIM_TARGET_SHA=deadbeef run D --dry-run --live --sha deadbeef
if [ "$(rc D)" = 0 ]; then ok "D1 --dry-run exits 0"; else no "D1 --dry-run exits 0" "rc $(rc D): $(tail -5 "$scratch/out-D" | tr '\n' ' ')"; fi
if grep -E "OHCAMEL_TAG=deadbeef .*compose .* pull" "$scratch/out-D" >/dev/null; then ok "D2 the pull line carries OHCAMEL_TAG=deadbeef"; else no "D2 the pull line carries OHCAMEL_TAG=deadbeef" "$(grep pull "$scratch/out-D")"; fi
if grep -E "OHCAMEL_TAG=deadbeef .*compose .* up -d --remove-orphans" "$scratch/out-D" >/dev/null; then ok "D3 the up line carries OHCAMEL_TAG=deadbeef"; else no "D3 the up line carries OHCAMEL_TAG=deadbeef" "$(grep 'up -d' "$scratch/out-D")"; fi
if grep -E "manifest inspect|journal-backup|check-book| pull| up -d|^git fetch|^git checkout|^smoke" "$scratch/log-D" >/dev/null; then no "D4 --dry-run runs none of them" "$(grep -E 'manifest|backup|check-book|pull|up -d|fetch|checkout|smoke' "$scratch/log-D" | head -5 | tr '\n' ' ')"; else ok "D4 --dry-run runs none of them"; fi
if has out-D "manifest inspect ghcr.io/ajaiupadhyaya/ohcamel:deadbeef"; then ok "D5 --dry-run prints the manifest wait"; else no "D5 --dry-run prints the manifest wait" "absent"; fi

# --- U. --dry-run --build ---------------------------------------------------------

fresh U >/dev/null
SHIM_TARGET_SHA=deadbeef run U --dry-run --build --live --sha deadbeef
if [ "$(rc U)" = 0 ]; then ok "U1 --dry-run --build exits 0"; else no "U1 --dry-run --build exits 0" "rc $(rc U): $(tail -5 "$scratch/out-U" | tr '\n' ' ')"; fi
for pair in "deploy/Dockerfile ohcamel" "deploy/research.Dockerfile ohcamel-research" "quant/Dockerfile ohcamel-quant"; do
	# shellcheck disable=SC2086 # two words, split on purpose
	set -- $pair
	if grep -E "docker build .*-f $1 .*-t ghcr.io/ajaiupadhyaya/$2:deadbeef" "$scratch/out-U" | grep -q -- "OHCAMEL_GIT_SHA=deadbeef" &&
		grep -E "docker build .*-f $1 " "$scratch/out-U" | grep -q -- "OHCAMEL_BUILT_AT="; then
		ok "U2 one build of $1, tagged $2:deadbeef, with both stamp args"
	else
		no "U2 one build of $1, tagged $2:deadbeef, with both stamp args" "$(grep 'docker build' "$scratch/out-U" | tr '\n' ' ')"
	fi
done
n=$(grep -c "docker build " "$scratch/out-U")
if [ "$n" = 3 ]; then ok "U3 exactly one build per Dockerfile"; else no "U3 exactly one build per Dockerfile" "$n builds"; fi
n=$(grep -o -- "-t ghcr.io/ajaiupadhyaya/[a-z-]*:deadbeef" "$scratch/out-U" | sort -u | wc -l | tr -d ' ')
if [ "$n" = 3 ]; then ok "U4 one tag per Dockerfile"; else no "U4 one tag per Dockerfile" "$n distinct tags"; fi
if grep -q "manifest inspect" "$scratch/out-U"; then no "U5 --build has no manifest wait" "$(grep manifest "$scratch/out-U")"; else ok "U5 --build has no manifest wait"; fi
if grep -E "compose .* pull" "$scratch/out-U" >/dev/null; then no "U6 --build pulls nothing it just built" "$(grep pull "$scratch/out-U")"; else ok "U6 --build pulls nothing it just built"; fi

# --- G. the market-hours guard -----------------------------------------------------

fresh G >/dev/null
DEPLOY_NOW=2026-09-21T15:00Z run G --live --sha deadbeef --dry-run
if [ "$(rc G)" = 1 ] && grep -q "refusing a live deploy" "$scratch/out-G"; then ok "G1 DEPLOY_NOW=2026-09-21T15:00Z prints the market-hours refusal"; else no "G1 DEPLOY_NOW=2026-09-21T15:00Z prints the market-hours refusal" "rc $(rc G): $(tail -3 "$scratch/out-G" | tr '\n' ' ')"; fi
if has out-G "1100"; then ok "G2 the refusal reads 11:00 New York time"; else no "G2 the refusal reads 11:00 New York time" "$(grep refusing "$scratch/out-G")"; fi
fresh G2 >/dev/null
DEPLOY_NOW=2026-09-21T15:00Z run G2 --live --during-market --sha deadbeef --dry-run
if [ "$(rc G2)" = 0 ]; then ok "G3 --during-market overrides it"; else no "G3 --during-market overrides it" "rc $(rc G2): $(tail -3 "$scratch/out-G2" | tr '\n' ' ')"; fi
fresh G3 >/dev/null
DEPLOY_NOW=2026-09-21T15:00Z run G3 --public-only --sha deadbeef --dry-run
if [ "$(rc G3)" = 0 ]; then ok "G4 --public-only is never refused"; else no "G4 --public-only is never refused" "rc $(rc G3)"; fi
fresh G4 >/dev/null
DEPLOY_NOW=1790002800 run G4 --live --sha deadbeef --dry-run
if [ "$(rc G4)" = 1 ]; then ok "G5 DEPLOY_NOW still takes a Unix epoch (2026-09-21 15:00Z)"; else no "G5 DEPLOY_NOW still takes a Unix epoch" "rc $(rc G4)"; fi

# --- S. a failing smoke -------------------------------------------------------------

fresh S >/dev/null
printf '2026-09-30T10:00:00Z %s ok\n' "$OLD" >"$scratch/log-S"
SHIM_SMOKE_RC=1 run S --live --sha "$SHA"
if [ "$(rc S)" = 1 ]; then ok "S1 a failing smoke exits 1"; else no "S1 a failing smoke exits 1" "rc $(rc S)"; fi
if grep -qE "Z $SHA failed$" "$scratch/log-S"; then ok "S2 deploys.log records the failure"; else no "S2 deploys.log records the failure" "$(tail -2 "$scratch/log-S")"; fi
if grep -qE "image rm|image prune" "$scratch/log-S"; then no "S3 nothing is pruned" "$(grep -E 'image (rm|prune)' "$scratch/log-S")"; else ok "S3 nothing is pruned"; fi
if grep -q -- "--sha $OLD" "$scratch/out-S"; then ok "S4 the rollback names the previous good sha"; else no "S4 the rollback names the previous good sha" "$(tail -5 "$scratch/out-S" | tr '\n' ' ')"; fi

# --- P. keep the last 3 tags --------------------------------------------------------

fresh P >/dev/null
{
	echo "2026-09-26T10:00:00Z aaaa ok"
	echo "2026-09-27T10:00:00Z bbbb ok"
	echo "2026-09-28T10:00:00Z cccc failed"
	echo "2026-09-29T10:00:00Z dddd ok"
} >"$scratch/log-P"
SHIM_IMAGE_TAGS="aaaa bbbb cccc dddd $SHA" run P --live --sha "$SHA"
if [ "$(rc P)" = 0 ]; then ok "P1 the deploy exits 0"; else no "P1 the deploy exits 0" "rc $(rc P)"; fi
for t in aaaa cccc; do
	if has log-P "docker image rm ghcr.io/ajaiupadhyaya/ohcamel:$t"; then ok "P2 $t is pruned"; else no "P2 $t is pruned" "$(grep 'image rm' "$scratch/log-P" | tr '\n' ' ')"; fi
done
for t in bbbb dddd "$SHA"; do
	if has log-P "image rm ghcr.io/ajaiupadhyaya/ohcamel:$t"; then no "P3 $t is kept" "removed"; else ok "P3 $t is kept"; fi
done
if has log-P "docker image rm ghcr.io/ajaiupadhyaya/ohcamel-quant:aaaa" && has log-P "docker image rm ghcr.io/ajaiupadhyaya/ohcamel-research:aaaa"; then ok "P4 all three images are pruned alike"; else no "P4 all three images are pruned alike" "$(grep 'image rm' "$scratch/log-P" | tr '\n' ' ')"; fi

# --- H. --help ----------------------------------------------------------------------

fresh H >/dev/null
run H --help
if [ "$(rc H)" = 0 ]; then ok "H1 --help exits 0"; else no "H1 --help exits 0" "rc $(rc H)"; fi
for flag in --sha --live --build --during-market --dry-run --public-only; do
	if grep -q -- "$flag" "$scratch/out-H"; then ok "H2 --help documents $flag"; else no "H2 --help documents $flag" "absent"; fi
done
if grep -q -- "deploy/deploy.sh --sha <sha> --live" "$scratch/out-H"; then ok "H3 --help gives the public+live command"; else no "H3 --help gives the public+live command" "absent"; fi
if [ ! -s "$scratch/log-H" ]; then ok "H4 --help runs nothing"; else no "H4 --help runs nothing" "$(head -3 "$scratch/log-H" | tr '\n' ' ')"; fi
fresh X >/dev/null
run X --nonsense
if [ "$(rc X)" = 2 ]; then ok "H5 an unknown flag is exit 2"; else no "H5 an unknown flag is exit 2" "rc $(rc X)"; fi

echo "deploy_dryrun_test: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
