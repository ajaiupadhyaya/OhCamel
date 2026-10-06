#!/usr/bin/env bash
#
# Pins how the three compose files name their images, against real renders:
#
#   deploy/docker-compose.yml        pulls an IMMUTABLE tag and builds nothing
#   deploy/docker-compose.local.yml  the localhost harness: the build blocks, tag local
#   deploy/docker-compose.ci.yml     the image job: the sha tags it loaded, no build
#
#   deploy/test/compose_tags_test.sh
#
# Exits non-zero, naming the assertion, if any render disagrees with the
# hand-derived expectation below. Needs `docker compose` (the render is
# client-side; no daemon, no image, no container) and nothing else. Every
# render goes to a scratch file and only its image/build/logging lines are
# echoed, so nothing an env_file resolved is ever printed. The live profile
# renders through a scratch override under $TMPDIR that `!override`s the two
# required env_files to a dummy, exactly as CI's lint job does with sudo on
# the runner -- /etc/ohcamel is never created or read here.
#
# Why this exists. Until Task 9 of the finish plan, docker-compose.yml said
# `image: ohcamel:latest` with a `build:` block on the engine anchor, and the
# same for ohcamel-quant and ohcamel-research: the droplet built every deploy
# itself (twenty minutes, next to the live engine) and `latest` named
# whatever was built last, so a rollback had nothing to name. Now the base
# file names ghcr.io/ajaiupadhyaya/{ohcamel,ohcamel-research,ohcamel-quant}
# at ${OHCAMEL_TAG:?...}, a commit sha deploy.sh passes per command, and the
# build blocks live only in the harness override. Each of those facts is a
# line in a YAML file that nothing else reads, so each is pinned here:
#
#   A. base + live at OHCAMEL_TAG=deadbeef: exactly five image lines --
#      caddy:2.11.4-alpine and the four ghcr refs at :deadbeef (the default
#      profile is caddy + ohcamel-quant + ohcamel-hostd, --profile live adds
#      ohcamel-live + ohcamel-research: 3 + 2 = 5) -- and no build key, no
#      :latest.
#   B. base + demo: ohcamel-demo runs the SAME engine ref (one anchor).
#   C. OHCAMEL_IMAGE re-points the engine only; research, quant and hostd
#      stay ghcr.
#   D. OHCAMEL_TAG unset: the render fails and names deploy.sh in the message.
#   E. base + local at OHCAMEL_TAG=local: four distinct Dockerfiles rendered
#      (deploy/Dockerfile, shared by ohcamel-demo and ohcamel-live;
#      deploy/research.Dockerfile; quant/Dockerfile; native/hostd/Dockerfile),
#      build on those five services and not on caddy, every ghcr ref at :local, and the Makefile's
#      -t tags and deploy/local.env's OHCAMEL_TAG agree with that.
#   F. base + ci: no build key in the file at all, both profiles render with
#      no /etc/ohcamel and no scratch override because the two required
#      env_files point at deploy/ci.env, the four refs at :deadbeef, and
#      every value in deploy/ci.env is visibly fake.
#   G. no floating tag in docker-compose.yml (:latest, :2-alpine), no build
#      key, and caddy's exact version equals the one CI's lint job validates
#      the Caddyfiles with and the one deploy.env.example hashes with.
#   H. the json-file 10m x 3 logging block on all five long-running services,
#      caddy included (it had none, and a proxy's per-request line grows
#      without bound under the daemon's default driver).

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."

base=deploy/docker-compose.yml
local_yml=deploy/docker-compose.local.yml
ci_yml=deploy/docker-compose.ci.yml
ci_env=deploy/ci.env
harness_env=deploy/local.env
workflow=.github/workflows/ci.yml
example_env=deploy/deploy.env.example

pass=0
fail=0
ok() { printf '  ok    %s\n' "$1"; pass=$((pass + 1)); }
no() { printf '  FAIL  %s -- %s\n' "$1" "$2"; fail=$((fail + 1)); }
finish() {
	printf '\n  %d passed, %d failed\n\n' "$pass" "$fail"
	[ "$fail" -eq 0 ] || exit 1
}

if ! docker compose version >/dev/null 2>&1; then
	echo "docker compose is not available -- this test renders the compose files and cannot run without it"
	exit 1
fi

