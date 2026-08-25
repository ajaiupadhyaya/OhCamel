(* Phase 1. The heart of the project.

   The Incremental dependency graph. Input Var.t cells for positions and prices;
   derived nodes for exposure (per-instrument, per-sector), VaR, expected
   shortfall, and limit checks.

   The rule that makes this module different from a normal recompute function:
   a node may only read its declared inputs. Reaching outside the graph for a
   value -- a global, a mutable ref, a fresh API call -- makes that dependency
   invisible to Incremental, which will then happily serve a stale answer
   because it has no idea anything changed. Every dependency must be an edge.

   Per the README, each node's dependencies get a comment explaining *why* it
   depends on what it depends on. The dependency structure is the deliverable
   here, not an implementation detail, so it should be readable cold.

   ------------------------------------------------------------------------
   SHAPE OF THE GRAPH

     price[S] ---+
                 +--> exposure[S] --+--> sector[K] --+
     qty[S]   ---+                  |                |
                                    +----------------+--> exposure_map
                                    |                +--> gross ---+---> weights
                                    |                +--> net --+  |
                                    |                           |  |
     cash --------------------------|---------------------------+--|--> equity
                                    |                              |     |
     equity_history ----------------|------------------------------|-----+--> drawdown
                                    |                              |
     returns[S] --> aligned_returns +--> covariance                |
                            |       +--> covariance_ewma          |
                            |                     \              /
                            +----------------------+-> portfolio_returns
                                                    \      |
                                                     \     +--> historical_var --> var_notional
                                                      \    +--> expected_shortfall --> es_notional
                                                       +------> parametric_var
                                                       +------> parametric_var_ewma
                                                            |
     factor_returns ----------------------------------------+--> portfolio_beta
                                                                        |
     (each limit reads exactly one of the nodes above) --> limit[name] -+--> breaches

     -- and, deliberately disconnected from everything above --

     last_tick[S] --+
                    +--> feed[S] --> feed_health
     now -----------+

   The three edges worth staring at, because they are the ones that justify the
   architecture:

   - [covariance] hangs off [aligned_returns] and NOTHING else. A price tick
     does not touch it. Rebuilding an n x n covariance matrix is the single most
     expensive thing this engine does, and in a poll-and-recompute design it
     would be redone on every tick for no reason. [covariance_ewma] is its
     sibling on the same edge -- the same matrix under exponentially decaying
     weights -- so the engine now computes the most expensive thing it does
     TWICE, and a tick still reaches neither. That is the clearest statement of
     the architecture available: the cost of an estimator is paid when its
     inputs move, not when the screen refreshes.

   - Each limit is its own node hanging off the one quantity it measures. An
     instrument-scoped limit on AAPL is downstream of exposure[AAPL] alone, so a
     tick in an unrelated name leaves it strictly untouched -- not "recomputed
     and found equal", but never visited.

   - The feed-health branch is a DEAD END on purpose. [now] is advanced by a
     timer, and if any risk node were downstream of it, that timer would
     recompute the book on a schedule -- which is precisely the poll-driven
     design this project exists to replace. Staleness is time-dependent and has
     to be; the discipline is that nothing else may be. Note the asymmetry:
     [feed[S]] depends on [last_tick[S]], never on [price[S]], so the two
     branches share an event but not an edge.

   test/test_graph.ml asserts all three as recomputation counts. That test is
   the architectural premise stated as an executable claim.
   ------------------------------------------------------------------------ *)

open Core
open Types

(* One Incremental state for the whole engine. The functor is generative, so
   this application is distinct from the one in toolchain_check.ml -- but every
   Graph.t built in this process shares this state, and therefore shares
   [stabilize]. That is normal Incremental usage (the state is a scheduler, not
   a graph), and it is why [destroy] exists: an abandoned graph whose observers
   are still live would keep recomputing on every stabilize. *)
module Inc = Incremental.Make ()

(* Node labels, used by the [on_compute] diagnostic hook and hence by the tests.

   These are a public contract, not debug strings: test_graph.ml asserts on
   exact names, and renaming one silently weakens the test that guards the whole
   design. *)
module Node_name = struct
  let exposure (s : Symbol.t) = "exposure:" ^ Symbol.to_string s
  let sector (s : Sector.t) = "sector:" ^ Sector.to_string s
  let limit (name : string) = "limit:" ^ name
  let exposure_map = "exposure_map"
  let sector_map = "sector_map"
  let gross = "gross_exposure"
  let net = "net_exposure"
  let weights = "weights"
  let aligned_returns = "aligned_returns"
  let covariance = "covariance"
  let covariance_ewma = "covariance_ewma"
  let portfolio_returns = "portfolio_returns"
  let historical_var = "historical_var"
  let expected_shortfall = "expected_shortfall"
  let parametric_var = "parametric_var"
  let parametric_var_ewma = "parametric_var_ewma"
  let attribution = "attribution"
  let component_var_map = "component_var_map"
  let component_var_sector_map = "component_var_sector_map"
  let diversification_ratio = "diversification_ratio"
  let var_notional = "var_notional"
  let es_notional = "es_notional"
  let equity = "equity"
  let drawdown = "current_drawdown"
  let breaches = "breaches"
  let portfolio_beta = "portfolio_beta"
  let feed (s : Symbol.t) = "feed:" ^ Symbol.to_string s
  let feed_health = "feed_health"
end

(* Which covariance matrix the Euler decomposition reads.

   The graph publishes both matrices unconditionally -- they are siblings off
   the same edge and the comparison between them is the point -- but the
   attribution path has to pick one, because a decomposition is a statement
   about a single covariance structure and averaging two of them would be a
   third thing that is neither.

   This is a construction-time constant, not a runtime read. The attribution
   node's edge is therefore still declared and still static, exactly like
   [confidence] and [return_window]; what varies is which of two existing edges
   it is wired to, decided once before any node exists. A node that chose its
   input by reading a mutable cell at evaluation time would be the thing this
   module forbids.

   The default is [Equal_weighted] so that an existing caller's component-VaR
   numbers -- and every limit written against them -- do not change under it. *)
module Covariance_estimator = struct
  type t = Equal_weighted | Ewma [@@deriving sexp_of, compare, equal]

  let to_string = function Equal_weighted -> "equal_weighted" | Ewma -> "ewma"
end

(* Is the data arriving?

   This exists because the most dangerous failure a risk engine has is not a
   wrong number, it is a right-looking number computed from inputs that stopped
   arriving twenty minutes ago. Every other value in this module answers "what
   is the risk"; this one answers "should you believe the answer".

   [never_seen] and [stale] are distinct states and are kept distinct. A symbol
   that has never printed since startup is a subscription that did not take --
   a configuration problem. A symbol that printed and then went quiet is a feed
   that dropped, or a name that simply is not trading. Collapsing them into one
   "no data" flag would hide which of those is happening at the moment you most
   need to know. *)
module Feed_health = struct
  module Symbol_state = struct
    type t = {
      symbol : Symbol.t;
      last_tick : Time.t option;
      never_seen : bool;
      stale : bool;
    }
    [@@deriving sexp_of, compare, equal, fields ~getters]
  end

  type t = {
    symbols : Symbol_state.t list;
    stale : Symbol.t list;
    never_seen : Symbol.t list;
  }
  [@@deriving sexp_of, fields ~getters]

  (* Age is deliberately NOT a field, and this is the design decision worth
     understanding in this module.

     Age changes continuously -- by definition, every time the clock moves. A
     node holding it would therefore produce a new value on every clock tick and
     wake everything downstream, forever, whether or not anything decision-
     relevant had happened. The obvious dodge, cutting off on the other fields
     and letting age ride along, is worse: the node would then report an age it
     had already declared unchanged, which is a stale number wearing a fresh
     timestamp -- exactly the failure this whole branch exists to detect.

     So the graph computes the CLASSIFICATION, which changes rarely, and age is
     derived here on demand from a timestamp the caller already has. *)
  let age ~(now : Time.t) (state : Symbol_state.t) : Time.Span.t option =
    Option.map state.Symbol_state.last_tick ~f:(fun tick -> Time.diff now tick)

  (* True when every symbol has printed recently. The single bit a dashboard
     puts a colour on; the lists above are what it shows when the bit is red. *)
  let all_healthy t = List.is_empty t.stale && List.is_empty t.never_seen
