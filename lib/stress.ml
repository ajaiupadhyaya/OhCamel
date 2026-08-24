(* Phase 5: what would it take to break this book?

   Every other number in this engine is a statement about the world as it is.
   VaR summarises a distribution that has already been observed; a limit
   compares today's exposure to a line. Both are backward-looking by
   construction, and both share a blind spot that is not a flaw in the
   estimator but a property of the question: they can only speak about moves
   that have already happened somewhere in the return window.

   A scenario asks the other question. Not "how bad has it been" but "how bad
   would THIS be", where THIS is chosen rather than sampled -- a 2008-sized
   selloff, a sector unwinding, rates moving a hundred basis points. The book
   has no history of most of them and does not need one. That is the point: the
   answer does not depend on whether the last two hundred days happened to
   contain a crisis.

   NO SECOND IMPLEMENTATION

   The obvious way to build this is to write shocked-P&L arithmetic here:
   multiply positions by shocked prices, sum, compare to the limits. That would
   be a second implementation of exposure, equity, drawdown and every limit
   rule, living next to the first and drifting from it on the first day someone
   changes a convention in one and not the other. A stress number that
   disagreed with the live number for a reason nobody could locate would be
   worse than no stress number.

   So there is no arithmetic in this module at all. [Graph.fork] copies the
   engine, the shocks are written into the fork's input cells, and the answer is
   read out of the fork's snapshot by exactly the nodes that produce the live
   one. The scenario is not a model of the engine; it is the engine, fed
   different inputs. Anything true of the live numbers is true of these by
   construction -- including the units discipline, the warming-up handling, and
   the fact that a limit is evaluated by the same code that evaluates it live.

   WHAT A PRICE SHOCK DOES NOT MOVE

   Worth being explicit, because it is the first thing that looks like a bug.
   Shocking prices moves exposure, weights, equity and drawdown, and therefore
   moves every limit written against those -- but it does NOT move VaR as a
   fraction, because VaR is estimated from the return WINDOW and a hypothetical
   move today is not in it. The dollar VaR does change, since it is the fraction
   times gross and gross moved.

   That is the correct answer and not an omission. A one-day shock is not
   evidence about the distribution. If the question is "what if volatility
   regime-changes", that is a different shock and it is [Volatility] below,
   which scales the return window and therefore does move the estimate. Keeping
   the two separate is what stops a scenario from quietly answering a question
   nobody asked. *)

open Core
open Types

module Shock = struct
  (* One shock. A scenario is a list of these, and the price moves they imply
     ADD before being applied -- a broad -10% together with a TECH-specific -5%
     is -15% for a technology name and -10% for everything else. Additive
     composition is the desk convention and it is the one that makes a scenario
     readable as a sentence: a market move plus a sector move plus an
     idiosyncratic move.

     All moves are proportional and signed: -0.10 is "down ten percent". *)
  type t =
    | All of float  (** every instrument moves by this proportion *)
    | Instrument of Symbol.t * float  (** one name moves *)
    | Sector of Sector.t * float  (** every name in one sector moves *)
    | Factor of float
        (** The macro factor moves by this much, and each instrument responds through its
            OWN beta to that factor -- cov(r_i, f) / var(f), estimated from the return
            windows the engine already holds. So a rate shock does not hit every name
            equally; it hits rate-sensitive names harder, which is the entire reason to
            express it as a factor move rather than as a price move.

            UNITS. This number is in the FACTOR SERIES' own units, not in returns, because
            beta is a ratio and carries the conversion. The factor fred_client.ml supplies
            is the daily CHANGE in DGS10, in percentage points -- so a 100bp move is
            [Factor 1.0], not [Factor 0.01]. Getting this wrong is a silent factor of a
            hundred in a scenario that still prints plausible-looking small numbers, which
            is why it is written here rather than left to the reader to infer from the
            FRED client.

            Names without enough history, or any name at all when the factor has not moved
            over its window, get a beta of zero and therefore no move. That is the honest
            answer -- an unestimable beta is not a beta of one -- and
            [Outcome.unestimated_betas] reports which names it happened to rather than
            letting them silently sit still. *)
    | Volatility of float
        (** Scale every return in every window by this factor. The one shock that moves
            the RISK ESTIMATE rather than the book's value: 2.0 is "the world gets twice
            as volatile", which roughly doubles VaR and expected shortfall while leaving
            prices, exposure and equity exactly where they were. Must be positive. *)
  [@@deriving sexp_of]

  let to_string = function
    | All f -> Printf.sprintf "everything %+.1f%%" (f *. 100.0)
    | Instrument (s, f) -> Printf.sprintf "%s %+.1f%%" (Symbol.to_string s) (f *. 100.0)
    | Sector (s, f) -> Printf.sprintf "%s %+.1f%%" (Sector.to_string s) (f *. 100.0)
    | Factor f -> Printf.sprintf "factor %+.2f (through each name's beta)" f
    | Volatility k -> Printf.sprintf "volatility x%.2f" k