# A missing file is a failure, not an early exit: every assertion below that
# reads it fails on its own and says so, so one run reports the whole picture.
for f in "$base" "$local_yml" "$ci_yml" "$ci_env" "$harness_env" "$workflow" "$example_env" Makefile; do
	[ -f "$f" ] || no "$f" "missing"
done

scratch=$(mktemp -d "${TMPDIR:-/tmp}/compose-tags-test.XXXXXX")
trap 'rm -rf "$scratch"' EXIT

# The dummy the live profile's two required env_files are pointed at. Three
# visibly fake values, in a directory that disappears when this exits.
dummy="$scratch/live.env"
printf '%s\n' 'ALPACA_API_KEY=test-dummy-not-a-key' \
	'ALPACA_SECRET_KEY=test-dummy-not-a-key' \
	'FRED_API_KEY=test-dummy-not-a-key' >"$dummy"
override="$scratch/live-env-override.yml"
cat >"$override" <<YAML
services:
  ohcamel-live:
    env_file: !override
      - path: $dummy
        required: true
  ohcamel-research:
    env_file: !override
      - path: $dummy
        required: true
YAML

# The harness env without its OHCAMEL_TAG line, for the unset case: every
# other \${VAR:?} in the base file (the hostnames, the ACME contact, the
# basic-auth pair) stays satisfied, so the only thing that can fail is the tag.
notag_env="$scratch/no-tag.env"
grep -v '^OHCAMEL_TAG=' "$harness_env" >"$notag_env"

# render OUT ERR [compose args...]: `docker compose <args> config <flags>`
# with the shell's own OHCAMEL_TAG and OHCAMEL_IMAGE deliberately NOT
# inherited -- each case sets what it means to set through RENDER_ENV, and
# flags that belong after `config` go through RENDER_CONFIG_ARGS -- stdout
# and stderr to files, exit status returned.
RENDER_ENV=()
RENDER_CONFIG_ARGS=()
render() {
	local out="$1" err="$2"
	shift 2
	# ${arr[@]+"${arr[@]}"}: an empty array under `set -u` is an error on
	# bash 3.2 (macOS), fine on 5.x (the runner); this form is both.
	env -u OHCAMEL_TAG -u OHCAMEL_IMAGE ${RENDER_ENV[@]+"${RENDER_ENV[@]}"} \
		docker compose "$@" config ${RENDER_CONFIG_ARGS[@]+"${RENDER_CONFIG_ARGS[@]}"} >"$out" 2>"$err"
}

# service_block RENDER SERVICE: the lines of one service's rendered block,
# i.e. everything under `  SERVICE:` (two-space indent, under `services:`)
# up to the next two-space key. Rendered YAML is regular enough for awk.
service_block() {
	awk -v svc="$2" '
		/^services:$/ { in_services = 1; next }
		in_services && /^[^ ]/ { in_services = 0 }
		in_services && $0 == "  " svc ":" { in_block = 1; next }
		in_block && /^  [^ ]/ { in_block = 0 }
		in_block { print }
	' "$1"
}

# images RENDER: the image refs in a render, one per line, sorted.
images() { grep -E '^    image: ' "$1" | sed 's/^    image: //' | sort; }

expect_images() {
	local label="$1" render="$2" expected="$3" got
	got=$(images "$render")
	if [ "$got" = "$expected" ]; then
		ok "$label"
	else
		no "$label" "rendered images were: $(echo "$got" | tr '\n' ' ')"
	fi
}

# --- A. base + live at a fixed tag: the three ghcr refs, caddy pinned, no build ---
A="$scratch/A.yml"
RENDER_ENV=(OHCAMEL_TAG=deadbeef)
if render "$A" "$scratch/A.err" --env-file "$harness_env" -f "$base" -f "$override" --profile live; then
	ok "A1 base + live renders at OHCAMEL_TAG=deadbeef"
	# 5 = caddy + ohcamel-quant + ohcamel-hostd (default profile) + ohcamel-live + ohcamel-research (live).
	expect_images "A2 the five live-profile images are caddy 2.11.4 and the four ghcr refs at :deadbeef" "$A" \
		"$(printf '%s\n' caddy:2.11.4-alpine \
			ghcr.io/ajaiupadhyaya/ohcamel-hostd:deadbeef \
			ghcr.io/ajaiupadhyaya/ohcamel-quant:deadbeef \
			ghcr.io/ajaiupadhyaya/ohcamel-research:deadbeef \
			ghcr.io/ajaiupadhyaya/ohcamel:deadbeef | sort)"
	if grep -qE '^\s*build:' "$A"; then
		no "A3 the base render has no build key" "found: $(grep -nE '^\s*build:' "$A" | tr '\n' ' ')"
	else
		ok "A3 the base render has no build key"
	fi
	if grep -q ':latest' "$A"; then
		no "A4 the base render names no :latest" "found: $(grep -n ':latest' "$A" | tr '\n' ' ')"
	else
		ok "A4 the base render names no :latest"
	fi
