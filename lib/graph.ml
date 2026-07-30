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
                            |                     \               /
                            +----------------------+-> portfolio_returns
                                                    \      |
                                                     \     +--> historical_var --> var_notional
                                                      \    +--> expected_shortfall --> es_notional
                                                       +------> parametric_var
                                                                        |
     (each limit reads exactly one of the nodes above) --> limit[name] -+--> breaches

   The two edges worth staring at, because they are the ones that justify the
   architecture:

   - [covariance] hangs off [aligned_returns] and NOTHING else. A price tick
     does not touch it. Rebuilding an n x n covariance matrix is the single most
     expensive thing this engine does, and in a poll-and-recompute design it
     would be redone on every tick for no reason.

   - Each limit is its own node hanging off the one quantity it measures. An
     instrument-scoped limit on AAPL is downstream of exposure[AAPL] alone, so a
     tick in an unrelated name leaves it strictly untouched -- not "recomputed
     and found equal", but never visited.

   test/test_graph.ml asserts both of those as recomputation counts. That test
   is the architectural premise stated as an executable claim.
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
  let portfolio_returns = "portfolio_returns"
  let historical_var = "historical_var"
  let expected_shortfall = "expected_shortfall"
  let parametric_var = "parametric_var"
  let var_notional = "var_notional"
  let es_notional = "es_notional"
  let equity = "equity"
  let drawdown = "current_drawdown"
  let breaches = "breaches"
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
    value_at_risk_notional : Notional.t option;
    expected_shortfall_notional : Notional.t option;
    warming_up : bool;
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
  equity_history_limit : int;
  (* Inputs. These are the only mutable cells in the system; everything else
       is a function of them. *)
  price_vars : Price.t Inc.Var.t Symbol.Map.t;
  qty_vars : Qty.t Inc.Var.t Symbol.Map.t;
  returns_vars : float array Inc.Var.t Symbol.Map.t;
  cash_var : Notional.t Inc.Var.t;
  equity_history_var : float array Inc.Var.t;
  (* Outputs. Incremental is demand-driven: a node with no observer is not
       recomputed at all, so every value the engine is supposed to publish has
       to be observed here or it silently goes stale. *)
  obs_exposure_by_instrument : Notional.t Symbol.Map.t Inc.Observer.t;
  obs_exposure_by_sector : Notional.t Sector.Map.t Inc.Observer.t;
  obs_gross : Notional.t Inc.Observer.t;
  obs_net : Notional.t Inc.Observer.t;
  obs_weights : float array Inc.Observer.t;
  obs_covariance : Owl.Mat.mat option Inc.Observer.t;
  obs_historical_var : float option Inc.Observer.t;
  obs_expected_shortfall : float option Inc.Observer.t;
  obs_parametric_var : float option Inc.Observer.t;
  obs_var_notional : Notional.t option Inc.Observer.t;
  obs_es_notional : Notional.t option Inc.Observer.t;
  obs_equity : Notional.t Inc.Observer.t;
  obs_drawdown : float Inc.Observer.t;
  obs_breaches : Breach.t option list Inc.Observer.t;
  (* Closures that release the observers above. Held as thunks so [destroy]
       does not have to name fourteen differently-typed observers. *)
  releases : (unit -> unit) list;
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
    ?(equity_history_limit = 10_000) ~(instruments : Instrument.t list)
    ~(limits : Limit.t list) ~(confidence : float) ~(return_window : int) () : t =
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
  let observe node =
    let o = Inc.observe node in
    releases := (fun () -> Inc.Observer.disallow_future_use o) :: !releases;
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
  let t =
    {
      instruments = instruments_map;
      limits;
      confidence;
      return_window;
      equity_history_limit;
      price_vars;
      qty_vars;
      returns_vars;
      cash_var;
      equity_history_var;
      obs_exposure_by_instrument = observe exposure_map_node;
      obs_exposure_by_sector = observe sector_map_node;
      obs_gross = observe gross_node;
      obs_net = observe net_node;
      obs_weights = observe weights_node;
      obs_covariance = observe covariance_node;
      obs_historical_var = observe historical_var_node;
      obs_expected_shortfall = observe expected_shortfall_node;
      obs_parametric_var = observe parametric_var_node;
      obs_var_notional = observe var_notional_node;
      obs_es_notional = observe es_notional_node;
      obs_equity = observe equity_node;
      obs_drawdown = observe drawdown_node;
      obs_breaches = observe breaches_node;
      releases = !releases;
    }
  in
  (* Stabilize once so a freshly created graph is readable without the caller
     having to know that observers hold no value until the first stabilize. *)
  Inc.stabilize ();
  t

(* Release every observer. An abandoned graph whose observers are still live
   would keep being recomputed on every stabilize of the shared state -- and
   since the state is shared, that cost lands on whoever is still using it. *)
let destroy (t : t) : unit = List.iter t.releases ~f:(fun release -> release ())

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

let apply_tick (t : t) (tick : Tick.t) : unit =
  set_price t (Tick.symbol tick) (Tick.price tick)

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
let historical_var (t : t) : float option = Inc.Observer.value_exn t.obs_historical_var

let expected_shortfall (t : t) : float option =
  Inc.Observer.value_exn t.obs_expected_shortfall

let parametric_var (t : t) : float option = Inc.Observer.value_exn t.obs_parametric_var

let value_at_risk_notional (t : t) : Notional.t option =
  Inc.Observer.value_exn t.obs_var_notional

let expected_shortfall_notional (t : t) : Notional.t option =
  Inc.Observer.value_exn t.obs_es_notional

let equity (t : t) : Notional.t = Inc.Observer.value_exn t.obs_equity
let current_drawdown (t : t) : float = Inc.Observer.value_exn t.obs_drawdown

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
    value_at_risk_notional = value_at_risk_notional t;
    expected_shortfall_notional = expected_shortfall_notional t;
    warming_up = Option.is_none historical_var;
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
