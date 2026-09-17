; The book: positions, cash and limits.
;
; Copy to book.sexp (which is gitignored, because a book is account-specific)
; and edit. Live mode reads book.sexp by default, or a path given as the second
; argument:  ohcamel live /path/to/other-book.sexp
;
; Positions are set here, and the feed answers what they are worth. With an
; Alpaca paper key in the environment, `live` and `serve` go one step further:
; the desk reads the paper account every minute and takes its quantities and
; cash as the book's, while this file still declares the universe, the limits,
; the alerts and the desk. Without such a key, this file is the whole answer to
; "what do I hold".
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
   ((name dd-cap)     (scope Portfolio)          (kind (Max_drawdown 0.02)))))

 ; ---------------------------------------------------------------------------
 ; Phase 4: alerting and the kill switch. OPTIONAL -- omit this block entirely
 ; and the engine computes breaches, displays them, and does nothing else.
 ; That is the default, and it is the right one.
 ;
 ;   enabled                 master switch. false means nothing is ever sent.
 ;   sinks                   Log            -> stdout
 ;                           (File "path")  -> appended to a file
 ;                           Dry_run        -> prints the exact payload it WOULD
 ;                                             send, and sends nothing. Run this
 ;                                             first.
 ;                           Slack          -> POSTs to SLACK_WEBHOOK_URL. The
 ;                                             only sink that leaves the machine.
 ;   clear_below             hysteresis. Once raised, an alert clears only when
 ;                           utilisation falls back below this fraction of the
 ;                           limit -- otherwise a value resting on the threshold
 ;                           flaps and trains you to ignore the channel.
 ;   kill_switch_enabled     a SEPARATE decision from alerting. "Tell me when a
 ;                           limit breaks" and "act when a limit breaks" are
 ;                           different levels of trust.
 ;   kill_switch_trips_on    which limits are hard enough to trip it. Empty
 ;                           means none, even when enabled.
 ;
 ; What the kill switch actually does: it halts the desk. Every new order is
 ; refused, every open order the desk owns is cancelled, and positions are left
 ; exactly as they are -- it never liquidates. On the live host a person can
 ; also halt the desk by hand from the dashboard, and a halt stays until someone
 ; resets it there. The switch only exists when (enabled true) below: the whole
 ; alerting block, this switch included, is inert while alerting is off.
 (alerts
  ((enabled false)
   (sinks (Log))
   (clear_below 0.95)
   (kill_switch_enabled false)
   (kill_switch_trips_on ())))

 ; ---------------------------------------------------------------------------
 ; Phase A2: the desk. OPTIONAL -- omit this block, or leave trading disabled,
 ; and the desk previews an order and places none. That is the default.
 ;
 ; With (trading enabled), an Alpaca PAPER key pair in the environment and a
 ; book that is current, the dashboard's ticket can send an order. Nothing else
 ; can: this engine runs no automatic trader on the live host, and the public
 ; demo answers 405 to every route that would place, cancel or halt. Paper is
 ; the only venue there is -- the host is a compiled-in constant, and a key that
 ; does not begin PK is refused before the first request.
 ;
 ; Every order passes, in this order: the rules named below, then the book's own
 ; limits re-evaluated on a fork of the live graph (an order that would create
 ; or worsen a breach is refused), then the journal, and only then the wire.
 ;
 ;   trading                 enabled | disabled. Disabled previews only.
 ;   max_order_notional      |quantity x mark| may not exceed this, per order.
 ;   max_adv_participation   the order's share of the name's twenty-day volume.
 ;                           0.01 = 1%. Unknown volume refuses the order.
 ;   price_collar            how far a limit price may sit from the mark.
 ;                           0.05 = 5%.
 ;   duplicate_window_s      a same-name, same-side, same-quantity order inside
 ;                           this window is refused as a double-click.
 ;   max_open_orders         how many of the desk's own orders may rest at once.
 ;   spread_bps_default      the half-spread assumed when costing a fill, in
 ;                           basis points, for a name not named below.
 ;   spread_bps              per-name half-spreads, e.g. ((AAPL 2.0) (XOM 8.0)).
 ;
 ; The values below are the engine's own defaults, written out so that turning
 ; the desk on is one word rather than a guess.
 (desk
  ((trading disabled)
   (max_order_notional 25000.0)
   (max_adv_participation 0.01)
   (price_collar 0.05)
   (duplicate_window_s 10.0)
   (max_open_orders 20)
   (spread_bps_default 5.0)
   (spread_bps ()))))