else
	no "A1 base + live renders at OHCAMEL_TAG=deadbeef" "$(head -c 400 "$scratch/A.err" | tr '\n' ' ')"
fi

# --- B. the demo engine is the same image as the live engine ---
B="$scratch/B.yml"
if render "$B" "$scratch/B.err" --env-file "$harness_env" -f "$base" --profile demo; then
	got=$(service_block "$B" ohcamel-demo | grep -E '^    image: ' | sed 's/^    image: //')
	if [ "$got" = "ghcr.io/ajaiupadhyaya/ohcamel:deadbeef" ]; then
		ok "B1 ohcamel-demo runs the same engine ref, ghcr.io/ajaiupadhyaya/ohcamel:deadbeef"
	else
		no "B1 ohcamel-demo runs the same engine ref" "got '$got'"
	fi
else
	no "B1 base + demo renders" "$(head -c 400 "$scratch/B.err" | tr '\n' ' ')"
fi

# --- C. OHCAMEL_IMAGE re-points the engine, and only the engine ---
C="$scratch/C.yml"
RENDER_ENV=(OHCAMEL_TAG=deadbeef OHCAMEL_IMAGE=registry.example/mirror/ohcamel)
if render "$C" "$scratch/C.err" --env-file "$harness_env" -f "$base" -f "$override" --profile live; then
	expect_images "C1 OHCAMEL_IMAGE moves the engine ref alone; research, quant and hostd stay on ghcr" "$C" \
		"$(printf '%s\n' caddy:2.11.4-alpine \
			ghcr.io/ajaiupadhyaya/ohcamel-hostd:deadbeef \
			ghcr.io/ajaiupadhyaya/ohcamel-quant:deadbeef \
			ghcr.io/ajaiupadhyaya/ohcamel-research:deadbeef \
			registry.example/mirror/ohcamel:deadbeef | sort)"
else
	no "C1 base + live renders with OHCAMEL_IMAGE set" "$(head -c 400 "$scratch/C.err" | tr '\n' ' ')"
fi

# --- D. no OHCAMEL_TAG: refused, and the message says who sets it ---
D="$scratch/D.yml"
RENDER_ENV=()
if render "$D" "$scratch/D.err" --env-file "$notag_env" -f "$base" -f "$override" --profile live; then
	no "D1 an unset OHCAMEL_TAG is refused" "the render exited 0 with images: $(images "$D" | tr '\n' ' ')"
else
	ok "D1 an unset OHCAMEL_TAG is refused"
	if grep -q 'required variable OHCAMEL_TAG is missing a value: deploy.sh sets OHCAMEL_TAG' "$scratch/D.err"; then
		ok "D2 the refusal carries the :? message 'deploy.sh sets OHCAMEL_TAG'"
	else
		no "D2 the refusal carries the :? message 'deploy.sh sets OHCAMEL_TAG'" "stderr: $(head -c 400 "$scratch/D.err" | tr '\n' ' ')"
	fi
	# One interpolation error per image reference that reads the tag. Four
	# references are written (the engine anchor's, ohcamel-research's,
	# ohcamel-quant's, ohcamel-hostd's); Compose v5.5.1 reports more -- the
	# anchor expanded into ohcamel-demo and ohcamel-live, the fixed refs, and
	# the x-engine extension field itself -- so `at least 4`, not a count
	# that moves with how a compose version reports an anchor.
	# Older Compose (the v2 line on GitHub's runners) stops at the first
	# interpolation error, so the render proves the refusal and the source
	# proves the count: every image reference in the base file reads the tag.
	n=$(grep -c 'error while interpolating .*OHCAMEL_TAG' "$scratch/D.err" || true)
	refs=$(grep -cE '^\s*image:.*\$\{OHCAMEL_TAG' "$base" || true)
	untagged=$(grep -E '^\s*image:.*ghcr\.io/ajaiupadhyaya/' "$base" | grep -vc 'OHCAMEL_TAG' || true)
	if [ "$n" -ge 1 ] && [ "$refs" -ge 4 ] && [ "$untagged" -eq 0 ]; then
		ok "D3 every image reference reads OHCAMEL_TAG ($refs references, $n interpolation error(s) reported)"
	else
		no "D3 every image reference reads OHCAMEL_TAG" "$n interpolation error(s), $refs tagged references (at least 4), $untagged ghcr references without the tag"
	fi
