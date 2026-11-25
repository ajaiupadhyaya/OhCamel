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
; Limit kinds:    (Gross_notional DOLLARS)      |exposure| may not exceed this
;                 (Value_at_risk DOLLARS)       portfolio VaR, Portfolio scope only
;                 (Component_var DOLLARS)       a name's or sector's share of portfolio VaR
;                 (Max_drawdown FRACTION)       0.10 = 10%, Portfolio scope only
;                 (Name_weight FRACTION)        the heaviest name's |exposure| over
;                                               equity, Portfolio scope only: it
;                                               applies to every instrument
;                 (Class_gross FRACTION)        a sector class's gross (the sum of
;                                               its names' |exposure|) over equity,
;                                               Sector scope only
;                 (Expected_shortfall DOLLARS)  1-day expected shortfall of the
;                                               current book at the risk block's
;                                               es_confidence over its var_window
;                                               sessions, Portfolio scope only
; Steady-state gross is $550,000; $700,000 is the bug trip; 10% drawdown is the book halt; $30,000 VaR is about three times the 10% target's one-day 95% VaR.
;
; Value_at_risk, Expected_shortfall and Max_drawdown are portfolio statistics
; and this engine keeps no per-name version of any, so a sector- or
; instrument-scoped one is rejected at startup rather than answered with the
; book-level number. Component_var is additive by construction, so it is valid
; at every scope.
;
; The limits fit an ETF book (Task 1.9): no name above 15% of equity
; (name-cap); no sector class above 35% of equity, gross (one class-* limit per
; sector below -- once a book writes one Class_gross limit it must write one
; for every sector it holds, or it is refused, so no name escapes every class
; cap); and a 1-day 99% expected shortfall over 250 sessions of $45,000 as the
; risk limit the kill switch trips on (es-cap). The sectors are the site's
; universe groups (quant/src/ohcamel_quant/universes.json), one class per
; group; the six single names keep their own sectors and each sector its own
; class. var-cap stays, as an alert the switch does not trip on. No net-beta
; limit and no borrow or buying-power rule (the completion spec's Cut).

((cash 1000000.0)
 (positions
  (((symbol AAPL) (sector TECH)       (qty 400.0))
   ((symbol MSFT) (sector TECH)       (qty 200.0))
   ((symbol NVDA) (sector TECH)       (qty 60.0))
   ((symbol JPM)  (sector FINANCIALS) (qty 250.0) (stale_after_s 300))
   ((symbol XOM)  (sector ENERGY)     (qty -500.0))
   ((symbol CVX)  (sector ENERGY)     (qty -300.0) (stale_after_s 300))
   ; Every other name is here at zero, because a signal may name only a
   ; symbol the book declares (R7). On a host with a paper key the account
   ; says what is held; here, nothing. SPY and TLT are EXP-A01's two names.
   ;
   ; stale_after_s: how long a name may go without a print before the feed
   ; calls it stale, where the engine's 90 s is wrong for it. On IEX -- the
   ; feed a free plan streams -- a thin ETF prints minutes apart in the
   ; regular session, and at 90 s fourteen of these names read stale most
   ; of the day, which refused every intraday order in them (the desk's mark
   ; rule) and lit the health lamp for nothing. The values are tiers over
   ; the longest silence each name showed on 2026-10-27, 09:30-14:00 ET
   ; (tools/iex_gaps.py reproduces the measurement): 1800 for the names
   ; that went 11-34 minutes without a trade (DBC 34, DBA 23, VLUE 23, BIL
   ; 20, SHY 19, IEI 17, QUAL 15, MDY 12, TIP 11; SIZE printed not once in
   ; four and a half hours, and MTUM and USMV are its kin), 600 for 3-8
   ; minutes (IEF, EMB, INDA, FXI, DIA, VGK, UNG, USO, SLV), 300 for 90 s to
   ; 3 minutes (XLRE, LQD, EWJ, EFA, TLT, CVX, XLU, HYG, XLI, XLB, GLD, XLP,
   ; XLF, EEM, JPM, XLK, XLY). The rest never went a minute without one.
   ((symbol SPY)  (sector INDEX)      (qty 0.0))
   ; TLT is in the site's rates group with BIL, SHY, IEI, IEF and TIP, so it
   ; is RATES: a class of its own would give the Treasury curve two 35% caps.
   ((symbol TLT)  (sector RATES)      (qty 0.0) (stale_after_s 300))
   ; The rest of the site's 42 ETFs (quant/src/ohcamel_quant/universes.json,
   ; less its crypto and volatility groups), one sector per group, for the
   ; farm's strategies below. SPY and TLT above are two of the 42.
   ; US equity indices: INDEX.
   ((symbol QQQ)  (sector INDEX)      (qty 0.0))
   ((symbol DIA)  (sector INDEX)      (qty 0.0) (stale_after_s 600))
   ((symbol IWM)  (sector INDEX)      (qty 0.0))
   ((symbol MDY)  (sector INDEX)      (qty 0.0) (stale_after_s 1800))
   ; S&P 500 sectors: SECTOR.
   ((symbol XLK)  (sector SECTOR)     (qty 0.0) (stale_after_s 300))
   ((symbol XLF)  (sector SECTOR)     (qty 0.0) (stale_after_s 300))
   ((symbol XLE)  (sector SECTOR)     (qty 0.0))
   ((symbol XLV)  (sector SECTOR)     (qty 0.0))
   ((symbol XLI)  (sector SECTOR)     (qty 0.0) (stale_after_s 300))
   ((symbol XLY)  (sector SECTOR)     (qty 0.0) (stale_after_s 300))
   ((symbol XLP)  (sector SECTOR)     (qty 0.0) (stale_after_s 300))
   ((symbol XLU)  (sector SECTOR)     (qty 0.0) (stale_after_s 300))
   ((symbol XLB)  (sector SECTOR)     (qty 0.0) (stale_after_s 300))
   ((symbol XLRE) (sector SECTOR)     (qty 0.0) (stale_after_s 300))
   ((symbol XLC)  (sector SECTOR)     (qty 0.0))
   ; Style factors: STYLE.
   ((symbol MTUM) (sector STYLE)      (qty 0.0) (stale_after_s 1800))
   ((symbol QUAL) (sector STYLE)      (qty 0.0) (stale_after_s 1800))
   ((symbol VLUE) (sector STYLE)      (qty 0.0) (stale_after_s 1800))
   ((symbol USMV) (sector STYLE)      (qty 0.0) (stale_after_s 1800))
   ((symbol SIZE) (sector STYLE)      (qty 0.0) (stale_after_s 1800))
   ; US Treasuries: RATES.
   ((symbol BIL)  (sector RATES)      (qty 0.0) (stale_after_s 1800))
   ((symbol SHY)  (sector RATES)      (qty 0.0) (stale_after_s 1800))
   ((symbol IEI)  (sector RATES)      (qty 0.0) (stale_after_s 1800))
   ((symbol IEF)  (sector RATES)      (qty 0.0) (stale_after_s 600))
   ((symbol TIP)  (sector RATES)      (qty 0.0) (stale_after_s 1800))
   ; Credit: CREDIT.
   ((symbol LQD)  (sector CREDIT)     (qty 0.0) (stale_after_s 300))
   ((symbol HYG)  (sector CREDIT)     (qty 0.0) (stale_after_s 300))
   ((symbol EMB)  (sector CREDIT)     (qty 0.0) (stale_after_s 600))
   ; International equity: INTL.
   ((symbol EFA)  (sector INTL)       (qty 0.0) (stale_after_s 300))
   ((symbol EEM)  (sector INTL)       (qty 0.0) (stale_after_s 300))
   ((symbol VGK)  (sector INTL)       (qty 0.0) (stale_after_s 600))
   ((symbol EWJ)  (sector INTL)       (qty 0.0) (stale_after_s 300))
   ((symbol FXI)  (sector INTL)       (qty 0.0) (stale_after_s 600))
   ((symbol INDA) (sector INTL)       (qty 0.0) (stale_after_s 600))
   ; Commodities: COMMODITY.
   ((symbol GLD)  (sector COMMODITY)  (qty 0.0) (stale_after_s 300))
   ((symbol SLV)  (sector COMMODITY)  (qty 0.0) (stale_after_s 600))
   ((symbol USO)  (sector COMMODITY)  (qty 0.0) (stale_after_s 600))
   ((symbol UNG)  (sector COMMODITY)  (qty 0.0) (stale_after_s 600))
   ((symbol DBA)  (sector COMMODITY)  (qty 0.0) (stale_after_s 1800))
   ((symbol DBC)  (sector COMMODITY)  (qty 0.0) (stale_after_s 1800))))
 (limits
  (((name name-cap)         (scope Portfolio)           (kind (Name_weight 0.15)))
   ((name class-commodity)  (scope (Sector COMMODITY))  (kind (Class_gross 0.35)))
   ((name class-credit)     (scope (Sector CREDIT))     (kind (Class_gross 0.35)))
   ((name class-energy)     (scope (Sector ENERGY))     (kind (Class_gross 0.35)))
   ((name class-financials) (scope (Sector FINANCIALS)) (kind (Class_gross 0.35)))
   ((name class-index)      (scope (Sector INDEX))      (kind (Class_gross 0.35)))
   ((name class-intl)       (scope (Sector INTL))       (kind (Class_gross 0.35)))
   ((name class-rates)      (scope (Sector RATES))      (kind (Class_gross 0.35)))
   ((name class-sector)     (scope (Sector SECTOR))     (kind (Class_gross 0.35)))
   ((name class-style)      (scope (Sector STYLE))      (kind (Class_gross 0.35)))
   ((name class-tech)       (scope (Sector TECH))       (kind (Class_gross 0.35)))
   ((name book-cap)         (scope Portfolio)           (kind (Gross_notional 700000.0)))
   ((name es-cap)           (scope Portfolio)           (kind (Expected_shortfall 45000.0)))
   ((name var-cap)          (scope Portfolio)           (kind (Value_at_risk 30000.0)))
   ((name dd-cap)           (scope Portfolio)           (kind (Max_drawdown 0.10)))))

 ; ---------------------------------------------------------------------------
 ; The risk windows. OPTIONAL -- omit any word and it takes the value below.
 ;
 ;   drawdown_window         the live drawdown -- what a Max_drawdown limit and
 ;                           the drawdown governor read -- is the live equity's
 ;                           decline from the peak of the last this-many
 ;                           session closes and the live equity. 250 is a
 ;                           trading year. From the all-time peak instead, a
 ;                           book the governor had cut could never earn its
 ;                           size back by holding still. 1 to 10000.
 ;   var_window              how many daily returns the live risk model reads:
 ;                           the engine's return window, so VaR, the
 ;                           covariance and the shortfall all read these
 ;                           sessions, and the startup backfill fetches them.
 ;                           An Expected_shortfall limit reads only a FULL
 ;                           window: until it is full the limit cannot be
 ;                           evaluated, and while it is one the switch trips
 ;                           on, a proposal that raises gross is refused. 250
 ;                           is a trading year. 2 to 2520.
 ;   es_confidence           the confidence an Expected_shortfall limit takes
 ;                           its tail at: 0.99 is the worst 1% of the window,
 ;                           2.5 of 250 sessions, the third worst at half
 ;                           weight. The VaR's own confidence (95%) is
 ;                           separate. Strictly between 0 and 1.
 (risk ((drawdown_window 250) (var_window 250) (es_confidence 0.99)))

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
 ;                           means none, even when enabled. The names here
 ;                           also bind the pre-trade gate, whatever
 ;                           kill_switch_enabled and enabled say: while one
 ;                           of these limits cannot be evaluated (a VaR whose
 ;                           return window is not yet full, a drawdown that
 ;                           cannot be measured), every proposal that raises
 ;                           gross notional is refused, naming it. A proposal
 ;                           that lowers gross or leaves it equal still
 ;                           passes.
 ;
 ; What the kill switch actually does: it halts the desk. Every new order is
 ; refused, every open order the desk owns is cancelled, and positions are left
 ; exactly as they are -- the switch itself never sells anything. One
 ; exception, the desk block's (reduce_through_trip true): while a LIMIT has
 ; tripped it, an order that only shrinks a position still goes out -- a
 ; stopped strategy's flatten, a governor's cut. On the live host a person can
 ; also halt the desk by hand from the dashboard, and a halt stays until someone
 ; resets it there. The switch only exists when (enabled true) below: the whole
 ; alerting block, this switch included, is inert while alerting is off.
 ; Shipped ON since Task 1.11, to the log only (sinks (Log)); the kill switch is
 ; still a separate decision and ships off. Paging a person is not this block's
 ; job: deploy/watch.sh posts to OHCAMEL_ALERT_URL in /etc/ohcamel/ops.env.
 (alerts
  ((enabled true)
   (sinks (Log))
   (clear_below 0.95)
   (kill_switch_enabled false)
   (kill_switch_trips_on (book-cap es-cap dd-cap))))

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
 ;   overlay_max_age         how long an overlay's value may stand at all, in
 ;                           SESSION hours (09:30-16:00 New York, weekdays):
 ;                           past it, or when its age cannot be told, a sizing
 ;                           the adjust pass was holding decays to the
 ;                           fallback (never above the overlay's own value).
 ;                           An overlay written after Friday's close still
 ;                           stands at Monday's open. Positive and finite.
 ;   overlay_fallback        the scale used once the overlay is stale. The page
 ;                           says OVERLAY STALE.
 ;   overlay_band            a move in the scale larger than this re-sizes every
 ;                           strategy that adjusts intraday.
 ;   intraday_slip_bps       every desk order is a day limit this far past the
 ;                           quote fetched as it is sent: a buy above the
 ;                           ask, a sell below the bid. Below the collar.
 ;   quote_max_age_s         a quote FETCHED longer ago than this when its
 ;                           order is sent, or crossed, or missing a side, or
 ;                           stamped ahead of its fetch, is no quote, and the
 ;                           order is refused (rule quote). In (0, 60]. The
 ;                           quote's own stamp is when it last changed: an
 ;                           order that grows a position needs it within 60 s
 ;                           of the fetch; one that only shrinks the
 ;                           strategy's own position takes any, the collar
 ;                           standing guard.
 ;   min_leg_notional        an adjust-pass leg smaller than this, in dollars,
 ;                           is dropped, and the drop journaled (a signal's
 ;                           own rebalance drops none).
 ;   wave_timeout_s          how long the desk waits on one wave of legs before
 ;                           it cancels what is left.
 ;   flatten_at              New York time, HH:MM inside the session, at which a
 ;                           strategy with (horizon close) is flattened.
 ;   stream                  the names streamed first. The stream carries
 ;                           thirty; every other name is polled.
 ;   reduce_through_trip     true | false. While a LIMIT has tripped the kill
 ;                           switch, an order that only shrinks a position
 ;                           still passes it: it shrinks the strategy's own
 ;                           position, never through zero, and either the
 ;                           account's too, by no more than it holds, or the
 ;                           name's sub-ledger sums (the strategies' and the
 ;                           hand's positions are the account's, nothing in
 ;                           the name may still fill) -- so each of two
 ;                           strategies on opposite sides of one name can be
 ;                           flattened. A stopped strategy's flatten, a
 ;                           governor's cut. Under that netting a short's
 ;                           cover can RAISE the account's net position in
 ;                           the name, by at most the other strategies' own
 ;                           positions there (their long, hedged until then,
 ;                           is left unhedged); under a drawdown trip nothing
 ;                           at the account's level bounds it beyond that,
 ;                           and the gate still judges every such order. A
 ;                           hand's halt and the engine's stop still refuse
 ;                           every order. Default false (ruling R2, the
 ;                           spec's default, awaits the owner).
 ;
 ; The values below are the engine's own defaults, written out so that turning
 ; the desk on is one word rather than a guess. Three are not: incubation_cap,
 ; which sizes the roster below, stream, which names five of its names, and
 ; reduce_through_trip.
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
   (incubation_cap 0.55)
   (vol_target 0.10)
   (overlay_max_age_minutes 60)
   (overlay_max_age 4.0)
   (overlay_fallback 0.5)
   (overlay_band 0.10)
   (intraday_slip_bps 10.0)
   (quote_max_age_s 5.0)
   (min_leg_notional 200.0)
   (wave_timeout_s 180.0)
   (flatten_at "15:50")
   (stream (SPY QQQ IWM TLT GLD))
   ; true here, false by default. This DEPARTS from INC-01, which promised
   ; that "the kill switch halts and never liquidates": under INC-02, ruling
   ; R2 -- the completion spec's default, which awaits the owner's approval
   ; -- lets a limit's trip pass the orders that only take risk off. Until
   ; the owner approves R2 the word stays false on any real book, and a live
   ; host keeps INC-01's switch.
   (reduce_through_trip true)))

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
 ;   stop words       INC-02's volatility-scaled stop (a draft awaiting the
 ;                     owner): the strategy is stopped when its drawdown from
 ;                     peak reaches stop_vol_multiple x its ex_ante_vol, floored
 ;                     at stop_floor and capped at stop_cap, or stop_sessions
 ;                     sessions after its first fill, and is sized no more.
 ;                     Defaults 2.0, 0.05, 0.25, 500. ex_ante_vol has no
 ;                     default and a strategy without one does not load: it is
 ;                     the annualised volatility of the cell's 252-session
 ;                     backtest at its traded parameters, gross 1, filled at
 ;                     the next open, which the monthly freeze writes
 ;                     (signals/freeze.py).
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
 ; its suggested universes that it can run on -- every one incubation. Every
 ; one at an equal 0.025: 22 x 0.025 = 0.55, the incubation_cap of 0.55. This
 ; is INC-02's roster (research/experiments/INC-02/roster.yaml), a DRAFT that
 ; awaits the owner's approval: INC-01's 23 less farm_pairs_coint_sectors
 ; (pairs_coint takes two tickers, and sectors is eleven), with no slot held
 ; for EXP-Q01 (its pre-registration lets nothing reach the desk). INC-01
 ; closed at 2026-11-05.
 ;
 ; THE EX_ANTE_VOL VALUES BELOW ARE NOT ALL MEASURED. exp_a01_spy and exp_a01_tlt
 ; carry EXP-A01's own: its battery's holdout backtest re-run at the manifests'
 ; selected_params on the committed fixtures, the last 252 sessions
 ; (tools/inc02_exp_a01_vol.py; output research/experiments/INC-02/
 ; exp_a01_vol.json). The 20 farm rows carry 0.075, a placeholder and no
 ; measurement: twice it is 0.15, exactly the fixed line INC-01 drew for every
 ; strategy, so an unreplaced placeholder draws the very drawdown line INC-01 drew
 ; (the sessions count is 500, not 250).
 ; The fixtures hold no farm universe whole (9 of the roster's 42 ETFs), so the
 ; real figure comes only from the freeze on the droplet's warehouse. Before the
 ; owner deploys, tools/book_ex_ante_vol.py rewrites every row from the month's
 ; signals.freeze artifact and refuses a row that has none.
 (signals
  ((strategies
    (((name exp_a01_spy) (symbols (SPY)) (max_age 3) (sizing incubation)
      (capital_fraction 0.025) (cadence daily) (adjust intraday)
      (stop_vol_multiple 2.0) (stop_floor 0.05) (stop_cap 0.25) (stop_sessions 500)
      (ex_ante_vol 0.095712))
     ((name exp_a01_tlt) (symbols (TLT)) (max_age 3) (sizing incubation)
      (capital_fraction 0.025) (cadence daily) (adjust intraday)
      (stop_vol_multiple 2.0) (stop_floor 0.05) (stop_cap 0.25) (stop_sessions 500)
      (ex_ante_vol 0.050165))
     ((name farm_tsmom_us_equity_indices) (symbols (SPY QQQ DIA IWM MDY)) (max_age 3) (sizing incubation)
      (capital_fraction 0.025) (cadence daily) (adjust intraday)
      (stop_vol_multiple 2.0) (stop_floor 0.05) (stop_cap 0.25) (stop_sessions 500)
      (ex_ante_vol 0.075))
     ((name farm_tsmom_rates) (symbols (BIL SHY IEI IEF TLT TIP)) (max_age 3) (sizing incubation)
      (capital_fraction 0.025) (cadence daily) (adjust intraday)
      (stop_vol_multiple 2.0) (stop_floor 0.05) (stop_cap 0.25) (stop_sessions 500)
      (ex_ante_vol 0.075))
     ((name farm_tsmom_commodities) (symbols (GLD SLV USO UNG DBA DBC)) (max_age 3) (sizing incubation)
      (capital_fraction 0.025) (cadence daily) (adjust intraday)
      (stop_vol_multiple 2.0) (stop_floor 0.05) (stop_cap 0.25) (stop_sessions 500)
      (ex_ante_vol 0.075))
     ((name farm_tsmom_international) (symbols (EFA EEM VGK EWJ FXI INDA)) (max_age 3) (sizing incubation)
      (capital_fraction 0.025) (cadence daily) (adjust intraday)
      (stop_vol_multiple 2.0) (stop_floor 0.05) (stop_cap 0.25) (stop_sessions 500)
      (ex_ante_vol 0.075))
     ((name farm_tsmom_credit) (symbols (LQD HYG EMB)) (max_age 3) (sizing incubation)
      (capital_fraction 0.025) (cadence daily) (adjust intraday)
      (stop_vol_multiple 2.0) (stop_floor 0.05) (stop_cap 0.25) (stop_sessions 500)
      (ex_ante_vol 0.075))
     ((name farm_xsmom_sectors) (symbols (XLK XLF XLE XLV XLI XLY XLP XLU XLB XLRE XLC)) (max_age 3) (sizing incubation)
      (capital_fraction 0.025) (cadence daily) (adjust intraday)
      (stop_vol_multiple 2.0) (stop_floor 0.05) (stop_cap 0.25) (stop_sessions 500)
      (ex_ante_vol 0.075))
     ((name farm_xsmom_international) (symbols (EFA EEM VGK EWJ FXI INDA)) (max_age 3) (sizing incubation)
      (capital_fraction 0.025) (cadence daily) (adjust intraday)
      (stop_vol_multiple 2.0) (stop_floor 0.05) (stop_cap 0.25) (stop_sessions 500)
      (ex_ante_vol 0.075))
     ((name farm_dual_momentum_us_equity_indices) (symbols (SPY QQQ DIA IWM MDY)) (max_age 3) (sizing incubation)
      (capital_fraction 0.025) (cadence daily) (adjust intraday)
      (stop_vol_multiple 2.0) (stop_floor 0.05) (stop_cap 0.25) (stop_sessions 500)
      (ex_ante_vol 0.075))
     ((name farm_dual_momentum_international) (symbols (EFA EEM VGK EWJ FXI INDA)) (max_age 3) (sizing incubation)
      (capital_fraction 0.025) (cadence daily) (adjust intraday)
      (stop_vol_multiple 2.0) (stop_floor 0.05) (stop_cap 0.25) (stop_sessions 500)
      (ex_ante_vol 0.075))
     ((name farm_sma_trend_us_equity_indices) (symbols (SPY QQQ DIA IWM MDY)) (max_age 3) (sizing incubation)
      (capital_fraction 0.025) (cadence daily) (adjust intraday)
      (stop_vol_multiple 2.0) (stop_floor 0.05) (stop_cap 0.25) (stop_sessions 500)
      (ex_ante_vol 0.075))
     ((name farm_sma_trend_commodities) (symbols (GLD SLV USO UNG DBA DBC)) (max_age 3) (sizing incubation)
      (capital_fraction 0.025) (cadence daily) (adjust intraday)
      (stop_vol_multiple 2.0) (stop_floor 0.05) (stop_cap 0.25) (stop_sessions 500)
      (ex_ante_vol 0.075))
     ((name farm_sma_trend_international) (symbols (EFA EEM VGK EWJ FXI INDA)) (max_age 3) (sizing incubation)
      (capital_fraction 0.025) (cadence daily) (adjust intraday)
      (stop_vol_multiple 2.0) (stop_floor 0.05) (stop_cap 0.25) (stop_sessions 500)
      (ex_ante_vol 0.075))
     ((name farm_vol_managed_us_equity_indices) (symbols (SPY QQQ DIA IWM MDY)) (max_age 3) (sizing incubation)
      (capital_fraction 0.025) (cadence daily) (adjust intraday)
      (stop_vol_multiple 2.0) (stop_floor 0.05) (stop_cap 0.25) (stop_sessions 500)
      (ex_ante_vol 0.075))
     ((name farm_risk_parity_rates) (symbols (BIL SHY IEI IEF TLT TIP)) (max_age 3) (sizing incubation)
      (capital_fraction 0.025) (cadence daily) (adjust intraday)
      (stop_vol_multiple 2.0) (stop_floor 0.05) (stop_cap 0.25) (stop_sessions 500)
      (ex_ante_vol 0.075))
     ((name farm_risk_parity_credit) (symbols (LQD HYG EMB)) (max_age 3) (sizing incubation)
      (capital_fraction 0.025) (cadence daily) (adjust intraday)
      (stop_vol_multiple 2.0) (stop_floor 0.05) (stop_cap 0.25) (stop_sessions 500)
      (ex_ante_vol 0.075))
     ((name farm_risk_parity_commodities) (symbols (GLD SLV USO UNG DBA DBC)) (max_age 3) (sizing incubation)
      (capital_fraction 0.025) (cadence daily) (adjust intraday)
      (stop_vol_multiple 2.0) (stop_floor 0.05) (stop_cap 0.25) (stop_sessions 500)
      (ex_ante_vol 0.075))
     ((name farm_risk_parity_us_equity_indices) (symbols (SPY QQQ DIA IWM MDY)) (max_age 3) (sizing incubation)
      (capital_fraction 0.025) (cadence daily) (adjust intraday)
      (stop_vol_multiple 2.0) (stop_floor 0.05) (stop_cap 0.25) (stop_sessions 500)
      (ex_ante_vol 0.075))
     ((name farm_low_beta_sectors) (symbols (XLK XLF XLE XLV XLI XLY XLP XLU XLB XLRE XLC)) (max_age 3) (sizing incubation)
      (capital_fraction 0.025) (cadence daily) (adjust intraday)
      (stop_vol_multiple 2.0) (stop_floor 0.05) (stop_cap 0.25) (stop_sessions 500)
      (ex_ante_vol 0.075))
     ((name farm_st_reversal_sectors) (symbols (XLK XLF XLE XLV XLI XLY XLP XLU XLB XLRE XLC)) (max_age 3) (sizing incubation)
      (capital_fraction 0.025) (cadence daily) (adjust intraday)
      (stop_vol_multiple 2.0) (stop_floor 0.05) (stop_cap 0.25) (stop_sessions 500)
      (ex_ante_vol 0.075))
     ((name farm_pairs_distance_sectors) (symbols (XLK XLF XLE XLV XLI XLY XLP XLU XLB XLRE XLC)) (max_age 3) (sizing incubation)
      (capital_fraction 0.025) (cadence daily) (adjust intraday)
      (stop_vol_multiple 2.0) (stop_floor 0.05) (stop_cap 0.25) (stop_sessions 500)
      (ex_ante_vol 0.075)))))))
