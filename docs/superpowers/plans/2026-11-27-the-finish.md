# The finish -- the last steps to a finished OhCamel (2026-11-27)

The owner, 2026-11-27: "come up with a plan that FINISHES this project in its entirety ... FULL PERMISSIONS, FULL
AUTHORITY". **Finished** means what the completion spec says it means
(`docs/superpowers/specs/2026-11-15-the-completion.md`, *What "finished" means*): `scripts/check-done.sh --strict`
prints seven `ok` lines against the droplet's own data, with the desk trading INC-02 on paper. This plan is the
shortest dependable path there from the state of 2026-11-27 13:30 ET:

- `main` = hosts = `a10eeb4` (deployed 13:26 ET with the INC-02 book); INC-02 approved by the owner; desk **halted
  by hand**; INC-01's carried positions still held; worker wall-limit fix live.

Every decision the owner would otherwise be asked is taken here by the agent under that authority, recorded in the
rulings file (`2026-11-15-the-completion-rulings.md`, section *The finish*) with what it costs if wrong. Paper only
throughout; no real money moves.

## F1. An owner flatten on the desk (Roster-6(a) has no mechanism today)

A halted desk refuses every order (R2 off, as approved); a sale at the venue breaks the sub-ledger; a hand-written
signal document collides with the producers' sequences. So the desk gets an explicit owner action:
`POST /api/desk/flatten` (live host only; the same `X-OhCamel-Desk` + same-origin guard as `kill/reset`), naming
strategies or `all`, which sends each named strategy's reduce-only legs to zero through the OMS -- journaled under
that strategy with the note `owner-flatten`, waived past the halt and the exposure limits as a stop's flatten is,
never increasing a position or crossing zero, idempotent while its orders may fill. The demo host answers 405.
Tests: OCaml unit tests on the leg construction and the halt waiver, and the desk_async soak case extended (a
halted desk flattens on the owner's word and stays halted). A runbook entry.

## F2. The pooled test's two parked defects (final review, items 1 and 2)

1. `/api/desk/sessions` serves only the newest 250 closes, so the 500-session test stalls at 250: the route takes
   `?since=DATE` and serves every close from it; `report.py` asks from INC-02's start.
2. The verdict is recomputed nightly and could flip on a night the rate feed fails: once session 500 is evaluated
   the row is written to a durable file on the worker volume and every later run serves that, never recomputes.

## F3. Decisions the owner left open, taken under the authority

- R3's two riding questions (EXP-I01–I03 and Q01's holdout; cells that cannot reach three regimes): the spec's
  default stands for both, recorded as the agent's ruling.
- USO: measured 151 bp on IEX's own book, above every tier. Re-measured on the consolidated (SIP) historical quotes
  Alpaca serves free past 15 minutes (`tools/measure_spreads.py --feed sip`); its tier is that measurement's, and if
  that too is above 8 bp it gets a fourth tier at its measured half-spread rounded up.
- EODHD (about $20 a month): not bought. The delisted-name build stays switched off and says so; no payment is made
  by the agent.
- The two never-parametrized farm rows stay out of the live book until a freeze carries them.

## F4. Droplet chores that need the final image

- The kernel bench on the deployed image; its row committed; it takes effect with the final deploy.
- The integrity lamp: name the shut-market ex-dates in `DRIFT_EXCEPTIONS` (the warning `docs/status.md` gives).
- `web/execution.js` renders the per-fill cost fields (deferred from the reviews).

## F5. The final deploy, the flatten, the start

1. Merge F1, F2, F4; CI green; images published; deploy `--live` outside market hours (Saturday 2026-11-28).
2. Enqueue the nightly products once at the final sha (farm, atlas, surface league, desk.report) so check 3 and 5
   read artifacts the deployed sha made; record the surface league's first droplet runtime.
3. Monday 2026-11-30, in session: check the roster's names for splits since 2026-11-05; run the owner flatten for
   every INC-02 strategy; confirm every roster strategy flat in the journal.
4. Write `Roster-6(a) flatten completed: 2026-11-30` in the pre-registration; deploy it (`--public-only` suffices:
   the worker's desk.report reads it).
5. After the close: lift the hand's halt (`POST /api/desk/kill/reset`, owner's authority given 2026-11-27). INC-02's
   first session is Tuesday 2026-12-01.
6. Tuesday after the evening chain: run `scripts/check-done.sh --strict` against the droplet's data by the owner's
   command in `docs/status.md`; every line `ok` is finished. Anything not `ok` is fixed and the check rerun.

## Not the agent's, by construction

- `OHCAMEL_ALERT_URL`: `/etc/ohcamel` is root's (no sudo for the deploy user since 2026-11-03) and the URL is a
  credential for a channel only the owner receives. One command, as root:
  `printf 'OHCAMEL_ALERT_URL=%s\n' '<url>' > /etc/ohcamel/ops.env && chmod 640 /etc/ohcamel/ops.env && chgrp ohcamel /etc/ohcamel/ops.env`.
- Cboe's HTTP 403 to the droplet and to CI: an outside block, not worked around. Option snapshots have no new day
  until Cboe answers again; the pages say so.
