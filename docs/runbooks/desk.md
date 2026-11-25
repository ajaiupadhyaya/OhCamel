# The desk: what a page means and what to do

Completion task 1.11. `deploy/watch.sh` (every 5 minutes, as the `ohcamel` user) posts a one-line page to the URL in
`OHCAMEL_ALERT_URL` when one of the five things below is true. Every page names only a kind, a strategy or
estimator, a time and the section here. It never carries a position size, a limit or an observed value: read those
on the dashboard, not on your phone. Each kind pages at most once in 24 hours (per strategy or estimator where one
is named), and a restart does not re-page: the stamps live in `~/.local/state/ohcamel-watch/paged/`.

Every check below is read-only. **Nothing here asks you to reset the kill switch, edit `book.sexp` or restart
`ohcamel-live` during 09:25-17:10 ET.** Those are yours to decide, from the dashboard and `ssh ohcamel`, never from
the watch.

## One-time setup (owner)

Add one line to `/etc/ohcamel/ops.env` on the droplet (0640 root:ohcamel, the file that already holds
`SLACK_WEBHOOK_URL` and `HEALTHCHECKS_URL`), then wait for the next run:

    OHCAMEL_ALERT_URL=https://your-webhook-url

The watch posts `{"text": "..."}` (Slack-shaped; most webhooks accept it). The URL is a credential: it is read for
that key only, never sourced, never printed, never on a command line.
**While it is unset the watch logs each page as a `PAGE` line in `journalctl -t ohcamel-watch` and says
"OHCAMEL_ALERT_URL is not set" once a day.** Nothing reaches you.

The once-a-day `STATUS` line in the same journal says where the switch stands (for example
`desk status: halted by hand since 2026-11-16`). A by-hand halt does **not** page: you set it, and a daily page
would be noise. It is the one switch state that stays quiet.

## A tripped switch

**Page:** `PAGE trip: the kill switch has been tripped since <date>, across <n> sessions`.

**Means:** a limit tripped the kill switch (`kill_switch: tripped` in `/api/desk`), or a trip survived a restart,
and it has stood through at least two sessions. A trip refuses new orders (except a reduction while
`reduce_through_trip` holds) and cancels the open ones; it never sells anything. It does not clear itself
unless the book sets `auto_reset_after`.

**Read-only check:** open the dashboard's desk page (the switch card names the limit and the time); or
`ssh ohcamel` then `docker exec ohcamel-ohcamel-live-1 curl -s localhost:8081/api/desk | jq '.switch, .kill_switch'`;
the live log (`docker logs ohcamel-ohcamel-live-1 | grep -i trip`) says which limit and when.

**Owner action:** decide whether the limit was right. If the book really breached it, leave the switch and fix the
book (reduce positions at the venue, outside the desk). If it was a bad reading, reset from the dashboard, not from
a script, after you have looked at the limit on the dashboard. The agent never resets it.

## A sub-ledger break

**Page:** `PAGE subledger: a strategy sub-ledger does not sum to the account in <SYMBOL>`.

**Means:** the sum of the strategies' own positions (and the hand tickets' fills) in a name disagrees with the
account's position at two checks in a row (Task 1.6). While it stands, nothing that grows a strategy's position in
that name is sent. The page fires from the live log's `sub-ledger at a sync:` or `sub-ledger at the close of`
line within the last hour. The watch pages at onset and again after every nightly close while the break stands
(each its own once-a-day page, per symbol), so a standing break is not quiet after its first hour. The close's
"names checked, no break" line is not a break and does not page.

**Read-only check:** `docker logs --since 6h ohcamel-ohcamel-live-1 | grep 'sub-ledger'` (the line carries the
figures; keep them off chat), and the journal's `subledger_break` alert on the dashboard. Then compare the
account's position with the sum in the Alpaca paper dashboard.

**Owner action:** follow "A sub-ledger break" in `docs/status.md`: find the trade made outside the desk (a trade on
the Alpaca dashboard, a corporate action, a fill the desk never heard of), reverse it at the venue so the account
returns to the sub-ledger's sum, and let the next check find the name whole: one whole check clears a break. A
desk ticket never clears a break.

## Completions exhausted

**Page:** `PAGE completions: order completions exhausted for <strategy> until tomorrow's session`.

**Means:** the intake tried a strategy's order completions (orders that ended unfilled) or a stopped strategy's
flatten three times this session and will not try again until the next. The strategy is part-way to its target and
stays there; a stopped strategy may still hold shares. The log line is `its completion was tried 3 times this
session` or `its flatten was tried 3 times this session`.

**Read-only check:** `docker logs --since 2h ohcamel-ohcamel-live-1 | grep 'was tried'`; the dashboard's orders
and fills tables show which orders ended unfilled and why (halted names, a closed market, a rejection).

