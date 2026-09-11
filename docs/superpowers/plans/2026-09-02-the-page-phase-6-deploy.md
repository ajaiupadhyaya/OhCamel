# The page — Phase 6: deploy and measure — Implementation Plan

> **Superseded 2026-09-10.** Not executed as written. Deploying and closing out are items 9–10 of `2026-09-10-the-page-finish-line.md`, with the cuts listed there.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put Phases 1–5 on both droplet hosts, measure on the s-2vcpu box every number the page and the docs assert, and re-date `docs/status.md`, `README.md`, `lib/verified.ml` and the two stale spec lines so nothing in the repository is carried forward unmeasured.

**Architecture:** Nothing in `lib/` or `bin/` changes in this phase except the three dated constants in `lib/verified.ml`; everything else is a deploy, a measurement, or a document. The deploy is `git pull --ff-only && deploy/deploy.sh --live` run **on the droplet** as the `ohcamel` user from `~/OhCamel` — `deploy.sh` pulls, creates `book.sexp` from the example if absent, builds the image natively (amd64, so it is not built on the Mac), brings up `caddy` + `ohcamel-demo` + `ohcamel-live` from one compose file, sleeps 15 s and runs `deploy/smoke.sh`. Measurement is taken through the public demo origin, through `docker compose logs`, and through `/api/ops`, `/api/reports` and `/api/reports/garch`; the record of what was measured goes into `docs/status.md` and nowhere else.

**Tech Stack:** OCaml 5.2.1, dune 3.x, Jane Street Core/Async/Incremental, Owl, cohttp-async, Yojson, alcotest + qcheck; vanilla HTML/CSS/JS with inline SVG; Docker + Caddy on the droplet.

**Spec:** docs/superpowers/specs/2026-09-02-the-page-design.md

## Global Constraints

- No mutating route: every route stays GET and read-only, including the scenario suite's "run it again".
- No persistence: nothing is written to disk; reports and the history ring die with the process.
- No external asset: no CDN, no web font, no charting library, no framework.
- The live host's gate is untouched: no Caddy CORS, no `basic_auth` matcher exemption, no `/api/up`.
- No invented vol surface on either deployed book: the options figure stays the CLI's synthetic one-name walk, labelled SYNTHETIC.
- No number this process did not produce styled as if it had: quoted figures keep the `QUOTED · source · hardware · date` tag and the soft ink.
- No second implementation of engine arithmetic: the client derives from served records, never recomputes risk.
- Every named-node recomputation set pinned by `test_graph.ml` stays unchanged.
- The eight invariants in `docs/status.md` (§ *The invariants*, lines 236–243) hold; a change that ships faster by breaking one is a regression even if the tests pass.
- `dune build @fmt` must pass (ocamlformat 0.29.0, `.ocamlformat` in the repo).
- All tests stay hermetic: no network, no credentials, nothing waiting on a clock.
- The byte-identical stdout gate for the six credential-free modes applies wherever `bin/main.ml` is touched (only the `.dockerignore` fallback in Task 5 can touch it).
- Never commit `book.sexp` or any `.env`.
- Never source `deploy/.env` into a shell — bash reads its `$$` as its own pid; `deploy.sh` reads the two hostnames with `sed` for exactly this reason.
- Never delete the `caddy_data` volume: it holds the certificate and the ACME account, and Let's Encrypt rate-limits re-issuance. `docker compose down` is safe; `down -v` is not.
- Never add `ports:` to an engine service — smoke assertion 6 checks 8080/8081 from outside and the firewall will not save it.

---

## Working conventions for this phase

- **Two machines.** Steps marked **[mac]** run in the repository at `/Users/ajaiupadhyaya/Documents/OhCamel`. Steps marked **[droplet]** run over `ssh ohcamel` (the deploy user, repo at `~/OhCamel`) or `ssh ohcamel-root` (root). Never edit files on the droplet: it is a checkout, and the only thing that changes it is `git pull`.
- **Scratch.** Measurements are collected under `/tmp/ohcamel-phase6/` on the Mac and `~/phase6/` on the droplet. Neither is ever committed. Create the Mac one once: `mkdir -p /tmp/ohcamel-phase6`.
- **The record lives in `docs/status.md`.** Not in a new file, not in the plan, not in a commit message. `status.md` is the dated inventory; a measurement that is not in it did not happen.
- **The live host's password** is the owner's and is typed, never stored. Where a step needs it, it is read with `read -rs` into a shell variable for the length of one command.
- **Commit per task**, in this repository's voice: a lowercase area prefix (`deploy:`, `docs:`) then a sentence that says *why*. The executor adds the trailers.

---

### Task 1: The rollback path, written before it is needed

**Files:**
- Modify: `docs/status.md:320-347` (the `## Operating it` section, from `All on the droplet, as the deploy user, from ~/OhCamel.` to the closing `Credentials:` paragraph)
- Test: `grep -n "rollback" docs/status.md` prints the new block; `grep -c "git checkout --detach" docs/status.md` prints `1`

**Interfaces:**
- Consumes: `deploy/deploy.sh` (existing; pulls at line 61, builds at 87, ups at 91, sleeps 15, verifies at 106); `deploy/smoke.sh --expect-sha <sha>` (Phase 2); the `OHCAMEL_GIT_SHA` / `OHCAMEL_BUILT_AT` build args and the per-command env prefix `deploy.sh` uses (Phase 2); `deploy/docker-compose.yml` service names `caddy`, `ohcamel-demo`, `ohcamel-live` and the `live` profile (existing).
- Produces: the exact rollback sequence every later task refers to, and the rule that the previous sha is captured **before** the pull.

- [ ] **Step 1: Read the section you are about to change.** **[mac]**
  ```bash
  sed -n '318,352p' /Users/ajaiupadhyaya/Documents/OhCamel/docs/status.md
  ```
  Expected: the `## Operating it` heading, the fenced block with `provision.sh`, the two `git pull --ff-only && deploy/deploy.sh` lines, the `# look` commands, the `# change the book` note, then the `Things not to do:` and `Credentials:` paragraphs.

- [ ] **Step 2: Establish that `deploy.sh` cannot itself perform a rollback.** **[mac]** This is the reason the sequence below is written out rather than delegated.
  ```bash
  sed -n '59,62p' /Users/ajaiupadhyaya/Documents/OhCamel/deploy/deploy.sh
  ```
  Expected output contains `git -C "$REPO" pull --ff-only` under `say "Pulling"`. Because the script is `set -euo pipefail`, a detached HEAD (or a branch with no upstream) makes that pull fail and the script exits before it builds — so a rollback must reproduce `deploy.sh`'s remaining steps by hand.

- [ ] **Step 3: Add the rollback block to `## Operating it`.** **[mac]** Insert it immediately after the `# change the book without a rebuild` stanza and before the `Things not to do:` paragraph, inside the same fenced block, followed by the prose paragraph below the fence. The text, verbatim:

  Inside the existing fence, appended:
  ```
  # rollback -- capture the sha you are ON before deploy.sh pulls, every time
  PREV=$(git -C ~/OhCamel rev-parse HEAD)
  ```

  And immediately after the fence, a new paragraph and its own fenced block:

  ~~~markdown
  **If the smoke suite fails.** `deploy.sh` cannot roll back: its first step is
  `git pull --ff-only`, which fails on a detached HEAD and, under `set -e`, exits
  before it builds. So the rollback repeats the rest of the script by hand, with
  the sha captured *before* the pull:

  ```
  cd ~/OhCamel
  docker compose -f deploy/docker-compose.yml --profile live down   # never -v
  git checkout --detach "$PREV"
  OHCAMEL_GIT_SHA="$PREV" OHCAMEL_BUILT_AT="$(date -u +%FT%TZ)" \
    docker compose -f deploy/docker-compose.yml build
  docker compose -f deploy/docker-compose.yml --profile live up -d --remove-orphans
  /bin/sleep 15
  deploy/smoke.sh https://ohcamel.ajaiupadhyaya.com \
    --live https://live.ohcamel.ajaiupadhyaya.com --expect-sha "$PREV"
  ```

  Two details that are easy to get wrong. `down` without `-v` keeps `caddy_data`,
  and `-v` would destroy the certificate and the ACME account for a rate-limited
  re-issue. And `up` is run with `--profile live` even when only the demo is
  suspect: `--remove-orphans` removes containers for services outside the active
  profile set, so the demo-only form of the command would take the live host down
  as a side effect while claiming to deploy.

  Coming forward again is `git checkout main && git pull --ff-only &&
  deploy/deploy.sh --live`.
  ~~~

- [ ] **Step 4: Verify the block reads correctly and the fences balance.** **[mac]**
  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && grep -c '^```' docs/status.md && grep -n "rollback\|--remove-orphans\|caddy_data" docs/status.md
  ```
  Expected: the backtick-fence count is even, and the greps show the new block plus the pre-existing `caddy_data` warning in *Things not to do*.

- [ ] **Step 5: Commit.** **[mac]**
  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && git add docs/status.md && git commit -m "docs: a rollback nobody has to invent at two in the morning -- deploy.sh pulls, so it cannot be the way back"
  ```

---

### Task 2: Push, and capture the droplet's pre-deploy state

**Files:**
- Create: `/tmp/ohcamel-phase6/prev.sha` (scratch, never committed)
- Create: `/tmp/ohcamel-phase6/pre-deploy.txt` (scratch, never committed)
- Test: `cat /tmp/ohcamel-phase6/prev.sha` prints a 40-character sha that is **not** the Mac's `git rev-parse HEAD`

**Interfaces:**
- Consumes: ssh aliases `ohcamel` and `ohcamel-root`; `deploy/docker-compose.yml` (project name `ohcamel`); `/etc/ohcamel/live.env` (0640 root:ohcamel); `deploy/.env` on the droplet.
- Produces: `$PREV` — the sha the droplet was running before this phase, which Task 1's rollback and Task 14's honesty note both depend on.

- [ ] **Step 1: Push the phase's work so the droplet can pull it.** **[mac]**
  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && git status -sb && git push origin main
  ```
  Expected: the status line reads `## main...origin/main` with no `??` entries other than `claudecodehandoff.md` (which belongs to another project and is never added), and the push reports the new head. If anything else is uncommitted, stop: the droplet builds from `origin/main` and an unpushed change is a build of the wrong tree.

- [ ] **Step 2: Capture the sha the droplet is on, BEFORE anything pulls.** **[droplet]**
  ```bash
  mkdir -p /tmp/ohcamel-phase6 && ssh ohcamel 'git -C ~/OhCamel rev-parse HEAD' | tee /tmp/ohcamel-phase6/prev.sha
  ```
  Expected: one 40-character sha. This is `$PREV` for the rest of the phase.

