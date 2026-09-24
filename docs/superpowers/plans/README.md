# Plans index

This directory is the project's plan history. Every file that was ever committed here stays, unedited except for one status header line each: it is either the historical record of a finished, merged phase, or a superseded draft. The active plan is named below first.

**Active plan:** `docs/superpowers/plans/2026-09-19-final-completion.md` — "the finish." Design doc: `docs/superpowers/specs/2026-09-19-the-finish.md`. Ledger: `.superpowers/sdd/2026-09-19-final/progress.md` (gitignored). It replaces the A4 plan below and the separate A5 and A6 phases, which were never written as their own plan documents (see the design doc §2).

## Superseded, never merged

- **`2026-09-19-desk-a4-depth.md`** — the A4 (risk depth) plan. It was never merged to `main`; it exists only on branch `desk/a4-depth` at `701f45c`, so it is not a file in this directory or on `main`. Superseded whole by `2026-09-19-final-completion.md`. Its four pure tasks are finished and carried forward as code, not as plan (`lib/long_panel.ml`, `lib/factor_model.ml`, `lib/liquidity.ml`, and Cornish–Fisher in `lib/risk_metrics.ml`); its remaining fourteen tasks and its twelve rulings are carried forward as tasks and rulings in the new plan. Task 7 of the new plan tags this branch tip `archive/a4-plan-v2` (housekeeping, after owner step O1) so the file resolves by tag even though it never lived on `main`. Its ruling 5 was amended to a 0.01 conditioning floor (not 1e-8) and its line 363's `1e-8` is a superseded code sketch, per its own line 97 and per `docs/superpowers/specs/2026-09-19-the-finish.md` §2 — the file's body is left exactly as written because it is evidence of how that decision was reached, and this index states the amendment instead of editing it. Because the file is not present on `main` or in this worktree, its own header line is added on `desk/a4-depth` (Task 7's branch), not here.

## Historical record of a finished, merged phase

Each file below carries its own one-line header (added 2026-09-19) pointing at the active plan. Listed in the order the work happened:

| Plan | Phase |
|---|---|
| `2026-08-19-readable-front-door.md` | The README rewrite against the code as it exists |
| `2026-08-25-risk-engine-roadmap.md` | The eight-phase risk-engine roadmap (EWMA, crisis backtests, options/Greeks, coverage) |
| `2026-09-02-phase-1-validated-signal.md` | One strategy through the charter's battery |
| `2026-09-02-the-page-phase-1-plumbing.md` | The page, phase 1: extraction into `web/`, no behaviour change |
| `2026-09-02-the-page-phase-2-ops.md` | The page, phase 2: build sha and operations surface |
| `2026-09-02-the-page-phase-3-extraction.md` | The page, phase 3: the six CLI reports extracted under the byte-identical gate |
| `2026-09-02-the-page-phase-4-graph.md` | The page, phase 4: the graph on the wire (wrap-up folded into the finish-line plan) |
| `2026-09-02-the-page-phase-5-argument.md` | The page, phase 5: the reports and the argument (folded into the finish-line plan) |
| `2026-09-02-the-page-phase-6-deploy.md` | The page, phase 6: deploy and measure (folded into the finish-line plan) |
| `2026-09-10-the-page-finish-line.md` | The page's finish line — 10 of 10, deployed |
| `2026-09-12-desk-a1-record.md` | Desk A1 — the record: journal, venue interface, session close |
| `2026-09-12-desk-a2-orders.md` | Desk A2 — the order manager: rules, gate, journal, venue, TCA |
| `2026-09-12-desk-w1-figure.md` | Desk W1 — Figure 1 |
| `2026-09-17-desk-w2-site.md` | Phase W2 — the site as a navigated desk |
| `2026-09-18-desk-a3-signals.md` | Phase A3 — signals: the contract, the intake, the research battery |

None of these fifteen files is edited otherwise, and none is deleted: they are the project's audit trail of how each decision was reached.
