-- Contract II.5 of docs/superpowers/plans/2026-09-24-quant-compute-program.md (amended 2026-10-06)
CREATE TABLE universe_members (universe TEXT, ticker TEXT, name TEXT, added DATE, source TEXT,
                               survivorship TEXT, PRIMARY KEY (universe, ticker));
CREATE TABLE bars_daily  (ticker TEXT, date DATE, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
                          adj_close DOUBLE, volume DOUBLE, source TEXT, fetched_at TIMESTAMP,
                          PRIMARY KEY (ticker, date));
CREATE TABLE bars_minute (ticker TEXT, ts TIMESTAMP, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
                          volume DOUBLE, source TEXT, PRIMARY KEY (ticker, ts));
CREATE TABLE fred        (series TEXT, date DATE, value DOUBLE, fetched_at TIMESTAMP, PRIMARY KEY (series, date));
CREATE TABLE factors     (dataset TEXT, factor TEXT, date DATE, value DOUBLE, PRIMARY KEY (dataset, factor, date));
CREATE TABLE option_snapshots (underlying TEXT, "asof" DATE, expiry DATE, strike DOUBLE, cp TEXT,
                          bid DOUBLE, ask DOUBLE, last DOUBLE, volume DOUBLE, open_interest DOUBLE,
                          source TEXT, PRIMARY KEY (underlying, "asof", expiry, strike, cp));
CREATE TABLE sec_facts   (cik TEXT, ticker TEXT, tag TEXT, unit TEXT, period_start DATE, period_end DATE,
                          filed DATE, form TEXT, value DOUBLE,
                          PRIMARY KEY (cik, tag, unit, period_start, period_end, filed));
CREATE TABLE holdings_13f (cik TEXT, filer TEXT, accession TEXT, form TEXT, period DATE, filed DATE,
                          cusip TEXT, put_call TEXT, issuer TEXT, title TEXT, ticker TEXT, shares DOUBLE,
                          shares_type TEXT, value_usd DOUBLE, weight DOUBLE,
                          PRIMARY KEY (accession, cusip, put_call));
CREATE TABLE ingest_log  (dataset TEXT, key TEXT, ran_at TIMESTAMP, rows INTEGER, status TEXT,
                          detail TEXT, data_asof DATE);