end

module Scenario = struct
  type t = { name : string; description : string; shocks : Shock.t list }
  [@@deriving sexp_of, fields ~getters]

  let create ~name ~description shocks = { name; description; shocks }
end

module Outcome = struct
  type t = {
    scenario : Scenario.t;
    (* The book before and after, as full snapshots rather than a handful of
       extracted numbers. A scenario that changes something nobody thought to
       extract is exactly the scenario worth looking at. *)
    before : Graph.Snapshot.t;
    after : Graph.Snapshot.t;
    (* Equity after minus equity before. Signed: a scenario can be good news,
       and a book that is short energy makes money when energy sells off. *)
    pnl : Notional.t;
    (* P&L as a fraction of starting equity. The comparable number across
       scenarios and across books; dollars are not. *)
    pnl_fraction : float;
    (* Limits that are breached after and were not before. The output most
       likely to be read in a hurry, so it is computed rather than left to the
       reader to diff two lists. *)
    new_breaches : Breach.t list;
    (* And the reverse: limits the scenario would take back inside their line.
       Kept because it is the honest other half -- a scenario that relieves a
       breach is telling you the breach is directional. *)
    cleared_breaches : Breach.t list;
    (* Names whose factor beta could not be estimated, so a [Factor] shock left
       them unmoved. Empty for every scenario without a factor shock. Reported
       rather than hidden, because "this name did not move" and "this name could
       not be moved" look identical in the output and mean opposite things. *)
    unestimated_betas : Symbol.t list;
  }
  [@@deriving fields ~getters]

  let breached_names (s : Graph.Snapshot.t) : Set.M(String).t =
    Graph.Snapshot.breaches s
    |> List.filter ~f:Breach.breached
    |> List.map ~f:(fun b -> Limit.name (Breach.limit b))
    |> Set.of_list (module String)
end

(* The per-symbol proportional price move a scenario implies, and the volatility
   multiplier it implies, computed together because a scenario is a list and
   both are folds over it.

   [Factor] is the only shock that needs data: it reads each instrument's return
   window and the factor's, and estimates a beta. Everything else is arithmetic
   on the shock list. *)
let resolve ~(graph : Graph.t) ~(shocks : Shock.t list) =
  let symbols = Graph.symbols graph in
  let moves = ref (Symbol.Map.of_alist_exn (List.map symbols ~f:(fun s -> (s, 0.0)))) in
  let vol_scale = ref 1.0 in
  let unestimated = ref [] in
  let bump symbol delta =
    moves := Map.update !moves symbol ~f:(function None -> delta | Some x -> x +. delta)
  in
  List.iter shocks ~f:(fun shock ->
      match shock with
      | Shock.All f -> List.iter symbols ~f:(fun s -> bump s f)
      | Shock.Instrument (symbol, f) ->
          if not (Graph.knows_symbol graph symbol) then
            invalid_argf "stress: scenario shocks unknown instrument %S"
              (Symbol.to_string symbol) ();
          bump symbol f
      | Shock.Sector (sector, f) ->
          let members =
            List.filter symbols ~f:(fun s ->
                match Graph.sector_of graph s with
                | Some own -> Sector.equal own sector
                | None -> false)
          in
          if List.is_empty members then
            invalid_argf "stress: scenario shocks sector %S, which holds nothing"
              (Sector.to_string sector) ();
          List.iter members ~f:(fun s -> bump s f)
      | Shock.Volatility k ->
          if not (Float.( > ) k 0.0) then
            invalid_argf "stress: volatility scale must be positive, got %f" k ();
          vol_scale := !vol_scale *. k
      | Shock.Factor move ->
          let factor = Graph.factor_returns graph in
          List.iter symbols ~f:(fun symbol ->
              let own = Graph.returns graph symbol in
              let common = Int.min (Array.length own) (Array.length factor) in
              (* Aligned at the RIGHT edge, exactly as graph.ml aligns the
                 return windows and for the same reason: the two series fill at
                 different rates, and pairing this week's prices with last
                 month's rates would produce a beta of pure fiction. *)
              let tail xs = Array.sub xs ~pos:(Array.length xs - common) ~len:common in
              if common < 2 then unestimated := symbol :: !unestimated
              else
                let factor = tail factor in
                if Risk_metrics.is_effectively_constant factor then
                  unestimated := symbol :: !unestimated
                else
                  let beta = Risk_metrics.beta ~asset:(tail own) ~factor in
                  bump symbol (beta *. move)));
  (!moves, !vol_scale, List.dedup_and_sort !unestimated ~compare:Symbol.compare)

(* Run one scenario against the current state of a graph.

   The live graph is never written to. [Graph.fork] copies it, the shocks land
   on the copy, and the copy is destroyed before this returns -- so calling this
   from a request handler cannot leave the engine in a scenario's world. *)
let run ~(graph : Graph.t) ~(scenario : Scenario.t) : Outcome.t =
  let before = Graph.snapshot graph in
  let moves, vol_scale, unestimated_betas =
    resolve ~graph ~shocks:(Scenario.shocks scenario)
  in
  let forked = Graph.fork graph in
  Exn.protect
    ~finally:(fun () -> Graph.destroy forked)
    ~f:(fun () ->
      Map.iteri moves ~f:(fun ~key:symbol ~data:move ->
          if not (Float.equal move 0.0) then
            let current = Price.to_float (Graph.price forked symbol) in
            Graph.set_price forked symbol (Price.of_float (current *. (1.0 +. move))));
      if not (Float.equal vol_scale 1.0) then
        List.iter (Graph.symbols forked) ~f:(fun symbol ->
            Graph.set_returns forked symbol
              (Array.map (Graph.returns forked symbol) ~f:(fun r -> r *. vol_scale)));
      let after = Graph.snapshot forked in
      let breached_before = Outcome.breached_names before in
      let breached_after = Outcome.breached_names after in
      let equity_before = Notional.to_float (Graph.Snapshot.equity before) in
      let equity_after = Notional.to_float (Graph.Snapshot.equity after) in
      let pnl = equity_after -. equity_before in
      {
        Outcome.scenario;
        before;
        after;
        pnl = Notional.of_float pnl;
        (* Guard the divisor. A book whose equity is exactly zero is pathological
           rather than impossible -- a fully hedged book financed entirely on
           margin gets there -- and an infinity here would propagate into a
           table of otherwise readable percentages. *)
        pnl_fraction =
          (if Float.equal equity_before 0.0 then 0.0 else pnl /. equity_before);
        new_breaches =
          List.filter (Graph.Snapshot.breaches after) ~f:(fun b ->
              Breach.breached b
              && not (Set.mem breached_before (Limit.name (Breach.limit b))));
        cleared_breaches =
          List.filter (Graph.Snapshot.breaches before) ~f:(fun b ->
              Breach.breached b
              && not (Set.mem breached_after (Limit.name (Breach.limit b))));
        unestimated_betas;
      })

let run_all ~(graph : Graph.t) ~(scenarios : Scenario.t list) : Outcome.t list =
  List.map scenarios ~f:(fun scenario -> run ~graph ~scenario)

(* -------------------------------------------------------------------------
   A standard suite
   ------------------------------------------------------------------------- *)

(* Scenarios chosen to span the ways a book breaks, not to reproduce particular
   dates.

   The magnitudes are sized off real episodes -- the S&P fell about 20% in the
   worst week of October 2008 and about 12% in the worst week of March 2020 --
   but naming a scenario after a date implies a claim this engine cannot
   support: that shocking today's book by that week's index move reproduces what
   that week would do to it. It does not, because it ignores every correlation
   and dispersion effect that made those weeks what they were. So the names
   describe the shape of the move and the descriptions say where the number came
   from.

   [Volatility] appears twice on purpose, once alone and once alongside a price
   move. Alone, it isolates the effect on the risk ESTIMATE -- prices do not
   move, exposure does not move, and any limit that changes is a risk limit.
   That separation is hard to see when both are shocked at once, and it is the
   difference between "the book got smaller" and "the book got scarier". *)
let standard : Scenario.t list =
  [
    Scenario.create ~name:"broad-selloff"
      ~description:"Everything down 10% at once, correlations at one."
      [ Shock.All (-0.10) ];
    Scenario.create ~name:"crash"
      ~description:
        "Everything down 20%, the scale of the worst week of October 2008. Sized off the \
         index move, not a replay of it."
      [ Shock.All (-0.20) ];
    Scenario.create ~name:"melt-up"
      ~description:
        "Everything up 15%. Included because a short leg loses money in a rally and a \
         one-sided stress suite would never say so."
      [ Shock.All 0.15 ];
    Scenario.create ~name:"vol-regime"
      ~description:
        "Prices unchanged, volatility doubled. Isolates the risk estimate: nothing the \
         book HOLDS changes, only what it is expected to do."
      [ Shock.Volatility 2.0 ];
    Scenario.create ~name:"panic"
      ~description:"Down 12% and three times as volatile -- March 2020 in shape."
      [ Shock.All (-0.12); Shock.Volatility 3.0 ];
    Scenario.create ~name:"rate-shock"
      ~description:
        "The 10-year yield moves 100bp in a day, propagated through each name's own beta \
         to it. One percentage point, because the factor series is measured in \
         percentage points -- see Shock.Factor. Names whose beta cannot be estimated do \
         not move, and are named in the output."
      [ Shock.Factor 1.0 ];
  ]

(* Add the sector shocks a particular book makes sense to run.

   Sector scenarios cannot be a fixed list, because the sectors are whatever the
   book holds. Both directions for each: a sector selloff and a sector rally are
   different scenarios for any book with a short leg, and assuming down is the
   bad direction is exactly the assumption a stress suite exists to avoid. *)
let sector_scenarios ~(graph : Graph.t) : Scenario.t list =
  Graph.symbols graph
  |> List.filter_map ~f:(Graph.sector_of graph)
  |> List.dedup_and_sort ~compare:Sector.compare
  |> List.concat_map ~f:(fun sector ->
      let name = Sector.to_string sector in
      [
        Scenario.create
          ~name:(Printf.sprintf "%s-selloff" (String.lowercase name))
          ~description:
            (Printf.sprintf "%s down 20%%, the rest of the book unchanged." name)
          [ Shock.Sector (sector, -0.20) ];
        Scenario.create
          ~name:(Printf.sprintf "%s-squeeze" (String.lowercase name))
          ~description:
            (Printf.sprintf "%s up 20%%. The painful direction for a short leg." name)
          [ Shock.Sector (sector, 0.20) ];
      ])

let suite_for ~(graph : Graph.t) : Scenario.t list = standard @ sector_scenarios ~graph

(* The worst outcome in a set, by P&L. [None] on an empty list rather than a
   sentinel: "no scenario was run" and "no scenario lost money" are different
   findings and a zero would conflate them. *)
let worst (outcomes : Outcome.t list) : Outcome.t option =
  List.min_elt outcomes ~compare:(fun a b ->
      Float.compare
        (Notional.to_float (Outcome.pnl a))
        (Notional.to_float (Outcome.pnl b)))
