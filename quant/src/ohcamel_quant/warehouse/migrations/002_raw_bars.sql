-- Completion task 3.2: raw bars and the corporate actions that adjust them (warehouse/adjust.py derives
-- the adjusted series from the two). Additive only: three new tables, nothing read or rewritten, so on the
-- live file it takes no longer than creating three empty tables. bars_daily stays the read path until the
-- owner's cut-over.
--
-- bars_raw: the unadjusted prints, one row per ticker and session, never deleted by a re-adjustment
-- (a new dividend does not change a raw price). source is the vendor whose response gave the row.
CREATE TABLE IF NOT EXISTS bars_raw (ticker TEXT, date DATE, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
                                     volume DOUBLE, source TEXT, PRIMARY KEY (ticker, date));
-- corp_actions: one row per action, keyed so the same action cannot be stored twice. kind is split
-- (ratio = new shares per old share) or dividend (cash = per share as paid on ex_date). It holds the
-- vendor's latest statement of each action. An earlier, different statement is kept in
-- corp_action_revisions, never silently overwritten.
CREATE TABLE IF NOT EXISTS corp_actions (ticker TEXT, ex_date DATE, kind TEXT, ratio DOUBLE, cash DOUBLE,
                                         source TEXT, fetched_at TIMESTAMP, PRIMARY KEY (ticker, ex_date, kind));
-- corp_action_revisions: append-only. A changed ratio or cash has old and new values, an action the vendor
-- no longer lists inside a window it was asked for has new values NULL (the stored row is kept).
CREATE TABLE IF NOT EXISTS corp_action_revisions (ticker TEXT, ex_date DATE, kind TEXT, old_ratio DOUBLE,
                                                  old_cash DOUBLE, new_ratio DOUBLE, new_cash DOUBLE, source TEXT,
                                                  seen_at TIMESTAMP);
