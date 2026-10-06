#!/usr/bin/env bash
#
# Deploy OhCamel at one commit sha, by pulling the images CI published for it.
# Runs ON the droplet, as the deploy user, from ~/OhCamel. `deploy/deploy.sh
# --help` prints the flags and the exact commands; deploy_usage below is that
# text.
#
# What a run does, in this order (finish plan Task 16):
#
#    0. Refuses a live deploy inside the market window (step 4's guard, asked
#       once here as well, so a refusal costs no fetch, no checkout and no
#       40-minute wait).
#    1. git fetch origin.
#    2. Checks the sha out, detached, so docker-compose.yml, the Caddyfile and
#       smoke.sh are the ones that match the images. book.sexp is gitignored
#       and untouched. If deploy.sh itself differs at the sha, the version
#       just checked out is re-run, once, with the same arguments -- bash has
#       already read this one, and an old deploy.sh driving new compose and
#       smoke files is how an old script once rejected a new flag.
#    3. Waits up to 40 minutes for `docker manifest inspect` to find each of
#       the three images compose pulls -- ghcr.io/ajaiupadhyaya/ohcamel,
#       ohcamel-research and ohcamel-quant -- at the sha, and otherwise exits
#       1 naming the sha and saying CI has not published it. --build skips
#       the wait: it builds all three here instead, one per Dockerfile, with
#       the OHCAMEL_GIT_SHA and OHCAMEL_BUILT_AT build args, and the pull
#       below is skipped for the images it just built.
#    4. Refuses a live deploy on a weekday inside 09:25-16:10 America/New_York
#       unless --during-market (Task 4's guard, kept -- ruling 12): a restart
#       drops the one allowed Alpaca stream and forces reconciliation. Asked
#       again after the wait, because the wait can carry an 08:50 start past
#       09:25.
#    5. Takes a pre-deploy backup of the live journal through the backup
#       service (docker-compose.yml's ohcamel-backup, Task 14), into
#       /var/backups/ohcamel as pre-deploy-<UTC>-<old sha>.db. The UTC stamp
#       comes first because `ohcamel journal-backup` keeps the 5 newest
#       pre-deploy copies BY NAME, and refuses a name its own prune would
#       delete: with the old sha first, the order would be the shas' and a
#       sixth deploy whose old sha sorted low would be refused. No journal in
#       the volume yet is a message and not a failure; any other failure
#       aborts the deploy, with nothing restarted. Live deploys only: a
#       public-only deploy restarts nothing that owns a journal.
#    6. Runs `ohcamel check-book` on book.sexp in the new engine image, with
#       no network and a read-only root, and aborts on failure -- before the
#       pull, so a bad book never restarts an engine.
#    7. OHCAMEL_TAG=<sha> docker compose ... pull. OHCAMEL_TAG is a
#       per-command prefix on every compose call, never exported, so nothing
#       here sits beside the values compose reads from deploy/.env.
#    8. up -d --remove-orphans (and retires any old ohcamel-demo container,
#       and recreates caddy if its single-file mounts are stale).
#    9. Waits up to 120 s for ohcamel-quant and (live) ohcamel-live to report
#       healthy -- this replaced a fixed fifteen-second sleep.
#   10. Smokes the public hosts, then (live) the live desk from inside its
#       container: one `deploy/smoke.sh https://DEMO --expect-sha SHA [--live
#       https://LIVE --live-container]`.
#   11. Appends `UTC sha result` (ok or failed) to ~/deploys.log. The newest
#       `ok` line before this one is the rollback reference, and a failed run
#       prints the exact command: rollback is `--sha <previous>`.
#   12. Only after a good deploy: keeps the three most recent good shas' tags
#       of the three images (this one included) and removes the rest, then
#       prunes dangling images and old build cache.
#
# The --live profile is also added automatically when /etc/ohcamel/live.env
# is readable or an ohcamel-live container exists, so a deploy run out of
# habit cannot leave ohcamel-research on a stale image; when that auto-add
# meets an unreadable live.env it falls back to a public-only deploy with a
# warning, while an explicit --live in the same state exits 1
# (resolve_live_profile, deploy/test/deploy_profile_test.sh). --public-only
# touches caddy and ohcamel-quant only, never the live profile, so the market
# guard has nothing to protect and does not apply.
#
# Test seams, read from the environment and never set by the droplet:
# DEPLOY_NOW (the guard's clock: a Unix epoch, or YYYY-MM-DDTHH:MM[:SS]Z),
# DEPLOY_LOG (default ~/deploys.log), DEPLOY_LIVE_ENV (default
# /etc/ohcamel/live.env), DEPLOY_MANIFEST_WAIT_S (default 2400),
# DEPLOY_HEALTH_WAIT_S (default 120), DEPLOY_POLL_S (default 20),
# OHCAMEL_BACKUP_DIR (default /var/backups/ohcamel, as backup.sh reads it).
# deploy/test/deploy_dryrun_test.sh drives the whole run through shims;
# deploy_guard_test.sh and deploy_profile_test.sh source this file for the
# pure functions, and so does deploy/restore.sh.

