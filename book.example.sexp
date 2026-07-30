; The book: positions, cash and limits.
;
; Copy to book.sexp (which is gitignored, because a book is account-specific)
; and edit. Live mode reads book.sexp by default, or a path given as the second
; argument:  ohcamel live /path/to/other-book.sexp
;
; Positions are set here and only PRICES are live. Alpaca is the source of
; marks, not of the position set -- so this file is the answer to "what do I
; hold", and the feed is the answer to "what is it worth".
;
; Quantities are signed: positive is long, negative is short.
;
; Limit scopes:   (Instrument SYM) | (Sector NAME) | Portfolio
; Limit kinds:    (Gross_notional DOLLARS)   |exposure| may not exceed this
;                 (Value_at_risk DOLLARS)    portfolio VaR, Portfolio scope only
;                 (Max_drawdown FRACTION)    0.02 = 2%, Portfolio scope only
;
; Value_at_risk and Max_drawdown are portfolio statistics and this engine keeps
; no per-name version of either, so a sector- or instrument-scoped one is
; rejected at startup rather than answered with the book-level number.

((cash 1000000.0)
 (positions
  (((symbol AAPL) (sector TECH)       (qty 400.0))
   ((symbol MSFT) (sector TECH)       (qty 200.0))
   ((symbol NVDA) (sector TECH)       (qty 60.0))
   ((symbol JPM)  (sector FINANCIALS) (qty 250.0))
   ((symbol XOM)  (sector ENERGY)     (qty -500.0))
   ((symbol CVX)  (sector ENERGY)     (qty -300.0))))
 (limits
  (((name aapl-cap)   (scope (Instrument AAPL))  (kind (Gross_notional 150000.0)))
   ((name nvda-cap)   (scope (Instrument NVDA))  (kind (Gross_notional 60000.0)))
   ((name tech-cap)   (scope (Sector TECH))      (kind (Gross_notional 260000.0)))
   ((name energy-cap) (scope (Sector ENERGY))    (kind (Gross_notional 150000.0)))
   ((name book-cap)   (scope Portfolio)          (kind (Gross_notional 500000.0)))
   ((name var-cap)    (scope Portfolio)          (kind (Value_at_risk 12000.0)))
   ((name dd-cap)     (scope Portfolio)          (kind (Max_drawdown 0.02))))))
