# OhCamel — the server side

## Problem

The engine runs on one laptop. `make demo` starts a dashboard on `localhost:8080`
and it dies when the terminal closes. Everything the README claims — that the
stream is driven by observer callbacks rather than a timer, that an idle book
costs nothing, that a stale symbol is visible rather than theoretical — is
claimed to a reader who cannot watch it happen.

A recruiter at a trading firm will not clone an OCaml repository, install a
5.2.1 opam switch, resolve OpenBLAS, and run `make demo`. They will click a
link or they will not look. The project's own thesis is about a system that is
watched continuously; it has never been watched by anyone but its author, and
never for longer than a terminal session.

There is no deployment surface in the repository at all: no Dockerfile, no
process supervision, no TLS, no reverse proxy, no CI leg that deploys anything.

## Goal

Two URLs that stay up.

- `ohcamel.ajaiupadhyaya.com` — the synthetic demo, public, no credentials.
  Works at three in the morning on a Sunday, because the feed is generated
  rather than received. This is the link that goes on a resume.
- `live.ohcamel.ajaiupadhyaya.com` — the same engine against Alpaca and FRED,
  behind a password. Real prints, real ten-year yield, real staleness.

Both over HTTPS, both restarting on their own after a reboot or a crash, both
redeployable with one command.

## Non-goals

- **Horizontal scale.** One droplet. The engine holds its graph in memory and
  there is exactly one book; a second replica would be a second, differently
  aged copy of the same state, which is worse than no replica.
- **A database.** `History_buffer` is a bounded in-memory ring by design. Making
  the trail durable is a different project with a different argument behind it.
- **Any mutating route.** The server has none today — the kill switch is wired
  to nothing on purpose — and deployment must not become the reason one appears.
- **Trading.** Live mode reads market data. It does not send orders and the
  deployment must not make that easier.

## Constraints

- **The dependency tree is heavy.** `owl` needs OpenBLAS and LAPACKE; `async_ssl`
  needs OpenSSL and libffi; `mirage-crypto-rng` pulls zarith, which needs GMP.
  These are C libraries, and getting them right once and never again is the
  single largest argument for containerising rather than provisioning a switch.
- **The build machine is arm64, the target is amd64.** Cross-compiling an OCaml
  tree this size through emulation is slow enough to be a deterrent to
  deploying, and a deploy step you avoid is a deploy step that rots.
- **The dashboard is self-contained.** Inline SVG, no CDN, and both of its
  network calls — `fetch("/api/history")` and `EventSource("/api/stream")` — are
  root-relative. This rules *in* a subdomain per mode and rules *out* a path
  prefix, which would need rewriting on both sides for no gain.
- **Credentials already exist and are already in use.** The three variables live
  mode wants are the same three the quant-trading system uses. See §Secrets.

## Architecture

```
                    Porkbun DNS
       ohcamel.ajaiupadhyaya.com ─┐
  live.ohcamel.ajaiupadhyaya.com ─┤ A → droplet IP
                                  ▼
                    ┌─────────── DROPLET (nyc3) ───────────┐
                    │  ufw: 22, 80, 443 only               │
                    │                                       │
   :443 ────────────┼──►  Caddy  (auto Let's Encrypt)      │
                    │       │                               │
                    │       ├─ ohcamel.* ────► demo :8080  │  public
                    │       │                  (synthetic)  │
                    │       └─ live.ohcamel.* ► serve :8081 │  basic auth
                    │            + basicauth    (Alpaca+FRED)│
                    │                              ▲         │
                    │            /etc/ohcamel/live.env (0600)│
                    └───────────────────────────────────────┘
```

Only Caddy publishes ports. The two engine containers sit on an internal Docker
network and are unreachable from the internet except through the proxy, which
means the basic-auth on the live host cannot be walked around by addressing
`:8081` directly.

### Components

All new, all under `deploy/`. Nothing in `lib/` or `bin/` changes: the server
already emits the correct SSE headers and already binds `0.0.0.0`.

| File | Purpose |
|---|---|
| `Dockerfile` | Multi-stage. Builder from `ocaml/opam:debian-12-ocaml-5.2`; runtime from `debian:12-slim` carrying only the shared objects the binary actually loads. |
| `docker-compose.yml` | `caddy`, `ohcamel-demo`, `ohcamel-live`. Restart policies, healthchecks, the internal network, the secrets mount. |
| `Caddyfile` | TLS, the two hosts, basic-auth on one, and `flush_interval -1` on both. |
| `provision.sh` | First boot, idempotent: docker, ufw, fail2ban, unattended-upgrades, swap, a non-root deploy user. |
| `deploy.sh` | On the droplet: pull, build, up. |
| `smoke.sh` | Verification that the deployment did not break the thing the project is about. |
| `.dockerignore` | `_build/`, `_opam/`, `.venv/`, `.git/`, and every `.env`. |