fi

# --- E. the harness override: the build blocks, at tag local ---
E="$scratch/E.yml"
RENDER_ENV=(OHCAMEL_TAG=local)
if render "$E" "$scratch/E.err" --env-file "$harness_env" -f "$base" -f "$local_yml" -f "$override" --profile demo --profile live; then
	ok "E1 base + local renders with both profiles"
	# 4 = one Dockerfile per image: deploy/Dockerfile (the engine, shared by
	# ohcamel-demo and ohcamel-live), deploy/research.Dockerfile, quant/Dockerfile,
	# native/hostd/Dockerfile.
	got=$(grep -E '^\s*dockerfile: ' "$E" | sed 's/^ *dockerfile: //' | sort -u)
	if [ "$got" = "$(printf '%s\n' deploy/Dockerfile deploy/research.Dockerfile native/hostd/Dockerfile quant/Dockerfile)" ]; then
		ok "E2 exactly four Dockerfiles are built: deploy/Dockerfile, deploy/research.Dockerfile, native/hostd/Dockerfile, quant/Dockerfile"
	else
		no "E2 exactly four Dockerfiles are built" "rendered dockerfiles: $(echo "$got" | tr '\n' ' ')"
	fi
	# 5 services carry a build block (2 engine services + research + quant + hostd); caddy never.
	for svc in ohcamel-demo ohcamel-live ohcamel-research ohcamel-quant ohcamel-hostd; do
		if service_block "$E" "$svc" | grep -qE '^    build:'; then
			ok "E3 $svc has a build block in the harness"
		else
			no "E3 $svc has a build block in the harness" "none rendered"
		fi
	done
	if service_block "$E" caddy | grep -qE '^    build:'; then
		no "E3 caddy has no build block" "one rendered"
	else
		ok "E3 caddy has no build block"
	fi
	# 6 image lines with both profiles on: caddy, ohcamel-quant, ohcamel-hostd,
	# ohcamel-demo, ohcamel-live, ohcamel-research -- the engine ref twice, once
	# per engine service, because both expand the same anchor.
	expect_images "E4 the harness runs the four ghcr refs at :local and caddy 2.11.4" "$E" \
		"$(printf '%s\n' caddy:2.11.4-alpine \
			ghcr.io/ajaiupadhyaya/ohcamel-hostd:local \
			ghcr.io/ajaiupadhyaya/ohcamel-quant:local \
			ghcr.io/ajaiupadhyaya/ohcamel-research:local \
			ghcr.io/ajaiupadhyaya/ohcamel:local \
			ghcr.io/ajaiupadhyaya/ohcamel:local | sort)"
else
	no "E1 base + local renders with both profiles" "$(head -c 400 "$scratch/E.err" | tr '\n' ' ')"
fi
# The tag the Makefile builds must be the tag compose runs, or `make
# deploy-up --no-build` starts on an image that does not exist. Three places
# name it and they are read as text here: deploy/local.env (for a hand-run
# `docker compose --env-file deploy/local.env ...`), the Makefile's LOCAL_TAG
# (its -t tags and the OHCAMEL_TAG= prefix on LOCAL_COMPOSE), and this test.
if grep -qxE 'OHCAMEL_TAG=local' "$harness_env"; then
	ok "E5 deploy/local.env carries OHCAMEL_TAG=local"
else
	no "E5 deploy/local.env carries OHCAMEL_TAG=local" "$(grep -n 'OHCAMEL_TAG' "$harness_env" || echo 'no OHCAMEL_TAG line')"
fi
if grep -qxE 'LOCAL_TAG *:= *local' Makefile; then
	ok "E5 Makefile defines LOCAL_TAG := local"
else
	no "E5 Makefile defines LOCAL_TAG := local" "$(grep -n 'LOCAL_TAG' Makefile | head -3 || echo 'no LOCAL_TAG')"
