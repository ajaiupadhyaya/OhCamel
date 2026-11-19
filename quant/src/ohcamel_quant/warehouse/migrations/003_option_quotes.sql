-- Completion task 3.4: an option snapshot keeps what a volatility surface needs -- the underlying price the
-- vendor stated with the quotes (spot), when the quotes were taken (quote_ts, naive UTC, and quote_ts_source,
-- the vendor field that gave it), the vendor's own implied vol per contract (vendor_iv, for comparison only),
-- and which pull it came from (pull: '1545' before the close, '1620' after it). The primary key gains pull,
-- so the two pulls of one day are two sets of rows.
--
-- DuckDB cannot alter a primary key in place. This file only renames the old table and creates the new one
-- (both instant, inside the runner's transaction). The rows are then moved by warehouse/db.py
-- (_move_option_snapshots), one snapshot day per transaction, and the old table is dropped once every day is
-- across: copying a few million rows into a keyed table in ONE transaction exhausts the writer's 256 MB
-- (measured with DuckDB 1.5.6 on 4.8 million rows -- it fails even at 1 GB), while one day of about 120
-- thousand rows per transaction moved 4.8 million rows in 11 s and 12 million in 27 s at 256 MB. An interrupted
-- move resumes on the next open. Old rows keep pull '1620' and NULL in the new columns.
ALTER TABLE option_snapshots RENAME TO option_snapshots_v2;
CREATE TABLE option_snapshots (underlying TEXT, "asof" DATE, expiry DATE, strike DOUBLE, cp TEXT,
                               bid DOUBLE, ask DOUBLE, last DOUBLE, volume DOUBLE, open_interest DOUBLE,
                               source TEXT, spot DOUBLE, quote_ts TIMESTAMP, vendor_iv DOUBLE, pull TEXT,
                               quote_ts_source TEXT,
                               PRIMARY KEY (underlying, "asof", expiry, strike, cp, pull));