### The build

Two stages, and the split matters for more than image size. The builder carries
a full opam switch and the `-dev` headers for four C libraries — roughly two
gigabytes that have no business being on a running server, where they are
attack surface rather than capability. The runtime stage installs only the
`.so`s and copies one statically-linked-except-for-stubs executable in.

The risk in that split is getting the runtime library list wrong: a missing
shared object does not fail the build, it fails the first time the container
starts, and it fails as a crash loop rather than a diagnosis. So the runtime
stage runs `ldd` on the binary at build time and fails the build if any entry
resolves to `not found`. A missing dependency becomes a build error with a name
in it instead of a restart loop at two in the morning.

Layer caching is ordered so that `dune-project` and `ohcamel.opam` are copied
and `opam install --deps-only` runs *before* the source is copied. Editing
`lib/graph.ml` then rebuilds in a minute rather than twenty.

### The failure mode this design is most afraid of

Caddy buffers proxied responses by default. A buffered SSE stream produces a
dashboard that loads correctly, renders one snapshot from `/api/snapshot`, and
then never moves again. It looks exactly like an engine bug. It is not one, and
the whole architectural claim of the project — that the stream is reactive —
becomes unfalsifiable from the outside precisely when it is most visible.

`flush_interval -1` on both `reverse_proxy` blocks disables that buffering. But
a configuration line is a claim, and this project's habit is to test its claims:
`smoke.sh` holds `/api/stream` open and asserts that **at least two distinct
frames arrive within thirty seconds**. A deployment that serves a beautiful,
frozen dashboard fails the smoke test.

The health route returning 200 is necessary and proves almost nothing.

## Secrets

`live.env` lives at `/etc/ohcamel/live.env`, root-owned, mode `0600`, mounted
read-only into the live container only. It is never in git, never in the Docker
build context (`.dockerignore` excludes `.env*`), and never baked into a layer —
an image layer is forever, and a leaked key in layer three survives every
subsequent `rm`.

Six variables, three of them required:

| Variable | Required | Note |
|---|---|---|
| `ALPACA_API_KEY` | yes | market data |
| `ALPACA_SECRET_KEY` | yes | |
| `FRED_API_KEY` | yes | the macro factor series |
| `OHCAMEL_ALPACA_FEED` | no | defaults to `iex` |
| `OHCAMEL_FRED_SERIES` | no | defaults to `DGS10` |
| `OHCAMEL_LOG_LEVEL` | no | |

**An open question, and it is the owner's to answer.** These are the same Alpaca
keys the quant-trading system uses on the M4. Alpaca does not issue
market-data-only credentials: a key that can read bars can place orders on that
paper account. Putting them on an internet-facing host widens their exposure and
splits their rotation across two machines. Three ways out — reuse them (it is a
paper account, and the blast radius is paper), open a second Alpaca paper
account with its own pair (clean, and the recommendation), or ship live mode
with FRED only and let Alpaca stay home. This is deferred to Phase 4; Phases 1–3
do not touch it.

## Sizing

`s-2vcpu-4gb`, Ubuntu 24.04 LTS, nyc3. Twenty-four dollars a month against
credits.

Four gigabytes is chosen for the *build*, not the runtime. Compiling `owl`
alongside Jane Street's `core` and `async` is memory-hungry in a way the
steady-state process is not; both engines together should sit comfortably under
five hundred megabytes resident. The two-gigabyte swapfile in `provision.sh` is
insurance for the build, not a plan for the runtime — a server that is swapping
while serving is a server that is already wrong.

## Testing

Deployment has no unit tests worth writing; what it has is verification that
runs against the real thing after every deploy. `smoke.sh` asserts, in order:

1. Port 80 redirects to 443.
2. The certificate is valid and matches the host.
3. `/api/health` returns 200.
4. `/api/snapshot` parses as JSON and carries a non-null `gross_notional`.
5. `/api/stream` delivers ≥2 distinct frames inside 30 seconds. *(the one that matters)*
6. The live host returns 401 without credentials.
7. `:8080` and `:8081` are not reachable from outside.

Exit non-zero on any failure, and print which assertion failed rather than a
stack trace.

## Build order

| Phase | Work | Blocked on |
|---|---|---|
| 1 | `Dockerfile`, `docker-compose.yml`, `Caddyfile`, `.dockerignore`, `smoke.sh`, Makefile targets — verified against local Docker | nothing |
| 2 | Droplet created and provisioned | a DigitalOcean API token |
| 3 | DNS records, TLS issuance, the demo host live | a droplet IP |
| 4 | The live service and its secrets | the Alpaca key decision above |
| 5 | Smoke suite green against production, README section, ops notes | 1–4 |

Phase 1 is the majority of the work and needs nothing from anyone.
