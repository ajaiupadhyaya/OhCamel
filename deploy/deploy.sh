#!/usr/bin/env bash
#
# Deploy OhCamel. Runs ON the droplet, as the deploy user:
#
#   cd ~/OhCamel && deploy/deploy.sh
#   cd ~/OhCamel && deploy/deploy.sh --live            # bring the gated engine up too
#   cd ~/OhCamel && deploy/deploy.sh --live --during-market  # override the market-hours guard
#
# Pull, rebuild, restart, verify. The verify step is not optional and not
# advisory: if the smoke suite fails, this exits non-zero and says so, because
# a deploy that reports success while serving a frozen dashboard is the exact
# failure this project cannot afford.
#
# The --live profile is also added automatically when /etc/ohcamel/live.env
# exists or an ohcamel-live container is already present, so a demo-only
# deploy run out of habit can no longer leave ohcamel-research (profiles:
# ["live"], never built by a plain `up -d`) on a stale image while the smoke
# suite against the demo host still passes.
#
# A live deploy refuses to run on a weekday inside 09:25-16:10 in
# America/New_York -- because a restart drops the one allowed stream and
# forces reconciliation -- unless --during-market is given. A fixed UTC band
# is wrong for this: New York keeps two different UTC offsets across the
# year, so a band tuned to one of them either leaves the last fifty minutes
# of every EST session unguarded or over-blocks an hour of EDT mornings.
# Reading the zone database directly -- instead of hardcoding either offset,
# or a DST transition date -- is the fix. DEPLOY_NOW overrides the clock this
# guard reads (see ny_wall_clock below for its format), so
# deploy/test/deploy_guard_test.sh can exercise it without waiting for the
# calendar -- including both of 2026's DST transition instants.

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

say() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }

