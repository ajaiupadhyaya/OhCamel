-- Completion task 3.6: the exchange calendar, from Alpaca's GET /v2/calendar (the paper trading API), so that
-- freshness and ops.data_integrity know holidays and early closes instead of counting weekdays.
-- Additive only: one new empty table, nothing read, moved or rewritten, so on the live file it takes as long as
-- creating an empty table. It fills from the nightly bars ingest (warehouse/ingest/calendar.py).
--
-- One row per session: its New York date, the regular open and close in New York time (13:00 on a half day),
-- where it came from and when it was fetched. A date inside the fetched range with no row is not a session.
-- The range fetched is in ingest_log (dataset exchange_calendar, detail window_start and window_end).
CREATE TABLE IF NOT EXISTS exchange_calendar ("date" DATE PRIMARY KEY, open TIME, close TIME, source TEXT,
                                              fetched_at TIMESTAMP);
