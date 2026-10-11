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
;                 (Component_var DOLLARS)    a name's or sector's share of portfolio VaR
;                 (Max_drawdown FRACTION)    0.02 = 2%, Portfolio scope only
;
; Value_at_risk and Max_drawdown are portfolio statistics and this engine keeps
; no per-name version of either, so a sector- or instrument-scoped one is
; rejected at startup rather than answered with the book-level number.
; Component_var is additive by construction, so it is valid at every scope.

((cash 1000000.0)
 (positions
  (((symbol AAPL) (sector TECH)       (qty 400.0))
   ((symbol MSFT) (sector TECH)       (qty 200.0))
   ((symbol NVDA) (sector TECH)       (qty 60.0))
   ((symbol JPM)  (sector FINANCIALS) (qty 250.0))
   ((symbol XOM)  (sector ENERGY)     (qty -500.0))
   ((symbol CVX)  (sector ENERGY)     (qty -300.0))
   ; Every other name is here at zero, because a signal may name only a
   ; symbol the book declares (R7). On a host with a paper key the account
   ; says what is held; here, nothing. SPY and TLT are EXP-A01's two names.
   ((symbol SPY)  (sector INDEX)      (qty 0.0))
   ((symbol TLT)  (sector TREASURIES) (qty 0.0))
   ; The rest of the site's 42 ETFs (quant/src/ohcamel_quant/universes.json,
   ; less its crypto and volatility groups), one sector per group, for the
   ; farm's strategies below. SPY and TLT above are two of the 42.
   ; US equity indices: INDEX.
   ((symbol QQQ)  (sector INDEX)      (qty 0.0))
   ((symbol DIA)  (sector INDEX)      (qty 0.0))
   ((symbol IWM)  (sector INDEX)      (qty 0.0))
   ((symbol MDY)  (sector INDEX)      (qty 0.0))
   ; S&P 500 sectors: SECTOR.
   ((symbol XLK)  (sector SECTOR)     (qty 0.0))
   ((symbol XLF)  (sector SECTOR)     (qty 0.0))
   ((symbol XLE)  (sector SECTOR)     (qty 0.0))
   ((symbol XLV)  (sector SECTOR)     (qty 0.0))
   ((symbol XLI)  (sector SECTOR)     (qty 0.0))
   ((symbol XLY)  (sector SECTOR)     (qty 0.0))
   ((symbol XLP)  (sector SECTOR)     (qty 0.0))
   ((symbol XLU)  (sector SECTOR)     (qty 0.0))
   ((symbol XLB)  (sector SECTOR)     (qty 0.0))
   ((symbol XLRE) (sector SECTOR)     (qty 0.0))
   ((symbol XLC)  (sector SECTOR)     (qty 0.0))
   ; Style factors: STYLE.
   ((symbol MTUM) (sector STYLE)      (qty 0.0))
   ((symbol QUAL) (sector STYLE)      (qty 0.0))
   ((symbol VLUE) (sector STYLE)      (qty 0.0))
   ((symbol USMV) (sector STYLE)      (qty 0.0))
   ((symbol SIZE) (sector STYLE)      (qty 0.0))
   ; US Treasuries: RATES.
   ((symbol BIL)  (sector RATES)      (qty 0.0))
   ((symbol SHY)  (sector RATES)      (qty 0.0))
   ((symbol IEI)  (sector RATES)      (qty 0.0))
   ((symbol IEF)  (sector RATES)      (qty 0.0))
   ((symbol TIP)  (sector RATES)      (qty 0.0))
   ; Credit: CREDIT.
   ((symbol LQD)  (sector CREDIT)     (qty 0.0))
   ((symbol HYG)  (sector CREDIT)     (qty 0.0))
   ((symbol EMB)  (sector CREDIT)     (qty 0.0))
   ; International equity: INTL.
   ((symbol EFA)  (sector INTL)       (qty 0.0))
   ((symbol EEM)  (sector INTL)       (qty 0.0))
   ((symbol VGK)  (sector INTL)       (qty 0.0))
   ((symbol EWJ)  (sector INTL)       (qty 0.0))
   ((symbol FXI)  (sector INTL)       (qty 0.0))
   ((symbol INDA) (sector INTL)       (qty 0.0))
   ; Commodities: COMMODITY.
   ((symbol GLD)  (sector COMMODITY)  (qty 0.0))
   ((symbol SLV)  (sector COMMODITY)  (qty 0.0))
   ((symbol USO)  (sector COMMODITY)  (qty 0.0))
   ((symbol UNG)  (sector COMMODITY)  (qty 0.0))
   ((symbol DBA)  (sector COMMODITY)  (qty 0.0))
   ((symbol DBC)  (sector COMMODITY)  (qty 0.0))))
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
 ; book that is current, the dashboard's ticket can send an order. The only
 ; other sender is a strategy in the signals block below that the owner has
 ; promoted to live sizing -- none is, as shipped -- whose accepted signal
 ; becomes one rebalance of market-on-open orders through the same rules and
 ; limits.
 ; The public demo answers 405 to every route that would place, cancel or halt. Paper is
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
 ;                           200, because a roster of two dozen strategies rests
 ;                           more than twenty market-on-open orders at once.
 ;   spread_bps_default      the half-spread assumed when costing a fill, in
 ;                           basis points, for a name not named below.
 ;   spread_bps              per-name half-spreads, e.g. ((AAPL 2.0) (XOM 8.0)).
 ;   incubation_cap          a proportional ceiling on the incubation
 ;                           strategies below: each one's effective fraction is
 ;                           capital_fraction x min(1, incubation_cap / the sum
 ;                           of their capital_fraction values). The canary is
 ;                           (incubation_cap 0.10) and raising it is one edit.
 ;                           0, the default, sizes none.
 ;   vol_target              the overlay's annualised volatility target. The
 ;                           overlay scales every incubation and live target.
 ;   overlay_max_age_minutes an overlay older than this is stale; the desk
 ;                           holds its last value until then.
 ;   overlay_fallback        the scale used once the overlay is stale. The page
 ;                           says OVERLAY STALE.
 ;   overlay_band            a move in the scale larger than this re-sizes every
 ;                           strategy that adjusts intraday.
 ;   intraday_slip_bps       an intraday leg is a limit order this far past the
 ;                           mark, toward the far side. Below the collar.
 ;   min_leg_notional        an intraday leg smaller than this, in dollars, is
 ;                           dropped, and the drop journaled.
 ;   wave_timeout_s          how long the desk waits on one wave of legs before
 ;                           it cancels what is left.
 ;   flatten_at              New York time, HH:MM inside the session, at which a
 ;                           strategy with (horizon close) is flattened.
 ;   stream                  the names streamed first. The stream carries
 ;                           thirty; every other name is polled.
 ;
 ; The values below are the engine's own defaults, written out so that turning
 ; the desk on is one word rather than a guess. Two are not: incubation_cap,
 ; which sizes the roster below, and stream, which names five of its names.
 (desk
  ((trading disabled)
   (max_order_notional 25000.0)
   (max_adv_participation 0.01)
   (price_collar 0.05)
   (duplicate_window_s 10.0)
   (max_open_orders 200)
   (spread_bps_default 5.0)
   ; SPY and TLT are research/config/friction_v1.yaml's spreads, halved: that
   ; file's 2.0 and 3.0 are the full round-trip spread EXP-A01 was tested at
   ; ("per-symbol spread, halved per fill", its pre-registration), and this is
   ; a per-fill half-spread. One cost configuration for the backtest and the
   ; desk; test/test_example_book.ml reads both files and holds them to it.
   (spread_bps ((SPY 1.0) (TLT 1.5)))
   (incubation_cap 0.60)
   (vol_target 0.10)
   (overlay_max_age_minutes 60)
   (overlay_fallback 0.5)
   (overlay_band 0.10)
   (intraday_slip_bps 10.0)
   (min_leg_notional 200.0)
   (wave_timeout_s 180.0)
   (flatten_at "15:50")
   (stream (SPY QQQ IWM TLT GLD))))

 ; ---------------------------------------------------------------------------
 ; Phase A3: signals. OPTIONAL -- omit this block and the desk registers no
 ; strategy and reads no signal file. That is the default.
 ;
 ; With this block present and OHCAMEL_SIGNALS_DIR naming a directory, the
 ; desk reads that directory's *.json files once a minute, judges each against
 ; the signal contract's rules R1-R7 (interface/README.md), and records every
 ; judgement in its journal: accepted, advisory, or rejected with the rule
 ; named. R8, the data hash, is not enforced. The research service writes the
 ; files; the desk trusts none of their timing.
 ;
 ;   name              the signal's `strategy` slug, as the research service
 ;                     writes it: ^[a-z][a-z0-9_]{1,63}$
 ;   symbols           the only names this strategy may target, each one in
 ;                     the positions above. A signal naming any other is
 ;                     rejected.
 ;   max_age           R4: how many recorded sessions old a signal may be.
 ;   sizing            advisory | live | incubation. Advisory, the default,
 ;                     shows a passing signal with its weights and never sizes
 ;                     it. Live is sized when its signal passes validation;
 ;                     incubation, below, whatever its validation says.
 ;   capital_fraction  the share of equity the strategy's weights apply to, in
 ;                     (0, 1]. The live strategies' fractions may sum to at
 ;                     most 1, the incubation ones to at most 1, and the two
 ;                     together to at most 1. The desk's incubation_cap then
 ;                     scales the incubation ones down in proportion.
 ;   cadence           daily | intraday: how often its documents arrive.
 ;   adjust            intraday | open: whether an overlay move re-sizes it
 ;                     during the session or at the next open.
 ;   max_age_minutes   R4 for an intraday document, in minutes. Default 20.
 ;   horizon           carry | close. Close is flattened at the desk's
 ;                     flatten_at. Default carry.
 ;   stop              ((drawdown F) (sessions N)): the strategy is stopped
 ;                     when its drawdown from peak reaches F, or N sessions
 ;                     after its first fill, and is sized no more.
 ;   reinstated        a date the owner writes after a stop to resume it.
 ;
 ; Incubation is the charter's paper tier (docs/CHARTER.md). An incubation
 ; strategy is sized on the paper venue whatever its validation status, scaled
 ; by incubation_cap, and every order, page and report it touches says so. Its
 ; record is scored against the stop rules written here before it trades, and
 ; promotion to live never follows from that record by itself. It is not
 ; evidence of edge.
 ;
 ; The roster: EXP-A01's two strategies, then one strategy per farm cell --
 ; each strategy in quant/src/ohcamel_quant/backtest/strategies.py on each of
 ; its suggested universes -- every one incubation. Every one at an equal
 ; 0.025: 23 x 0.025 = 0.575, under the incubation_cap of 0.60, the other
 ; 0.025 held for EXP-Q01's portfolio when it joins (INC-01).
 (signals
  ((strategies
    (((name exp_a01_spy) (symbols (SPY)) (max_age 3) (sizing incubation)
      (capital_fraction 0.025) (cadence daily) (adjust intraday)
      (stop ((drawdown 0.15) (sessions 250))))
     ((name exp_a01_tlt) (symbols (TLT)) (max_age 3) (sizing incubation)
      (capital_fraction 0.025) (cadence daily) (adjust intraday)
      (stop ((drawdown 0.15) (sessions 250))))
     ((name farm_tsmom_us_equity_indices) (symbols (SPY QQQ DIA IWM MDY)) (max_age 3) (sizing incubation)
      (capital_fraction 0.025) (cadence daily) (adjust intraday)
      (stop ((drawdown 0.15) (sessions 250))))
     ((name farm_tsmom_rates) (symbols (BIL SHY IEI IEF TLT TIP)) (max_age 3) (sizing incubation)
      (capital_fraction 0.025) (cadence daily) (adjust intraday)
      (stop ((drawdown 0.15) (sessions 250))))
     ((name farm_tsmom_commodities) (symbols (GLD SLV USO UNG DBA DBC)) (max_age 3) (sizing incubation)
      (capital_fraction 0.025) (cadence daily) (adjust intraday)
      (stop ((drawdown 0.15) (sessions 250))))
     ((name farm_tsmom_international) (symbols (EFA EEM VGK EWJ FXI INDA)) (max_age 3) (sizing incubation)
      (capital_fraction 0.025) (cadence daily) (adjust intraday)
      (stop ((drawdown 0.15) (sessions 250))))
     ((name farm_tsmom_credit) (symbols (LQD HYG EMB)) (max_age 3) (sizing incubation)
      (capital_fraction 0.025) (cadence daily) (adjust intraday)
      (stop ((drawdown 0.15) (sessions 250))))
     ((name farm_xsmom_sectors) (symbols (XLK XLF XLE XLV XLI XLY XLP XLU XLB XLRE XLC)) (max_age 3) (sizing incubation)
      (capital_fraction 0.025) (cadence daily) (adjust intraday)
      (stop ((drawdown 0.15) (sessions 250))))
     ((name farm_xsmom_international) (symbols (EFA EEM VGK EWJ FXI INDA)) (max_age 3) (sizing incubation)
      (capital_fraction 0.025) (cadence daily) (adjust intraday)
      (stop ((drawdown 0.15) (sessions 250))))
     ((name farm_dual_momentum_us_equity_indices) (symbols (SPY QQQ DIA IWM MDY)) (max_age 3) (sizing incubation)
      (capital_fraction 0.025) (cadence daily) (adjust intraday)
      (stop ((drawdown 0.15) (sessions 250))))
     ((name farm_dual_momentum_international) (symbols (EFA EEM VGK EWJ FXI INDA)) (max_age 3) (sizing incubation)
      (capital_fraction 0.025) (cadence daily) (adjust intraday)
      (stop ((drawdown 0.15) (sessions 250))))
     ((name farm_sma_trend_us_equity_indices) (symbols (SPY QQQ DIA IWM MDY)) (max_age 3) (sizing incubation)
      (capital_fraction 0.025) (cadence daily) (adjust intraday)
      (stop ((drawdown 0.15) (sessions 250))))
     ((name farm_sma_trend_commodities) (symbols (GLD SLV USO UNG DBA DBC)) (max_age 3) (sizing incubation)
      (capital_fraction 0.025) (cadence daily) (adjust intraday)
      (stop ((drawdown 0.15) (sessions 250))))
     ((name farm_sma_trend_international) (symbols (EFA EEM VGK EWJ FXI INDA)) (max_age 3) (sizing incubation)
      (capital_fraction 0.025) (cadence daily) (adjust intraday)
      (stop ((drawdown 0.15) (sessions 250))))
     ((name farm_vol_managed_us_equity_indices) (symbols (SPY QQQ DIA IWM MDY)) (max_age 3) (sizing incubation)
      (capital_fraction 0.025) (cadence daily) (adjust intraday)
      (stop ((drawdown 0.15) (sessions 250))))
     ((name farm_risk_parity_rates) (symbols (BIL SHY IEI IEF TLT TIP)) (max_age 3) (sizing incubation)
      (capital_fraction 0.025) (cadence daily) (adjust intraday)
      (stop ((drawdown 0.15) (sessions 250))))
     ((name farm_risk_parity_credit) (symbols (LQD HYG EMB)) (max_age 3) (sizing incubation)
      (capital_fraction 0.025) (cadence daily) (adjust intraday)
      (stop ((drawdown 0.15) (sessions 250))))
     ((name farm_risk_parity_commodities) (symbols (GLD SLV USO UNG DBA DBC)) (max_age 3) (sizing incubation)
      (capital_fraction 0.025) (cadence daily) (adjust intraday)
      (stop ((drawdown 0.15) (sessions 250))))
     ((name farm_risk_parity_us_equity_indices) (symbols (SPY QQQ DIA IWM MDY)) (max_age 3) (sizing incubation)
      (capital_fraction 0.025) (cadence daily) (adjust intraday)
      (stop ((drawdown 0.15) (sessions 250))))
     ((name farm_low_beta_sectors) (symbols (XLK XLF XLE XLV XLI XLY XLP XLU XLB XLRE XLC)) (max_age 3) (sizing incubation)
      (capital_fraction 0.025) (cadence daily) (adjust intraday)
      (stop ((drawdown 0.15) (sessions 250))))
     ((name farm_st_reversal_sectors) (symbols (XLK XLF XLE XLV XLI XLY XLP XLU XLB XLRE XLC)) (max_age 3) (sizing incubation)
      (capital_fraction 0.025) (cadence daily) (adjust intraday)
      (stop ((drawdown 0.15) (sessions 250))))
     ((name farm_pairs_distance_sectors) (symbols (XLK XLF XLE XLV XLI XLY XLP XLU XLB XLRE XLC)) (max_age 3) (sizing incubation)
      (capital_fraction 0.025) (cadence daily) (adjust intraday)
      (stop ((drawdown 0.15) (sessions 250))))
     ((name farm_pairs_coint_sectors) (symbols (XLK XLF XLE XLV XLI XLY XLP XLU XLB XLRE XLC)) (max_age 3) (sizing incubation)
      (capital_fraction 0.025) (cadence daily) (adjust intraday)
      (stop ((drawdown 0.15) (sessions 250)))))))))
