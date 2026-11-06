# Honest book Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the paper book's limits match the incubation book, stop printing an unadjusted split as a return, and make the public ledger and the health check tell the truth.

**Architecture:** Limits change in the committed example book, which is the contract the live book is regenerated from. The public summary gains the tripped limit's name and nothing else. Split repair runs on the way out to a page and never on the way into the warehouse, so the integrity lamp stays a lamp. The SPA route stops claiming `/api`. Option snapshots are exported read-only; the DuckDB file is not copied.

**Tech Stack:** Python 3.12 (FastAPI, pandas, DuckDB), TypeScript (Vitest), OCaml (Alcotest on `book.example.sexp`), bash (`deploy/backup.sh`).

**Spec:** `docs/superpowers/specs/2026-11-05-honest-book.md`

## Global Constraints

- Kill switch stays armed on `(book-cap var-cap dd-cap)`. Never POST `/api/desk/kill/reset`.
- `book-cap` $700,000, `var-cap` $30,000, `dd-cap` 0.10. Other limits unchanged.
- Public book summary may add `tripped_by` (a limit name or null). No threshold, observed value, size, or position name beyond the scopes already published.
- Split rule: close-to-close beyond 30%, previous close / open within 3% of a ratio in the existing list, and adjusted close beyond 30%. Divide prior OHLC and `adj_close` by the ratio; multiply prior volume. Leave the ex-date bar. Note: `split-adjusted 4:1 before YYYY-MM-DD (vendor adj_close did not absorb it)`.
- `readers.ohlcv` and the bar ingest stay raw. `ops.data_integrity` must still fail on a dirty warehouse bar.
- `duckdb` backup method stays reserved. The new method is `snapshot`, read-only, Quant image uid 10002.
- Do not restart `ohcamel-live` inside 09:25–17:10 America/New_York.
- Kronos is unchanged.
- Ledger date is `2026-11-05`. No git sha in a ledger row.

## Review Focus

- A crash whose open is near the previous close must come back unrepaired, including a −40% adjusted move. Test in Task 5.
- A series whose adjusted close already absorbed the split must not be divided again. Test in Task 5.
- `tripped_by` must not carry a threshold or a quantity. The existing leak strings in `test_book_summary_is_config_and_counts` stay, and the new column is asserted null or a limit name. Test in Task 4.
- With a built `web/dist` mounted, `HEAD /api/health` is 200 and `HEAD /api/no-such` is 404. Test in Task 1.
- After the read-path repair, an integrity run over a warehouse bar with MTUM's shape still returns `split_suspect` and `gating` true. Test in Task 5.

---

### Task 1: HEAD /api/health

**Files:**
- Modify: `quant/src/ohcamel_quant/api/app.py` (the `/api/health` route and the SPA catch-all)
- Test: `quant/tests/test_scaffold.py`

**Interfaces:**
- Consumes: `create_app`, `WEB_DIST`
- Produces: `HEAD /api/health` → 200 with the same JSON keys as GET when a body is returned; status 200 either way. Unknown `/api/*` stays 404.

- [ ] **Step 1: Write the failing test**

Mount a temporary dist so the catch-all exists. Patch `ohcamel_quant.api.app.WEB_DIST` to a directory that contains `index.html` with the literal text `SHELL`, then `create_app()`.

```python
def test_head_health_is_not_stolen_by_the_shell(tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text("SHELL", encoding="utf-8")
    monkeypatch.setattr("ohcamel_quant.api.app.WEB_DIST", tmp_path)
    from ohcamel_quant.api.app import create_app
    from fastapi.testclient import TestClient
    c = TestClient(create_app())
    assert c.get("/api/health").status_code == 200
    assert c.head("/api/health").status_code == 200
    assert c.head("/api/no-such").status_code == 404
    assert c.head("/").status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd quant && OHCAMEL_QUANT_OFFLINE=1 .venv/bin/pytest tests/test_scaffold.py::test_head_health_is_not_stolen_by_the_shell -q`

Expected: FAIL, `HEAD /api/health` is 404.

- [ ] **Step 3: Stop the SPA route from owning `/api`**