set -euo pipefail

# in_market_window ISO_WEEKDAY HHMM
#
# Pure: no I/O, no globals read or set, nothing beyond its two arguments and
# bash's own arithmetic -- so a test can call it directly, in isolation, for
# any wall-clock reading it likes. ISO_WEEKDAY is date's own %u (1=Monday ..
# 7=Sunday); HHMM is a 4-digit 24h wall-clock time as %H%M prints it (e.g.
# "0925", "1610"). Both are meant to already be New York local time -- this
# function does not know or care what produced them, which is deliberate:
# all of the DST correctness lives in ny_wall_clock's call into the zone
# database, not in any arithmetic here.
#
# Returns 0 (true) when a live deploy at that wall-clock reading should be
# REFUSED: a weekday, 09:25-16:10 (a 5-minute lead and 10-minute tail around
# the 09:30-16:00 session). Returns 1 otherwise.
in_market_window() {
	local dow="$1" hhmm="$2"
	case "$dow" in
	6 | 7) return 1 ;; # Saturday, Sunday: never refused
	esac
	hhmm=$((10#$hhmm))
	[ "$hhmm" -ge 925 ] && [ "$hhmm" -lt 1610 ]
}

# ny_wall_clock [EPOCH]
#
# Prints "ISO_WEEKDAY HHMM" (the two arguments in_market_window wants) for
# EPOCH -- Unix seconds since 1970-01-01 UTC, timezone-neutral by
# construction -- read as America/New_York wall-clock time. With no argument,
# prints the current instant. The IANA zone database supplies whatever offset
# (EST or EDT) was actually in force at that instant, so no transition date
# is hardcoded here.
#
# EPOCH is read two ways because this script has to run unmodified on the
# droplet (GNU date, which understands `-d @EPOCH`) and under a hand-run test
# on a laptop (BSD date on macOS, which does not: confirmed by hand on this
# Mac, `date -d @<epoch>` fails with "illegal option -- d", so the GNU form is
# tried first and the BSD form, `-r EPOCH`, is the fallback that actually
# runs here). DEPLOY_NOW carries this same EPOCH format, so a test can pin an
# exact instant -- including right up against a DST transition -- without
# depending on which flavour of `date` is running it.
ny_wall_clock() {
	if [ $# -eq 0 ]; then
		TZ=America/New_York date +'%u %H%M'
	else
		TZ=America/New_York date -d "@$1" +'%u %H%M' 2>/dev/null ||
			TZ=America/New_York date -r "$1" +'%u %H%M'
	fi
}

# should_refuse_live_deploy DURING_MARKET ISO_WEEKDAY HHMM
#
# Pure, like in_market_window: combines the operator's --during-market
# override (DURING_MARKET is "1" when given, empty/"0" otherwise) with the
# window check, so main() and the test exercise the exact same decision
# instead of the test re-implementing it and risking drift.
should_refuse_live_deploy() {
	local during="$1" dow="$2" hhmm="$3"
	[ "$during" = 1 ] && return 1
	in_market_window "$dow" "$hhmm"
}

# resolve_live_profile LIVE_FLAG LIVE_ENV_READABLE HAS_LIVE_CONTAINER
#
# Pure, like the two functions above: the whole "does this run use the live
# profile" decision, from three 1/0 inputs, no filesystem or docker access of
# its own -- so a test can drive every combination directly instead of
# faking a file's permission bits or a running daemon. LIVE_FLAG is 1 when
# --live was given on the command line. LIVE_ENV_READABLE and
# HAS_LIVE_CONTAINER stand in for `[ -r /etc/ohcamel/live.env ]` and an
# already-existing ohcamel-live container; main() reads both exactly once,
# before calling this, and never re-reads either.
#
# This is also the fix for the bug where a habitual, flagless `deploy.sh`
# could be refused outright: the auto-add heuristic and the
# readable-by-this-user check used to live in two separate `if`s in main(),
# so a profile guessed from "a container already exists" had no path back to
# a demo-only deploy when the file underneath that guess turned out
# unreadable -- only an explicit --live is allowed to make that failure
# fatal. Putting both decisions in one pure function is what makes that
# guarantee checkable at all: deploy/test/deploy_profile_test.sh below calls
# this directly, in isolation, for every combination of the three inputs.
#
# Prints "PROFILE AUTO_ADDED REFUSE" (each 1 or 0):
#   PROFILE     1 if this run should pass --profile live to compose.
#   AUTO_ADDED  1 if PROFILE's value (on OR off) came from the heuristic
#               rather than an explicit --live.
#   REFUSE      1 if main() must exit 1: --live was requested explicitly and
#               live.env is not readable, so there is nothing to fall back
#               to. Always 0 when PROFILE=1, and when AUTO_ADDED=1.
#
# PUBLIC_ONLY, an optional fourth input (default 0), is --public-only: when 1
# the answer is always "0 0 0" -- no profile, nothing auto-added, nothing to
# refuse -- whatever the other three say. main() refuses --live together with
# --public-only before ever calling this, so PUBLIC_ONLY=1 with LIVE_FLAG=1 is
# not a case main() produces; it still answers "0 0 0" rather than guessing.
resolve_live_profile() {
	local live_flag="$1" live_env_readable="$2" has_live_container="$3"
	local public_only="${4:-0}"
	local profile=0 auto=0 refuse=0

	if [ "$public_only" = 1 ]; then
		printf '0 0 0\n'
		return 0
	fi

	if [ "$live_flag" = 1 ]; then
		profile=1
	elif [ "$live_env_readable" = 1 ] || [ "$has_live_container" = 1 ]; then
		profile=1
		auto=1
	fi

	if [ "$profile" = 1 ] && [ "$live_env_readable" != 1 ]; then
		if [ "$auto" = 1 ]; then
			profile=0 # auto-added only, never asked for -- fall back to demo-only
		else
			refuse=1 # --live was explicit -- asked for something that cannot work
		fi
	fi

	printf '%s %s %s\n' "$profile" "$auto" "$refuse"
}


say() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }

# The three images docker-compose.yml pulls, one per Dockerfile, as
# .github/workflows/image.yml builds and publishes them: "NAME DOCKERFILE".
DEPLOY_REGISTRY=ghcr.io/ajaiupadhyaya
DEPLOY_IMAGES=(
	"ohcamel deploy/Dockerfile"
	"ohcamel-research deploy/research.Dockerfile"
	"ohcamel-quant quant/Dockerfile"
)

# deploy_epoch NOW: DEPLOY_NOW as a Unix epoch. NOW is an epoch already, or
# YYYY-MM-DDTHH:MMZ / YYYY-MM-DDTHH:MM:SSZ in UTC; GNU date first (the
# droplet), BSD date second (a laptop running the tests). Seconds are always
# given to BSD date, which otherwise fills them in from the current time.
deploy_epoch() {
	local now="$1" stamp
	case "$now" in
	'' | *[!0-9]*) ;;
	*)
		printf '%s\n' "$now"
		return 0
		;;
	esac
	stamp=${now%Z}
	case "$stamp" in
	????-??-??T??:??) stamp="$stamp:00" ;;
	????-??-??T??:??:??) ;;
	*)
		echo "deploy: DEPLOY_NOW='$now' is neither a Unix epoch nor YYYY-MM-DDTHH:MM[:SS]Z" >&2
		return 1
		;;
	esac
	date -u -d "${stamp/T/ }" +%s 2>/dev/null ||
		date -u -j -f '%Y-%m-%dT%H:%M:%S' "$stamp" +%s
}