main() {
	cd "$(dirname "${BASH_SOURCE[0]}")/.."
	local REPO="$PWD"
	local COMPOSE=(docker compose -f deploy/docker-compose.yml)

	local live_flag=0 during_market=0
	local arg
	for arg in "$@"; do
		case "$arg" in
		--live) live_flag=1 ;;
		--during-market) during_market=1 ;;
		*)
			echo "deploy: unknown argument $arg" >&2
			exit 1
			;;
		esac
	done

	local PROFILE=()
	[ "$live_flag" = 1 ] && PROFILE=(--profile live)

	# Auto-add the live profile so a habitual `deploy.sh` (no flag) never
	# leaves ohcamel-research -- behind the same profile, built only by a
	# profiled `build`/`up` -- on whatever image it last had.
	if [ ${#PROFILE[@]} -eq 0 ]; then
		local why=""
		if [ -r /etc/ohcamel/live.env ]; then
			why="/etc/ohcamel/live.env exists"
		elif docker ps -a --filter "label=com.docker.compose.service=ohcamel-live" --quiet 2>/dev/null | grep -q .; then
			why="an ohcamel-live container already exists"
		fi
		if [ -n "$why" ]; then
			PROFILE=(--profile live)
			echo "deploy: $why -- adding --profile live automatically so ohcamel-research is rebuilt too"
		fi
	fi

	[ -f deploy/.env ] || {
		echo "deploy: deploy/.env is missing -- copy deploy/deploy.env.example and fill it in" >&2
		exit 1
	}

	# Read the two hostnames the smoke suite needs WITHOUT sourcing the file.
	#
	# deploy/.env is written for docker compose, whose parser treats `$$` as a
	# literal `$` -- which is why deploy.env.example says to double every dollar in
	# the bcrypt hash. Bash has a different opinion: `$$` is the shell's own process
	# id. Sourcing the file turned `$2a$14$...` into `<pid>2a<pid>14<pid>...`, and
	# because compose lets an exported variable override the .env file, that is the
	# value Caddy received. Its basic_auth module could not parse it, and Caddy
	# crash-looped on the first production deploy while the engine behind it sat
	# healthy. The local harness never saw this: Caddyfile.local has no basic_auth,
	# and `make deploy-verify` hands the file to compose with --env-file rather than
	# sourcing it into a shell.
	#
	# So: pull the two values out with sed, and let compose read the file itself.
	# Nothing here may ever export OHCAMEL_LIVE_HASH into the environment.
	env_value() { sed -n "s/^$1=//p" deploy/.env | tail -n1; }
	local OHCAMEL_DEMO_HOST OHCAMEL_LIVE_HOST
	OHCAMEL_DEMO_HOST=$(env_value OHCAMEL_DEMO_HOST)
	OHCAMEL_LIVE_HOST=$(env_value OHCAMEL_LIVE_HOST)
	[ -n "$OHCAMEL_DEMO_HOST" ] && [ -n "$OHCAMEL_LIVE_HOST" ] || {
		echo "deploy: OHCAMEL_DEMO_HOST and OHCAMEL_LIVE_HOST must be set in deploy/.env" >&2
		exit 1
	}

	# Readable BY THIS USER: compose reads env_file on the client side, so a file
	# root can read and the deploy user cannot is a file the container never sees.
	if [ ${#PROFILE[@]} -gt 0 ] && [ ! -r /etc/ohcamel/live.env ]; then
		echo "deploy: --live needs /etc/ohcamel/live.env, readable by $(id -un) -- 0640 root:$(id -un); see deploy/live.env.example" >&2
		exit 1
	fi

	# ---------------------------------------------------------------------------
	# The market-hours guard. DEPLOY_NOW (a Unix epoch) overrides the clock this
	# reads, which is how deploy/test/deploy_guard_test.sh exercises every hand
	# case -- including both of 2026's DST transition instants -- without waiting
	# for the calendar to cooperate.
	if [ ${#PROFILE[@]} -gt 0 ]; then
		local wall dow hhmm
		if [ -n "${DEPLOY_NOW:-}" ]; then
			wall=$(ny_wall_clock "$DEPLOY_NOW")
		else
			wall=$(ny_wall_clock)
		fi
		read -r dow hhmm <<<"$wall"
		if should_refuse_live_deploy "$during_market" "$dow" "$hhmm"; then
			echo "deploy: refusing a live deploy -- it is $hhmm America/New_York time (ISO weekday $dow), inside the 09:25-16:10 trading window; a restart drops the one allowed stream and forces reconciliation; pass --during-market to override" >&2
			exit 1
		fi
	fi

	# ---------------------------------------------------------------------------
	say "Pulling"
	git -C "$REPO" pull --ff-only

	# ---------------------------------------------------------------------------
	say "Book"
	#
	# book.sexp is gitignored, so a fresh clone does not have one, and the compose
	# file bind-mounts ../book.sexp into both engines. Docker's behaviour when a
	# bind-mount source is missing is to CREATE it -- as a directory -- and a
	# directory mounted over a file makes the container fail to start with an
	# error that names neither the book nor the mount. So the file is created
	# here, from the committed example, before anything can go looking for it.
	# An existing book.sexp is the owner's and is never touched.
	if [ ! -f book.sexp ]; then
		cp book.example.sexp book.sexp
		echo "  book.sexp created from book.example.sexp -- edit it and redeploy to change the book"
	else
		echo "  book.sexp present, leaving it alone"
	fi

	# ---------------------------------------------------------------------------
	say "Building"
	#
	# On the droplet, natively. This is the whole reason the build happens here
	# rather than on a laptop: the laptop is arm64 and this is amd64, and running
	# an OCaml build of this size through emulation is slow enough that the deploy
	# step stops being one anybody runs.
	#
	# A per-command prefix, never an export. The rule is the one at the top of this
	# file: nothing here may put a value into the environment that compose would
	# then prefer over deploy/.env. These two are harmless to export and the next
	# pair after them would not be, so the habit is kept rather than the exception
	# made.
	#
	# "${PROFILE[@]}" goes to `build` here too, not only to `up` below: without
	# it, compose skips ohcamel-research (profiles: ["live"]) on every build, `up
	# -d` only ever builds it through its missing-image path the very first time,
	# and every later live deploy runs the research image it happened to have
	# rather than the one this commit ships.
	OHCAMEL_GIT_SHA="$(git -C "$REPO" rev-parse HEAD)" \
	OHCAMEL_BUILT_AT="$(date -u +%FT%TZ)" \
		"${COMPOSE[@]}" "${PROFILE[@]}" build

	# ---------------------------------------------------------------------------
	say "Starting"
	"${COMPOSE[@]}" "${PROFILE[@]}" up -d --remove-orphans

	# ---------------------------------------------------------------------------
	say "Pruning"
	# Dangling images (the ones `build` just orphaned by replacing them) and old
	# build cache. -f: no interactive confirmation on an unattended deploy.
	# --keep-storage 5GB: bounded, not emptied -- an emptied cache turns the next
	# deploy's build back into a cold one.
	docker image prune -f
	docker builder prune -f --keep-storage 5GB

	# Give Caddy a moment to bind and, on a first run, to complete the ACME
	# handshake. A smoke test that starts before the certificate exists reports a
	# TLS failure that is really just impatience.
	say "Settling"
	/bin/sleep 15

	"${COMPOSE[@]}" "${PROFILE[@]}" ps

	# ---------------------------------------------------------------------------
	say "Verifying"
	# The sha this script just built from, read from the checkout and not from
	# the container: the assertion is that the two agree, and one side of an
	# agreement has to come from somewhere the other side cannot reach.
	local SMOKE_ARGS=("https://${OHCAMEL_DEMO_HOST}" --expect-sha "$(git -C "$REPO" rev-parse HEAD)")
	[ ${#PROFILE[@]} -gt 0 ] && SMOKE_ARGS+=(--live "https://${OHCAMEL_LIVE_HOST}")

	if deploy/smoke.sh "${SMOKE_ARGS[@]}"; then
		say "Deployed"
	else
		say "Deployed, but the smoke suite FAILED -- see above"
		echo "  logs:  docker compose -f deploy/docker-compose.yml logs --tail 100" >&2
		exit 1
	fi
}

# Guarded so deploy/test/deploy_guard_test.sh can `source` this file to reach
# in_market_window without running a deploy -- $0 is the invoking script when
# sourced, but ${BASH_SOURCE[0]} is always this file.
if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
	main "$@"
fi