end

(* Everything the engine currently believes, read out in one consistent pass.

   A snapshot is taken after a stabilize, so all of its fields are from the same
   fixed point of the graph. Reading observers one at a time across a stabilize
   boundary could mix a pre-tick VaR with a post-tick exposure, which is the
   kind of inconsistency that makes a risk display untrustworthy in exactly the
   minute it matters. *)
module Snapshot = struct
  type t = {
    exposure_by_instrument : Notional.t Symbol.Map.t;
    exposure_by_sector : Notional.t Sector.Map.t;
    gross_exposure : Notional.t;
    net_exposure : Notional.t;
    weights : float Symbol.Map.t;
    equity : Notional.t;
    current_drawdown : float;
    (* The three risk numbers are options rather than floats, and this is not
         defensiveness for its own sake. Until every instrument has enough
         return history to form a common window there is no distribution to take
         a quantile of, and the honest answer is "unknown". Returning 0.0
         instead would render on a dashboard as "no risk", which risk_metrics.ml
         calls out as the single most dangerous wrong answer this system could
         give. [warming_up] is the same fact in the form a UI can put a badge
         on. *)
    historical_var : float option;
    expected_shortfall : float option;
    parametric_var : float option;
    (* The same closed-form VaR computed from an exponentially weighted
         covariance matrix instead of an equal-weighted one, and carried
         alongside rather than instead of it.

         Two parametric numbers rather than one, for the same reason there are
         already two VaRs of different kinds. Historical against parametric is
         a read on how non-normal the tail is. Equal-weighted against EWMA is a
         read on whether the volatility REGIME is moving: the equal-weighted
         window has to see a change accumulate before it reports it, so the
         EWMA number rising above it is the window still catching up, and the
         EWMA number falling below it is a shock ageing out of the window that
         the market has already stopped pricing. Neither is visible from one
         number. See vol_estimators.ml. *)
    parametric_var_ewma : float option;
    (* The decay factor the number above was computed with, carried in the
         snapshot rather than assumed by the reader. Two runs at different
         lambdas produce different numbers under the same field name, and a
         wire format that does not say which is one that cannot be compared
         against itself later. *)
    ewma_lambda : float;
    value_at_risk_notional : Notional.t option;
    expected_shortfall_notional : Notional.t option;
    (* Rolling beta of the book against the macro factor series. [None] for the
         same reason as the risk numbers above, plus one more: a factor that has
         not moved over the window makes beta genuinely undefined rather than
         zero. Flat rate series are routine, not exceptional. *)
    portfolio_beta : float option;
    (* Where the risk is, rather than how much of it there is.

         Each instrument's Euler share of the book's parametric VaR, in dollars,
         summing to the portfolio parametric VaR exactly. Entries can be
         NEGATIVE: a position that moves against the rest of the book reduces
         portfolio risk, and reporting that as a small positive number -- which
         is what an absolute value here would do -- would hide the one thing
         worth knowing about a hedge.

         The sector map is the same numbers grouped, and it is a plain sum
         because component risk is additive. That is the property standalone
         VaR does not have and the reason this is the decomposition worth
         publishing. *)
    component_var_by_instrument : Notional.t Symbol.Map.t option;
    component_var_by_sector : Notional.t Sector.Map.t option;
    (* Sum of standalone position volatilities over portfolio volatility, so
         at least 1.0. How much the book is getting from being a portfolio
         rather than a pile of positions -- and, watched over time, the number
         that falls toward 1.0 as correlations converge in a selloff. *)
    diversification_ratio : float option;
    warming_up : bool;
    (* Whether the inputs the numbers above were computed from are still
         arriving. Read this before reading anything else. *)
    feed_health : Feed_health.t;
    (* Limits that could be evaluated, breached or not -- a limit sitting at
         30% of its cap is information, so non-breaches are kept. *)
    breaches : Breach.t list;
    (* Limits whose input was unavailable (a VaR limit while warming up).
         Listed explicitly rather than omitted, because a limit missing from a
         list of breaches reads as a limit that is fine. *)
    unevaluated_limits : string list;
  }
  [@@deriving sexp_of, fields ~getters]

  let breached t = List.filter t.breaches ~f:Breach.breached
end

type t = {
  instruments : Instrument.t Symbol.Map.t;
  limits : Limit.t list;
  confidence : float;
  return_window : int;
  ewma_lambda : float;
  covariance_for_attribution : Covariance_estimator.t;
  equity_history_limit : int;
  staleness_threshold : Time.Span.t;
  (* Inputs. These are the only mutable cells in the system; everything else
       is a function of them. *)
  price_vars : Price.t Inc.Var.t Symbol.Map.t;
  qty_vars : Qty.t Inc.Var.t Symbol.Map.t;
  returns_vars : float array Inc.Var.t Symbol.Map.t;
  cash_var : Notional.t Inc.Var.t;
  equity_history_var : float array Inc.Var.t;
  (* The macro factor's return window, fed by fred_client.ml. Separate from
       [returns_vars] because it is not an instrument: nothing is held in it,
       so it contributes to beta and to nothing else. *)
  factor_returns_var : float array Inc.Var.t;
  (* Feed liveness. [last_tick_vars] is written by [apply_tick]; [now_var] is
       advanced by a timer. Both feed the dead-end branch described at the top
       of this file and must never be read by a risk node. *)
  last_tick_vars : Time.t option Inc.Var.t Symbol.Map.t;
  now_var : Time.t Inc.Var.t;
  (* Outputs. Incremental is demand-driven: a node with no observer is not
       recomputed at all, so every value the engine is supposed to publish has
       to be observed here or it silently goes stale. *)
  obs_exposure_by_instrument : Notional.t Symbol.Map.t Inc.Observer.t;
  obs_exposure_by_sector : Notional.t Sector.Map.t Inc.Observer.t;
  obs_gross : Notional.t Inc.Observer.t;
  obs_net : Notional.t Inc.Observer.t;
  obs_weights : float array Inc.Observer.t;
  obs_covariance : Owl.Mat.mat option Inc.Observer.t;
  obs_covariance_ewma : Owl.Mat.mat option Inc.Observer.t;
  obs_historical_var : float option Inc.Observer.t;
  obs_expected_shortfall : float option Inc.Observer.t;
  obs_parametric_var : float option Inc.Observer.t;
  obs_parametric_var_ewma : float option Inc.Observer.t;
  obs_component_var_by_instrument : Notional.t Symbol.Map.t option Inc.Observer.t;
  obs_component_var_by_sector : Notional.t Sector.Map.t option Inc.Observer.t;
  obs_diversification_ratio : float option Inc.Observer.t;
  obs_var_notional : Notional.t option Inc.Observer.t;
  obs_es_notional : Notional.t option Inc.Observer.t;
  obs_equity : Notional.t Inc.Observer.t;
  obs_drawdown : float Inc.Observer.t;
  obs_breaches : Breach.t option list Inc.Observer.t;
  obs_portfolio_beta : float option Inc.Observer.t;
  obs_feed_health : Feed_health.t Inc.Observer.t;
  (* Closures that release the observers above. Held as thunks so [destroy]
       does not have to name fourteen differently-typed observers. *)
  releases : (unit -> unit) list;
  (* Callbacks fired when any published value changes -- see [on_change]. *)
  change_listeners : (unit -> unit) list ref;
}

