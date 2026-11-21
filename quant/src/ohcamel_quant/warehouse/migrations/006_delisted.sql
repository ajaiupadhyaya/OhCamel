-- Completion task 3.8: delisted names behind a switch (spec R5). Additive only: two new empty tables, nothing
-- read, moved or rewritten, and no existing table changes its columns -- so the code before this migration keeps
-- working against a migrated file (its positional insert into universe_membership still has five columns to fill),
-- and a rollback past this commit leaves ingest.universes running. On the live file it takes as long as creating
-- two empty tables. They fill from ingest.universes (the series) and tools/accept_delisted.py (the acceptance).
--
-- membership_series: for each universe_membership row (matched on universe, ticker, start and end, NULLs equal),
-- the key under which that stint's company keeps its prices in bars_daily and bars_raw. A company that changed
-- ticker keeps one series across the rename (its last ticker, the code the vendors keep its history under), so its
-- rows (one per ticker it traded under while a member) share a series. A ticker one member company took from
-- another is two companies: the earlier one's series is TICKER@DATE, the date its stint under that ticker ended.
-- A membership row with no match here is read as its own ticker (the behaviour before this migration). Written
-- whole per universe together with universe_membership, in the same transaction.
CREATE TABLE IF NOT EXISTS membership_series (universe TEXT, ticker TEXT, "start" DATE, "end" DATE, series TEXT);
-- delisted_acceptance: append-only, one row per PASSED and recorded run of tools/accept_delisted.py (the acceptance
-- test on known failures against the delisted-names source). Nothing else writes it. list_sha256 is the sha256 of
-- the committed acceptance list the run used: a row for another list is not acceptance of this one.
CREATE TABLE IF NOT EXISTS delisted_acceptance (accepted_at TIMESTAMP, source TEXT, passed INTEGER,
                                                total INTEGER, list_sha256 TEXT, detail TEXT);
