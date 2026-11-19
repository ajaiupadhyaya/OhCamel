-- Completion task 3.5: index membership with dates, and every published vintage of the revised macro series.
-- Additive only: two new empty tables, nothing read, moved or rewritten, so on the live file it takes as long
-- as creating two empty tables. They fill from their ingests (docs/runbooks/compute.md).
--
-- universe_membership: one row per stint of a ticker in an index. start is the first date it was a member
-- (NULL: before the changes table's coverage), "end" the first date it was NOT (NULL: still a member when the
-- list was retrieved). Written whole per universe by ingest.universes from the committed lists
-- (warehouse/constituents), like universe_members. No key: a NULL start cannot be in one, and a ticker can
-- have more than one stint.
CREATE TABLE IF NOT EXISTS universe_membership (universe TEXT, ticker TEXT, "start" DATE, "end" DATE,
                                                source TEXT);
-- fred_vintages: append-only, never deleted or updated. realtime_start is the date from which value was the
-- published figure for (series, date) -- ALFRED's realtime_start through the API, or the first probed date on
-- which ALFRED showed it (never earlier than the true release). source says which.
CREATE TABLE IF NOT EXISTS fred_vintages (series TEXT, date DATE, realtime_start DATE, value DOUBLE,
                                          source TEXT, fetched_at TIMESTAMP,
                                          PRIMARY KEY (series, date, realtime_start));