fi
for want in '-t ghcr.io/ajaiupadhyaya/ohcamel:$(LOCAL_TAG) .' \
	'-t ghcr.io/ajaiupadhyaya/ohcamel-quant:$(LOCAL_TAG) .' \
	'-t ghcr.io/ajaiupadhyaya/ohcamel-hostd:$(LOCAL_TAG) .' \
	'OHCAMEL_TAG=$(LOCAL_TAG) docker compose'; do
	if grep -qF -- "$want" Makefile; then
		ok "E5 Makefile carries '$want'"
	else
		no "E5 Makefile carries '$want'" "not found"
	fi
done

# --- F. the image job's override: no build, its own dummy env, the loaded sha tags ---
n=$(grep -c 'build:' "$ci_yml" || true)
if [ "$n" -eq 0 ]; then
	ok "F1 grep -c 'build:' $ci_yml is 0"
else
	no "F1 grep -c 'build:' $ci_yml is 0" "it is $n"
fi
F="$scratch/F.yml"
RENDER_ENV=(OHCAMEL_TAG=deadbeef)
if render "$F" "$scratch/F.err" --env-file "$harness_env" -f "$base" -f "$ci_yml"; then
	ok "F2 base + ci renders, default profile"
else
	no "F2 base + ci renders, default profile" "$(head -c 400 "$scratch/F.err" | tr '\n' ' ')"
fi
# No scratch override here: the ci override must supply its own env_file for
# the two services that require one, or this render fails on the missing
# /etc/ohcamel/live.env exactly as the droplet's base file would.
FL="$scratch/FL.yml"
if render "$FL" "$scratch/FL.err" --env-file "$harness_env" -f "$base" -f "$ci_yml" --profile live; then
	ok "F2 base + ci renders, live profile, with no /etc/ohcamel and no scratch override"
	expect_images "F3 the ci override runs caddy 2.11.4 and the four ghcr refs at :deadbeef" "$FL" \
		"$(printf '%s\n' caddy:2.11.4-alpine \
			ghcr.io/ajaiupadhyaya/ohcamel-hostd:deadbeef \
			ghcr.io/ajaiupadhyaya/ohcamel-quant:deadbeef \
			ghcr.io/ajaiupadhyaya/ohcamel-research:deadbeef \
			ghcr.io/ajaiupadhyaya/ohcamel:deadbeef | sort)"
	if grep -qE '^\s*build:' "$FL"; then
		no "F3 the ci render has no build key" "found: $(grep -nE '^\s*build:' "$FL" | tr '\n' ' ')"
	else
		ok "F3 the ci render has no build key"
	fi
else
	no "F2 base + ci renders, live profile, with no /etc/ohcamel and no scratch override" "$(head -c 400 "$scratch/FL.err" | tr '\n' ' ')"
fi
# Which file each service reads, two ways. `config --no-env-resolution`
# keeps the env_file entries in the render instead of inlining their values,
# so the path itself is visible; and the plain render, which does inline
# them, must show the dummy's three values on both services -- a proof that
# holds on a host where /etc/ohcamel/live.env exists too (a real file read
# by mistake fails the equality without this test ever printing its value).
FE="$scratch/FE.yml"
RENDER_CONFIG_ARGS=(--no-env-resolution)
if render "$FE" "$scratch/FE.err" --env-file "$harness_env" -f "$base" -f "$ci_yml" --profile live; then
	for svc in ohcamel-live ohcamel-research; do
		block=$(service_block "$FE" "$svc")
		if echo "$block" | grep -q '/etc/ohcamel/live.env'; then
			no "F4 $svc reads deploy/ci.env under the ci override, not /etc/ohcamel/live.env" "still names /etc/ohcamel/live.env"
		elif echo "$block" | grep -qE '(path: |- )[^ ]*/deploy/ci\.env$'; then
			ok "F4 $svc reads deploy/ci.env under the ci override, not /etc/ohcamel/live.env"
		elif ! echo "$block" | grep -q 'env_file' && [ "$(echo "$block" | grep -c 'ci-dummy-not-a-key')" -ge 3 ]; then
			# Compose v2 inlines env_file values even under --no-env-resolution;
			# the three dummies can only have come from deploy/ci.env.
			ok "F4 $svc reads deploy/ci.env under the ci override (inlined by this Compose)"
		else
			no "F4 $svc reads deploy/ci.env under the ci override" "no env_file path ending in deploy/ci.env in its block"
		fi
	done