In `app.py`, replace the catch-all `Route` with a subclass whose `matches` returns `Match.NONE` for path `/api` and any path starting `/api/`. Keep methods `GET` and `HEAD` for every other path. Do not change the health handler's body. If, after the catch-all steps aside, `HEAD /api/health` is 405, declare that route with methods `GET` and `HEAD` explicitly. The handler is unchanged.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd quant && OHCAMEL_QUANT_OFFLINE=1 .venv/bin/pytest tests/test_scaffold.py::test_head_health_is_not_stolen_by_the_shell tests/test_scaffold.py::test_health -q`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add quant/src/ohcamel_quant/api/app.py quant/tests/test_scaffold.py
git commit -m "$(cat <<'EOF'
fix: HEAD /api/health answers 200 when the site is mounted

The shell route was the only HEAD match under /api, so a probe read a live API as missing.

EOF
)"
```

---

### Task 2: Ledger

**Files:**
- Modify: `quant/web/src/pages/ledger/entries.ts`
- Modify: `quant/web/src/pages/ledger/entries.test.ts`

**Interfaces:**
- Consumes: nothing from Task 1
- Produces: `LEDGER_AS_OF === "2026-11-05"`. No item named `BACKUPS PAUSED` or `LIVE HOST BEHIND`. Rows below, exact detail strings.

- [ ] **Step 1: Write the failing test**

Replace the assertions that require `2026-10-08`, `/var/backups/ohcamel` as an outstanding root step, and `88ebc96`. Keep the tests that every product row has a real kind, every cut is listed, labels are unique, and no detail ends in `?`.

```typescript
it("is dated 2026-11-05 and does not pin a deploy", () => {
  expect(LEDGER_AS_OF).toBe("2026-11-05");
  for (const e of LEDGER) expect(`${e.item} ${e.detail}`).not.toMatch(/\b[0-9a-f]{7}\b/);
  expect(LEDGER.map((e) => e.item)).not.toContain("BACKUPS PAUSED");
  expect(LEDGER.map((e) => e.item)).not.toContain("LIVE HOST BEHIND");
});

