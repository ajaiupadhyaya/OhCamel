# The page — the finish line

*2026-09-10. This replaces everything that was left of the page redesign: the Phase 4 wrap-up and all
of Phases 5 and 6, which came to 29 plan tasks plus a review and a fix wave per phase. The owner asked
for ten things and for results, not a longer list. The spec
(`docs/superpowers/specs/2026-09-02-the-page-design.md`) still describes what the page is. These ten
items decide how much of it gets built, and in what order.*

**Already done:** Phases 1–3 are merged and deployed. All 14 Phase 4 tasks are committed on
`the-page/phase-4` (302 tests). The engine itself has been finished since 2026-08-25.

**How the work runs now:** the code gets written directly: no per-task briefs, no per-task reviewer,
no dispatch-and-relay. An item is done when `make test` is green, `dune build @fmt` is clean, and the
evidence named in its row exists. Each merge to `main` gets one review pass, and only real bugs get
fixed in it. The Phase 4–6 plans stay in the repo as a parts bin. Encoders, routes and the GARCH domain
are worked out there in detail, so the code can be borrowed from them. They no longer set the order,
the process, or the scope.

## The ten

| # | Item | Done when |
|---|------|-----------|
| 1 | **Figure 1 fits the first screen.** Geometry-only change to `web/graph.js`: per-column widths, a tighter vertical rhythm, limits packed into their rank columns. A band dims only when every member is stale (`price[S] × 5 · 1 stale`). The garch slot collides with nothing. | A 1440×900 screenshot of `/` shows the lit graph whole and the top of the ledger. The existing Playwright checks still pass. |
| 2 | **Phase 4 merged.** One review pass over `950d633..HEAD`, real bugs fixed, merged to `main`, pushed. | CI green on `main`. |
| 3 | **The graph is live.** Deploy both hosts from `main` on the droplet. | `deploy.sh --live` ends in `Deployed` with smoke green, and a screenshot of https://ohcamel.ajaiupadhyaya.com shows the drawn graph lit by frames. |
| 4 | **`/api/reports`.** The scaling probe, the synthetic battery, the crisis battery and the options walk are computed once before the socket binds and encoded as one cached JSON string. The route is in the routes table. | Server tests pin the shape: nulls rather than NaN, 9 + 9 validation rows, 3 scaling rows. The demo serves it. |
| 5 | **`/api/reports/garch`.** The GARCH study runs on a second domain after listen. The route reports `computing` with a count, then `done` with its rows. `/api/ops` gets a `reports` slot. | Tests cover both states over a tiny study. The local demo reaches `done`. |
| 6 | **`web/charts.js`.** Hand-drawn inline SVG for the forms the sections need: scaling dot-and-line, attribution paired bars, tenor buckets, exceedance strips, crisis timelines, GARCH whiskers, stress bars. | Each form renders from real `/api/reports` or `/api/stress` data in a screenshot. |
| 7 | **The argument on the page.** §01–§07 and §09 under the README's headings, each tagged LIVE, COMPUTED or QUOTED, with the computed-vs-quoted line checked against `web/quoted.json`. §08 is short text: the build sha, what the smoke suite asserts, and the quoted test count and coverage. | A full-page screenshot reads top to bottom. The computed-vs-quoted lines print on the local demo. |
| 8 | **Phase 5 merged.** `deploy/smoke.sh` asserts `/api/reports` (18 rows), `/api/reports/garch` reaching `done`, and `/api/stress` (12 scenarios). One review pass, then merge and push. | CI green on both legs, including the README pins. |
| 9 | **The reports are live.** Deploy both hosts again. | Smoke is green. On the demo origin, each computed-vs-quoted line reads *agrees* or names the cells that differ, and a screenshot shows it. |
| 10 | **Close the books.** Re-measure the test count and coverage into `lib/verified.ml`. Take a fresh `docs/media/dashboard.png`. Update the README's *Watching it* and `docs/status.md` to what shipped, with one line on rollback. Mark the spec's header as shipped. | Pushed and CI green. The project is finished. |

## Cut, and why that is fine

- **A separate whole-branch review and fix wave per phase.** One review pass sits inside items 2 and 8.
- **§08's in-browser smoke run and the frame-arrival strip.** The smoke suite already runs on every
  deploy. Re-running it in the browser was a flourish, and §08 says in text what the suite checks.
- **The quoted bench chart on a log axis.** It becomes a table in the quoted ink. Six numbers read
  fine as a table.
- **A filtered graph fragment at the head of every section.** These get built only if
  `OhCamelGraph.filter` produces them without new layout work. Otherwise a section names its nodes in text.
- **Phase 5 Task 9's GARCH completion log line**, which only existed for the deploy to grep. The
  smoke suite polls the route instead.
- **Phase 6's rollback runbook** (rollback is one line: check out the last good sha on the droplet
  and rerun `deploy.sh --live`), the first-five-minutes log read, the separate `.dockerignore` check (a
  successful build on the droplet is that check), a measurement study of startup cost and GARCH wall
  time on the droplet (what the deploy prints gets recorded), the stale-lines task, and the redeploy
  that only verified the docs.
- **Phase 3's six carried minor cleanups.**

## Owner-gated

Nothing blocks. Deploys need no password, because the live smoke asserts the live host *refuses*
anonymous callers. Seeing the live page with your own eyes needs the basic-auth password, and that
stays optional.