- [ ] **Step 3: Record what is running now.** **[droplet]**
  ```bash
  ssh ohcamel 'cd ~/OhCamel && docker compose -f deploy/docker-compose.yml ps && echo "--- images ---" && docker images ohcamel:latest && echo "--- disk ---" && df -h / | tail -1 && echo "--- uptime ---" && uptime' | tee /tmp/ohcamel-phase6/pre-deploy.txt
  ```
  Expected: `caddy` and `ohcamel-demo` (and `ohcamel-live`, if the live profile is up) `running`/`healthy`; one `ohcamel:latest` image; the root filesystem with several GB free — a build needs headroom, and a full disk fails the deploy in a way that reads as a compiler error.

- [ ] **Step 4: Confirm the two credential files are where and how they must be.** **[droplet]**
  ```bash
  ssh ohcamel 'ls -l /etc/ohcamel/live.env; ls -ld /etc/ohcamel; test -r /etc/ohcamel/live.env && echo "readable by $(id -un)"; ls -l ~/OhCamel/deploy/.env'
  ```
  Expected: `-rw-r----- 1 root ohcamel` on `live.env`, `drwxr-x--- root ohcamel` on the directory, the line `readable by ohcamel`, and `deploy/.env` present. **These are the facts Task 13 corrects in the docs** — `0640 root:ohcamel`, not `0600`. Do not `cat` either file and do not source them.

- [ ] **Step 5: Confirm the free disk is enough for a build and note the box.** **[droplet]**
  ```bash
  ssh ohcamel 'nproc && free -m | head -2 && docker system df'
  ```
  Expected: `2` vCPUs; roughly 4 GB total memory with swap present (provision.sh adds it); the Docker disk usage table. The `2` is the number every timing in this phase is *of* — an s-2vcpu droplet, not the M2 Pro the bench table is quoted from.

- [ ] **Step 6: No commit.** This task writes only scratch files. Note `$PREV` in the working log and continue.

---

### Task 3: Deploy both hosts from the droplet

**Files:**
- Create: `/tmp/ohcamel-phase6/deploy.log` (scratch, never committed)
- Test: the tail of the deploy log reads `==> Deployed` and the smoke summary line reads `23 passed, 0 failed, 0 skipped`

**Interfaces:**
- Consumes: `deploy/deploy.sh --live` (existing, extended in Phase 2 to pass `OHCAMEL_GIT_SHA=$(git -C "$REPO" rev-parse HEAD)` and `OHCAMEL_BUILT_AT` as a per-command prefix to `build`, and to invoke `deploy/smoke.sh … --expect-sha "$(git -C "$REPO" rev-parse HEAD)"`); `deploy/docker-compose.yml` build args `OHCAMEL_GIT_SHA` / `OHCAMEL_BUILT_AT` (Phase 2); the `live` profile and `OHCAMEL_PEER_ORIGIN` on the live service (Phase 2).
- Produces: both hosts running the sha at the tip of `origin/main`; the deploy's own stdout, which Task 4 and Task 6 read timings out of.

- [ ] **Step 1: Start the deploy detached on the droplet.** **[droplet]** It must survive this session: a build whose opam layer misses runs twenty minutes, which is longer than any single foreground call here should hold.
  ```bash
  ssh ohcamel 'mkdir -p ~/phase6 && cd ~/OhCamel && setsid nohup bash -c "git pull --ff-only && deploy/deploy.sh --live" > ~/phase6/deploy.log 2>&1 < /dev/null & echo started'
  ```
  Expected: `started`, immediately. Nothing else. (`git pull` first, then the script — a script that replaces itself mid-run keeps executing the old text, which is why `status.md` writes the pull as its own command.)

- [ ] **Step 2: Watch it until it finishes.** **[droplet]** Poll rather than block:
  ```bash
  ssh ohcamel 'tail -5 ~/phase6/deploy.log'
  ```
  Repeat every 30–60 s. Expected progression: `==> Pulling`, `==> Book` (`book.sexp present, leaving it alone`), `==> Building` with dune output, `==> Starting`, `==> Settling`, the `ps` table, `==> Verifying`, the smoke suite, `==> Deployed`. A cached opam layer makes this one to three minutes; a changed `dune-project` or `ohcamel.opam` makes it fifteen to twenty.

