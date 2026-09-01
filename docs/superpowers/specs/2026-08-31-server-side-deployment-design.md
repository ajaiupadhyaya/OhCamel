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

### The failure mode this design was most afraid of, and what testing it found

The fear, as written before any of this was built: Caddy buffers proxied
response bodies by default, and a buffered SSE stream produces a dashboard that
loads correctly, renders one snapshot from `/api/snapshot`, and then never moves
again. It looks exactly like an engine bug. It is not one, and the whole
architectural claim of the project becomes unfalsifiable from the outside
precisely where it is most visible.

`flush_interval -1` on both `reverse_proxy` blocks was the answer, plus an
exclusion keeping the stream out of `encode`.

**Both turned out to be unnecessary, and finding that out was worth the hour.**
Three deliberate misconfigurations were run against the local stack with the
smoke suite watching:

| Deliberate break | Result |
|---|---|
| `flush_interval 30s` — longer than the entire probe window | still streamed, 52 frames over 20s |
| `encode` applied to `/api/stream`, client requesting gzip | still streamed, 52 frames over 20s |
| nginx with `proxy_buffering on` substituted for Caddy | still streamed, 51 frames over 20s |

Caddy special-cases responses whose `Content-Type` is `text/event-stream` and
flushes them immediately regardless of `flush_interval`; its `encode` module
does the same; nginx forwards available data rather than waiting for a full
buffer. The directives stay as defence in depth — the auto-detection keys off
the Content-Type, so a future route streaming something that is not
`text/event-stream` would get no protection, and an explicit `-1` documents the
requirement for anyone who swaps the proxy for one less careful. But the spec
should not go on claiming they are what holds the dashboard up.

Correcting this mattered for the smoke suite too. The probe originally did not
send `Accept-Encoding`, which a browser always does — so it was not exercising
the compression path it was written to protect. It now runs `--compressed`.

**What the stream assertion does catch**, verified by pausing the engine
container mid-run: an engine that has died, wedged, or stopped ticking. All four
assertions failed and the suite exited 1. That is the outcome that matters — a
deploy must never report success while serving a dashboard that never moves —
and it is the reason `deploy.sh` treats a smoke failure as a failed deploy
rather than a warning.

The health route returning 200 remains necessary and proves almost nothing;
`nodes_recomputed` advancing between two reads is the assertion that proves the
graph is alive.

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

**A question that was open, and how the owner answered it.** As first written,
these were the same Alpaca keys the quant-trading system used on the M4, and
that was the concern: Alpaca does not issue market-data-only credentials, so a
key that can read bars can place orders on that paper account, and sharing one
pair across an internet-facing host and a laptop widens exposure and splits
rotation. Three ways out were offered — reuse the keys, open a second paper
account with its own pair, or ship live mode with FRED only.

**Decided 2026-09-01: reuse them.** The other system is no longer active, so
the keys now serve exactly one consumer and the two objections dissolve with
it. There is no second machine to split rotation across, and the free tier's
one-stream-per-account limit — which would have made the two systems fight
over the feed and 406 the loser — has nothing left to contend with. The blast
radius remains paper. If that other system is ever revived, this decision
should be revisited before it reconnects, because the contention comes back
with it.

The domain is likewise settled: `ajaiupadhyaya.com` is the owner's, hosts
their portfolio site at the apex, and takes two new A records for the
subdomains above. Nothing about the apex or `www` is touched.

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

1. `/` renders.
2. `/api/health` returns 200.
3. `/api/snapshot` parses, has positions and a numeric `gross_exposure`, and
   its `nodes_recomputed` **advances** across two reads two seconds apart —
   a frozen graph serves valid JSON forever, so the counter is the real test.
4. `/api/stream` delivers ≥2 *distinct* frames **spread across** the window,
   with the client requesting compression as a browser does.
5. Port 80 redirects to 443, and the certificate is valid. *(production only)*
6. `:8080` and `:8081` are unreachable from outside. *(production only)*
7. The live host returns 401 without credentials. *(production only)*

Note the field name: the engine emits `gross_exposure`, not `gross_notional`.
The first draft of the suite asserted on the latter and failed against a
perfectly healthy engine.

Exit non-zero on any failure, and print which assertion failed rather than a
stack trace.

## Build order

| Phase | Work | Blocked on |
|---|---|---|
| 1 | `Dockerfile`, `docker-compose.yml`, `Caddyfile`, `.dockerignore`, `smoke.sh`, Makefile targets — verified against local Docker | nothing |
| 2 | Droplet created and provisioned | a DigitalOcean API token |
| 3 | DNS records, TLS issuance, the demo host live | a droplet IP |
| 4 | The live service and its secrets | ~~the Alpaca key decision above~~ — decided, reuse; see §Secrets |
| 5 | Smoke suite green against production, README section, ops notes | 1–4 |

Phase 1 is the majority of the work and needs nothing from anyone.