**Owner action:** usually nothing today: it retries tomorrow. If the cause is a name the venue will not trade or
a halted desk, fix that. If a stopped strategy still holds shares that matter, flatten them by hand at the venue
after reading the dashboard. Do not restart the live container during 09:25-17:10 ET to "retry".

A latched stop's flatten can also be refused by the pre-trade gate, not the venue. A stop's flatten is waived past
the exposure limits only (book, class and name caps); a VaR or ES limit is still judged. In a netted pair -- two
strategies holding opposite sides of one name, or a hedge leg -- closing one strategy's leg can create or worsen a
`var-cap` or `es-cap` breach, and that flatten is refused each time until completions run out. The refusal is in
the log beside the `was tried` line. That is the owner's call: flatten the pair together by hand at the venue (the
desk never un-stops a strategy; only the book's `(reinstated DATE)` does).

## A stale overlay

**Page:** `PAGE overlay: the signal overlay is <h> h <m> m old in session time (limit 4 h)`.

**Means:** the latest accepted exposure overlay is older than 4 **session** hours: the hours of 09:30-16:00 New
York on weekdays, from the older of the overlay's `as_of` and its receipt. Nights, weekends and weekdays with no
session row (holidays) do not count, so Friday's overlay is about an hour old at 09:31 on Monday. Past 4 session
hours the held factor decays to the fallback (it only ever shrinks the book). The watch cannot tell a holiday today
from a session that has not been recorded yet, so it counts today only while the clock says the market is open or
after today's session row exists; it errs toward silence.

**Read-only check:** the dashboard's overlay card, or `/api/desk | jq .overlay` (`state`, `as_of`, `age_s`);
the research service's log: `docker logs --since 2h ohcamel-ohcamel-research-1 | tail`.

**Owner action:** find out why the research service stopped writing the overlay (the container, its schedule, the
data feed). Fixing it is a deploy or a service restart: outside 09:25-17:10 ET for the live container, and never by
the agent. An absent overlay (none ever accepted) is a different condition and does not page here.

## A red risk-model score

**Page:** `PAGE varscore: the risk model's score is red for the <estimator> estimator`.

**Means:** the nightly backtest of the journaled risk forecasts (Task 1.10) shows an estimator whose exceedances
are too many for its confidence level at the Basel-style light (`var_score.scores[].light = red`). The model is
under-forecasting risk. **An absent light does not page**: fewer than 60 same-window sessions says
`n of 60 sessions scored`, and a simulated venue's `var_score` carries only a `reason`. Yellow does not page.

**Read-only check:** `/api/desk | jq .var_score` (n, exceptions, the two p-values, the light, `as_of`); the
public `desk.report` `var_score` table says the same without sizes.

**Owner action:** a research decision, not an emergency. Decide whether the limits that read this estimator
(`es-cap`, `var-cap`) are still safe, and whether to switch the estimator or the window. Any change is a book edit
and a restart, outside 09:25-17:10 ET. Record the decision in `docs/status.md`.

## Checking that the project is finished

`scripts/check-done.sh` prints seven lines, one for each item of the completion spec's *What "finished" means*. It is
read-only and takes the droplet's data as arguments, so the droplet's own files are never opened by it. **Owner command:**
pull the backups (`deploy/pull-backups.sh`), copy the newest `risk.mc_atlas`, `farm.sweep` and `vol.surface_league`
artifact directories and `trial_ledger.jsonl` out of the worker container with `docker cp` over `ssh ohcamel`, check out
the deployed sha, and run `scripts/check-done.sh --strict` with `--journal`, `--farm`, `--trial-ledger`, `--atlas`,
`--surface`, `--warehouse` and `--ledger https://ohcamel.ajaiupadhyaya.com/api/ledger`. The exact lines are in
[`docs/status.md`](../status.md) under *Running the definition of done*. A `FAIL` names what is wrong; `not checked`
names what the script was not given. Check 4 fails until INC-02 is approved, and that is the owner's to give. The artifacts the checks read exist on the droplet only after a deploy at or after `b5fabac` (held by the `594df16` rule until INC-02 is approved) and a nightly run at that sha; before then those lines read `FAIL` or `not checked`. Check 5 covers the atlas and the surface league only (`farm.sweep` is not regenerable; check 3 is the farm's evidence). Check 2 needs the `ohcamel` binary (`dune build ./bin/main.exe` in the checkout) and judges the desk's own sub-ledger rule.

## If the pages stop

A silent watch is a worse failure than a page. `healthchecks.io` (`HEALTHCHECKS_URL` in `/etc/ohcamel/ops.env`)
notices a watch that stopped running; `journalctl -t ohcamel-watch -n 50` shows the last runs.