- [ ] **Step 3: Pull the whole log back for the record.** **[mac]**
  ```bash
  ssh ohcamel 'cat ~/phase6/deploy.log' > /tmp/ohcamel-phase6/deploy.log && tail -30 /tmp/ohcamel-phase6/deploy.log
  ```
  Expected tail: the smoke suite's `PASS` lines and `  23 passed, 0 failed, 0 skipped` — Phase 2's eighteen (the four original, block 4a's five with `--expect-sha`, the redirect, TLS, the two ports, the five 401s) plus Phase 5's five (`/api/graph`, `/api/reports`, `/api/reports/garch`, `/api/stress`, `/api/history`), the 404 line listing the 11 routes — then `==> Deployed`. If it ends at `==> Deployed, but the smoke suite FAILED`, stop and run Task 1's rollback with `$PREV` from `/tmp/ohcamel-phase6/prev.sha` before doing anything else.

- [ ] **Step 4: Confirm the running containers carry the sha that was just built.** **[droplet]**
  ```bash
  ssh ohcamel 'cd ~/OhCamel && echo "checkout: $(git rev-parse HEAD)" && curl -fsS http://localhost:80 -o /dev/null -w "" ; curl -fsS https://ohcamel.ajaiupadhyaya.com/api/ops | python3 -c "import sys,json; d=json.load(sys.stdin); b=d[\"build\"]; print(\"served: \", b[\"git_sha\"], b[\"git_short\"], b[\"built_at\"], \"uptime_s=\", d[\"uptime_s\"], \"mode=\", d[\"mode\"])"'
  ```
  Expected: `checkout:` and `served:` name the same 40-character sha; `built_at` is an ISO-8601 UTC timestamp minutes old, never the string `unknown`; `uptime_s` is a small integer; `mode` is `demo`. A `git_sha` of `unknown` means the build args did not reach `Build_info` and Phase 2 is not actually deployed — stop and fix that before measuring anything.

- [ ] **Step 5: Confirm the live host is up, gated, and knows its peer.** **[droplet]**
  ```bash
  ssh ohcamel 'cd ~/OhCamel && docker compose -f deploy/docker-compose.yml ps && for p in / /ops /api/ops /api/snapshot /api/health; do printf "%-16s %s\n" "$p" "$(curl -sS -o /dev/null -w "%{http_code}" --max-time 15 https://live.ohcamel.ajaiupadhyaya.com$p)"; done'
  ```
  Expected: three services `running`, the two engines `healthy`; every one of the five paths answers `401`. The gate is site-wide and has no matcher exemptions — that is the constraint, and this is the check of it.

- [ ] **Step 6: Confirm the demo engine's CORS header is on JSON and nowhere else.** **[mac]**
  ```bash
  curl -sSI https://ohcamel.ajaiupadhyaya.com/api/ops | grep -i "access-control-allow-origin"
  curl -sSI https://ohcamel.ajaiupadhyaya.com/ | grep -ci "access-control-allow-origin"
  curl -sS -o /dev/null -D - --max-time 3 -H 'Accept: text/event-stream' https://ohcamel.ajaiupadhyaya.com/api/stream | grep -ci "access-control-allow-origin"
  ```
  Expected: `access-control-allow-origin: *` on `/api/ops`; `0` on `/` and `0` on the stream. (The curl on the stream ends by timeout; only its headers matter.)

- [ ] **Step 7: No commit.** The deploy changes the droplet, not the repository.

---

### Task 4: The first five minutes of logs — the GARCH line, and any Async warning

**Files:**
- Create: `/tmp/ohcamel-phase6/logs-5min.txt` (scratch, never committed)
- Test: `grep -ic garch /tmp/ohcamel-phase6/logs-5min.txt` is ≥ 1 for the demo engine, and the Async/exception grep is empty

**Interfaces:**
- Consumes: Phase 5's container log line emitted when the GARCH domain completes; `docker compose logs` with `--since` and `--timestamps`; service names `caddy`, `ohcamel-demo`, `ohcamel-live`.
- Produces: the GARCH completion timestamp used in Task 6, and the yes/no on Async misbehaviour that the spec's *That GARCH stalls the stream after a deploy* fear asks for.

- [ ] **Step 1: Capture five minutes of every service's log with timestamps.** **[mac]** Run this within five minutes of Task 3's `==> Deployed`; if longer has passed, restart the demo engine first (Task 6 Step 2 does exactly that and this capture can be taken from that restart instead).
  ```bash
  ssh ohcamel 'cd ~/OhCamel && docker compose -f deploy/docker-compose.yml logs --since 5m --timestamps --no-color caddy ohcamel-demo ohcamel-live' > /tmp/ohcamel-phase6/logs-5min.txt && wc -l /tmp/ohcamel-phase6/logs-5min.txt
  ```
  Expected: a few hundred lines, each prefixed with the service name and an RFC-3339 timestamp.

- [ ] **Step 2: Find the GARCH domain's completion line and time it against the engine's banner.** **[mac]**
  ```bash
  grep -in "garch" /tmp/ohcamel-phase6/logs-5min.txt
  grep -n "ohcamel-demo" /tmp/ohcamel-phase6/logs-5min.txt | head -20
  ```
  Expected: exactly one demo-engine line reading `  garch       done -- 180 of 180 fits in <N> ms on a second domain; /api/reports/garch is complete` (Phase 5 Task 9's pinned text, emitted once from `finish` when the domain joins; the live engine prints the same line behind `live_line`'s timestamp column; a `garch       FAILED after <N> ms -- …` line instead is the spec's named fear and stops this task), and the engine's startup banner lines above it. Record both timestamps; their difference is the GARCH wall time as the *container* saw it, which Task 6 cross-checks against `/api/reports/garch.computed_in_ms`.

- [ ] **Step 3: Look for Async complaining.** **[mac]** Async's failure modes here are a monitor catching an exception on the scheduler, a "thread-safe queue" warning from a second domain touching Async's structures, or a backtrace.
  ```bash
  grep -inE "monitor\.ml|uncaught|backtrace|scheduler|thread-safe|Fatal|Failure\(|Assert_failure|Domain" /tmp/ohcamel-phase6/logs-5min.txt
  ```
  Expected: **no output**. Any hit is the spec's named fear coming true — record the exact line, and the fallback is the chunked one-fit-per-scheduler-turn loop the spec keeps in reserve (not built here; it becomes a finding in `status.md` *Next* and a decision for the owner).

- [ ] **Step 4: Look for Caddy complaining.** **[mac]**
  ```bash
  grep -iE "caddy.*(error|warn)" /tmp/ohcamel-phase6/logs-5min.txt | head -20
  ```
  Expected: nothing, or only routine `WARN` lines about HTTP/3 or the ACME cache. A certificate error here is a deploy failure even though the smoke suite passed on the demo host, because the live host's certificate is separate.

- [ ] **Step 5: Confirm the log driver is still bounded.** **[droplet]** The compose file caps each service at 3 × 10 MB; a container logging a line per fit would blow through that.
  ```bash
  ssh ohcamel 'cd ~/OhCamel && docker compose -f deploy/docker-compose.yml logs --tail 0 ohcamel-demo >/dev/null; sudo du -sh /var/lib/docker/containers 2>/dev/null || docker system df -v | head -12'
  ```
  Expected: container log storage in the tens of megabytes, not gigabytes. (If `sudo` prompts, use the `docker system df -v` half only; this is a sanity check, not a gate.)

- [ ] **Step 6: No commit.** These findings are written into `status.md` by Task 11.

---

### Task 5: The `.dockerignore` re-inclusion, verified on the droplet's own Docker

**Files:**
- Modify (only on failure): `.dockerignore:25` (the bare `docs/` line and the `!docs/crisis/*.csv` line Phase 1 added beneath it)
- Modify (only on the deepest fallback): `deploy/Dockerfile:120-121` (the runtime stage's `WORKDIR /app` / `COPY book.example.sexp` pair) and `bin/main.ml` (the crisis mode's `load_all_embedded` call)
- Test: `/api/reports` reports three crisis windows with 631 / 400 / 401 sessions, and `ohcamel backtest-crisis` run inside the runtime image prints all three

**Interfaces:**
- Consumes: Phase 1's `.dockerignore` entry `!docs/crisis/*.csv`, the `lib/dune` rule that builds `crisis_csv.ml` from `docs/crisis/*.csv`, and `Crisis_data.embedded` / `load_all_embedded`; Phase 5's `/api/reports` field `validation.crisis.windows[].{name, sessions, forecasts}`.
- Produces: the verdict on the spec's *Deployment changes* first bullet — re-inclusion works on the droplet's Docker, or the fallback is taken and recorded.

- [ ] **Step 1: Confirm the re-inclusion is in the checked-out tree on the droplet.** **[droplet]**
  ```bash
  ssh ohcamel 'grep -n "docs\|crisis" ~/OhCamel/.dockerignore && ls -la ~/OhCamel/docs/crisis/'
  ```
  Expected: a bare `docs/` line followed by `!docs/crisis/*.csv`, and three `.csv` files in `docs/crisis/` (the GFC, COVID and 2022 caches, ~105 KB in total).

- [ ] **Step 2: Ask the running image whether the CSVs made it into the binary.** **[droplet]** The runtime stage carries one executable and `book.example.sexp` — no `docs/` — so a crisis battery that prints three windows can only be reading the embedded strings.
  ```bash
  ssh ohcamel 'cd ~/OhCamel && docker compose -f deploy/docker-compose.yml run --rm --no-deps --entrypoint /usr/local/bin/ohcamel ohcamel-demo backtest-crisis | head -40'
  ```
  Expected: the battery's header and all three windows named (the GFC window, COVID, and the 2022 rate shock), with their date spans and session counts. A failure here reads as a missing-file error naming `docs/crisis`, and means the re-inclusion did not survive this Docker's ignore-file matcher.

- [ ] **Step 3: Ask the deployed engine the same thing over HTTP.** **[mac]**
  ```bash
  curl -sS https://ohcamel.ajaiupadhyaya.com/api/reports | python3 -c '
  import sys, json
  d = json.load(sys.stdin)
  w = d["validation"]["crisis"]["windows"]
  print("windows:", len(w))
  for x in w:
      print(" ", x["name"], x["first"], "->", x["last"], x["sessions"], "sessions", x["forecasts"], "forecasts")
  print("validation rows:", len(d["validation"]["synthetic"]["rows"]) + len(d["validation"]["crisis"]["rows"]))
  '
  ```
  Expected exactly:
  ```
  windows: 3
    <gfc>       2007-07-03 -> 2009-12-30 631 sessions 570 forecasts
    <covid>     2019-06-03 -> 2020-12-30 400 sessions 339 forecasts
    <rates2022> 2021-06-01 -> 2022-12-30 401 sessions 340 forecasts
  validation rows: 18
  ```
  (The window *names* are whatever `Crisis_data` calls them; the spans, session counts and forecast counts are the spec's and must match to the digit.)

- [ ] **Step 4: If Steps 2–3 passed, record it and skip to Task 6.** **[mac]** Write one line into the working log: `dockerignore re-inclusion: works on the droplet's Docker (<version>), verified <date> by three embedded windows`. Get the version with:
  ```bash
  ssh ohcamel 'docker version --format "{{.Server.Version}}" && docker buildx version'
  ```
  Expected: a Docker 24+/27+ server version and a buildx version. This is the fact the spec asked to re-verify *there* rather than only locally.

- [ ] **Step 5: FALLBACK A — only if Step 2 or 3 failed.** **[mac]** The blanket `docs/` exclusion plus a file-level negation is the fragile form; the directory-level form is not. Replace the two lines in `.dockerignore` with:
  ```
  # The crisis caches are the one thing under docs/ the build needs: lib/dune
  # embeds the three CSVs into crisis_csv.ml, so the battery runs from the
  # binary and the runtime image carries no files at all. Written as
  # `docs/*` + an un-ignored directory rather than `docs/` + a file glob,
  # because a blanket directory exclusion lets a matcher skip the directory
  # without ever considering the negation inside it.
  docs/*
  !docs/crisis/
  ```
  Then rebuild and re-run Steps 2–3:
  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && git add .dockerignore && git commit -m "deploy: the crisis caches must survive the ignore file on the droplet's docker, not only on the laptop's" && git push origin main
  ssh ohcamel 'cd ~/OhCamel && git pull --ff-only && setsid nohup deploy/deploy.sh --live > ~/phase6/deploy-b.log 2>&1 < /dev/null & echo started'
  ```
  Expected: the deploy completes and Step 3's assertion passes.

- [ ] **Step 6: FALLBACK B — only if Fallback A failed.** **[mac]** Drop the `docs/` exclusion entirely (the whole directory is ~200 KB of markdown plus the caches; the cost is context transfer, not image size) and rebuild as in Step 5. Expected: the same assertion passes. Record the size cost from the build log's `transferring context` line.

- [ ] **Step 7: FALLBACK C — only if the dune rule itself cannot run in the image.** **[mac]** This is the spec's named fallback and it is the expensive one because it touches `bin/main.ml`. In `deploy/Dockerfile`'s runtime stage, immediately after `WORKDIR /app`, add:
  ```dockerfile
  # The battery reads the caches from the filesystem in this build, because the
  # builder's ignore file would not hand them to the embedding rule. The root
  # filesystem is read-only and these are only ever read.
  COPY --chown=ohcamel:ohcamel docs/crisis /app/docs/crisis
  ```
  and switch the CLI's crisis mode back from `Crisis_data.load_all_embedded` to `Crisis_data.load_all` with the path `docs/crisis`. Because this changes `bin/main.ml`, the byte-identical stdout gate applies — the same one every phase ran, Phase 3 Task 1's `gate.sh` against the six captures beside it. Before the edit, confirm the baseline is still in scratch:
  ```bash
  G=/private/tmp/claude-501/-Users-ajaiupadhyaya/2b8d28c2-fd4e-480e-88bd-1f525014ebf2/scratchpad/phase3-baseline
  ls "$G"/gate.sh "$G"/synthetic.txt "$G"/stress.txt "$G"/backtest.txt "$G"/backtest-crisis.txt "$G"/options.txt "$G"/garch.txt
  ```
  Expected: seven paths. If the six captures are gone, re-capture them from the last commit — from a worktree, because the working tree may already carry this fallback's edit — built with the repository's own switch:
  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && WT=/tmp/ohcamel-phase6-before && rm -rf "$WT" && git worktree prune && git worktree add --detach "$WT" "$(git rev-parse HEAD)" && eval "$(opam env --switch=$PWD --set-switch)" && (cd "$WT" && dune build bin/main.exe) && git -C "$WT" rev-parse HEAD > "$G/BASELINE_SHA" && for m in synthetic stress backtest backtest-crisis options garch; do (cd "$WT" && ./_build/default/bin/main.exe "$m") > "$G/$m.txt" 2>&1; echo "captured $m ($(wc -l < "$G/$m.txt") lines)"; done && shasum -a 256 "$G"/*.txt > "$G/BASELINE_SHA256" && git worktree remove --force "$WT"
  ```
  Then make the two edits above, and run the gate from the repository root (it builds, runs the six modes from the root — where `docs/crisis` now has to be read from — and diffs):
  ```bash
  /private/tmp/claude-501/-Users-ajaiupadhyaya/2b8d28c2-fd4e-480e-88bd-1f525014ebf2/scratchpad/phase3-baseline/gate.sh
  ```
  Expected: six `GATE ok` lines. A `GATE DIFFERS` means the move is wrong, not that the output is new — `backtest-crisis` must print the same three windows from the files that it printed from the binary. (`/tmp/ohcamel-phase6/` stays what it is elsewhere in this plan: scratch for measurements, not the gate.)

- [ ] **Step 8: Commit — only if a fallback was taken.** **[mac]**
  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && git add .dockerignore deploy/Dockerfile bin/main.ml && git commit -m "deploy: the droplet's docker reads the ignore file differently, and the crisis caches were the casualty"
  ```
  If nothing failed, there is no commit for this task — the verification *is* the deliverable, and it lands in `status.md` in Task 11.

---

### Task 6: Measure the startup cost and the GARCH domain on the s-2vcpu box

**Files:**
- Create: `/tmp/ohcamel-phase6/measurements.txt` (scratch, never committed)
- Test: every number below is present in `measurements.txt` with the command that produced it beside it

**Interfaces:**
- Consumes: Phase 5's `GET /api/reports` (`computed_at`, `computed_in_ms`, `scaling.rows`, `scaling.cost_nodes_recomputed`) and `GET /api/reports/garch` (`status`, `done`, `of`, `computed_in_ms`, `rows`); Phase 2's `GET /api/ops` (`uptime_s`, `started_at`, `process.*`, `gc.*`, `rss_bytes`, `stream.*`, `history.*`, `reports.*`); Phase 5's container log line on GARCH completion; `deploy/smoke.sh` (existing SSE probe).
- Produces: the four numbers `status.md` gains in Task 11 — pre-listen cost, static-report cost, GARCH wall time solo, GARCH wall time with both engines starting together — plus resident memory and the stream's survival during the GARCH window.

- [ ] **Step 1: Read the cost of the static reports as the process reports it.** **[mac]**
  ```bash
  mkdir -p /tmp/ohcamel-phase6
  curl -sS https://ohcamel.ajaiupadhyaya.com/api/reports | python3 -c '
  import sys, json
  d = json.load(sys.stdin)
  print("computed_at      ", d["computed_at"])
  print("computed_in_ms   ", d["computed_in_ms"])
  print("platform         ", d["platform"])
  print("scaling rows     ", [(r["instruments"], r["nodes_in_graph"], r["nodes_per_tick"], r["if_polled"]) for r in d["scaling"]["rows"]])
  print("probe cost nodes ", d["scaling"]["cost_nodes_recomputed"])
  ' | tee -a /tmp/ohcamel-phase6/measurements.txt
  ```
  Expected: `computed_in_ms` a few hundred; the platform record naming `5.2.1`, `amd64`, `linux`; the scaling rows exactly `(10, 63, ~26, 63)`, `(100, 342, ~26, 342)`, `(400, 1272, ~26, 1272)` — the corrected table, not `58 / 337 / 1267`. This one JSON object holds the probe *and* the two batteries *and* the options walk, so it is an upper bound on the probe alone, and it is reported as that rather than as the probe's own number.

- [ ] **Step 2: Measure the whole pre-listen cost by restarting the demo engine and timing the first 200.** **[droplet]** Restart only the demo: restarting the live engine drops the Alpaca socket and re-runs its backfill for no gain here.
  ```bash
  ssh ohcamel 'cd ~/OhCamel && docker compose -f deploy/docker-compose.yml restart ohcamel-demo >/dev/null 2>&1
  cid=$(docker compose -f deploy/docker-compose.yml ps -q ohcamel-demo)
  started=$(docker inspect -f "{{.State.StartedAt}}" "$cid")
  t0=$(date -d "$started" +%s%3N)
  until curl -fsS -o /dev/null --max-time 2 https://ohcamel.ajaiupadhyaya.com/api/health 2>/dev/null; do sleep 0.2; done
  t1=$(date +%s%3N)
  echo "container StartedAt: $started"
  echo "pre-listen (container start -> first 200 on /api/health): $((t1 - t0)) ms"' | tee -a /tmp/ohcamel-phase6/measurements.txt
  ```
  Expected: a figure in the low seconds. The spec budgets 1.5 s for the probe alone on this box and the healthcheck allows a 20 s start period; anything above ~10 s here is a finding, not a failure, and goes into `status.md` as measured.

- [ ] **Step 3: Decompose that number from the container's own log timestamps.** **[droplet]**
  ```bash
  ssh ohcamel 'cd ~/OhCamel && docker compose -f deploy/docker-compose.yml logs --since 3m --timestamps --no-color ohcamel-demo | head -30' | tee -a /tmp/ohcamel-phase6/measurements.txt
  ```
  Expected: the first log line at container start, the startup banner (`OhCamel -- reactive risk and limits engine`, `DEMO (synthetic feed…)`, `dashboard http://localhost:8080`) some hundreds of milliseconds later, and the GARCH completion line after that. The delta from the first line to the banner is the pre-listen work; the delta from the banner to the GARCH line is the domain's wall time as the container saw it. Record both with the timestamps that produced them.

- [ ] **Step 4: Read the GARCH study's own wall time, solo.** **[mac]** Poll until it is done — the route answers 200 in every state, so this is a loop on `status`, not on the HTTP code.
  ```bash
  for i in $(seq 1 60); do
    curl -sS https://ohcamel.ajaiupadhyaya.com/api/reports/garch | python3 -c '
  import sys, json
  d = json.load(sys.stdin)
  print(d["status"], d["done"], "of", d["of"], "computed_in_ms", d.get("computed_in_ms"), "rows", len(d["rows"]))
  '
    sleep 2
  done | tee -a /tmp/ohcamel-phase6/measurements.txt
  ```
  Expected: a short run of `computing k of 180 …` lines with `k` climbing and `rows` filling in sample-size order (n = 60 first), then `done 180 of 180 computed_in_ms <N> rows 6`, repeated for the remainder of the loop. `<N>` is the number `status.md` records as the GARCH wall time on one free vCPU. Kill the loop once it reads `done` twice.

- [ ] **Step 5: Prove the stream did not stall while the domain ran.** **[mac]** This is the spec's *That GARCH stalls the stream after a deploy* fear, answered with the suite's own two load-bearing assertions taken *during* the window. Restart the demo engine and immediately run the smoke suite against it:
  ```bash
  ssh ohcamel 'cd ~/OhCamel && docker compose -f deploy/docker-compose.yml restart ohcamel-demo >/dev/null 2>&1 && sleep 3 && deploy/smoke.sh https://ohcamel.ajaiupadhyaya.com' | tee -a /tmp/ohcamel-phase6/measurements.txt
  ```
  Expected: the counter assertion passes (`N nodes recomputed in 2s`, N well above zero) and the SSE assertion passes (`M distinct frames spread over 20s`) even though the GARCH domain is running throughout. Record N and M with the note that they were taken during the study. If either fails here but passes when the study is done, that is the domain interfering with the scheduler and the fallback is the chunked loop — a finding for `status.md` *Next*, not a change made in this phase.

- [ ] **Step 6: Measure the GARCH wall time when both engines start together.** **[droplet]** Two engines on two vCPUs each spawning a study is the state every real deploy leaves the box in, so it is the number that matters more than the solo one.
  ```bash
  ssh ohcamel 'cd ~/OhCamel && docker compose -f deploy/docker-compose.yml restart ohcamel-demo ohcamel-live >/dev/null 2>&1 && date -u +%FT%T.%3NZ'
  ```
  then, from the Mac, poll the demo's route as in Step 4 and read the live engine's from inside the network:
  ```bash
  ssh ohcamel 'cd ~/OhCamel && for i in $(seq 1 45); do docker compose -f deploy/docker-compose.yml exec -T ohcamel-live curl -fsS http://localhost:8081/api/reports/garch 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(d[\"status\"], d[\"done\"], d.get(\"computed_in_ms\"))"; sleep 2; done' | tee -a /tmp/ohcamel-phase6/measurements.txt
  ```
  Expected: both reach `done`; both `computed_in_ms` values are larger than the solo figure, and the ratio is the contention cost on a two-vCPU box. Record all three numbers.

- [ ] **Step 7: Read resident memory, the heap and the process counters at rest.** **[mac]** Wait two minutes after Step 6 so the studies are finished and the engines are only ticking.
  ```bash
  curl -sS https://ohcamel.ajaiupadhyaya.com/api/ops | python3 -c '
  import sys, json
  d = json.load(sys.stdin)
  print("mode        ", d["mode"], "uptime_s", d["uptime_s"], "started_at", d["started_at"])
  print("build       ", d["build"]["git_short"], d["build"]["built_at"], d["build"]["profile"], d["build"]["architecture"], d["build"]["system"])
  print("process     ", d["process"])
  print("stream      ", d["stream"])
  print("history     ", d["history"])
  print("gc heap MB  ", round(d["gc"]["heap_words"] * 8 / 1e6, 1), "top", round(d["gc"]["top_heap_words"] * 8 / 1e6, 1), "major", d["gc"]["major_collections"])
  print("rss MB      ", None if d["rss_bytes"] is None else round(d["rss_bytes"] / 1e6, 1))
  print("reports     ", d["reports"])
  ' | tee -a /tmp/ohcamel-phase6/measurements.txt
  ```
  Expected: `rss MB` a real number (this is Linux, so `/proc/self/statm` answers — `None` would mean the reader is wrong), `reports` reading `{"static": "ready", "garch": "done"}`, and `process` counters far above the named-node count because they are process-wide and include every fork and the startup probe.

- [ ] **Step 8: Read what the box itself thinks, since the engine cannot.** **[droplet]** CPU share and Caddy's memory are stated on the page as not engine-observable; `docker stats` is where they are.
  ```bash
  ssh ohcamel 'docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}" && free -m | head -2'
  ```
  Expected: the two engines and Caddy at low single-digit CPU percentages at rest, each engine in the tens of megabytes (the pre-page figure was ~41 MB; this phase adds the cached report strings and the page, so expect it to have moved and record what it is), Caddy near 27 MB, and the droplet still at a small fraction of 4 GB.

- [ ] **Step 9: Re-read the two counters the page draws its liveness from, thirty seconds apart.** **[mac]** This is the `/ops` pulse, taken by hand once so the recorded numbers have a witness.
  ```bash
  a=$(curl -sS https://ohcamel.ajaiupadhyaya.com/api/ops); sleep 30; b=$(curl -sS https://ohcamel.ajaiupadhyaya.com/api/ops)
  printf '%s\n%s' "$a" "$b" | python3 -c '
  import sys, json
  a, b = [json.loads(l) for l in sys.stdin.read().splitlines() if l.strip()]
  print("nodes_recomputed +", b["process"]["nodes_recomputed"] - a["process"]["nodes_recomputed"], "in 30s")
  print("frames_sent      +", b["stream"]["frames_sent"] - a["stream"]["frames_sent"], "in 30s")
  print("history.appended +", b["history"]["appended"] - a["history"]["appended"], "in 30s")
  ' | tee -a /tmp/ohcamel-phase6/measurements.txt
  ```
  Expected: all three deltas positive on the demo host. `history.appended` is the one a stress fork cannot inflate, which is why the page prints it beside the counter.

- [ ] **Step 10: No commit.** Task 11 writes these into `status.md`.

---

### Task 7: Read the computed-vs-quoted lines on both origins

**Files:**
- Create: `/tmp/ohcamel-phase6/quoted-verdict.txt` (scratch, never committed)
- Test: the verdict for each of the four comparable tables is captured verbatim from the page, on both origins

**Interfaces:**
- Consumes: Phase 5's rendered computed-vs-quoted lines (the spec's fixed wordings: `agrees with the README's table to four decimals`, or `computed on this host (linux, amd64): differs from the README's table (macOS, arm64) in N cells: …`); `web/quoted.json` (Phase 1); `GET /api/reports`; the Playwright MCP tools `browser_resize`, `browser_navigate`, `browser_wait_for`, `browser_evaluate`.
- Produces: the sentence `status.md` records under *Numbers worth knowing*, and — only if a cell differs — the README footnote naming the machine.

- [ ] **Step 1: Load the browser tools.** **[mac]** One call, not four:
  ```
  ToolSearch: select:mcp__plugin_playwright_playwright__browser_resize,mcp__plugin_playwright_playwright__browser_navigate,mcp__plugin_playwright_playwright__browser_wait_for,mcp__plugin_playwright_playwright__browser_evaluate,mcp__plugin_playwright_playwright__browser_take_screenshot,mcp__plugin_playwright_playwright__browser_console_messages
  ```
  Expected: six function schemas returned. (The `webapp-testing` skill covers this toolkit; its guidance applies unchanged.)

- [ ] **Step 2: Open the demo origin at the width the page is designed for.** **[mac]**
  - `browser_resize` with `{"width": 1440, "height": 1000}`
  - `browser_navigate` with `{"url": "https://ohcamel.ajaiupadhyaya.com/"}`
  - `browser_wait_for` with `{"time": 10}` — long enough for `/api/reports` to be fetched as §01 approaches the viewport and for the GARCH column to be `done`.

  Expected: no error from any call.

- [ ] **Step 3: Extract every provenance verdict the page rendered.** **[mac]** `browser_evaluate` with this function — it reads leaf elements only, so a sentence is not reported once per ancestor:
  ```js
  () => {
    const leaves = Array.from(document.querySelectorAll('body *')).filter(e => e.children.length === 0);
    const want = /agrees with the README|differs from the README|computed on this host|COMPUTED · AT STARTUP|QUOTED ·|SYNTHETIC/;
    const seen = new Set();
    return leaves.map(e => e.textContent.trim()).filter(t => t && want.test(t))
                 .filter(t => (seen.has(t) ? false : (seen.add(t), true)));
  }
  ```
  Expected: an array containing the four computed-vs-quoted verdicts (§01(b) scaling, §04 the synthetic battery, §05 the GARCH study, §06 the crisis battery), each reading either `agrees with the README's table to four decimals` or the `differs … in N cells` form, plus the `COMPUTED · AT STARTUP · HH:MM:SSZ` and `QUOTED · README · M2 PRO · 2026-08` tags and the SYNTHETIC label on §03. Save the array to `/tmp/ohcamel-phase6/quoted-verdict.txt` under a `demo origin` heading.

- [ ] **Step 4: Check the console is clean while you are there.** **[mac]** `browser_console_messages` with no arguments.
  Expected: no errors. A blocked external asset would show here, and the design forbids external assets entirely.

- [ ] **Step 5: Do the same on the live origin, behind the password.** **[mac]** Chromium accepts credentials in a top-level navigation URL. Ask the owner for the password rather than reading it from anywhere:
  - `browser_navigate` with `{"url": "https://ohcamel:<PASSWORD>@live.ohcamel.ajaiupadhyaya.com/"}` (the user is `ohcamel` unless `OHCAMEL_LIVE_USER` in the droplet's `deploy/.env` says otherwise — read that one value with `ssh ohcamel 'sed -n "s/^OHCAMEL_LIVE_USER=//p" ~/OhCamel/deploy/.env'`, which is the same `sed` read `deploy.sh` uses and is not a source).
  - `browser_wait_for` with `{"time": 10}`, then repeat Step 3's `browser_evaluate`.

  Expected: the same four verdicts, and on this origin every COMPUTED tag additionally reads `SYNTHETIC BOOK, NOT THIS HOST'S`, because §01(b), §03, §04 and §06 are computed on the CLI's seeded book and not on the book the ledger above shows. Save under a `live origin` heading.

- [ ] **Step 6: If the credentialed URL is refused, take the same reading without a browser.** **[droplet]** The gate is at Caddy, so the engine answers unauthenticated from inside the network — this is the owner reading their own box, not a way around the password:
  ```bash
  ssh ohcamel 'cd ~/OhCamel && docker compose -f deploy/docker-compose.yml exec -T ohcamel-live curl -fsS http://localhost:8081/api/reports' > /tmp/ohcamel-phase6/reports-live.json
  curl -sS https://ohcamel.ajaiupadhyaya.com/api/reports > /tmp/ohcamel-phase6/reports-demo.json
  python3 - <<'PY'
  import json
  a = json.load(open('/tmp/ohcamel-phase6/reports-demo.json'))
  b = json.load(open('/tmp/ohcamel-phase6/reports-live.json'))
  key = lambda r: (r.get("series") or r.get("window"), r["estimator"]["label"])
  for part in ("synthetic", "crisis"):
      ra = {key(r): r for r in a["validation"][part]["rows"]}
      rb = {key(r): r for r in b["validation"][part]["rows"]}
      assert ra.keys() == rb.keys(), part
      diff = [k for k in ra if any(round(ra[k][f], 4) != round(rb[k][f], 4)
              for f in ("kupiec_p", "independence_p", "conditional_coverage_p")
              if ra[k][f] is not None and rb[k][f] is not None)
              or ra[k]["exceptions"] != rb[k]["exceptions"] or ra[k]["zone"] != rb[k]["zone"]]
      print(part, "rows", len(ra), "differing between the two hosts:", diff or "none")
  PY
  ```
  Expected: `differing between the two hosts: none` for both parts — the two hosts run the same image over the same seeds, so the computed side is identical and only the page's *labelling* differs. Record that, and take the demo origin's rendered verdicts as the answer for both.

- [ ] **Step 7: If any verdict reads `differs`, capture the cells and stop to decide the wording.** **[mac]** The spec's rule is that the page prints the difference and the README gains a footnote naming the machine, never a silent edit. Capture the full `differs … in N cells: …` sentence into `/tmp/ohcamel-phase6/quoted-verdict.txt`, and add the footnote to the README in Task 10 beside the affected table, in this form:
  > *These verdicts were produced on macOS/arm64. The deployed engine (Debian/glibc/amd64) computes the same battery and differs in N cells, listed on the page at [ohcamel.ajaiupadhyaya.com](https://ohcamel.ajaiupadhyaya.com); the seeds are the same and the difference is the platform's libm.*

  Expected in the ordinary case: no footnote is needed, and this step ends with a recorded `agrees`.

- [ ] **Step 8: No commit.** The verdict is written into `status.md` by Task 11 and, if it differs, into the README by Task 10.

---

### Task 8: Re-measure and re-date the test count and the coverage

**Files:**
- Modify: `lib/verified.ml` (all three constants: `tests : int`, `coverage_pct : float`, `dated : string`)
- Test: `dune runtest --force` passes, including `test_ohcamel.ml`'s assertion that `Verified.tests` equals the sum of the registered suites' lengths; `dune build @fmt` passes

**Interfaces:**
- Consumes: Phase 3's `lib/verified.ml` and `test_ohcamel.ml`'s suite-count assertion; `make coverage` (Makefile:198-205, `bisect_ppx`, writing `_coverage/`); the `OPAM_ENV` pattern (`Makefile:9`) that re-enters the project-local switch.
- Produces: the two numbers §08 of the page prints and the README badge and `status.md` quote — measured on 2026-09-03, not carried forward from 2026-08-25.

- [ ] **Step 1: Count the tests as alcotest counts them.** **[mac]**
  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && make test 2>&1 | tail -5
  ```
  Expected: `Test Successful in <t>s. <N> tests run.` — `<N>` is the count. It will be above the 210 recorded on 2026-08-25, because Phases 3–5 added `test_recompute_log`, `test_scaling_probe`, `test_validation_report`, `test_options_walk`, `test_garch_study` and the `test_graph` / `test_server` additions.

- [ ] **Step 2: Measure coverage.** **[mac]** This rebuilds instrumented and takes a few minutes.
  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && make coverage 2>&1 | tail -30
  ```
  Expected: the per-file table, then a total line of the form `Coverage: 2099/2982 (70.39%)` with both numbers moved — Phase 3 added ~1,600 lines to `lib/` and Phase 5's encoders are largely exercised. Record the fraction and the percentage exactly as printed.

- [ ] **Step 3: Read the current constants before changing them.** **[mac]**
  ```bash
  cat -n /Users/ajaiupadhyaya/Documents/OhCamel/lib/verified.ml
  ```
  Expected: three `let` bindings with the 2026-08-25 values (`tests = 210`, `coverage_pct = 70.4`, `dated = "2026-08-25"`) and the comment explaining that these are quoted numbers the page must not style as its own.

- [ ] **Step 4: Update the three constants and the comment's date.** **[mac]** Keep the file's existing prose and structure; change only the values and any date inside them. The `dated` string is today, `"2026-09-03"`, and it is the date the *measurement* was taken, not the date the file was edited — if those differ, re-measure rather than back-date.

- [ ] **Step 5: Sweep the repository for the old numbers wherever they were typed by hand.** **[mac]** The page's §08 prints `70.4% = 2,099 / 2,982 lines`; the line fraction may live in `web/` or `web/quoted.json` rather than in `Verified`.
  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && grep -rn "2,099\|2099\|2,982\|2982\|70\.4\|210 hermetic\|210 tests" web/ lib/ README.md docs/status.md | grep -v "_build"
  ```
  Expected: a short list. Update every hit that is one of these two measurements to the new values; leave anything that is coincidentally the same digits alone (read each hit before editing). README and `status.md` hits are updated in Tasks 10 and 11 — note them and move on.

- [ ] **Step 6: Format, then run the suite that pins the count.** **[mac]**
  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && dune build @fmt && make test 2>&1 | tail -5
  ```
  Expected: `@fmt` produces no diff and no error; the suite passes, which means `Verified.tests` now equals the summed suite lengths. If it fails, alcotest names the expected and actual values — take the actual.

- [ ] **Step 7: Measure the size of the tree, since `status.md` quotes it.** **[mac]**
  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && echo "lib: $(find lib -name '*.ml' -not -name 'crisis_csv.ml' -not -name 'dashboard_html.ml' -not -name 'ops_html.ml' -not -name 'build_info.ml' -not -name 'quoted.ml' | xargs wc -l | tail -1)" && echo "test: $(find test -name '*.ml' | xargs wc -l | tail -1)" && echo "web: $(wc -l web/* | tail -1)"
  ```
  Expected: three totals. The generated modules are excluded from the `lib/` figure deliberately — they are embedded assets, not code, and counting a 105 KB CSV string as library lines would make the number a lie.

- [ ] **Step 8: Commit.** **[mac]**
  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && git add lib/verified.ml web/ && git commit -m "docs: re-measure what the page quotes about itself, because three phases of tests moved both numbers"
  ```

---

### Task 9: A fresh `docs/media/dashboard.png`, and the page's own smoke numbers

**Files:**
- Modify: `docs/media/dashboard.png` (replaced; the README embeds it at `README.md:28`)
- Create: `/tmp/ohcamel-phase6/page-smoke.txt` (scratch, never committed)
- Test: `sips -g pixelWidth -g pixelHeight docs/media/dashboard.png` reports a width of 1440; the file is smaller than 1 MB

**Interfaces:**
- Consumes: Phase 5's §08, which re-runs the smoke suite's two load-bearing assertions in the browser and prints them as `nodes_recomputed +N across the last 2 s`, `book changed +M` and `K distinct frames spread over 20 s`; the Playwright MCP tools loaded in Task 7 Step 1.
- Produces: the README's hero image and the three numbers the README's *Watching it* paragraph quotes in Task 10.

- [ ] **Step 1: Size the browser to the width the layout's `wide` line is drawn for.** **[mac]** `browser_resize` with `{"width": 1440, "height": 1000}`. Expected: no error. 1440 is above the 1180 `wide` grid line and above the 1100 below which Figure 1 starts scrolling inside its container, so the graph is drawn whole.

- [ ] **Step 2: Load the demo origin and let it fill.** **[mac]**
  - `browser_navigate` with `{"url": "https://ohcamel.ajaiupadhyaya.com/"}`
  - `browser_wait_for` with `{"time": 15}` — the GARCH column fills row by row and the frame-arrival strip needs frames in it; a screenshot taken at three seconds shows a page mid-warm-up and would be a picture of nothing having happened yet.

- [ ] **Step 3: Read §08's browser-side assertions before shooting.** **[mac]** `browser_evaluate` with:
  ```js
  () => {
    const leaves = Array.from(document.querySelectorAll('body *')).filter(e => e.children.length === 0);
    const want = /nodes_recomputed \+|book changed \+|distinct frames spread over|no frame in the last|frames — nothing changed/;
    return leaves.map(e => e.textContent.trim()).filter(t => t && want.test(t));
  }
  ```
  Expected on the demo host: three sentences with live numbers in them — the counter delta across two seconds, the `history.appended` delta beside it, and the frame count spread over twenty seconds. Save them to `/tmp/ohcamel-phase6/page-smoke.txt`; Task 10 quotes them. (On the live host at night these read `no frame in the last N s` and `0 frames — nothing changed`, which is why the demo origin is the one quoted.)

- [ ] **Step 4: Take the screenshot.** **[mac]** `browser_take_screenshot` with `{"filename": "ohcamel-dashboard-1440.png", "type": "png", "scale": "css"}` and **no** `fullPage` — the viewport shot is the first screen (header, Figure 1 with the current frame lit, the inspector line), which is what the README's hero must show. A full-page shot of a nine-section document is fifteen thousand pixels tall and unreadable at README width.
  Expected: the tool returns the path it saved to.

- [ ] **Step 5: Put it where the README looks for it.** **[mac]**
  ```bash
  cp "<path returned by Step 4>" /Users/ajaiupadhyaya/Documents/OhCamel/docs/media/dashboard.png
  sips -g pixelWidth -g pixelHeight /Users/ajaiupadhyaya/Documents/OhCamel/docs/media/dashboard.png
  ls -lh /Users/ajaiupadhyaya/Documents/OhCamel/docs/media/dashboard.png
  ```
  Expected: `pixelWidth: 1440`, `pixelHeight: 1000`, and a file in the low hundreds of kilobytes (the previous one was 209 KB). If it is above 1 MB, re-take it as `{"type": "jpeg"}` is *not* an option — the page is type and hairlines and JPEG ruins both; instead reduce the height to 900 and re-shoot.

- [ ] **Step 6: Look at it.** **[mac]** Read the PNG back and check three things: the graph is drawn (not a hairball of overlapping labels), at least one node carries the `--mark` rule from a recent frame, and CVX's downstream closure is visibly dimmed while the other five names are at full strength. If it is a hairball, that is the spec's *That the drawing is a hairball* fear arriving late — record it in `status.md` *Next* and shoot the best available frame anyway; the fallback (family bands) is a Phase 4 change, not this phase's.

- [ ] **Step 7: Commit the image on its own.** **[mac]**
  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && git add docs/media/dashboard.png && git commit -m "docs: the README's first picture is the deployed page, taken from the demo origin at 1440"
  ```

---

### Task 10: The README — *Watching it*, the page, and the two dated numbers

**Files:**
- Modify: `README.md:4` (the coverage badge)
- Modify: `README.md:28` (the hero image's alt text)
- Modify: `README.md:1026-1060` (the whole `## Watching it` section, from `None of the above is required to see it move.` to the `Redeploying is one command` paragraph)
- Modify: `README.md:1062-1064` (`## What's verified`'s opening sentence, `make test` runs 210 tests…)
- Test: `grep -n "210" README.md` returns nothing that refers to the test count; `grep -c "docs/media/dashboard.png" README.md` returns `1`

**Interfaces:**
- Consumes: Task 8's measured test count and coverage percentage; Task 9's three §08 sentences; Task 3's smoke output; Phase 2's `--expect-sha` and `uptime_s < 300` assertions; Phase 5's smoke assertions for `/api/reports`, `/api/reports/garch`, `/api/graph`, `/api/stress`, `/api/history`, `/ops` and the 404 route list; the live-host 401 list.
- Produces: the README paragraph the page's §08 is cut from, so the two do not drift.

- [ ] **Step 1: Update the coverage badge.** **[mac]** Line 4 is
  ```
  [![coverage 70%](https://img.shields.io/badge/coverage-70%25-brightgreen)](#coverage-and-what-it-is-not-measuring)
  ```
  Replace both `70`s with the integer part of Task 8's measured percentage. Keep the anchor exactly as it is — it points at an existing heading and a renamed anchor is a broken link.

- [ ] **Step 2: Update the hero's alt text.** **[mac]** Line 28 currently reads `![The dashboard, driven by the synthetic feed](docs/media/dashboard.png)`. The picture is no longer a dashboard of three columns; make the alt text say what it now shows, e.g. `![The Incremental graph, drawn from Incremental, with the current frame lit on it](docs/media/dashboard.png)`. The path does not change — Task 9 replaced the file in place.

- [ ] **Step 3: Re-read *Watching it* before rewriting it.** **[mac]**
  ```bash
  sed -n '1026,1062p' /Users/ajaiupadhyaya/Documents/OhCamel/README.md
  ```
  Expected: the section as it stands — the demo URL, the deployment paragraph, the two load-bearing assertions with `234 nodes` and `52 of them`, the three-deploy-bugs paragraph, and the redeploy paragraph.

- [ ] **Step 4: Replace the two quoted smoke numbers with this deploy's.** **[mac]** The sentence to change is the one naming `234 nodes, in the run that verified this paragraph` and `52 of them`. Take both from Task 3's smoke output (`N nodes recomputed in 2s`, `M distinct frames spread over Ns`), and add the assertion that did not exist when the paragraph was written:

  > …and that `/api/stream` delivers distinct frames spread across a twenty-second window rather than piled up at its end: M of them. The suite now also asserts that the container answering is the build that was just made — `/api/ops` returns the sha `deploy.sh` passed as `--expect-sha`, with an uptime under five minutes — which is the check none of the original nine could make, and that every report route the page reads answers: eighteen validation rows on `/api/reports`, the GARCH study reaching `done` within two minutes, twelve scenarios on `/api/stress`, the topology on `/api/graph`, the history ring's capacity of 500 on `/api/history`, and, on the live host, a 401 for `/`, `/ops`, `/api/ops`, `/api/snapshot` and `/api/health` alike, so the gate cannot narrow without the deploy failing.

- [ ] **Step 5: Add the paragraph on the page itself.** **[mac]** Immediately after the paragraph above, before the three-deploy-bugs paragraph. It says what `/` became and what `/ops` is, and it quotes the browser-side assertions from Task 9 — this is the README's half of §08, and the two are written from the same numbers:

  > What is served at that URL is no longer the three-column dashboard the picture at the top of this file used to show. `/` is one document: the Incremental graph drawn from Incremental's own node table, with each frame's recomputation set lit on it and staleness dimming exactly the downstream closure of the price that went quiet; then the ledger; then the argument of this README continued under its own headings, with the coverage battery, the crisis battery, the attribution decomposition, the options walk, the GARCH study and the scenario suite computed by the process that serves them and tagged with where each number came from. `/ops` is the small page for a phone at two in the morning: two hosts, ages in seconds, and a flat line where a counter should be climbing. The page re-runs the smoke suite's two load-bearing assertions in your browser rather than claiming a result it cannot persist — as this was written it read *«N»*, *«M»* and *«K»*.

  Replace the three quoted fragments with the sentences captured in `/tmp/ohcamel-phase6/page-smoke.txt`. Do not paraphrase them: they are the page's own text.

- [ ] **Step 6: Update the test count.** **[mac]** `## What's verified` opens `make test` runs 210 tests, all hermetic. Replace `210` with Task 8's count. Then check the rest of the section for any other stale figure:
  ```bash
  grep -n "210\|70\.4\|70%" /Users/ajaiupadhyaya/Documents/OhCamel/README.md
  ```
  Expected after editing: no hit that refers to the test count or the coverage figure.

- [ ] **Step 7: Add the platform footnote — only if Task 7 found a difference.** **[mac]** Place it directly beneath the affected table (the synthetic battery in *Is the number any good*, or the crisis battery), in the wording drafted in Task 7 Step 7. If Task 7 recorded `agrees`, add nothing: an unnecessary footnote about a difference that does not exist is its own kind of dishonesty.

- [ ] **Step 8: Commit.** **[mac]**
  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && git add README.md && git commit -m "docs: the README's deployment paragraph now describes the page that is actually deployed, with this deploy's numbers"
  ```

---

### Task 11: `docs/status.md` — the measured half

**Files:**
- Modify: `docs/status.md:45-49` (the resource-use paragraph)
- Modify: `docs/status.md:162-176` (`## The interface` and its route table)
- Modify: `docs/status.md:177-203` (`## What is verified`)
- Modify: `docs/status.md:205-228` (`## Numbers worth knowing`)
- Test: `grep -n "/api/ops\|/api/graph\|/api/reports\|/ops" docs/status.md` shows every new route in the table; no number in the section is older than its stated date

**Interfaces:**
- Consumes: Task 6's `/tmp/ohcamel-phase6/measurements.txt`; Task 7's verdict; Task 8's test count, coverage fraction and line counts; Task 4's log findings; Task 5's `.dockerignore` result; the route set from Phases 2, 4 and 5 (`/ops`, `/api/graph`, `/api/ops`, `/api/reports`, `/api/reports/garch`, and the changed `/api/snapshot`, `/api/stream`, `/api/stress`).
- Produces: the dated inventory entry that is the *only* place this phase's measurements live.

- [ ] **Step 1: Re-date the document's header.** **[mac]** Line 3 reads `*As of 2026-09-02. …*`. Make it `*As of 2026-09-03. …*`. Every number below it is now claimed as of that date, which is the point of the line.

- [ ] **Step 2: Re-measure the resource paragraph.** **[mac]** Lines 45–49 quote "about 41 MB and six percent of one core" for the engine and "27 MB and one percent" for Caddy, and "roughly three percent of capacity" for the droplet. Replace all of them with Task 6 Step 8's `docker stats` figures, and add one clause the old paragraph could not have: that the figure is taken *after* the startup reports are cached and the GARCH domain has joined, because the peak during startup is higher and a resting number that hides its own peak is not useful for sizing a box. If the resting figure moved materially (the page and the cached report strings are new), say by how much and why.

- [ ] **Step 3: Rewrite the route table.** **[mac]** Replace lines 168–176 with the full set. Keep the two-column form the file uses:

  | Route | |
  |---|---|
  | `/` | The document: the graph drawn from Incremental, the ledger, and the reports under the README's headings. One embedded HTML string, no external assets |
  | `/ops` | The operations page: two hosts, ages in seconds, the liveness pulse |
  | `/api/snapshot` | The whole book as JSON. `recomputed` is `null` here by design — only the stream knows what ran between frames, and a poller must not steal its set |
  | `/api/stream` | Server-sent events, emitted on an actual graph change, coalesced over 80 ms, each frame carrying the named node bodies that ran since the last one |
  | `/api/health` | Feed liveness per symbol; `healthy: false` when anything is stale |
  | `/api/history` | The in-memory trail, 500 points, gone on restart |
  | `/api/graph` | The topology, taken from Incremental's own node table and memoised at startup |
  | `/api/ops` | Mode, build sha, uptime, process counters, stream and feed stats, GC and RSS |
  | `/api/reports` | The scaling probe, both coverage batteries and the options walk, computed before the socket binds |
  | `/api/reports/garch` | The GARCH sample-size study, computed on a second domain; `computing k of 180` until it is `done` |
  | `/api/stress` | The scenario suite, run on a fork, with the cost it charged the process-wide counter |

  Add one sentence beneath: the demo engine adds `Access-Control-Allow-Origin: *` to JSON routes only — never the pages, never the stream — so the live host's `/ops` can fill its peer column; the live engine adds nothing and the Caddyfile is untouched.

- [ ] **Step 4: Rewrite *What is verified* with the measured numbers.** **[mac]** Three changes and one addition. The test count (Task 8). The coverage figure and its fraction (Task 8), keeping the bimodal explanation, which is still true. The production-smoke bullet, which now lists what Phases 2 and 5 added: the sha assertion, the uptime bound, `/api/graph`, the eighteen validation rows, the GARCH route reaching `done`, the twelve scenarios, `/ops`, the 404 body equalling the routes table, and the five-path 401 list on the live host. And one new bullet: the computed-vs-quoted comparison, which runs as a test on both CI legs *and* renders on the deployed page, with Task 7's verdict quoted.

- [ ] **Step 5: Correct the scaling table if Phase 3 has not.** **[mac]** Lines 209–213 must read 10 → 63, 100 → 342, 400 → 1272, matching `/api/reports`'s `scaling.rows` from Task 6 Step 1. If they still read 58 / 337 / 1267, correct them here and say in the same edit that the five option singletons were added after the table was first recorded.

- [ ] **Step 6: Add the new measured block to *Numbers worth knowing*.** **[mac]** After the bench table (which stays quoted, in its own ink, with its M2 Pro attribution intact — this droplet cannot reproduce it and `bench/` is not in the image), add a subsection headed *On the droplet, measured 2026-09-03* holding, one line each with the command that produced it named:
  - container start → first 200 on `/api/health`, in ms (Task 6 Step 2), and the log-timestamp decomposition (Step 3);
  - `/api/reports.computed_in_ms`, stated as covering the probe *and* both batteries *and* the options walk, so it is an upper bound on the probe and is not styled as the probe's own number;
  - the GARCH study's wall time solo and with both engines starting together, and the contention ratio between them (Task 6 Steps 4 and 6);
  - the smoke suite's two load-bearing assertions taken *during* the GARCH window (Task 6 Step 5), with the sentence that this is the answer to whether a second domain stalls the stream;
  - resident memory, heap MB and the process counters at rest (Task 6 Step 7), with the note that the counters are process-wide and include every fork and the startup probe;
  - the thirty-second deltas for `nodes_recomputed`, `frames_sent` and `history.appended` (Task 6 Step 9);
  - Task 7's computed-vs-quoted verdict, in the page's own wording;
  - the `.dockerignore` result from Task 5, naming the droplet's Docker version, and any fallback taken;
  - anything Task 4's log grep found, and explicitly the fact if it found nothing: no Async warning, no monitor error, no backtrace in the first five minutes.

- [ ] **Step 7: Update the size line.** **[mac]** Line 228 reads `Size: about 8,200 lines in lib/, 6,600 in test/.` Replace with Task 8 Step 7's counts and add `web/`, noting that the generated modules are excluded because a 105 KB CSV embedded as a string literal is an asset, not library code.

- [ ] **Step 8: Commit.** **[mac]**
  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && git add docs/status.md && git commit -m "docs: what the droplet actually costs and how long the GARCH domain takes, measured rather than estimated"
  ```

---

### Task 12: `docs/status.md` — the narrative half

**Files:**
- Modify: `docs/status.md:15-32` (`## What it is`, one added sentence)
- Modify: `docs/status.md:79-89` (the *Validation* paragraph's 2008 claim) and `docs/status.md:245-256` (`## What it is not, and known limits`)
- Modify: `docs/status.md:258-271` (`## How it got here`)
- Modify: `docs/status.md:273-318` (`## Next`)
- Test: `grep -n "The 2008 window is not there\|No 2008" docs/status.md` returns nothing; `grep -n "2026-09-03" docs/status.md` shows the new history row

**Interfaces:**
- Consumes: the spec's *Build order* (six phases) and *What the page does not show*; Phase 3's corrections to the 2008 claim; Task 11's measured block.
- Produces: the inventory's account of how the page got here and what is left, which the next person reads first.

- [ ] **Step 1: Say what the interface became.** **[mac]** One or two sentences at the end of `## What it is`: the engine now also *shows* the graph — the page draws the topology from Incremental's own node table and lights each frame's recomputation set on it, so the thesis in the paragraph above is drawn rather than asserted.

- [ ] **Step 2: Kill the stale 2008 claim if Phase 3 left it.** **[mac]**
  ```bash
  grep -n "2008" /Users/ajaiupadhyaya/Documents/OhCamel/docs/status.md
  ```
  Expected after this step: no line claiming the 2008 window is absent. What is true is that 2008 is unreachable *through Alpaca*, whose history begins in 2016, so the GFC cache came from Yahoo via `tools/fetch_crisis_data.py`; the committed cache holds `gfc`, `covid` and `rates-2022` and `make backtest-crisis` runs all three. Correct both the *Validation* paragraph (lines 87–89) and the *known limits* bullet (line 254) in the same edit, since they are the same claim stated twice.

- [ ] **Step 3: Add the history row.** **[mac]** In `## How it got here`, after the `2026-09-01 → 02` row:

  | `2026-09-02 → 03` | The page: six phases from one spec — `web/` and the dune embedding rules; the build sha and `/api/ops`; the extraction of the reports out of `bin/main.ml` behind a byte-identical stdout gate; the topology on the wire with the recompute log; the reports, the argument and the GARCH domain; then this deploy and its measurements. `lib/dashboard_html.ml` is gone and its design essay is archived verbatim at the head of `web/index.html` |

- [ ] **Step 4: Rewrite *Next*.** **[mac]** The section opens `**The deployment is complete.**` and describes a state two phases old. What it should now say, in order:
  - the page is deployed on both hosts, and the smoke suite that proves it is the one quoted in *What is verified*;
  - the one mechanical follow-up the spec itself deferred: moving `server.ml`'s encoders into `lib/wire.ml`, in its own commit, because `server.ml` is now roughly 900 lines;
  - anything Task 4 or Task 6 turned up that is not fixed here (an Async warning, a GARCH wall time long enough to want the chunked fallback, a hairball drawing) — each as a sentence naming what was measured;
  - then the pre-existing list, unchanged in substance: reading positions from Alpaca and the pre-trade check through `Graph.fork`; live options risk if the account tier has snapshots; the engine validating itself, which needs persistence and is the one item the page explicitly says it cannot show; building the image in Actions so the droplet only pulls.

  Keep the `ohcamel-alpha` paragraph as it is — that project's state did not change here.

- [ ] **Step 5: Check the invariants section still reads true.** **[mac]**
  ```bash
  sed -n '/## The invariants/,/## What it is not/p' /Users/ajaiupadhyaya/Documents/OhCamel/docs/status.md
  ```
  Expected: eight numbered invariants, unchanged. Invariant 6 in particular — the kill switch stays a bool wired to nothing — is now *drawn* on the page as a dotted edge ending at a terminal bar; that is worth one clause after invariant 6's line, and it is the only change this section takes.

- [ ] **Step 6: Read the whole file once, top to bottom.** **[mac]**
  ```bash
  wc -l /Users/ajaiupadhyaya/Documents/OhCamel/docs/status.md && grep -n "^## " /Users/ajaiupadhyaya/Documents/OhCamel/docs/status.md
  ```
  Expected: the same section order as before, and a document still short enough to read in ten minutes — which is what the header promises and what makes it get read.

- [ ] **Step 7: Commit.** **[mac]**
  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && git add docs/status.md && git commit -m "docs: the inventory tells the page's story and stops repeating a 2008 claim that stopped being true"
  ```

---

### Task 13: The two stale lines, and the spec's own header

**Files:**
- Modify: `docs/superpowers/specs/2026-08-31-server-side-deployment-design.md:78` (the architecture diagram's `live.env (0600)`)
- Modify: `deploy/deploy.env.example:7` (the same claim in a comment)
- Modify: `docs/superpowers/specs/2026-09-02-the-page-design.md:3` (a one-line shipped-and-measured note after the design's dateline)
- Test: `grep -rn "0600" docs/superpowers/specs/ deploy/` returns nothing; the diagram's box still closes

**Interfaces:**
- Consumes: Task 2 Step 4's `ls -l` proof that the file on the droplet is `0640 root:ohcamel`; the deployment spec's own prose at lines 184–186, which already says `0640` and contradicts its diagram.
- Produces: a deployment spec whose picture and prose agree, and a design spec whose header says when it shipped and what it cost.

- [ ] **Step 1: Confirm the contradiction before fixing it.** **[mac]**
  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && sed -n '78p' docs/superpowers/specs/2026-08-31-server-side-deployment-design.md && sed -n '182,188p' docs/superpowers/specs/2026-08-31-server-side-deployment-design.md && sed -n '5,9p' deploy/deploy.env.example
  ```
  Expected: the diagram line ending `/etc/ohcamel/live.env (0600)│`; the prose at 184–186 saying `0640` with the group-readable reasoning and the note that the first draft said `0600`; and `deploy.env.example`'s comment repeating `0600`. The prose is right and the two other places are the stragglers.

- [ ] **Step 2: Fix the diagram without breaking the box.** **[mac]** The line is inside a fenced ASCII diagram, so its width is load-bearing: line 78 is 20 spaces, `│`, forty columns of content, `│` — the same width as line 76. Replace it with exactly:
  ```
                      │ /etc/ohcamel/live.env 0640 root:ohcamel│
  ```
  (twenty leading spaces, `│`, one space, `/etc/ohcamel/live.env 0640 root:ohcamel`, `│` — the parentheses are dropped because the parenthesised form is one column too wide for the box.)

- [ ] **Step 3: Verify the width by measuring, not by eye.** **[mac]**
  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && python3 -c "
  lines = open('docs/superpowers/specs/2026-08-31-server-side-deployment-design.md', encoding='utf-8').read().split('\n')
  for n in (76, 78):
      print(n, len(lines[n-1]), repr(lines[n-1]))
  "
  ```
  Expected: both lines report the same character length (61), and line 78 ends `root:ohcamel│`.

- [ ] **Step 4: Fix the comment in `deploy.env.example`.** **[mac]** Line 7 reads
  ```
  # /etc/ohcamel/live.env, root-owned and 0600 -- because these values are
  ```
  Replace it with two lines that state the mode that is actually installed and why it is not tighter:
  ```
  # /etc/ohcamel/live.env, 0640 root:ohcamel -- group-readable because compose
  # reads env_file as the invoking user, not as root, and a 0600 file cost the
  # first live deploy. These values are convenience and those are not.
  ```
  and drop the now-duplicated trailing `# convenience and those are not.` line so the comment reads as one sentence.

- [ ] **Step 5: Confirm nothing else still says 0600.** **[mac]**
  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && grep -rn "0600" --include='*.md' --include='*.example' --include='*.sh' --include='*.yml' . | grep -v "_build\|_opam"
  ```
  Expected: no output. `deploy/live.env.example` and `deploy/provision.sh` already say 0640; this proves it.

- [ ] **Step 6: Add the shipped note to the page design spec.** **[mac]** After line 3's italic dateline paragraph in `docs/superpowers/specs/2026-09-02-the-page-design.md`, add one italic line in the same voice, with the real figures from Task 6 and Task 8 substituted:

  > *Shipped 2026-09-03 in the six phases below. Measured on the s-2vcpu droplet at the first deploy: «pre-listen» ms from container start to the first 200, «reports» ms for the static reports, «garch» ms for the GARCH domain on a free vCPU and «garch2» ms with both engines starting together; the stream's two smoke assertions held throughout. «tests» tests, «coverage»% coverage, re-dated in `lib/verified.ml`. The computed tables «agree with / differ from» the README's.*

  Every `«…»` is replaced with a measured value; none is left as a placeholder, and none is rounded in a direction that flatters.

- [ ] **Step 7: Commit.** **[mac]**
  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && git add docs/superpowers/specs/2026-08-31-server-side-deployment-design.md deploy/deploy.env.example docs/superpowers/specs/2026-09-02-the-page-design.md && git commit -m "docs: the secrets file has been 0640 root:ohcamel since the first live deploy, and two places still said 0600"
  ```

---

### Task 14: The deploy that verifies the deploy

**Files:**
- Modify: `docs/status.md:35-43` (the *Where it runs* table's `Deployed` row) and the top of `## Numbers worth knowing`
- Create: `/tmp/ohcamel-phase6/smoke-final.txt` (scratch, never committed)
- Test: `deploy/smoke.sh … --live … --expect-sha "$(git rev-parse HEAD)"` on the droplet prints `23 passed, 0 failed, 0 skipped`

**Interfaces:**
- Consumes: `deploy/smoke.sh <base> --live <url> --expect-sha <sha>` (Phase 2, extended in Phase 5); `deploy/deploy.sh --live`; Task 2's `$PREV`.
- Produces: the `Deployed` row every later reader trusts, and a droplet whose checkout, running image and `origin/main` all name the same sha.

- [ ] **Step 1: Push every documentation commit.** **[mac]**
  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && git log --oneline -8 && git push origin main && git rev-parse HEAD
  ```
  Expected: the six or seven commits from Tasks 1, 5 (if a fallback was taken), 8, 9, 10, 11, 12 and 13; a successful push; the sha that must now reach the droplet.

- [ ] **Step 2: Deploy again, so the running container carries the documented sha.** **[droplet]** The doc commits changed `HEAD`, and `--expect-sha` compares the container's `Build_info.git_sha` to the droplet's `git rev-parse HEAD` — so without this the final smoke run would fail on an assertion that is doing exactly its job.
  ```bash
  ssh ohcamel 'cd ~/OhCamel && setsid nohup bash -c "git pull --ff-only && deploy/deploy.sh --live" > ~/phase6/deploy-final.log 2>&1 < /dev/null & echo started'
  ```
  then poll `ssh ohcamel 'tail -5 ~/phase6/deploy-final.log'` until `==> Deployed`. Expected: a short build — only the `COPY . .` layer and `dune build` are invalidated by a docs change, the opam layer is cached.

- [ ] **Step 3: Run the full suite by hand and keep the output.** **[droplet]**
  ```bash
  ssh ohcamel 'cd ~/OhCamel && deploy/smoke.sh https://ohcamel.ajaiupadhyaya.com --live https://live.ohcamel.ajaiupadhyaya.com --expect-sha "$(git rev-parse HEAD)"' | tee /tmp/ohcamel-phase6/smoke-final.txt
  ```
  Expected: every assertion `PASS`, ending `  23 passed, 0 failed, 0 skipped` (Phase 2's eighteen plus Phase 5's five; the 404 line lists the 11 routes). If anything fails, do not edit the docs to match — run Task 1's rollback with `$PREV` from `/tmp/ohcamel-phase6/prev.sha`, then fix the cause.

- [ ] **Step 4: Record the run in the `Deployed` row.** **[mac]** Replace `docs/status.md`'s row as Phase 2's Task 17 left it,

  `| Deployed | <Phase 2's date>, both hosts, from `<7 of Phase 2's sha>` — both origins report it as `build.git_sha` on `/api/ops` — verified by the production smoke suite: 18 passed, 0 failed |`

  with the new date, the sha, and the counts from Step 3, in the same form — `| Deployed | 2026-09-03, both hosts, from `<7 of HEAD>` — both origins report it as `build.git_sha` on `/api/ops` — verified by the production smoke suite: 23 passed, 0 failed |`.

- [ ] **Step 5: Paste the suite's own output into *Numbers worth knowing*.** **[mac]** At the head of the measured block Task 11 created, in a fenced code block, the assertion lines from `/tmp/ohcamel-phase6/smoke-final.txt` verbatim (strip the ANSI colour codes with `sed -e 's/\x1b\[[0-9;]*m//g'`). Beneath it, one sentence of honesty about the sha:

  > The run above verified the commit immediately before this one: recording a smoke result necessarily creates a commit the verified image predates. The droplet is redeployed to this commit in the same sitting, and the next deploy's `--expect-sha` is what proves it.

- [ ] **Step 6: Commit and push.** **[mac]**
  ```bash
  cd /Users/ajaiupadhyaya/Documents/OhCamel && git add docs/status.md && git commit -m "docs: the smoke run that verified this deploy, and the sha it verified" && git push origin main
  ```

- [ ] **Step 7: Bring the droplet forward to that commit and confirm it is green.** **[droplet]** This closes the one-commit gap Step 5 admits to, and its smoke run needs no recording.
  ```bash
  ssh ohcamel 'cd ~/OhCamel && setsid nohup bash -c "git pull --ff-only && deploy/deploy.sh --live" > ~/phase6/deploy-close.log 2>&1 < /dev/null & echo started'
  ```
  then poll until `==> Deployed`, and finish with:
  ```bash
  ssh ohcamel 'cd ~/OhCamel && echo "checkout $(git rev-parse HEAD)" && curl -fsS https://ohcamel.ajaiupadhyaya.com/api/ops | python3 -c "import sys,json; d=json.load(sys.stdin); print(\"served  \", d[\"build\"][\"git_sha\"], \"uptime_s\", d[\"uptime_s\"])"'
  cd /Users/ajaiupadhyaya/Documents/OhCamel && git rev-parse HEAD && git status -sb
  ```
  Expected: the droplet's checkout, the served `git_sha` and the Mac's `HEAD` are the same 40-character sha; `git status` shows a clean tree with only the untracked `claudecodehandoff.md`, which belongs to another project and is never added.

- [ ] **Step 8: Clear the scratch.** **[mac]**
  ```bash
  ls -la /tmp/ohcamel-phase6/ && ssh ohcamel 'ls -la ~/phase6/'
  ```
  Read both once more against `docs/status.md` — anything measured here that is not in that file is about to be forgotten. Then, and only then, `rm -rf /tmp/ohcamel-phase6` and `ssh ohcamel 'rm -rf ~/phase6'`.

---

## Done when

- Both hosts run the sha at the tip of `origin/main`, and `/api/ops` on the demo origin says so.
- `deploy/smoke.sh … --live … --expect-sha` passes every assertion, and its output is in `docs/status.md`.
- `docs/status.md` carries, dated 2026-09-03: the route table, the resource figures, the startup and GARCH measurements, the computed-vs-quoted verdict, the `.dockerignore` result, the test count, the coverage fraction and the log findings — and no number older than that date is presented as current.
- `lib/verified.ml`'s three constants are measured today and the suite-count test passes.
- `README.md`'s *Watching it* quotes this deploy's assertions and describes the page; `docs/media/dashboard.png` is the deployed page at 1440.
- Neither spec nor `deploy/deploy.env.example` still says `0600`; the page design spec's header says when it shipped and what it measured.
- `dune build @fmt` passes and `make test` is green on the Mac.