let find_var (vars : 'a Inc.Var.t Symbol.Map.t) (symbol : Symbol.t) ~(what : string) :
    'a Inc.Var.t =
  match Map.find vars symbol with
  | Some v -> v
  | None ->
      (* Loud rather than silent. A feed pushing a symbol the graph does not know
       about means the subscription and the book have diverged, and dropping the
       update quietly would leave a position marked at a stale price with
       nothing to indicate it. Phase 2's feed filters against [symbols] before
       calling in. *)
      failwithf "graph: no %s cell for unknown instrument %S" what
        (Symbol.to_string symbol) ()

(* -------------------------------------------------------------------------
   Construction
   ------------------------------------------------------------------------- *)

let create ?(on_compute = fun (_ : string) -> ()) ?(starting_cash = Notional.zero)
    ?(equity_history_limit = 10_000) ?(staleness_threshold = Time.Span.of_sec 90.0)
    ?(ewma_lambda = Vol_estimators.Ewma.default_lambda)
    ?(covariance_for_attribution = Covariance_estimator.Equal_weighted)
    ~(instruments : Instrument.t list) ~(limits : Limit.t list) ~(confidence : float)
    ~(return_window : int) () : t =
  if List.is_empty instruments then invalid_arg "graph: need at least one instrument";
  (match
     List.find_a_dup instruments ~compare:(fun a b ->
         Symbol.compare (Instrument.symbol a) (Instrument.symbol b))
   with
  | Some dup ->
      invalid_argf "graph: instrument %S appears twice"
        (Symbol.to_string (Instrument.symbol dup))
        ()
  | None -> ());
  if not (Float.( > ) confidence 0.0 && Float.( < ) confidence 1.0) then
    invalid_argf "graph: confidence must be strictly between 0 and 1, got %f" confidence
      ();
  if return_window < 2 then
    invalid_argf
      "graph: return_window must be at least 2 (a one-observation window has zero \
       variance, which makes every risk number meaningless rather than merely \
       imprecise), got %d"
      return_window ();
  if equity_history_limit < 1 then
    invalid_argf "graph: equity_history_limit must be positive, got %d"
      equity_history_limit ();
  (* Validated here rather than left to the first stabilize. Vol_estimators
     raises on a lambda outside (0, 1), and an exception inside a node body
     leaves the graph in a state Incremental cannot recover -- so the same rule
     as every other argument applies: reject it before a node exists. *)
  Vol_estimators.Ewma.validate_lambda ~lambda:ewma_lambda;
  if Time.Span.( <= ) staleness_threshold Time.Span.zero then
    invalid_argf "graph: staleness_threshold must be positive, got %s"
      (Time.Span.to_string_hum staleness_threshold)
      ();
  (* Validated before a single node exists, so that no node body can ever raise:
     an exception during stabilization leaves the graph in a state Incremental
     cannot recover, and a risk engine that dies on a bad limit definition is
     worse than one that refuses to start. *)
  Limits.validate ~instruments limits;
  let instruments_map =
    Symbol.Map.of_alist_exn (List.map instruments ~f:(fun i -> (Instrument.symbol i, i)))
  in
  (* The diagnostic hook. It runs inside node bodies, which is normally exactly
     what this codebase forbids -- see the note in limits.ml about why effects
     belong on observers. The exception is deliberate and narrow: counting
     recomputations is the one effect that must happen inside the body, because
     it is *about* the recomputation. It writes nothing the engine reads, so it
     cannot make the graph's output depend on evaluation order. Business effects
     still go on observers. *)
  let note (name : string) = on_compute name in
  let releases = ref [] in
  let change_listeners = ref [] in
  let observe node =
    let o = Inc.observe node in
    releases := (fun () -> Inc.Observer.disallow_future_use o) :: !releases;
    (* Every published value carries an update handler, which is what lets a
       consumer be told that something changed instead of asking. Incremental
       fires these only when a value actually changes -- the cutoffs upstream
       have already decided that -- so a quiet market produces no callbacks at
       all rather than a stream of "still the same".

       This is the last link in the chain the project is arguing for. Without
       it, a dashboard would have to poll the engine, and an engine that is
       reactive internally but polled at its edge has moved the timer rather
       than removed it. *)
    Inc.Observer.on_update_exn o ~f:(fun _ ->
        List.iter !change_listeners ~f:(fun listener -> listener ()));
    o
  in
  (* Input cells carry a value-equality cutoff, so re-sending an unchanged value
     costs nothing downstream. This matters more than it looks: a real feed
     republishes the same last-trade price constantly, and under the default
     physical-equality cutoff every one of those would boxed-float its way into
     a full VaR recomputation. *)
  let make_var ~equal init =
    let v = Inc.Var.create init in
    Inc.set_cutoff (Inc.Var.watch v) (Inc.Cutoff.of_equal equal);
    v
  in
  let cutoff ~equal node =
    Inc.set_cutoff node (Inc.Cutoff.of_equal equal);
    node
  in
  let symbols = Map.keys instruments_map in
  (* Prices start at zero, not at some invented reference. A book marked at zero
     is obviously unmarked; a book marked at a plausible-looking default is not,
     and would produce exposures that look real. The driver sets prices before
     the first stabilize the user sees. *)
  let price_vars =
    Symbol.Map.of_alist_exn
      (List.map symbols ~f:(fun s ->
           (s, make_var ~equal:Price.equal (Price.of_float 0.0))))
  in
  let qty_vars =
    Symbol.Map.of_alist_exn
      (List.map symbols ~f:(fun s -> (s, make_var ~equal:Qty.equal Qty.zero)))
  in
  let returns_vars =
    Symbol.Map.of_alist_exn
      (List.map symbols ~f:(fun s -> (s, make_var ~equal:(Array.equal Float.equal) [||])))
  in
  let cash_var = make_var ~equal:Notional.equal starting_cash in
  let equity_history_var = make_var ~equal:(Array.equal Float.equal) [||] in
  let factor_returns_var = make_var ~equal:(Array.equal Float.equal) [||] in
  (* [None] rather than the epoch for "never seen". The epoch would make a
     symbol that has never printed look like one that printed in 1970, which is
     a difference the feed-health node exists to report. *)
  let last_tick_vars =
    Symbol.Map.of_alist_exn
      (List.map symbols ~f:(fun s -> (s, make_var ~equal:(Option.equal Time.equal) None)))
  in
  let now_var = make_var ~equal:Time.equal Time.epoch in
  (* --- exposure --------------------------------------------------------- *)
  (* Depends on exactly one instrument's price and quantity, because market
     value is price x quantity and nothing else enters into it. One node per
     instrument rather than a single node over the whole book: this is the split
     that makes an instrument-scoped limit cheap, and it is why a tick in one
     name cannot reach a limit on another. *)
  let exposure_nodes =
    Map.mapi instruments_map ~f:(fun ~key:symbol ~data:_ ->
        let price = Map.find_exn price_vars symbol in
        let qty = Map.find_exn qty_vars symbol in
        cutoff ~equal:Notional.equal
          (Inc.map2 (Inc.Var.watch price) (Inc.Var.watch qty) ~f:(fun price qty ->
               note (Node_name.exposure symbol);
               notional ~price ~qty)))
  in
  (* [Map.data] is ordered by key, and [symbols] is [Map.keys] of the same map,
     so the two line up. Every place below that zips a list of results back
     against a list of symbols relies on this, including the alignment between
     [weights].(i) and row i of the covariance matrix -- get that wrong and
     parametric VaR silently computes the risk of a portfolio nobody holds. *)
  let exposure_list = Inc.all (Map.data exposure_nodes) in
  let exposure_map_node =
    Inc.map exposure_list ~f:(fun xs ->
        note Node_name.exposure_map;
        Symbol.Map.of_alist_exn (List.zip_exn symbols xs))
  in
  (* --- sector ----------------------------------------------------------- *)
  let symbols_by_sector =
    Map.fold instruments_map ~init:Sector.Map.empty
      ~f:(fun ~key:symbol ~data:instrument acc ->
        Map.add_multi acc ~key:(Instrument.sector instrument) ~data:symbol)
  in
  (* Depends on the exposures of its member instruments only -- the sector is
     read off Instrument.t, which is why Types.ml puts it there instead of in a
     lookup table: a side table would be a dependency Incremental cannot see. A
     tick in an energy name therefore never reaches the technology node. *)
  let sector_nodes =
    Map.mapi symbols_by_sector ~f:(fun ~key:sector ~data:members ->
        let member_nodes = List.map members ~f:(Map.find_exn exposure_nodes) in
        cutoff ~equal:Notional.equal
          (Inc.map (Inc.all member_nodes) ~f:(fun exposures ->
               note (Node_name.sector sector);
               Notional.sum exposures)))
  in
  let sectors = Map.keys sector_nodes in
  let sector_map_node =
    Inc.map
      (Inc.all (Map.data sector_nodes))
      ~f:(fun xs ->
        note Node_name.sector_map;
        Sector.Map.of_alist_exn (List.zip_exn sectors xs))
  in
  (* --- book-level exposure ---------------------------------------------- *)
  (* Both depend on every instrument, which is not a design failure -- a total
     genuinely is a function of all its parts. The incrementality that matters
     at this level is that the *inputs* to the sum are cached: a tick in one
     name re-adds n numbers instead of re-fetching and re-multiplying n
     positions. *)
  let gross_node =
    cutoff ~equal:Notional.equal
      (Inc.map exposure_list ~f:(fun xs ->
           note Node_name.gross;
           Notional.sum (List.map xs ~f:Notional.abs)))
  in
  let net_node =
    cutoff ~equal:Notional.equal
      (Inc.map exposure_list ~f:(fun xs ->
           note Node_name.net;
           Notional.sum xs))
  in
  (* Signed portfolio weights, normalised by gross so their absolute values sum
     to one. Depends on the per-instrument exposures and on the gross total,
     since a weight is one relative to the other.

     A flat book has gross zero and no weights are defined; zeros are the right
     answer there because they make every downstream risk number zero, which is
     true of a book holding nothing. *)
  let weights_node =
    cutoff ~equal:(Array.equal Float.equal)
      (Inc.map2 exposure_list gross_node ~f:(fun xs gross ->
           note Node_name.weights;
           let gross = Notional.to_float gross in
           if Float.equal gross 0.0 then Array.create ~len:(List.length xs) 0.0
           else Array.of_list_map xs ~f:(fun x -> Notional.to_float x /. gross)))
  in
  (* --- returns and covariance ------------------------------------------- *)
  (* Depends on the return windows only. Instruments accumulate history at
     different rates (a name that has not traded yet has a shorter window), so
     the windows are trimmed to the shortest common length and aligned at the
     RIGHT edge -- the most recent observations. Aligning at the left would pair
     one instrument's Monday with another's Wednesday and produce a covariance
     matrix of pure fiction. *)
  let aligned_returns_node =
    Inc.map
      (Inc.all (List.map (Map.data returns_vars) ~f:Inc.Var.watch))
      ~f:(fun series ->
        note Node_name.aligned_returns;
        let common =
          List.fold series ~init:Int.max_value ~f:(fun acc s ->
              Int.min acc (Array.length s))
        in
        Array.of_list_map series ~f:(fun s ->
            Array.sub s ~pos:(Array.length s - common) ~len:common))
  in
  (* Depends on the aligned return windows and NOTHING ELSE.

     This is the edge the whole project is arguing for. Covariance is O(n^2 * w)
     and it is a property of the return series, not of what is currently held --
     changing a position changes which linear combination of this matrix you
     care about, not the matrix. Under "recompute everything on a timer" it
     would be rebuilt on every tick. Here a price change cannot reach it, and
     test_graph.ml asserts exactly that. *)
  let covariance_node =
    Inc.map aligned_returns_node ~f:(fun series ->
        note Node_name.covariance;
        (* Two observations is the minimum that admits a non-degenerate second
         moment. One gives a zero matrix, which is not "low risk". *)
        if Array.is_empty series || Array.length series.(0) < 2 then None
        else Some (Risk_metrics.covariance_matrix series))
  in
  (* The same matrix under exponentially decaying weights, hanging off exactly
     the same edge -- a SIBLING of [covariance], never a replacement for it.

     Two reasons it is wired this way rather than as a swap. First, the
     comparison is the deliverable: an equal-weighted covariance and an EWMA one
     computed from the identical window disagree only about how quickly the past
     stops counting, so the gap between the two parametric VaRs below is a
     regime-change signal that neither number carries alone. Making that a
     silent configuration choice would have deleted the signal to change a
     number. Second, every component-VaR limit already written against the
     equal-weighted decomposition keeps meaning what it meant.

     It inherits [covariance]'s isolation for free, which is the whole reason to
     hang it here: a price tick cannot reach either matrix, because neither is
     downstream of price. That doubles the cost of a return-window update and
     leaves the cost of a tick exactly where it was. test_graph.ml asserts it. *)
  let covariance_ewma_node =
    Inc.map aligned_returns_node ~f:(fun series ->
        note Node_name.covariance_ewma;
        if Array.is_empty series || Array.length series.(0) < 2 then None
        else Some (Vol_estimators.Ewma.covariance_matrix ~series ~lambda:ewma_lambda))
  in
  (* The book's own return series: r_p(t) = sum_i w_i * r_i(t), using current
     weights. Depends on the return windows (the r_i) and on the weights (hence
     on prices and positions), because the same market moves hurt differently
     depending on what is held.

     Holding weights fixed across the window is the standard approximation --
     it answers "what would today's book have done through this history", which
     is the question a limit is asking, rather than "what did the book that
     existed then actually do". *)
  let portfolio_returns_node =
    Inc.map2 aligned_returns_node weights_node ~f:(fun series weights ->
        note Node_name.portfolio_returns;
        let periods = if Array.is_empty series then 0 else Array.length series.(0) in
        if periods = 0 then None
        else
          Some
            (Array.init periods ~f:(fun t ->
                 Array.foldi series ~init:0.0 ~f:(fun i acc s ->
                     acc +. (weights.(i) *. s.(t))))))
  in
  (* --- risk numbers ------------------------------------------------------ *)
  let historical_var_node =
    Inc.map portfolio_returns_node ~f:(fun returns ->
        note Node_name.historical_var;
        Option.map returns ~f:(fun returns ->
            Risk_metrics.historical_var ~returns ~confidence))
  in
  let expected_shortfall_node =
    Inc.map portfolio_returns_node ~f:(fun returns ->
        note Node_name.expected_shortfall;
        Option.map returns ~f:(fun returns ->
            Risk_metrics.expected_shortfall ~returns ~confidence))
  in
  (* Depends on weights and covariance -- the closed-form path, which never
     touches the return series directly. Reported alongside the historical
     numbers rather than instead of them: the gap between a parametric VaR and
     an empirical one is a read on how non-normal the book's tail currently is,
     and it is only visible if both are computed. *)
  let parametric_var_node =
    Inc.map2 weights_node covariance_node ~f:(fun weights covariance ->
        note Node_name.parametric_var;
        Option.map covariance ~f:(fun covariance ->
            Risk_metrics.portfolio_parametric_var ~weights ~covariance ~confidence))
  in
  (* The same closed form against the decay-weighted matrix.

     Identical arithmetic, one different input, and that is the point: the two
     numbers differ by the weighting and by nothing else, so the difference
     between them is interpretable. vol_estimators.ml argues that comparability
     at length -- it is why the EWMA estimator demeans against a weighted mean
     rather than assuming zero the way RiskMetrics does. *)
  let parametric_var_ewma_node =
    Inc.map2 weights_node covariance_ewma_node ~f:(fun weights covariance ->
        note Node_name.parametric_var_ewma;
        Option.map covariance ~f:(fun covariance ->
            Risk_metrics.portfolio_parametric_var ~weights ~covariance ~confidence))
  in
  (* --- risk attribution -------------------------------------------------- *)
  (* WHERE the risk is, as opposed to how much of it there is.

     Depends on weights and covariance -- the same two inputs as
     [parametric_var], and for the same reason: the Euler decomposition needs a
     differentiable closed form for portfolio risk, and only the covariance path
     has one. attribution.ml explains why the historical quantile does not admit
     an exact split.

     Note honestly what this edge costs. Unlike [covariance], this node IS
     downstream of price: weights move on every tick, so every tick recomputes
     it. But it recomputes a matrix-VECTOR product, O(n^2), against the matrix
     rebuild that a polling design would do, O(n^2 * w) for a w-day window. The
     incrementality claim here is not "a tick does not reach it" -- it does --
     but that the expensive thing upstream of it stays cached. *)
  (* Which of the two matrices this reads is fixed at construction, and
     defaults to the equal-weighted one so that existing component-VaR limits
     keep measuring what they were written against. See [Covariance_estimator].
     The edge is chosen once and then static; the node itself reads one declared
     input, as every node here must. *)
  let attribution_covariance_node =
    match covariance_for_attribution with
    | Covariance_estimator.Equal_weighted -> covariance_node
    | Covariance_estimator.Ewma -> covariance_ewma_node
  in
  let attribution_node =
    Inc.map2 weights_node attribution_covariance_node ~f:(fun weights covariance ->
        note Node_name.attribution;
        Option.bind covariance ~f:(fun covariance ->
            Attribution.compute ~weights ~covariance))
  in
  (* Component VaR in dollars, per instrument.

     Scaled by gross for exactly the reason [to_notional] is: the weights are
     normalised by gross, so a return-space quantity times gross is money. The
     entries therefore sum to the book's parametric VaR notional, which is what
     makes an instrument-scoped component limit a limit on a real share of a
     real total.

     Keyed by symbol rather than returned as an array because the ordering is
     load-bearing -- [weights] is [Map.data] of the same map that [symbols] is
     [Map.keys] of -- and an array crossing a module boundary is one refactor
     away from being silently reordered. *)
  let component_var_node =
    Inc.map2 attribution_node gross_node ~f:(fun attribution gross ->
        note Node_name.component_var_map;
        Option.map attribution ~f:(fun attribution ->
            let gross = Notional.to_float gross in
            let shares = Attribution.component_var attribution ~confidence in
            Symbol.Map.of_alist_exn
              (List.mapi symbols ~f:(fun i symbol ->
                   (symbol, Notional.of_float (shares.(i) *. gross))))))
  in
  (* The same numbers grouped by sector, and it is a plain sum.

     That is the whole argument for component risk over standalone risk: these
     add. A sector's share of portfolio VaR is the sum of its members' shares,
     with no correction for the correlation between them, because the
     correlation is already inside each member's number. Summing standalone
     VaRs the same way would double-count every diversification benefit in the
     sector and report a number larger than the book's total. *)
  let component_var_sector_node =
    Inc.map component_var_node ~f:(fun by_instrument ->
        note Node_name.component_var_sector_map;
        Option.map by_instrument ~f:(fun by_instrument ->
            Map.fold by_instrument ~init:Sector.Map.empty
              ~f:(fun ~key:symbol ~data:share acc ->
                let sector = (Map.find_exn instruments_map symbol).Instrument.sector in
                Map.update acc sector ~f:(function
                  | None -> share
                  | Some running -> Notional.add running share))))
  in
  let diversification_ratio_node =
    Inc.map attribution_node ~f:(fun attribution ->
        note Node_name.diversification_ratio;
        Option.map attribution ~f:Attribution.diversification_ratio)
  in
  (* Rolling beta of the book against the macro factor series that fred_client.ml
     supplies. Depends on the book's own return series and on the factor's, and
     on nothing else -- beta is a property of two return series, so positions
     reach it only through the weights that shape [portfolio_returns].

     The two series are trimmed to a common length at the RIGHT edge, exactly as
     [aligned_returns] does and for the same reason: FRED publishes daily and the
     book's window fills at its own rate, so the two are almost never the same
     length. Aligning at the left would regress this week's book against last
     month's rates.

     Returns [None] rather than raising, in three cases:

     - either series is too short to have a second moment;
     - the factor never moved over the window. Risk_metrics.beta raises here,
       and it is right to: a constant factor explains nothing, so beta is
       undefined rather than zero. But a flat rate series is ROUTINE -- rates do
       not move most days -- so what is an exceptional condition for a pure
       function is an ordinary Tuesday for this node. A node body that raised
       would take the whole graph down on a quiet day.
     - anything else Risk_metrics.beta objects to.

     Every node body in this module is total; this is the one where that
     property took real thought. *)
  let portfolio_beta_node =
    Inc.map2 portfolio_returns_node (Inc.Var.watch factor_returns_var)
      ~f:(fun portfolio factor ->
        note Node_name.portfolio_beta;
        match portfolio with
        | None -> None
        | Some portfolio ->
            let common = Int.min (Array.length portfolio) (Array.length factor) in
            if common < 2 then None
            else
              let tail xs = Array.sub xs ~pos:(Array.length xs - common) ~len:common in
              (* [Risk_metrics.beta] raises on a factor that does not move.
                 Testing the same predicate here rather than catching the
                 exception keeps the reason for [None] explicit at the call site
                 -- a bare try-with would also swallow a genuine bug in the
                 metric, which is the last thing a risk number should do. *)
              let factor = tail factor in
              if Risk_metrics.is_effectively_constant factor then None
              else Some (Risk_metrics.beta ~asset:(tail portfolio) ~factor))
  in
  (* Return-space risk is a fraction; a limit is written in dollars. Because the
     weights are normalised by gross, r_p * gross is the book's P&L, so gross is
     exactly the right multiplier -- and it is a graph edge rather than a
     constant, so the dollar figure moves when the book does. *)
  let to_notional name fraction_node =
    Inc.map2 fraction_node gross_node ~f:(fun fraction gross ->
        note name;
        Option.map fraction ~f:(fun f -> Notional.of_float (f *. Notional.to_float gross)))
  in
  let var_notional_node = to_notional Node_name.var_notional historical_var_node in
  let es_notional_node = to_notional Node_name.es_notional expected_shortfall_node in
  (* --- equity and drawdown ---------------------------------------------- *)
  (* Equity is cash plus the mark-to-market of the book, so it depends on cash
     and on net (not gross) exposure. A tick therefore moves equity, which is
     the point -- a drawdown breaker that only noticed at fill time would be
     watching the wrong thing. *)
  let equity_node =
    cutoff ~equal:Notional.equal
      (Inc.map2 (Inc.Var.watch cash_var) net_node ~f:(fun cash net ->
           note Node_name.equity;
           Notional.add cash net))
  in
  (* Depends on the recorded equity history and on live equity, with the live
     value appended as the final point. The history holds closed marks only, so
     the running peak is real history while the trough is current -- which is
     what a circuit breaker needs.

     Deliberately [current_drawdown], not [max_drawdown]: a breaker keyed to the
     historical maximum would latch on forever after one bad morning and could
     never be cleared by recovery. risk_metrics.ml makes the same point. *)
  let drawdown_node =
    cutoff ~equal:Float.equal
      (Inc.map2 (Inc.Var.watch equity_history_var) equity_node ~f:(fun history equity ->
           note Node_name.drawdown;
           Risk_metrics.current_drawdown
             ~equity:(Array.append history [| Notional.to_float equity |])))
  in
  (* --- limits ------------------------------------------------------------ *)
  (* One node per limit, hanging off the single quantity that limit measures.
     Not one node evaluating all limits: that would make every limit downstream
     of every input, so a tick in one instrument would re-evaluate the entire
     limit book. With this shape, an instrument-scoped limit is downstream of
     exactly one exposure node.

     Every [find_exn] here is safe because Limits.validate already rejected any
     limit naming an unknown symbol or sector, and rejected the kind/scope
     pairings the final case would otherwise have to handle. *)
  let breach_node (limit : Limit.t) : Breach.t option Inc.t =
    let name = Node_name.limit (Limit.name limit) in
    let of_magnitude node =
      Inc.map node ~f:(fun v ->
          note name;
          Some (Limits.evaluate ~limit ~observed:(Float.abs (Notional.to_float v))))
    in
    match (Limit.kind limit, Limit.scope limit) with
    | Limit.Gross_notional _, Limit.Instrument symbol ->
        of_magnitude (Map.find_exn exposure_nodes symbol)
    | Limit.Gross_notional _, Limit.Sector sector ->
        of_magnitude (Map.find_exn sector_nodes sector)
    | Limit.Gross_notional _, Limit.Portfolio -> of_magnitude gross_node
    | Limit.Value_at_risk _, Limit.Portfolio ->
        Inc.map var_notional_node ~f:(fun v ->
            note name;
            Option.map v ~f:(fun v ->
                Limits.evaluate ~limit ~observed:(Notional.to_float v)))
    | Limit.Max_drawdown _, Limit.Portfolio ->
        Inc.map drawdown_node ~f:(fun d ->
            note name;
            Some (Limits.evaluate ~limit ~observed:d))
    (* Component VaR limits. Note what is NOT here: [Float.abs].

       [of_magnitude] above absolute-values an exposure, because a $50,000 short
       consumes a notional cap exactly as a $50,000 long does. A component risk
       limit is the opposite case. A negative component means the position moves
       against the book and REDUCES portfolio risk; wrapping it in an absolute
       value would report a hedge as consuming its risk limit, and would breach
       a limit for the act of hedging. So the signed number goes straight in,
       and a risk-reducing position sits comfortably under any positive
       threshold, which is the correct answer. *)
    | Limit.Component_var _, Limit.Instrument symbol ->
        Inc.map component_var_node ~f:(fun shares ->
            note name;
            Option.map shares ~f:(fun shares ->
                Limits.evaluate ~limit
                  ~observed:(Notional.to_float (Map.find_exn shares symbol))))
    | Limit.Component_var _, Limit.Sector sector ->
        Inc.map component_var_sector_node ~f:(fun shares ->
            note name;
            Option.map shares ~f:(fun shares ->
                Limits.evaluate ~limit
                  ~observed:(Notional.to_float (Map.find_exn shares sector))))
    (* At portfolio scope the shares sum to the whole, which by the Euler
       identity IS the parametric VaR notional. Read from that node rather than
       by summing the map: same number, one edge instead of n, and it cannot
       drift from the value the dashboard prints next to it. *)
    | Limit.Component_var _, Limit.Portfolio ->
        Inc.map2 parametric_var_node gross_node ~f:(fun fraction gross ->
            note name;
            Option.map fraction ~f:(fun fraction ->
                Limits.evaluate ~limit ~observed:(fraction *. Notional.to_float gross)))
    | (Limit.Value_at_risk _ | Limit.Max_drawdown _), (Limit.Instrument _ | Limit.Sector _)
      ->
        (* Unreachable: Limits.validate rejects these pairings above. Kept as an
         explicit failure rather than a wildcard so that adding a new scope to
         Types.Limit makes the compiler point here. *)
        failwithf
          "graph: limit %S has a kind/scope pairing that Limits.validate should have \
           rejected"
          (Limit.name limit) ()
  in
  let breach_nodes = List.map limits ~f:breach_node in
  let breaches_node =
    Inc.map (Inc.all breach_nodes) ~f:(fun results ->
        note Node_name.breaches;
        results)
  in
  (* --- feed health ------------------------------------------------------- *)
  (* A separate branch of the graph that shares no edge with anything above.

     Read the dependencies carefully, because the discipline is in what is
     ABSENT: [feed[S]] depends on [last_tick[S]] and on [now]. It does NOT
     depend on [price[S]], even though the same event writes both cells. And
     nothing upstream depends on [now] -- if any risk node did, the timer that
     advances the clock would recompute the book on a schedule, which is
     precisely the polling design this project exists to replace.

     One node per symbol rather than one node over all of them, for the same
     reason as everywhere else: a tick in one name should not re-examine the
     liveness of every other. Advancing [now] does recompute all of them, but
     that is honest -- every symbol's age genuinely changed. *)
  let feed_nodes =
    Map.mapi last_tick_vars ~f:(fun ~key:symbol ~data:last_tick_var ->
        (* Full structural equality, which is only honest because the state
           holds no continuously-changing field -- see the note on [age] in
           Feed_health. So a clock tick that leaves every status alone genuinely
           produces the same value, and stops here. *)
        cutoff ~equal:Feed_health.Symbol_state.equal
          (Inc.map2 (Inc.Var.watch last_tick_var) (Inc.Var.watch now_var)
             ~f:(fun last_tick now ->
               note (Node_name.feed symbol);
               {
                 Feed_health.Symbol_state.symbol;
                 last_tick;
                 never_seen = Option.is_none last_tick;
                 stale =
                   (match last_tick with
                   (* Never seen is its own state, not staleness. A symbol that
                      has never printed is a subscription that did not take; one
                      that printed and stopped is a feed that dropped. *)
                   | None -> false
                   | Some tick -> Time.Span.( > ) (Time.diff now tick) staleness_threshold);
               })))
  in
  (* Depends on the per-symbol states and NOT on [now] directly, so it changes
     only when some symbol's status actually flips. That makes feed health
     edge-triggered: an observer on it fires when the answer changes, not every
     time the clock moves. Phase 3's dashboard stream depends on this -- a health
     panel that re-rendered every five seconds to say the same thing is how a
     status display becomes something people stop looking at. *)
  let feed_health_node =
    Inc.map
      (Inc.all (Map.data feed_nodes))
      ~f:(fun states ->
        note Node_name.feed_health;
        {
          Feed_health.symbols = states;
          stale =
            List.filter_map states ~f:(fun s ->
                if s.Feed_health.Symbol_state.stale then Some s.symbol else None);
          never_seen =
            List.filter_map states ~f:(fun s ->
                if s.Feed_health.Symbol_state.never_seen then Some s.symbol else None);
        })
  in
  let t =
    {
      instruments = instruments_map;
      limits;
      confidence;
      return_window;
      ewma_lambda;
      covariance_for_attribution;
      equity_history_limit;
      staleness_threshold;
      price_vars;
      qty_vars;
      returns_vars;
      cash_var;
      equity_history_var;
      factor_returns_var;
      last_tick_vars;
      now_var;
      obs_exposure_by_instrument = observe exposure_map_node;
      obs_exposure_by_sector = observe sector_map_node;
      obs_gross = observe gross_node;
      obs_net = observe net_node;
      obs_weights = observe weights_node;
      obs_covariance = observe covariance_node;
      obs_covariance_ewma = observe covariance_ewma_node;
      obs_historical_var = observe historical_var_node;
      obs_expected_shortfall = observe expected_shortfall_node;
      obs_parametric_var = observe parametric_var_node;
      obs_parametric_var_ewma = observe parametric_var_ewma_node;
      obs_component_var_by_instrument = observe component_var_node;
      obs_component_var_by_sector = observe component_var_sector_node;
      obs_diversification_ratio = observe diversification_ratio_node;
      obs_var_notional = observe var_notional_node;
      obs_es_notional = observe es_notional_node;
      obs_equity = observe equity_node;
      obs_drawdown = observe drawdown_node;
      obs_breaches = observe breaches_node;
      obs_portfolio_beta = observe portfolio_beta_node;
      obs_feed_health = observe feed_health_node;
      releases = !releases;
      change_listeners;
    }
  in
  (* Stabilize once so a freshly created graph is readable without the caller
     having to know that observers hold no value until the first stabilize. *)
  Inc.stabilize ();
  t

(* Build a second graph with the same shape and the same current inputs.

   This exists so that stress.ml can answer "what would the book look like if
   prices moved like THIS" without a second implementation of any risk
   calculation. The scenario is not a model of the engine; it IS the engine,
   handed different inputs. There is nothing to keep in sync and nothing to
   drift, which matters more here than it looks: a stress number that disagreed
   with the live number for a reason nobody could locate would be worse than no
   stress number at all.

   Everything mutable is copied, including the feed-liveness cells. A scenario
   run on a book whose marks are twenty minutes old should say so, and it would
   not if the fork started with a clean clock.

   [limits] can be replaced, which is what lets a caller ask "what if the caps
   were different" without rebuilding a book by hand. Defaults to the same set.

   Two things the fork does NOT inherit, both deliberately. The [on_compute]
   diagnostic hook is not carried over, because a fork's recomputations are not
   the live graph's and merging the two counts would make the architecture tests
   meaningless. And the change listeners are not carried over, because a fork is
   a private calculation -- a scenario is not an event, and nothing downstream
   should wake up because someone asked a hypothetical.

   The fork does share the process-wide Incremental state, so its nodes are
   counted by [total_nodes_recomputed]. Anything measuring recomputation should
   not fork in the middle of the measurement. [destroy] it when done. *)
let fork ?on_compute ?(limits : Limit.t list option) (t : t) : t =
  let limits = Option.value limits ~default:t.limits in
  let forked =
    create ?on_compute ~starting_cash:(Inc.Var.value t.cash_var)
      ~equity_history_limit:t.equity_history_limit
      ~staleness_threshold:t.staleness_threshold ~ewma_lambda:t.ewma_lambda
      ~covariance_for_attribution:t.covariance_for_attribution
      ~instruments:(Map.data t.instruments) ~limits ~confidence:t.confidence
      ~return_window:t.return_window ()
  in
  Map.iteri t.price_vars ~f:(fun ~key:symbol ~data:var ->
      Inc.Var.set (Map.find_exn forked.price_vars symbol) (Inc.Var.value var));
  Map.iteri t.qty_vars ~f:(fun ~key:symbol ~data:var ->
      Inc.Var.set (Map.find_exn forked.qty_vars symbol) (Inc.Var.value var));
  Map.iteri t.returns_vars ~f:(fun ~key:symbol ~data:var ->
      Inc.Var.set (Map.find_exn forked.returns_vars symbol) (Inc.Var.value var));
  Map.iteri t.last_tick_vars ~f:(fun ~key:symbol ~data:var ->
      Inc.Var.set (Map.find_exn forked.last_tick_vars symbol) (Inc.Var.value var));
  Inc.Var.set forked.factor_returns_var (Inc.Var.value t.factor_returns_var);
  Inc.Var.set forked.equity_history_var (Inc.Var.value t.equity_history_var);
  Inc.Var.set forked.now_var (Inc.Var.value t.now_var);
  Inc.stabilize ();
  forked

(* Release every observer. An abandoned graph whose observers are still live
   would keep being recomputed on every stabilize of the shared state -- and
   since the state is shared, that cost lands on whoever is still using it. *)
let destroy (t : t) : unit =
  t.change_listeners := [];
  List.iter t.releases ~f:(fun release -> release ())

(* Be told when anything the engine publishes changes.

   [f] runs INSIDE stabilization, alongside the node bodies, so it is subject to
   the same rule as the [on_compute] hook: record the fact and return. Do not
   compute, do not block, and above all do not write to a Var -- writing during a
   stabilize is how a graph ends up chasing its own tail. The intended shape is
   to set a flag or fill an Ivar and let a consumer elsewhere do the work.

   Fires once per changed observer, so a single tick that moves twenty published
   values calls [f] twenty times. Consumers are expected to coalesce; server.ml
   does. *)
let on_change (t : t) ~(f : unit -> unit) : unit =
  t.change_listeners := f :: !(t.change_listeners)

(* Be told when the limit results change, specifically.

   This is the hook the README's Phase 4 asks for: "when a limit-check node
   breaches, trigger a side effect via an Incremental.Observer". Attaching to the
   OBSERVER rather than putting the effect in the node body is the whole point,
   and limits.ml has said why since Phase 1 -- Incremental is free to recompute a
   node whenever it likes, so a node that sent a Slack message could send
   several. An observer fires when a value CHANGES, which is the event an alert
   actually cares about.

   Same constraint as [on_change], and it bites harder here because the
   temptation is greater: [f] runs inside stabilization. Record the fact and
   return. alerts.ml writes to a pipe and lets an Async consumer do the sending,
   which is the only shape that keeps a slow webhook from stalling the graph. *)
let on_breaches (t : t) ~(f : Breach.t option list -> unit) : unit =
  Inc.Observer.on_update_exn t.obs_breaches ~f:(function
    | Inc.Observer.Update.Initialized results | Inc.Observer.Update.Changed (_, results)
      ->
        f results
    | Inc.Observer.Update.Invalidated -> ())

let sector_of (t : t) (symbol : Symbol.t) : Sector.t option =
  Option.map (Map.find t.instruments symbol) ~f:Instrument.sector

(* -------------------------------------------------------------------------
   Inputs
   ------------------------------------------------------------------------- *)

(* Setting an input marks it dirty; nothing is recomputed until [stabilize].
   That split is deliberate and it is where the throughput comes from: a burst
   of a hundred ticks can be applied and then settled once, so the graph does
   one propagation instead of a hundred. *)

let stabilize (_ : t) : unit = Inc.stabilize ()
let symbols (t : t) : Symbol.t list = Map.keys t.instruments
let knows_symbol (t : t) (symbol : Symbol.t) : bool = Map.mem t.instruments symbol

let set_price (t : t) (symbol : Symbol.t) (price : Price.t) : unit =
  Inc.Var.set (find_var t.price_vars symbol ~what:"price") price

let set_qty (t : t) (symbol : Symbol.t) (qty : Qty.t) : unit =
  Inc.Var.set (find_var t.qty_vars symbol ~what:"quantity") qty

let price (t : t) (symbol : Symbol.t) : Price.t =
  Inc.Var.value (find_var t.price_vars symbol ~what:"price")

let qty (t : t) (symbol : Symbol.t) : Qty.t =
  Inc.Var.value (find_var t.qty_vars symbol ~what:"quantity")

let cash (t : t) : Notional.t = Inc.Var.value t.cash_var
let set_cash (t : t) (cash : Notional.t) : unit = Inc.Var.set t.cash_var cash

(* A tick is a price AND an observation that the feed is alive. [set_price] does
   only the former, which is why the two are separate entry points rather than
   one: a manual reprice, a test, or a backfill should not make a dead feed look
   healthy. Only something that actually arrived over the wire counts as
   evidence of liveness, and only [apply_tick] is called from there.

   The two writes go to different branches of the graph and never meet -- see
   the diagram at the top of this file. *)
let apply_tick (t : t) (tick : Tick.t) : unit =
  let symbol = Tick.symbol tick in
  set_price t symbol (Tick.price tick);
  Inc.Var.set (find_var t.last_tick_vars symbol ~what:"last tick") (Some (Tick.time tick))

(* A fill moves two cells: the position and the cash that paid for it. Buying
   (positive quantity) spends cash, so the cash delta is the negation of the
   traded notional. Both go through Var.set before any stabilize, so the graph
   never observes the intermediate state where the shares arrived but the money
   had not left -- which would show as a one-stabilize jump in equity. *)
let apply_fill (t : t) (fill : Fill.t) : unit =
  let symbol = Fill.symbol fill in
  let position_var = find_var t.qty_vars symbol ~what:"quantity" in
  Inc.Var.set position_var (Qty.add (Inc.Var.value position_var) (Fill.qty fill));
  let traded = notional ~price:(Fill.price fill) ~qty:(Fill.qty fill) in
  Inc.Var.set t.cash_var (Notional.sub (Inc.Var.value t.cash_var) traded)

(* Bounded append. The window is a plain immutable array rather than a ring
   buffer because it is small (tens to hundreds of observations) and because
   Incremental's cutoff needs to compare whole values -- a mutated buffer would
   be physically identical to its own previous value and the graph would never
   see the change. *)
let bounded_append (xs : float array) (x : float) ~(limit : int) : float array =
  let appended = Array.append xs [| x |] in
  let len = Array.length appended in
  if len <= limit then appended else Array.sub appended ~pos:(len - limit) ~len:limit

let push_return (t : t) (symbol : Symbol.t) (return : float) : unit =
  let var = find_var t.returns_vars symbol ~what:"return window" in
  Inc.Var.set var (bounded_append (Inc.Var.value var) return ~limit:t.return_window)

(* Bulk seed, for backfilling history at startup or for tests. Trims to the
   configured window from the right, keeping the most recent observations. *)
let set_returns (t : t) (symbol : Symbol.t) (returns : float array) : unit =
  let var = find_var t.returns_vars symbol ~what:"return window" in
  let len = Array.length returns in
  let trimmed =
    if len <= t.return_window then Array.copy returns
    else Array.sub returns ~pos:(len - t.return_window) ~len:t.return_window
  in
  Inc.Var.set var trimmed

let returns (t : t) (symbol : Symbol.t) : float array =
  Inc.Var.value (find_var t.returns_vars symbol ~what:"return window")

let equity_history (t : t) : float array = Inc.Var.value t.equity_history_var

(* The macro factor's return window. Set wholesale rather than appended to,
   because fred_client.ml re-fetches the whole series on each poll -- FRED
   revises past observations, so treating the series as append-only would keep a
   value the publisher has since corrected. *)
let set_factor_returns (t : t) (returns : float array) : unit =
  Inc.Var.set t.factor_returns_var (Array.copy returns)

let factor_returns (t : t) : float array = Inc.Var.value t.factor_returns_var

(* Advance the staleness clock. Called by a timer, and the ONLY thing that
   should ever write this cell.

   Nothing in the risk chain is downstream of it -- see the diagram at the top
   of this file, and test_graph.ml, which asserts the isolation as an exact
   recomputation set. If that assertion ever fails, a risk node has acquired a
   dependency on wall-clock time and this engine has quietly become a poller. *)
let set_now (t : t) (now : Time.t) : unit = Inc.Var.set t.now_var now
let now (t : t) : Time.t = Inc.Var.value t.now_var

let last_tick (t : t) (symbol : Symbol.t) : Time.t option =
  Inc.Var.value (find_var t.last_tick_vars symbol ~what:"last tick")

(* Close the current equity mark and add it to the history the drawdown reads.

   Stabilizes first, on purpose: the value being recorded must be the equity
   implied by every input set so far, and reading the observer without settling
   would silently record the previous mark. This is the one setter that reads
   from the graph, so it is the one place that ordering matters.

   Called at bar boundaries by the driver, not per tick -- a drawdown measured
   against a peak made of every intra-second print is measuring noise. *)
let mark_equity (t : t) : unit =
  Inc.stabilize ();
  let now = Notional.to_float (Inc.Observer.value_exn t.obs_equity) in
  Inc.Var.set t.equity_history_var
    (bounded_append
       (Inc.Var.value t.equity_history_var)
       now ~limit:t.equity_history_limit)

(* -------------------------------------------------------------------------
   Outputs

   Each of these reads an observer, which holds the value from the most recent
   stabilize. They do not stabilize themselves: the caller controls when the
   graph settles (that is the whole point of batching), and a getter that
   settled behind the caller's back would turn a deliberate batch into a
   stabilize per read. Use [snapshot] to settle and read together.
   ------------------------------------------------------------------------- *)

let exposure_by_instrument (t : t) : Notional.t Symbol.Map.t =
  Inc.Observer.value_exn t.obs_exposure_by_instrument

let exposure_by_sector (t : t) : Notional.t Sector.Map.t =
  Inc.Observer.value_exn t.obs_exposure_by_sector

let gross_exposure (t : t) : Notional.t = Inc.Observer.value_exn t.obs_gross
let net_exposure (t : t) : Notional.t = Inc.Observer.value_exn t.obs_net
let weights_array (t : t) : float array = Inc.Observer.value_exn t.obs_weights
let covariance (t : t) : Owl.Mat.mat option = Inc.Observer.value_exn t.obs_covariance

let covariance_ewma (t : t) : Owl.Mat.mat option =
  Inc.Observer.value_exn t.obs_covariance_ewma

let ewma_lambda (t : t) : float = t.ewma_lambda

let covariance_for_attribution (t : t) : Covariance_estimator.t =
  t.covariance_for_attribution

let historical_var (t : t) : float option = Inc.Observer.value_exn t.obs_historical_var

let expected_shortfall (t : t) : float option =
  Inc.Observer.value_exn t.obs_expected_shortfall

let parametric_var (t : t) : float option = Inc.Observer.value_exn t.obs_parametric_var

let parametric_var_ewma (t : t) : float option =
  Inc.Observer.value_exn t.obs_parametric_var_ewma

let component_var_by_instrument (t : t) : Notional.t Symbol.Map.t option =
  Inc.Observer.value_exn t.obs_component_var_by_instrument

let component_var_by_sector (t : t) : Notional.t Sector.Map.t option =
  Inc.Observer.value_exn t.obs_component_var_by_sector

let diversification_ratio (t : t) : float option =
  Inc.Observer.value_exn t.obs_diversification_ratio

let value_at_risk_notional (t : t) : Notional.t option =
  Inc.Observer.value_exn t.obs_var_notional

let expected_shortfall_notional (t : t) : Notional.t option =
  Inc.Observer.value_exn t.obs_es_notional

let equity (t : t) : Notional.t = Inc.Observer.value_exn t.obs_equity
let current_drawdown (t : t) : float = Inc.Observer.value_exn t.obs_drawdown
let portfolio_beta (t : t) : float option = Inc.Observer.value_exn t.obs_portfolio_beta
let feed_health (t : t) : Feed_health.t = Inc.Observer.value_exn t.obs_feed_health

(* Limits paired with their result, in the order they were configured. [None]
   means the limit's input was unavailable -- not that it passed. *)
let limit_results (t : t) : (Limit.t * Breach.t option) list =
  List.zip_exn t.limits (Inc.Observer.value_exn t.obs_breaches)

let snapshot (t : t) : Snapshot.t =
  Inc.stabilize ();
  let results = limit_results t in
  let historical_var = historical_var t in
  {
    Snapshot.exposure_by_instrument = exposure_by_instrument t;
    exposure_by_sector = exposure_by_sector t;
    gross_exposure = gross_exposure t;
    net_exposure = net_exposure t;
    weights =
      Symbol.Map.of_alist_exn (List.zip_exn (symbols t) (Array.to_list (weights_array t)));
    equity = equity t;
    current_drawdown = current_drawdown t;
    historical_var;
    expected_shortfall = expected_shortfall t;
    parametric_var = parametric_var t;
    parametric_var_ewma = parametric_var_ewma t;
    ewma_lambda = t.ewma_lambda;
    value_at_risk_notional = value_at_risk_notional t;
    expected_shortfall_notional = expected_shortfall_notional t;
    portfolio_beta = portfolio_beta t;
    component_var_by_instrument = component_var_by_instrument t;
    component_var_by_sector = component_var_by_sector t;
    diversification_ratio = diversification_ratio t;
    warming_up = Option.is_none historical_var;
    feed_health = feed_health t;
    breaches = List.filter_map results ~f:(fun (_, breach) -> breach);
    unevaluated_limits =
      List.filter_map results ~f:(fun (limit, breach) ->
          if Option.is_none breach then Some (Limit.name limit) else None);
  }

(* Total node recomputations since the process started, straight from
   Incremental's own counters.

   A corroborating measure for the [on_compute] hook: the hook counts the nodes
   this module chose to name, while this counts every node in the state
   including the plumbing. If the named count stays flat while this one grows,
   something is recomputing that nobody has a name for. *)
let total_nodes_recomputed () : int = Inc.State.num_nodes_recomputed Inc.State.t
let total_stabilizes () : int = Inc.State.num_stabilizes Inc.State.t