# market_guard DURING_MARKET: exit status 1, with the refusal on stderr, when a
# live deploy must not run now. The clock is DEPLOY_NOW when set.
market_guard() {
	local during="$1" wall dow hhmm epoch
	if [ -n "${DEPLOY_NOW:-}" ]; then
		epoch=$(deploy_epoch "$DEPLOY_NOW") || return 1
		wall=$(ny_wall_clock "$epoch")
	else
		wall=$(ny_wall_clock)
	fi
	read -r dow hhmm <<<"$wall"
	if should_refuse_live_deploy "$during" "$dow" "$hhmm"; then
		echo "deploy: refusing a live deploy -- it is $hhmm America/New_York time (ISO weekday $dow), inside the 09:25-16:10 trading window; a restart drops the one allowed stream and forces reconciliation; pass --during-market to override" >&2
		return 1
	fi
	return 0
}

# wait_healthy TIMEOUT_S SERVICE...
#
# Polls each compose service's container health until every one reports
# healthy, or TIMEOUT_S passes. Prints one line per service as it turns
# healthy; returns 1 naming the ones that did not. Only services with a
# healthcheck belong here (ohcamel-quant, ohcamel-live).
wait_healthy() {
	local timeout="$1" deadline svc id state pending=()
	shift
	deadline=$(($(date +%s) + timeout))
	pending=("$@")
	while [ ${#pending[@]} -gt 0 ]; do
		local still=()
		for svc in "${pending[@]}"; do
			id=$(docker ps --filter "label=com.docker.compose.project=ohcamel" \
				--filter "label=com.docker.compose.service=$svc" --quiet | head -1)
			state=""
			[ -n "$id" ] && state=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$id" 2>/dev/null)
			case "$state" in
			healthy | none) echo "  $svc: ${state}" ;;
			*) still+=("$svc") ;;
			esac
		done
		pending=("${still[@]+"${still[@]}"}")
		[ ${#pending[@]} -eq 0 ] && return 0
		if [ "$(date +%s)" -ge "$deadline" ]; then
			echo "deploy: not healthy after ${timeout}s: ${pending[*]}" >&2
			return 1
		fi
		sleep "${DEPLOY_HEALTH_POLL_S:-3}"
	done
}

# good_shas LOG N: the N most recent distinct shas logged `ok`, newest first.
good_shas() {
	[ -f "$1" ] || return 0
	awk -v n="$2" '$3 == "ok" { a[++k] = $2 }
		END { c = 0; for (i = k; i >= 1 && c < n; i--) if (!(a[i] in s)) { s[a[i]] = 1; print a[i]; c++ } }' "$1"
}

# previous_good LOG SHA: the newest sha logged `ok` that is not SHA.
previous_good() {
	[ -f "$1" ] || return 0
	awk -v cur="$2" '$3 == "ok" && $2 != cur { p = $2 } END { if (p != "") print p }' "$1"
}

deploy_quoted() {
	local out="" a
	for a in "$@"; do out="$out $(printf '%q' "$a")"; done
	printf '%s\n' "${out# }"
}

deploy_usage() {
	cat <<'USAGE'
usage: deploy/deploy.sh [--sha SHA] [--live | --public-only] [--build]
                        [--during-market] [--dry-run]

Deploys one commit by pulling the images CI published for it. On the droplet,
as the deploy user, from ~/OhCamel.

  --sha SHA         the commit to deploy (default origin/main, resolved after
                    `git fetch origin`). Checked out detached; the images are
                    ghcr.io/ajaiupadhyaya/{ohcamel,ohcamel-research,ohcamel-quant}:<sha>
  --live            the live profile as well: ohcamel-live and ohcamel-research,
                    behind the password on the live host. Needs
                    /etc/ohcamel/live.env readable by this user. Added on its
                    own when that file is readable or ohcamel-live exists.
  --public-only     caddy and ohcamel-quant only; never touches the live
                    profile, so it may run during market hours. Not with --live.
  --build           build the three images here (one per Dockerfile, with the
                    build stamp) instead of waiting for CI to publish them
  --during-market   allow a live deploy inside 09:25-16:10 America/New_York
  --dry-run         print every step's command; run none of them
  -h, --help        this text

The public and live hosts together (what a release runs):

  deploy/deploy.sh --sha <sha> --live

The public host alone, any time of day:

  deploy/deploy.sh --sha <sha> --public-only

Roll back to the previous good deploy (the newest `ok` line in ~/deploys.log):

  deploy/deploy.sh --sha <previous sha> --live

From a checkout whose deploy.sh predates --sha (it rejects the flag), fetch
and check the sha out first, then run the version that knows it:

  git fetch origin && git checkout --detach <sha> && deploy/deploy.sh --sha <sha> --live

Order: market guard, fetch, detached checkout, wait up to 40 min for the three
images (or --build), market guard again, pre-deploy journal backup (live),
check-book in the new engine image, pull, up -d --remove-orphans, wait up to
120 s for health, smoke the public hosts then --live-container, append
`UTC sha ok|failed` to ~/deploys.log, keep the last 3 good tags and prune the
rest. Exit 0 only when the smoke suite passed.
USAGE
}

main() {
	cd "$(dirname "${BASH_SOURCE[0]}")/.."
	local REPO="$PWD"

	local sha_arg=origin/main live_flag=0 during_market=0 public_only=0 build=0 DRY=0
	while [ $# -gt 0 ]; do
		case "$1" in
		--sha)
			[ $# -ge 2 ] && [ -n "$2" ] || {
				echo "deploy: --sha needs a commit" >&2
				deploy_usage >&2
				exit 2
			}
			sha_arg="$2"
			shift
			;;
		--live) live_flag=1 ;;
		--during-market) during_market=1 ;;
		--public-only) public_only=1 ;;
		--build) build=1 ;;
		--dry-run) DRY=1 ;;
		-h | --help)
			deploy_usage
			exit 0
			;;
		*)
			echo "deploy: unknown argument $1" >&2
			deploy_usage >&2
			exit 2
			;;
		esac
		shift
	done
	# The arguments as given, for the re-exec below.
	local -a ORIG_ARGS=()
	[ "$sha_arg" != origin/main ] && ORIG_ARGS+=(--sha "$sha_arg")
	[ "$live_flag" = 1 ] && ORIG_ARGS+=(--live)
	[ "$public_only" = 1 ] && ORIG_ARGS+=(--public-only)
	[ "$build" = 1 ] && ORIG_ARGS+=(--build)
	[ "$during_market" = 1 ] && ORIG_ARGS+=(--during-market)
	[ "$DRY" = 1 ] && ORIG_ARGS+=(--dry-run)

	if [ "$live_flag" = 1 ] && [ "$public_only" = 1 ]; then
		echo "deploy: --live and --public-only contradict each other -- pick one" >&2
		exit 1
	fi
	[ "$DRY" = 1 ] && echo "deploy: dry run -- every command below is printed, none is run"

	# --- the live profile: decided once, from two facts read once ------------
	local live_env="${DEPLOY_LIVE_ENV:-/etc/ohcamel/live.env}"
	local live_env_readable=0 has_live_container=0 profile_on=0 profile_auto_added=0 refuse_live_deploy=0
	[ -r "$live_env" ] && live_env_readable=1
	if [ "$public_only" != 1 ] && [ "$live_flag" != 1 ] && [ "$live_env_readable" != 1 ]; then
		docker ps -a --filter "label=com.docker.compose.service=ohcamel-live" --quiet 2>/dev/null | grep -q . && has_live_container=1
	fi
	read -r profile_on profile_auto_added refuse_live_deploy < <(
		resolve_live_profile "$live_flag" "$live_env_readable" "$has_live_container" "$public_only"
	)
	[ "$public_only" = 1 ] && echo "deploy: --public-only -- caddy and ohcamel-quant only; the live profile is not touched"
	if [ "$profile_auto_added" = 1 ]; then
		local why="$live_env exists"
		[ "$live_env_readable" = 1 ] || why="an ohcamel-live container already exists"
		echo "deploy: $why -- adding --profile live automatically so ohcamel-research is redeployed too"
	fi
	if [ "$refuse_live_deploy" = 1 ]; then
		echo "deploy: --live needs $live_env, readable by $(id -un) -- 0640 root:$(id -un); see deploy/live.env.example" >&2
		exit 1
	fi
	if [ "$profile_auto_added" = 1 ] && [ "$profile_on" = 0 ]; then
		echo "deploy: $live_env is unreadable by $(id -un) (need 0640 root:$(id -un); see deploy/live.env.example) -- the live profile was only auto-added, not requested, so falling back to a public-only deploy; ohcamel-live and ohcamel-research will NOT be redeployed this run" >&2
	fi
	local PROFILE=()
	[ "$profile_on" = 1 ] && PROFILE=(--profile live)

	# 0. The guard, before anything moves.
	if [ ${#PROFILE[@]} -gt 0 ]; then market_guard "$during_market" || exit 1; fi

	# --- deploy/.env: the two hosts, read WITHOUT sourcing --------------------
	#
	# deploy/.env is written for compose, whose parser reads `$$` as a literal
	# `$` (deploy.env.example doubles every dollar in the bcrypt hash). Bash
	# reads `$$` as its pid: sourcing the file once turned the hash into pids,
	# compose preferred the exported value over the file, and Caddy
	# crash-looped on the first production deploy. So the two hosts come out
	# with sed, and compose reads the file itself. Nothing here may ever
	# export OHCAMEL_LIVE_HASH -- or OHCAMEL_TAG, which goes per command.
	local OHCAMEL_DEMO_HOST="" OHCAMEL_LIVE_HOST=""
	if [ -f deploy/.env ]; then
		OHCAMEL_DEMO_HOST=$(sed -n 's/^OHCAMEL_DEMO_HOST=//p' deploy/.env | tail -n1)
		OHCAMEL_LIVE_HOST=$(sed -n 's/^OHCAMEL_LIVE_HOST=//p' deploy/.env | tail -n1)
	elif [ "$DRY" = 1 ]; then
		echo "deploy: deploy/.env is missing (a real run stops here); the smoke line below names placeholder hosts"
	else
		echo "deploy: deploy/.env is missing -- copy deploy/deploy.env.example and fill it in" >&2
		exit 1
	fi
	if [ -z "$OHCAMEL_DEMO_HOST" ] || [ -z "$OHCAMEL_LIVE_HOST" ]; then
		if [ "$DRY" = 1 ]; then
			OHCAMEL_DEMO_HOST=${OHCAMEL_DEMO_HOST:-OHCAMEL_DEMO_HOST}
			OHCAMEL_LIVE_HOST=${OHCAMEL_LIVE_HOST:-OHCAMEL_LIVE_HOST}
		else
			echo "deploy: OHCAMEL_DEMO_HOST and OHCAMEL_LIVE_HOST must be set in deploy/.env" >&2
			exit 1
		fi
	fi

	# A dry run prints; a real run runs. Every command that changes anything
	# goes through one of these two.
	act() {
		if [ "$DRY" = 1 ]; then
			printf '+ %s\n' "$(deploy_quoted "$@")"
		else
			"$@"
		fi
	}
	local TAG=""
	dc() {
		if [ "$DRY" = 1 ]; then
			printf '+ OHCAMEL_TAG=%s %s\n' "$TAG" "$(deploy_quoted docker compose -f deploy/docker-compose.yml "$@")"
		else
			OHCAMEL_TAG="$TAG" docker compose -f deploy/docker-compose.yml "$@"
		fi
	}

	# --- 1, 2. fetch, then the sha, detached ----------------------------------
	local old_sha="${OHCAMEL_DEPLOY_OLD_SHA:-}"
	if [ -z "$old_sha" ]; then
		old_sha=$(git -C "$REPO" rev-parse --short=12 HEAD 2>/dev/null) || old_sha=none
		[ -n "$old_sha" ] || old_sha=none
	fi
	say "Fetching"
	act git -C "$REPO" fetch origin
	if [ "$DRY" = 1 ]; then
		TAG="$sha_arg"
		echo "  (a real run resolves $sha_arg to its full commit sha after the fetch)"
	else
		TAG=$(git -C "$REPO" rev-parse --verify "$sha_arg^{commit}") || {
			echo "deploy: $sha_arg is not a commit this checkout knows, even after git fetch origin" >&2
			exit 1
		}
	fi
	say "Checking out $TAG (detached; book.sexp is untouched)"
	local before
	before=$(git -C "$REPO" rev-parse HEAD 2>/dev/null || echo none)
	act git -C "$REPO" checkout --detach "$TAG"
	if [ "$DRY" != 1 ] && [ -z "${OHCAMEL_DEPLOY_REEXEC:-}" ] &&
		! git -C "$REPO" diff --quiet "$before" "$TAG" -- deploy/deploy.sh 2>/dev/null; then
		say "deploy.sh differs at $TAG -- re-running that version"
		OHCAMEL_DEPLOY_REEXEC=1 OHCAMEL_DEPLOY_OLD_SHA="$old_sha" \
			exec "$REPO/deploy/deploy.sh" ${ORIG_ARGS[@]+"${ORIG_ARGS[@]}"}
	fi

	# --- 3. the images: CI's, or built here -----------------------------------
	local entry name dockerfile
	if [ "$build" = 1 ]; then
		say "Building the three images here (--build: no wait for CI)"
		local built_at
		built_at=$(date -u +%FT%TZ)
		for entry in "${DEPLOY_IMAGES[@]}"; do
			read -r name dockerfile <<<"$entry"
			act docker build -f "$dockerfile" -t "$DEPLOY_REGISTRY/$name:$TAG" \
				--build-arg "OHCAMEL_GIT_SHA=$TAG" --build-arg "OHCAMEL_BUILT_AT=$built_at" .
		done
	else
		local wait_s="${DEPLOY_MANIFEST_WAIT_S:-2400}" poll="${DEPLOY_POLL_S:-20}"
		say "Waiting up to ${wait_s}s for CI to publish the three images at $TAG"
		if [ "$DRY" = 1 ]; then
			for entry in "${DEPLOY_IMAGES[@]}"; do
				read -r name dockerfile <<<"$entry"
				printf '+ (until it answers, at most %ss) docker manifest inspect %s\n' "$wait_s" "$DEPLOY_REGISTRY/$name:$TAG"
			done
		else
			local deadline missing
			deadline=$(($(date +%s) + wait_s))
			while :; do
				missing=""
				for entry in "${DEPLOY_IMAGES[@]}"; do
					read -r name dockerfile <<<"$entry"
					docker manifest inspect "$DEPLOY_REGISTRY/$name:$TAG" >/dev/null 2>&1 || missing="$missing $name"
				done
				[ -z "$missing" ] && break
				if [ "$(date +%s)" -ge "$deadline" ]; then
					echo "deploy: $TAG -- CI has not published it: after ${wait_s}s ghcr.io still has no${missing} at that sha. Check the image workflow's run for $TAG, or deploy with --build" >&2
					exit 1
				fi
				sleep "$poll"
			done
			echo "  all three images are published at $TAG"
		fi
	fi

	# --- 4. the guard again: the wait may have reached the window -------------
	if [ ${#PROFILE[@]} -gt 0 ]; then market_guard "$during_market" || exit 1; fi

	# --- the book: gitignored, so a fresh clone has none ----------------------
	#
	# Both engines bind-mount ../book.sexp, and Docker creates a missing
	# bind-mount source as a DIRECTORY, which fails the container with an
	# error naming neither the book nor the mount. An existing book is the
	# owner's and is never touched.
	say "Book"
	if [ -f book.sexp ]; then
		echo "  book.sexp present, leaving it alone"
	else
		act cp book.example.sexp book.sexp
		echo "  book.sexp created from book.example.sexp -- edit it and redeploy to change the book"
	fi

	# --- 5. the pre-deploy backup (live only) ---------------------------------
	#
	# Through the backup service, as backup.list's journal line names it (the
	# volume, the path inside it, the uid that owns it), with the NEW engine
	# image: CI has just been shown to have published it, where the running
	# container's tag may be a pre-registry local build with nothing to pull,
	# and its journal-backup verifies the copy with the code that is about to
	# open the journal anyway. Exit 3 from the container is "no journal in the
	# volume yet": the first live deploy has nothing to back up.
	if [ ${#PROFILE[@]} -gt 0 ]; then
		say "Pre-deploy backup of the journal"
		local bvol=desk_data bpath=/data/desk.db buid=10001 line
		line=$(awk '!/^[[:space:]]*(#|$)/ && $3 == "journal" { print $1, $2, $4; exit }' deploy/backup.list 2>/dev/null) || line=""
		[ -n "$line" ] && read -r bvol bpath buid <<<"$line"
		local bdir="${OHCAMEL_BACKUP_DIR:-/var/backups/ohcamel}" bname rc=0
		bname="pre-deploy-$(date -u +%Y%m%dT%H%M%SZ)-${old_sha}.db"
		if [ "$DRY" != 1 ] && { [ ! -d "$bdir" ] || [ ! -w "$bdir" ]; }; then
			echo "deploy: $bdir is missing or not writable by $(id -un); the pre-deploy backup cannot run, so nothing is deployed. Create it once (finish plan O12): install -d -m 0770 -o 10001 -g $(id -gn) $bdir" >&2
			exit 1
		fi
		# shellcheck disable=SC2016 # expanded by sh inside the container, not here
		local bscript='[ -f "$1" ] || exit 3; exec ohcamel journal-backup "$1" /backups --name "$2"'
		local bcmd=(docker compose -f deploy/docker-compose.yml --profile backup run --rm -T
			--user "$buid:$(id -g)" --entrypoint sh ohcamel-backup -ec "$bscript" pre-deploy-backup "$bpath" "$bname")
		if [ "$DRY" = 1 ]; then
			printf '+ OHCAMEL_TAG=%s OHCAMEL_BACKUP_VOLUME=%s OHCAMEL_BACKUP_DIR=%s %s\n' "$TAG" "$bvol" "$bdir" "$(deploy_quoted "${bcmd[@]}")"
		else
			OHCAMEL_TAG="$TAG" OHCAMEL_BACKUP_VOLUME="$bvol" OHCAMEL_BACKUP_DIR="$bdir" "${bcmd[@]}" || rc=$?
			case "$rc" in
			0) echo "  $bdir/$bname written and verified" ;;
			3) echo "  no journal yet at $bvol:$bpath -- nothing to back up before the first live run; skipped" ;;
			*)
				echo "deploy: the pre-deploy backup FAILED (exit $rc); nothing was pulled or restarted. The running deploy is unchanged" >&2
				exit 1
				;;
			esac
		fi
	fi

	# --- 6. check-book in the new engine image --------------------------------
	say "check-book with the new engine"
	if [ "$DRY" = 1 ]; then
		act docker run --rm --network none --read-only -v "$REPO/book.sexp:/app/book.sexp:ro" \
			"$DEPLOY_REGISTRY/ohcamel:$TAG" check-book /app/book.sexp
	elif ! docker run --rm --network none --read-only -v "$REPO/book.sexp:/app/book.sexp:ro" \
		"$DEPLOY_REGISTRY/ohcamel:$TAG" check-book /app/book.sexp; then
		echo "deploy: check-book refused book.sexp with the engine at $TAG (above); nothing was pulled or restarted. Fix the book, or deploy a sha whose engine accepts it" >&2
		exit 1
	fi

	# --- 7, 8. pull, up -------------------------------------------------------
	if [ "$build" = 1 ]; then
		say "Pull skipped: --build made the three images here"
	else
		say "Pulling $TAG"
		dc ${PROFILE[@]+"${PROFILE[@]}"} pull
	fi
	say "Starting"
	dc ${PROFILE[@]+"${PROFILE[@]}"} up -d --remove-orphans

	# The synthetic engine is behind the "demo" profile, which `up` never
	# enables, and a profiled service is not an orphan either -- so an old
	# container of it would run forever on a 4 GB box. Naming it lets compose
	# act on it with its profile off; a no-op when there is none.
	dc rm --stop --force ohcamel-demo

	# Caddy's two config files are single-file bind mounts, which pin the
	# inode: a checkout replaces a changed file with a new inode, so a caddy
	# container compose did not recreate keeps the OLD Caddyfile. Compare and
	# recreate only on a difference; certificates survive in caddy_data.
	if [ "$DRY" = 1 ]; then
		echo "+ (recreate caddy if /etc/caddy/Caddyfile or Caddyfile.snippets differs from deploy/)"
	else
		local f stale=0
		for f in Caddyfile Caddyfile.snippets; do
			if ! dc exec -T caddy cat "/etc/caddy/$f" 2>/dev/null | cmp -s - "deploy/$f"; then
				stale=1
			fi
		done
		if [ "$stale" = 1 ]; then
			say "Caddy's mounted config is stale -- recreating caddy"
			dc ${PROFILE[@]+"${PROFILE[@]}"} up -d --force-recreate --no-deps caddy
		fi
	fi

	# --- 9. health ------------------------------------------------------------
	local HEALTHY=(ohcamel-quant) health_ok=1 health_s="${DEPLOY_HEALTH_WAIT_S:-120}"
	[ ${#PROFILE[@]} -gt 0 ] && HEALTHY+=(ohcamel-live)
	say "Waiting up to ${health_s}s for ${HEALTHY[*]} to report healthy"
	if [ "$DRY" = 1 ]; then
		echo "+ (poll docker inspect health for ${HEALTHY[*]}, at most ${health_s}s)"
	else
		wait_healthy "$health_s" "${HEALTHY[@]}" || health_ok=0 # smoke says what is wrong
		dc ${PROFILE[@]+"${PROFILE[@]}"} ps || true
	fi

	# --- 10. smoke: the public hosts, then the live desk from inside ----------
	say "Verifying"
	local SMOKE_ARGS=("https://${OHCAMEL_DEMO_HOST}" --expect-sha "$TAG")
	[ ${#PROFILE[@]} -gt 0 ] && SMOKE_ARGS+=(--live "https://${OHCAMEL_LIVE_HOST}" --live-container)
	local log="${DEPLOY_LOG:-$HOME/deploys.log}" result=ok
	if [ "$DRY" = 1 ]; then
		act deploy/smoke.sh "${SMOKE_ARGS[@]}"
		printf '+ echo "<UTC> %s ok|failed" >> %s\n' "$TAG" "$log"
		echo "+ (keep the 3 newest ok shas' tags of the three images; docker image rm the rest; docker image prune -f; docker builder prune -f --keep-storage 5GB)"
		return 0
	fi
	deploy/smoke.sh "${SMOKE_ARGS[@]}" || result=failed
	[ "$health_ok" = 1 ] || result=failed

	# --- 11. the log: the rollback reference ----------------------------------
	printf '%s %s %s\n' "$(date -u +%FT%TZ)" "$TAG" "$result" >>"$log"

	if [ "$result" != ok ]; then
		say "Deployed $TAG, but it is not good -- see above"
		local prev rollback_flags=""
		prev=$(previous_good "$log" "$TAG")
		[ ${#PROFILE[@]} -gt 0 ] && rollback_flags=" --live"
		echo "  logs:      docker compose -f deploy/docker-compose.yml logs --tail 100" >&2
		echo "  not pruned: every earlier tag is still here" >&2
		if [ -n "$prev" ]; then
			echo "  roll back: deploy/deploy.sh --sha $prev$rollback_flags" >&2
		else
			echo "  roll back: no earlier 'ok' line in $log -- pick the sha by hand: deploy/deploy.sh --sha <sha>$rollback_flags" >&2
		fi
		exit 1
	fi
	say "Deployed $TAG"

	# --- 12. keep the last 3 good tags, prune the rest ------------------------
	say "Pruning"
	local keep tag
	keep=" $(good_shas "$log" 3 | tr '\n' ' ') $TAG "
	echo "  keeping:$keep"
	for entry in "${DEPLOY_IMAGES[@]}"; do
		read -r name dockerfile <<<"$entry"
		while IFS= read -r tag; do
			case "$tag" in '' | '<none>') continue ;; esac
			case "$keep" in *" $tag "*) continue ;; esac
			docker image rm "$DEPLOY_REGISTRY/$name:$tag" >/dev/null || true
			echo "  removed $DEPLOY_REGISTRY/$name:$tag"
		done < <(docker image ls "$DEPLOY_REGISTRY/$name" --format '{{.Tag}}' 2>/dev/null || true)
	done
	# Dangling layers and old build cache. --keep-storage: bounded, not
	# emptied, so a later --build is not a cold one.
	docker image prune -f >/dev/null || true
	docker builder prune -f --keep-storage 5GB >/dev/null || true
}

# Guarded so deploy/test/deploy_guard_test.sh, deploy_profile_test.sh and
# deploy/restore.sh can `source` this file for its functions without running
# a deploy -- $0 is the invoking script when sourced, but ${BASH_SOURCE[0]} is
# always this file.
if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
	main "$@"
fi