it("says incubation is sized on the paper book and the warehouse file is not copied", () => {
  const text = LEDGER.map((e) => `${e.section} ${e.item} ${e.detail}`).join("\n");
  expect(text).toMatch(/twenty-three strategies are sized as incubation/i);
  expect(text).not.toMatch(/No strategy on this site trades/);
  expect(text).toMatch(/option snapshots/);
  expect(text).toMatch(/\$700,000/);
  expect(text).toMatch(/\$30,000/);
  expect(text).toMatch(/10%/);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd quant/web && npm test -- src/pages/ledger/entries.test.ts`

Expected: FAIL on the date and the missing rows. Use the web package's existing vitest script if `npm test` is not that script (`package.json` `test`).

- [ ] **Step 3: Replace the stale rows**

Set `LEDGER_AS_OF` to `2026-11-05`.

- Delete `BACKUPS PAUSED` and `LIVE HOST BEHIND`.
- Add a LIMITS row `WAREHOUSE FILE NOT COPIED` with detail `The journal, the recorder, and jobs.sqlite are copied nightly. warehouse.duckdb is not file-copied. Option snapshots cannot be re-fetched and are not in the nightly copy.`
- Replace `MODEL OUTPUT` detail with `A research verdict does not promote a strategy. Incubation sizing is the book's, under the cap. Live sizing requires a passing battery and the owner.`
- Replace `NO LIVE STRATEGY` detail with `Twenty-three strategies are sized as incubation on the paper book. None is live. The public site shows the count and the kill switch, not a position.`
- In the P5 detail, delete the sentence that the universe is unfrozen. Keep the description of the model. Do not hard-code FAIL; the row already reads the artifact.
- Replace owner `BACKUP DIRECTORY` with `BOOK LIMITS`, detail `book-cap $700,000, var-cap $30,000, dd-cap 10%. The kill switch stays armed on those three.`
- Replace owner `EXP-Q01 UNIVERSE` detail with `Frozen 2026-10-08. The I-Q01-7 dollar-volume sign stays −1 until a new experiment.`

- [ ] **Step 4: Run test to verify it passes**

Run: `cd quant/web && npm test -- src/pages/ledger/entries.test.ts`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add quant/web/src/pages/ledger/entries.ts quant/web/src/pages/ledger/entries.test.ts
git commit -m "$(cat <<'EOF'
docs: the ledger matches the book that is actually running

The 8 October rows still said nothing trades and the live host was a sha from September.

EOF
)"
```

---

### Task 3: Operator docs

**Files:**
- Modify: `docs/status.md` (the "Stage 3, not yet built" sentence, and the 2026-10-08 "Not running yet" backup bullet)
- Modify: `docs/overview.md` (the "Where it runs" list only)

**Interfaces:**
- Consumes: the limit numbers and the ledger decisions from the spec
- Produces: no remaining sentence that the stop rules are unbuilt; no remaining sentence that the public host is a generated feed

- [ ] **Step 1: Write the failing check**

There is no docs test harness. The check is `rg`.

```bash
rg -n "Stage 3, not yet built" docs/status.md
rg -n "generated feed" docs/overview.md
```

Expected before the edit: both match.

- [ ] **Step 2: Confirm they match**

Run the two commands. Expected: one match each.

- [ ] **Step 3: Replace those sentences and nothing around them**

In `docs/status.md`, replace `**Stop rules and the record scoring:** Stage 3, not yet built. **Intraday strategies:** Stage 2, not yet built.` with `**Stop rules** run at the close (`desk/stop.ml`). **Intraday strategies** are not built.`

Replace the bullet that begins `**Not running yet (2026-10-08):**` with: `Nightly copies run from the ohcamel user's cron (see *What the owner holds*). The warehouse file is not among them. Option snapshots are exported by the snapshot method in deploy/backup.list.`

In `docs/overview.md`, replace the four bullets under `## Where it runs` with:

- **Public site:** [ohcamel.ajaiupadhyaya.com](https://ohcamel.ajaiupadhyaya.com), OhCamel Quant on real data. No credentials.
- **Live host:** `live.ohcamel.ajaiupadhyaya.com`, the OCaml engine and the paper desk, behind a password.
- **Infrastructure:** one DigitalOcean droplet (2 vCPU, 4 GB). What is deployed is `docs/status.md`.
- **Deploy check:** unchanged text of the existing deploy-check bullet, from `deploy/deploy.sh` through the end of that bullet. Do not rewrite it.

Leave the rest of `overview.md` and the rest of `status.md` alone, including the engine essay and the historical log.

- [ ] **Step 4: Run the check**

```bash
rg -n "Stage 3, not yet built|generated feed" docs/status.md docs/overview.md
```

Expected: no matches. `rg` exits 1, which is success for this step.

- [ ] **Step 5: Commit**

```bash
git add docs/status.md docs/overview.md
git commit -m "$(cat <<'EOF'
docs: status and the overview agree about what is deployed

The stop rules have run since 2 November, and the public host has not been the synthetic demo since September.

EOF
)"
```

---

### Task 4: Name the limit that tripped

**Files:**
- Modify: `quant/src/ohcamel_quant/signals/report.py` (`BOOK_COLUMNS`, `_kill_switch`)
- Modify: `quant/src/ohcamel_quant/machine/graph.py` (`_book_summary`, the kill node)
- Modify: `quant/web/src/pages/machine/tour.ts` (step 7, the book)
- Test: `quant/tests/test_signals_report.py`
- Test: `quant/tests/test_machine_graph.py`
- Test: `quant/web/src/pages/machine/tour.test.ts`

**Interfaces:**
- Consumes: engine `/api/ops` field `alerts.tripped_by` (string limit name, or JSON null)
- Produces: `BOOK_COLUMNS` includes `tripped_by` after `kill_switch`. `_book_summary` returns `tripped_by: str | None`. Kill node metric `tripped_by`. Node title `Kill switch: halted by <name>` when state is `halted` and the name is a non-empty string; otherwise the title stays `Kill switch: <state>`.

- [ ] **Step 1: Write the failing tests**

In `test_signals_report.py`, extend `test_book_summary_is_config_and_counts` so an armed book has `tripped_by` null, and add:

```python
def test_a_tripped_switch_publishes_the_limit_name_only(tmp_path):
    routes = _full()
    routes["/api/ops"] = _ok({**OPS, "alerts": {**OPS["alerts"], "kill_switch": "tripped", "tripped_by": "dd-cap"}})
    with _Engine(routes) as eng:
        row = report.run({}, _ctx(tmp_path, eng.url)).tables["book_summary"].iloc[0]
    assert row["kill_switch"] == "halted" and row["tripped_by"] == "dd-cap"
    assert "0.02" not in row.to_json() and "threshold" not in row.to_json()
```

Use the fixture names already in that file (`OPS`, `_ok`, `_full`, `_Engine`, `_ctx`). If `OPS["alerts"]` is shaped differently, spread the object the existing armed test uses.

In `test_machine_graph.py`, build a summary whose kill word is `halted` and `tripped_by` is `dd-cap`. Assert the kill node's title is `Kill switch: halted by dd-cap` and its metric `tripped_by` is `dd-cap`. Assert a summary with kill `armed` and `tripped_by` null still titles the node `Kill switch: armed`.

In `tour.test.ts`, add a case that clones the fixture, sets the book's `kill` to `halted` and the kill node's `tripped_by` to `dd-cap`, and expects the book step to contain `The kill switch is halted by dd-cap.` The existing armed sentence stays on the unmodified fixture.

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd quant && OHCAMEL_QUANT_OFFLINE=1 .venv/bin/pytest tests/test_signals_report.py::test_a_tripped_switch_publishes_the_limit_name_only tests/test_machine_graph.py -q -k "halted by"
cd quant/web && npm test -- src/pages/machine/tour.test.ts
```

Expected: FAIL because `tripped_by` is absent.

- [ ] **Step 3: Thread the name through, and only the name**

`_kill_switch` returns a third value, `tripped_by: str | None`. It is the engine's string when `kill_switch` maps to `halted` and that string is non-empty. Otherwise null. Do not read `firing`, `recent`, or any numeric alert field.

Add `tripped_by` to `BOOK_COLUMNS` immediately after `kill_switch`. Write null as JSON null, not the empty string.

`_book_summary` copies it onto the dict. The kill node title uses it only in the halted case. Tour step 7, when `kill` is `halted` and the kill node has a non-empty `tripped_by`, adds the sentence `The kill switch is halted by <name>.` When halted with no name, the sentence is `The kill switch is halted.` The armed sentence is unchanged.

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd quant && OHCAMEL_QUANT_OFFLINE=1 .venv/bin/pytest tests/test_signals_report.py tests/test_machine_graph.py -q
cd quant/web && npm test -- src/pages/machine/tour.test.ts src/pages/machine/readings.test.ts
```

Expected: PASS. `readings.test.ts` must still refuse position names in the reading that is not the book summary.

- [ ] **Step 5: Commit**

```bash
git add quant/src/ohcamel_quant/signals/report.py quant/src/ohcamel_quant/machine/graph.py \
  quant/web/src/pages/machine/tour.ts quant/tests/test_signals_report.py \
  quant/tests/test_machine_graph.py quant/web/src/pages/machine/tour.test.ts
git commit -m "$(cat <<'EOF'
fix: the public book says which limit halted it

The summary already showed that the switch was tripped. It did not show by which limit, so a halt read as a caption.

EOF
)"
```

---

### Task 5: Do not publish an unadjusted split as a return

**Files:**
- Create: `quant/src/ohcamel_quant/data/splits.py`
- Modify: `quant/src/ohcamel_quant/data/fixtures.py` (`_repair` calls `present` for the split half)
- Modify: `quant/src/ohcamel_quant/data/market.py` (`ohlcv`, after `fetch_ohlcv`)
- Modify: `quant/src/ohcamel_quant/warehouse/readers.py` (`_warehouse_bars` only)
- Modify: `quant/src/ohcamel_quant/products/data_integrity.py` (import `SPLIT_RATIOS` from `splits`)
- Test: `quant/tests/test_splits.py`
- Test: `quant/tests/test_data_prices.py` (the archived-file test must still pass)
- Test: `quant/tests/test_products_research_ops.py`

**Interfaces:**
- Consumes: columns `open, high, low, close, adj_close, volume` on a `DatetimeIndex`
- Produces:

```python
SPLIT_RATIOS: tuple[float, ...]  # the tuple now in fixtures._SPLIT_RATIOS, moved here

def present(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Copy of df with prior bars divided through each unadjusted split. Notes are the spec's sentence, one per ex-date, oldest first. Idempotent on an already adjusted series."""
```

- [ ] **Step 1: Write the failing tests** in `quant/tests/test_splits.py`

Build a two-row frame, index `2026-11-04` and `2026-11-05`. First row OHLC and `adj_close` all `272.86`, volume `1_000_000`. Second row open `66.76` (overnight ratio `272.86 / 66.76` ≈ 4.087, within 3% of 4), high `68`, low `62`, close `62.60`, `adj_close` `62.60`, volume `8_000_000`.

```python
def test_mtum_shape_is_a_four_for_one_and_the_prior_close_is_divided():
    out, notes = present(mtum)
    assert notes == ["split-adjusted 4:1 before 2026-11-05 (vendor adj_close did not absorb it)"]
    assert out.iloc[0]["close"] == pytest.approx(272.86 / 4)
    assert out.iloc[1]["close"] == pytest.approx(62.60)
    ret = out.iloc[1]["adj_close"] / out.iloc[0]["adj_close"] - 1
    assert ret == pytest.approx(62.60 / (272.86 / 4) - 1)
    assert abs(ret) < 0.20

def test_a_second_pass_does_not_divide_again():
    once, _ = present(mtum)
    twice, notes = present(once)
    assert notes == []
    pd.testing.assert_frame_equal(twice, once)

def test_a_crash_with_no_split_ratio_is_unchanged():
    # prev close 100, open 98, close 60, adj_close 60
    out, notes = present(crash)
    assert notes == []
    assert out.iloc[1]["close"] == 60

def test_an_adjusted_history_is_unchanged():
    # prev close 68.215, open 66.76, close 62.60, adj_close follows close; overnight ~1.02
    out, notes = present(adjusted)
    assert notes == []
```

Add one integrity test, in `test_products_research_ops.py` or `test_splits.py`, that runs `data_integrity.check_bars` on the raw MTUM-shaped frame and asserts a `split_suspect` row. That function must not import `present`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd quant && OHCAMEL_QUANT_OFFLINE=1 .venv/bin/pytest tests/test_splits.py -q`

Expected: FAIL, `present` is not defined.

- [ ] **Step 3: Implement `present` and call it on the way out**

Move the ratio tuple to `splits.SPLIT_RATIOS`. `fixtures._SPLIT_RATIOS` becomes an alias so existing imports keep working. `fixtures._repair` calls `present` first, then does the decimal-shift repair it already does, and concatenates notes.

`data_integrity` imports `SPLIT_RATIOS` from `splits` instead of `fixtures._SPLIT_RATIOS`.

`MarketData.ohlcv`, on the online path only, calls `present` on `fetch_ohlcv`'s frame and appends each note to the dataset's provenance notes. Do not call `present` inside `prices.fetch_ohlcv` or `prices.PROVIDERS`: the ingest uses those and must store vendor bytes.

`_warehouse_bars` calls `present` on each ticker frame before the frames are returned. Attach the notes to the dataset `warehouse_returns` and `warehouse_prices` build. `readers.ohlcv` does not call `present`.

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd quant && OHCAMEL_QUANT_OFFLINE=1 .venv/bin/pytest tests/test_splits.py tests/test_data_prices.py::test_the_load_time_repair_still_fixes_the_archived_vendor_files tests/test_products_research_ops.py -q -k "integrity or split or repair"
```

Expected: PASS, including the archived XLK repair and the integrity tests.

- [ ] **Step 5: Commit**

```bash
git add quant/src/ohcamel_quant/data/splits.py quant/src/ohcamel_quant/data/fixtures.py \
  quant/src/ohcamel_quant/data/market.py quant/src/ohcamel_quant/warehouse/readers.py \
  quant/src/ohcamel_quant/products/data_integrity.py quant/tests/test_splits.py \
  quant/tests/test_products_research_ops.py
git commit -m "$(cat <<'EOF'
fix: an unadjusted split is repaired on the way to a page

MTUM's 4-for-1 printed as a 77% crash. The warehouse still stores the vendor bar, and the integrity lamp still fails on it.

EOF
)"
```

---

### Task 6: Limits on the example book

**Files:**
- Modify: `book.example.sexp` (the three portfolio limits, and any comment that still quotes $500,000, $12,000, or 2% as the live cap)
- Modify: `test/test_example_book.ml` (the three expected strings around lines 240–242)

**Interfaces:**
- Consumes: the three numbers from the spec
- Produces: `check-book` accepts the file. The test expects `("book-cap", "portfolio", "$700000.00")`, `("var-cap", "portfolio", "$30000.00")`, `("dd-cap", "portfolio", "10.00%")`.

- [ ] **Step 1: Change the expected strings in `test/test_example_book.ml`**

Those three tuples only. Do not change tests that build their own books with `dd_cap:0.02`; those are fixtures, not this book.

- [ ] **Step 2: Run the test to verify it fails**

Run the example-book test the repo already uses for that file (`test/dune` builds it into the suite). From the repo root, with the project opam switch:

```bash
opam exec -- dune exec test/test_ohcamel.exe -- test example book
```

If that filter is not how Alcotest names it, run the executable with `--help` once and use the printed name. Expected: FAIL on `$500000.00`.

- [ ] **Step 3: Edit the three limits**

```sexp
((name book-cap) (scope Portfolio) (kind (Gross_notional 700000.0)))
((name var-cap)  (scope Portfolio) (kind (Value_at_risk 30000.0)))
((name dd-cap)   (scope Portfolio) (kind (Max_drawdown 0.10)))
```

Update the comment block that tells the next reader the old numbers are the incubation ceiling. One sentence: steady-state gross is $575,000; $700,000 is the bug trip; 10% drawdown is the book halt; $30,000 VaR is about three times the 10% target's one-day 95% VaR.

- [ ] **Step 4: Run the test to verify it passes**

Same command as Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add book.example.sexp test/test_example_book.ml
git commit -m "$(cat <<'EOF'
fix: the example book's kill limits fit the incubation roster

A 2% drawdown and a $12k VaR halt a 57.5% book on an ordinary down day. The switch stays armed on limits that mean the book is broken.

EOF
)"
```

---

### Task 7: Read-only export of option snapshots

**Files:**
- Modify: `deploy/backup.sh` (accept method `snapshot`; do not implement `duckdb`)
- Modify: `deploy/backup.list` (one new line)
- Modify: `deploy/test/backup_list_test.sh` (the reserved-duckdb case stays; add the snapshot case)
- Modify: `quant/web/src/pages/ledger/entries.ts` and `quant/web/src/pages/ledger/entries.test.ts` (the warehouse row, after the export exists)

**Interfaces:**
- Consumes: `quant_data` volume, path `/data/warehouse.duckdb`, uid `10002`
- Produces: a backup line

```text
quant_data /data/warehouse.duckdb snapshot 10002
```

Destination name `option-snapshots-YYYY-MM-DD.parquet`. The copy is produced by the Quant image, `duckdb.connect(path, read_only=True)`, then `COPY (SELECT * FROM option_snapshots) TO ? (FORMAT PARQUET)`. No `CHECKPOINT`. No write to `/data/warehouse.duckdb`. If the table is missing, that line fails the run with the file and the reason, and does not delete the previous day's parquet.

- [ ] **Step 1: Write the failing shell test**

Follow the style of `malformed "B4 duckdb is reserved..."`. Add a case `snapshot line is accepted` whose list is the real `backup.list` plus the new line, and assert `--check` exits 0. Keep the existing test that a `duckdb` method is refused. Add a dry-run assertion that the printed command contains `read_only=True` and `option_snapshots`, and does not contain `CHECKPOINT`.

- [ ] **Step 2: Run it to verify it fails**

Run: `bash deploy/test/backup_list_test.sh`

Expected: FAIL on the new case. The reserved-duckdb case still passes.

- [ ] **Step 3: Implement method `snapshot`**

In the parser, `snapshot` is a fourth method. Destination `option-snapshots-$today.parquet`. Stem `option-snapshots`, extension `parquet`. The same stem rules as sqlite apply, so the name cannot collide with `desk`.

The copy runs as uid 10002 in the Quant image (the image `backup.sh` already uses for sqlite on `quant_data`; use that service, not the OCaml image). The Python is one short script passed to `python -c`. It connects read-only, copies the table, closes, and exits non-zero if the table is absent. It does not open the database read-write.

- [ ] **Step 4: Run the shell tests**

Run: `bash deploy/test/backup_list_test.sh`

Expected: PASS, including the still-reserved `duckdb` method.

- [ ] **Step 5: Update the ledger row, then commit**

Change the `WAREHOUSE FILE NOT COPIED` detail's last sentence to `Option snapshots are exported read-only; they cannot be re-fetched.` The test that matches `/option snapshots/` still passes. Run `cd quant/web && npm test -- src/pages/ledger/entries.test.ts`.

```bash
git add deploy/backup.sh deploy/backup.list deploy/test/backup_list_test.sh \
  quant/web/src/pages/ledger/entries.ts quant/web/src/pages/ledger/entries.test.ts
git commit -m "$(cat <<'EOF'
fix: nightly backup exports option snapshots read-only

Those rows cannot be re-fetched. Copying the live DuckDB file from a second process stays refused.

EOF
)"
```

---

### Task 8: Load the limits on the live book, then deploy

**Files:**
- Modify: `docs/status.md` deployed-sha rows, only after a deploy succeeds
- The droplet's `book.sexp`, which is not in git

**Interfaces:**
- Consumes: the three limits from Task 6, byte-for-byte the same sexp forms
- Produces: a restarted `ohcamel-live` whose `/api/ops` alerts show `kill_switch` `armed` or, if still `tripped`, a `tripped_by` that is reported and not reset

- [ ] **Step 1: Refuse the window and the missing host**

```bash
TZ=America/New_York date +%H:%M
ssh -o BatchMode=yes -o ConnectTimeout=8 ohcamel 'echo ok'
```

If the New York clock is inside 09:25–17:10, stop this task. If `ssh` does not authenticate, stop this task and say so. Do not invent a password. Tasks 1–7 are still done.

- [ ] **Step 2: Diff the live book against the example**

On the droplet, copy `book.sexp` aside under `~/book-archive/` with the UTC timestamp in the name. Diff it against `book.example.sexp`. The only intended limit changes are the three portfolio lines. If the diff contains anything else (the signals block, trading enabled, alert sinks), leave those hunks. Apply only the three limit values. Run, inside the new image, with no network:

```bash
docker run --rm --network none --read-only \
  -v "$PWD/book.sexp:/app/book.sexp:ro" \
  ghcr.io/ajaiupadhyaya/ohcamel:"$(git rev-parse HEAD)" \
  check-book /app/book.sexp
