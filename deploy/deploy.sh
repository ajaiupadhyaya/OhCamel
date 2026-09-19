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
# A live deploy refuses to run on a weekday inside [13:25, 20:10) UTC --
# because a restart drops the one allowed stream and forces reconciliation --
# unless --during-market is given. DEPLOY_NOW overrides the clock this guard
# reads, so deploy/test/deploy_guard_test.sh can exercise it without waiting
# for the calendar.

set -euo pipefail

# in_market_window UTC_ISO
#
# Pure: no I/O, no globals read or set, nothing beyond its argument and bash's
# own arithmetic -- so a test can call it directly, in isolation, for any
# timestamp it likes. UTC_ISO is "YYYY-MM-DDTHH:MM:SSZ" (what `date -u
# +%FT%TZ` and this script's own DEPLOY_NOW both produce).
#
# Returns 0 (true) when a live deploy started at that instant should be
# REFUSED: a weekday, inside [13:25, 20:10) UTC. Returns 1 otherwise.
#
# The weekday is Zeller's congruence rather than `date -d`/`date -j`, because
# those two flags are GNU's and BSD's respectively and this script has to run
# unmodified on the droplet (GNU) and under a hand-run test on a laptop (BSD
# macOS) alike. Zeller's congruence over the ISO date's own digits needs
# neither.
in_market_window() {
	local iso="$1"
	local y m d hh mm
	y="${iso:0:4}"; m="${iso:5:2}"; d="${iso:8:2}"
	hh="${iso:11:2}"; mm="${iso:14:2}"
	y=$((10#$y)); m=$((10#$m)); d=$((10#$d))
	hh=$((10#$hh)); mm=$((10#$mm))

	# Zeller's congruence (Gregorian): January and February count as months
	# 13 and 14 of the PREVIOUS year.
	local zy zm K J h
	zy=$y; zm=$m
	if [ "$zm" -lt 3 ]; then
		zm=$((zm + 12))
		zy=$((zy - 1))
	fi
	K=$((zy % 100))
	J=$((zy / 100))
	h=$(( (d + (13 * (zm + 1)) / 5 + K + K / 4 + J / 4 + 5 * J) % 7 ))
	# h: 0=Saturday, 1=Sunday, 2=Monday, 3=Tuesday, 4=Wednesday, 5=Thursday, 6=Friday.
	case "$h" in
	0 | 1) return 1 ;; # weekend: never refused
	esac

	local minutes=$((hh * 60 + mm))
	[ "$minutes" -ge $((13 * 60 + 25)) ] && [ "$minutes" -lt $((20 * 60 + 10)) ]
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
	# The market-hours guard. DEPLOY_NOW overrides the clock this reads, which is
	# how deploy/test/deploy_guard_test.sh exercises all five hand cases without
	# waiting for the calendar to cooperate.
	if [ ${#PROFILE[@]} -gt 0 ]; then
		local now_utc="${DEPLOY_NOW:-$(date -u +%Y-%m-%dT%H:%M:%SZ)}"
		if [ "$during_market" != 1 ] && in_market_window "$now_utc"; then
			echo "deploy: refusing a live deploy at $now_utc UTC -- a weekday in [13:25, 20:10) UTC drops the one allowed stream and forces reconciliation; pass --during-market to override" >&2
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
