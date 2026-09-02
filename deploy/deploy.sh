#!/usr/bin/env bash
#
# Deploy OhCamel. Runs ON the droplet, as the deploy user:
#
#   cd ~/OhCamel && deploy/deploy.sh
#   cd ~/OhCamel && deploy/deploy.sh --live      # bring the gated engine up too
#
# Pull, rebuild, restart, verify. The verify step is not optional and not
# advisory: if the smoke suite fails, this exits non-zero and says so, because
# a deploy that reports success while serving a frozen dashboard is the exact
# failure this project cannot afford.

set -euo pipefail

cd "$(dirname "$0")/.."
REPO="$PWD"
COMPOSE=(docker compose -f deploy/docker-compose.yml)
PROFILE=()

[ "${1:-}" = "--live" ] && PROFILE=(--profile live)

say() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }

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
OHCAMEL_DEMO_HOST=$(env_value OHCAMEL_DEMO_HOST)
OHCAMEL_LIVE_HOST=$(env_value OHCAMEL_LIVE_HOST)
[ -n "$OHCAMEL_DEMO_HOST" ] && [ -n "$OHCAMEL_LIVE_HOST" ] || {
	echo "deploy: OHCAMEL_DEMO_HOST and OHCAMEL_LIVE_HOST must be set in deploy/.env" >&2
	exit 1
}

if [ ${#PROFILE[@]} -gt 0 ] && [ ! -r /etc/ohcamel/live.env ]; then
	echo "deploy: --live needs /etc/ohcamel/live.env (see deploy/live.env.example)" >&2
	exit 1
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
"${COMPOSE[@]}" build

# ---------------------------------------------------------------------------
say "Starting"
"${COMPOSE[@]}" "${PROFILE[@]}" up -d --remove-orphans

# Give Caddy a moment to bind and, on a first run, to complete the ACME
# handshake. A smoke test that starts before the certificate exists reports a
# TLS failure that is really just impatience.
say "Settling"
/bin/sleep 15

"${COMPOSE[@]}" "${PROFILE[@]}" ps

# ---------------------------------------------------------------------------
say "Verifying"
SMOKE_ARGS=("https://${OHCAMEL_DEMO_HOST}")
[ ${#PROFILE[@]} -gt 0 ] && SMOKE_ARGS+=(--live "https://${OHCAMEL_LIVE_HOST}")

if deploy/smoke.sh "${SMOKE_ARGS[@]}"; then
	say "Deployed"
else
	say "Deployed, but the smoke suite FAILED -- see above"
	echo "  logs:  docker compose -f deploy/docker-compose.yml logs --tail 100" >&2
	exit 1
fi