else
	no "F4 base + ci renders with --no-env-resolution" "$(head -c 400 "$scratch/FE.err" | tr '\n' ' ')"
fi
RENDER_CONFIG_ARGS=()
if [ -s "$FL" ]; then
	for svc in ohcamel-live ohcamel-research; do
		block=$(service_block "$FL" "$svc")
		# 3 = the three keys deploy/ci.env carries, each resolved to the dummy.
		n=$(echo "$block" | grep -cE '^      (ALPACA_API_KEY|ALPACA_SECRET_KEY|FRED_API_KEY): ci-dummy-not-a-key$' || true)
		if [ "$n" -eq 3 ]; then
			ok "F4 $svc resolves the three keys to deploy/ci.env's dummy values"
		else
			no "F4 $svc resolves the three keys to deploy/ci.env's dummy values" "$n of 3 resolved to ci-dummy-not-a-key (values not printed)"
		fi
	done
fi
# Every value in the committed dummy is visibly fake: a real key can never be
# committed through this file by accident.
bad=$(grep -vE '^\s*(#|$)' "$ci_env" | grep -vE '^[A-Z_]+=ci-dummy-not-a-key$' || true)
if [ -z "$bad" ]; then
	ok "F5 every value in $ci_env is ci-dummy-not-a-key"
else
	no "F5 every value in $ci_env is ci-dummy-not-a-key" "line(s) not of that form: $(echo "$bad" | cut -d= -f1 | tr '\n' ' ')"
fi

# --- G. nothing floats in docker-compose.yml, and caddy's pin agrees everywhere ---
if grep -nE ':latest|:2-alpine' "$base" >"$scratch/G.txt"; then
	no "G1 no floating tag (:latest, :2-alpine) in $base" "$(tr '\n' ' ' <"$scratch/G.txt")"
else
	ok "G1 no floating tag (:latest, :2-alpine) in $base"
fi
if grep -n 'build:' "$base" >"$scratch/G2.txt"; then
	no "G2 grep -n 'build:' $base is empty" "$(tr '\n' ' ' <"$scratch/G2.txt")"
else
	ok "G2 grep -n 'build:' $base is empty"
fi
compose_caddy=$(grep -oE 'image: caddy:[0-9A-Za-z.+_-]+' "$base" | sed 's/^image: //' | sort -u)
lint_caddy=$(grep -oE '^\s*caddy_image=caddy:[0-9A-Za-z.+_-]+' "$workflow" | sed 's/^ *caddy_image=//' | sort -u)
example_caddy=$(grep -oE 'docker run --rm caddy:[0-9A-Za-z.+_-]+ caddy hash-password' "$example_env" | sed -E 's/^docker run --rm //; s/ caddy hash-password$//' | sort -u)
if [ "$compose_caddy" = "caddy:2.11.4-alpine" ]; then
	ok "G3 compose runs caddy:2.11.4-alpine, exactly once"
else
	no "G3 compose runs caddy:2.11.4-alpine, exactly once" "found: '$(echo "$compose_caddy" | tr '\n' ' ')'"
fi
if [ "$lint_caddy" = "$compose_caddy" ]; then
	ok "G4 the lint job validates the Caddyfiles with the caddy compose runs ($lint_caddy)"
else
	no "G4 the lint job validates the Caddyfiles with the caddy compose runs" "lint: '$lint_caddy', compose: '$compose_caddy'"
fi
if [ "$example_caddy" = "$compose_caddy" ]; then
	ok "G5 deploy.env.example hashes the password with the caddy compose runs ($example_caddy)"
else
	no "G5 deploy.env.example hashes the password with the caddy compose runs" "example: '$example_caddy', compose: '$compose_caddy'"
fi

# --- H. json-file, 10m x 3, on every long-running service, caddy included ---
if [ -s "$A" ]; then
	for svc in caddy ohcamel-quant ohcamel-hostd ohcamel-live ohcamel-research; do
		block=$(service_block "$A" "$svc")
		if echo "$block" | grep -q 'driver: json-file' \
			&& echo "$block" | grep -qE 'max-size: "?10m"?' \
			&& echo "$block" | grep -qE 'max-file: "?3"?'; then
			ok "H1 $svc logs to json-file, 10m x 3"
		else
			no "H1 $svc logs to json-file, 10m x 3" "its rendered block lacks driver/max-size/max-file"
		fi
	done
else
	no "H1 logging blocks" "no base render to read (A1 failed)"
fi

finish
