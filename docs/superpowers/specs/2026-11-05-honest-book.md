# Honest book — 5 Nov 2026

The public site and `main` are `9eeb0c5`. The research workstation is doing its job: the farm is 0 pass / 18 fail, EXP-Q01 fails its deflated Sharpe, and the worker finished 80 jobs with none failed. Two things a visitor can see are wrong, and the page that is supposed to say what is true is a month behind.

This spec is the decisions. The plan is `docs/superpowers/plans/2026-11-05-honest-book.md`.

## Decisions

**The kill switch stays armed.** It tripped because the example book's limits belong to a toy book, not to a 23-strategy incubation book on a down day. Do not disarm it and do not reset it until the new limits are what the process loaded. A limit trip does not survive a restart; a halt by hand does. Never POST `/api/desk/kill/reset` from this work.

**New portfolio limits**, on a $1,000,000 paper equity, `vol_target` 0.10, incubation gross 0.575:

| Limit | Now | New | Why |
|---|---|---|---|
| `book-cap` | $500,000 | **$700,000** | Steady-state gross is $575,000. $700,000 is 22% of room. A double size still trips. The design does not. |
| `var-cap` | $12,000 | **$30,000** | One-day 95% Gaussian VaR at a 10% vol target is about $10,400. $12,000 is a hair trigger. $30,000 is about 2.9× that: a book running near a 30% vol, not a down day. |
| `dd-cap` | 2% | **10%** | A 2% drawdown is a normal week. 10% is the vol target realized as a loss. Per-strategy stops stay at 15% of that strategy's capital. |

Leave `aapl-cap`, `nvda-cap`, `tech-cap`, and `energy-cap` alone. They are not what halted an ETF book. `kill_switch_trips_on` stays `(book-cap var-cap dd-cap)`.

Today's tape was SPY −4.4%. At a 57.5% gross that is about a 2.5% book move. It breaks a 2% drawdown cap and does not break a 10% one. That is the point.

**Name the trip in public, and nothing more.** `book_summary` gains `tripped_by`: the limit name, or null. No threshold, no observed value, no position. The Machine's kill-switch label reads `Kill switch: halted by <name>` when that name exists. The book step of the tour says so. An armed switch keeps the sentence it has now.

**An unadjusted split is not a return.** Recognition, one rule, shared with the fixture repair:

- close-to-close move beyond 30%, and
- previous close / open within 3% of a standard split ratio (the existing ratio list), and
- adjusted close moved beyond 30% as well.

Then divide **prior** OHLC and `adj_close` by the ratio and multiply prior volume. The ex-date bar stays. Say so in provenance, one note per split: `split-adjusted 4:1 before 2026-11-05 (vendor adj_close did not absorb it)`.

Apply this on the way **out** to a page: `MarketData.ohlcv` after a vendor fetch, and `_warehouse_bars` before a warehouse series becomes a return. Do not apply it in `readers.ohlcv` and do not apply it in the ingest. The warehouse keeps the vendor's bytes. `ops.data_integrity` keeps reading those bytes and keeps failing while they are dirty. A series whose adjusted close already absorbed the split does not match, so a later vendor fix does not get divided twice. A crash whose open is near the previous close does not match.

The archived XLK 2-for-1 and SPY decimal-shift files must still repair exactly as they do now. Decimal-shift repair stays in the fixture loader only.

**The ledger tells the truth as of this work, and it stops pinning a git sha.**

- Date `2026-11-05`.
- Delete `BACKUPS PAUSED` and `LIVE HOST BEHIND`.
- Limits row `WAREHOUSE FILE NOT COPIED`: the journal, the recorder, and `jobs.sqlite` are copied nightly; `warehouse.duckdb` is not file-copied; option snapshots are not in that copy until the snapshot method lands, and the same row then says they are exported read-only.
- Advisory `MODEL OUTPUT`: a research verdict does not promote a strategy. Incubation sizing is the book's, under the cap. Live sizing still requires a passing battery and the owner.
- Advisory `NO LIVE STRATEGY`: twenty-three strategies are sized as incubation on the paper book. None is `live`. The public site shows the count and the kill switch, not a position.
- P5's static sentence no longer says the universe is unfrozen. The row's live verdict remains the artifact's.
- Owner row `BOOK LIMITS` states the three numbers above. The dollar-volume sign on EXP-Q01 stays −1 until a new experiment.

**Docs.** `docs/status.md` line that says the stop rules are "Stage 3, not yet built" is replaced: the stop rules run at the close (`desk/stop.ml`); intraday strategies are not built. The 8 October "backups have never run" sentence is replaced with a pointer at the cron section. `docs/overview.md` "Where it runs" stops calling the public host a generated-feed demo and stops citing the 17 September deploy. It points at `docs/status.md` for what is deployed. The engine essay below that heading stays.

**HEAD.** With the built site mounted, `HEAD /api/health` is 200, the same as GET. `HEAD` of an unknown `/api/...` path is 404. `HEAD /` still returns the shell. The SPA catch-all must not own `/api` or `/api/*`.

**Option snapshots get a copy. The warehouse file does not.** Method `duckdb` stays reserved: a second process must not `CHECKPOINT` or file-copy a database the worker has open. New method `snapshot` opens `warehouse.duckdb` **read-only** in the Quant image (uid 10002) and writes `option-snapshots-YYYY-MM-DD.parquet` from `option_snapshots`. A failure of that line fails the backup run the way a sqlite line already does. It never writes a partial copy over the live database.

**Kronos stays.** Ten of twenty prospective days is the minimum for a score, not a sunset. The job already changes its verdict text at twenty days. No schedule change.

## Non-goals

- No new risk engine, no kiosk mode, no scenario grid.
- No edit to strategy stop rules (15% / 250 sessions).
- No public position, size, or limit threshold.
- No reset of the kill switch by an HTTP client in this work.
- No deploy inside 09:25–17:10 America/New_York for anything that restarts `ohcamel-live`.
- If `ssh ohcamel` does not authenticate, the live book is not guessed at. The committed example and `check-book` are the deliverable, and the host step is reported blocked.
