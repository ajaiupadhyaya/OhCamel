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

# shellcheck disable=SC1091
set -a; . deploy/.env; set +a

if [ ${#PROFILE[@]} -gt 0 ] && [ ! -r /etc/ohcamel/live.env ]; then
	echo "deploy: --live needs /etc/ohcamel/live.env (see deploy/live.env.example)" >&2
	exit 1
fi

# ---------------------------------------------------------------------------
say "Pulling"
git -C "$REPO" pull --ff-only

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