```

Expected: exit 0. If the image for this sha does not exist yet, run `check-book` from the local switch on the file after copying it down. Do not restart on a failing `check-book`.

- [ ] **Step 3: Deploy outside the window**

`deploy/deploy.sh --sha <this commit> --live` from the droplet checkout, the way `docs/status.md` already describes. This restarts the engine, which drops a limit trip that is not still breached. It does not clear a halt that was recorded by hand.

- [ ] **Step 4: Read the switch**

From inside `ohcamel-live`, GET `/api/ops` and record `alerts.kill_switch` and `alerts.tripped_by`. Do not print the rest of the body.

- `armed`: done.
- `tripped` with a limit name: the new limit is still breached. Do not reset. Do not widen the numbers again in this task. Report the name.
- `tripped` with `tripped_by` null after the restart: that is a hand halt. Leave it. Report it. The owner resets it by hand.

Smoke the public host with `deploy/smoke.sh` as the deploy script already does. On `https://ohcamel.ajaiupadhyaya.com/api/machine`, the kill node title contains `halted by` only if the switch is still tripped.

Then set the deployed-sha rows in `docs/status.md` to this commit and commit that docs change only.

- [ ] **Step 5: Commit the status row, if the deploy happened**

```bash
git add docs/status.md
git commit -m "$(cat <<'EOF'
docs: status records the deploy that loaded the incubation limits

EOF
)"
```

If Step 1 stopped the task, there is nothing to commit.
