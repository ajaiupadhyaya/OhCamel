-- Completion task 3.8: delisted names behind a switch (spec R5). Additive only: one new column on a small table
-- (universe_membership, about 1,300 rows) and one new empty table, nothing moved or rewritten, so on the live
-- file it takes as long as adding a column and creating an empty table. It fills from ingest.universes (the
-- series) and from tools/accept_delisted.py (the acceptance).
--
-- universe_membership.series: the key under which this stint's company keeps its prices in bars_daily and
-- bars_raw. A company that changed ticker keeps one series across the rename (its last ticker, the code the
-- vendors keep its history under), so its rows (one per ticker it traded under while a member) share a series.
-- A ticker one member company took from another is two companies: the earlier one's series is TICKER@DATE, the
-- date its stint under that ticker ended. NULL on rows written before this migration: read as the ticker.
ALTER TABLE universe_membership ADD COLUMN IF NOT EXISTS series TEXT;
-- delisted_acceptance: append-only, one row per PASSED run of tools/accept_delisted.py (the acceptance test on
-- known failures against the delisted-names source). Nothing else writes it. list_sha256 is the sha256 of the
-- committed acceptance list the run used: a row for another list is not acceptance of this one.
CREATE TABLE IF NOT EXISTS delisted_acceptance (accepted_at TIMESTAMP, source TEXT, passed INTEGER,
                                                total INTEGER, list_sha256 TEXT, detail TEXT);
