(* The options demonstration as a value: a delta hedge, what it removes and
   what it leaves, then the calendar spread that one vega number hides.

   WHY A RECORD. `make options` printed four snapshots, a limits table and a
   bucket table straight from the graph; the served page needs the same
   numbers, and a page cannot read a printf. So the two walks run here, into a
   record, and bin/main.ml prints it. Every number is the engine's -- the
   hedge ratio is the graph's own delta divided by spot, the vega utilisation
   is Limits.utilisation on the graph's own breach -- and nothing in this file
   prices anything.

   THE VOL SURFACE HERE IS INVENTED, and it is invented in a shape that at
   least resembles a real one rather than being flat. A smile plus a skew: vol
   rises as a contract moves away from the money (the quadratic term) and
   equity puts trade richer than equity calls (the linear one), which is the
   shape every equity index surface has had since 1987 and is the reason a
   flat surface would misprice the hedge this mode demonstrates. It is
   labelled synthetic everywhere it appears -- [Surface.formula] is the label
   the page prints -- and live mode does not use it: a plausible surface
   presented as a real one is exactly the "looks live and is showing made-up
   numbers" failure the credentials path is arranged against.

   UNITS. [State.vega] and every vega below are in the ENGINE's unit, dollars
   per 1.00 of annualised vol -- a hundred times the desk's "per vol point".
   The /100 is a display decision and it belongs to the printer, because this
   conversion is exactly the one that silently makes a limit a hundred times
   too loose; the record carries what the limit thresholds are written in.

   GRAPHS ARE DESTROYED IN Exn.protect. Incremental's state is shared by every
   Graph.t in the process, and the served host runs this at startup: a graph
   left alive after a raise would recompute on every stabilize of the served
   graph forever, and the symptom is the process-wide counter the smoke suite
   reads to decide the deploy is alive. *)

open Core
open Types

module Surface = struct
  type t = { formula : string; floor : float; at_setup : float }
end

module Setup = struct
  type t = {
    underlying : string;
    id : string;
    strike : float;
    right : string;
    expiry_days : float;
    contracts : float;
    multiplier : float;
    spot : float;
    rate : float;
    implied_vol : float;
  }
end

module State = struct
  type t = { label : string; delta_equivalent : float; gamma : float; vega : float }
end

module Leg = struct
  type t = { id : string; days : float; contracts : float }
end

module Calendar = struct
  type t = {
    far : Leg.t;
    near : Leg.t;
    portfolio_vega : float;
    portfolio_gamma : float;
    (* All six tenor slots, in Tenor_bucket.ordered order, 0.0 where the graph
       holds no leg. The graph's map holds only occupied buckets; the page
       draws the empty ones too, because the point of the figure is that a
       total of zero hides two opposite bars, and slots that vanish when
       unoccupied would hide the hiding. *)
    buckets : (string * float) list;
  }
end

type t = {
  surface : Surface.t;
  setup : Setup.t;
  (* Four, in the CLI's order: options only, + the hedge, today, +20 days. The
     third is the second read again after the limits table, as the CLI did. *)
  states : State.t list;
  hedge_shares : float;
  (* After the hedge, sorted by utilisation descending, as the CLI prints. *)
  breaches : Breach.t list;
  clock_advance_days : float;
  calendar : Calendar.t;
}

let floor = 0.05
let formula = "max(0.05, 0.28 + 0.9 m^2 - 0.25 m), m = strike / spot - 1"

let synthetic_implied_vol ~(spot : float) ~(strike : float) : float =
  let moneyness = (strike /. spot) -. 1.0 in
  Float.max floor (0.28 +. (0.9 *. moneyness *. moneyness) -. (0.25 *. moneyness))

let underlying = Symbol.of_string "NVDA"
let sector = Sector.of_string "TECH"
let spot = 900.0
let strike = 950.0
let expiry_days = 30.0
let contracts = -50.0
let rate = 0.04
let contract_id = "NVDA-950C-30d"
let clock_advance_days = 20.0

(* A vega cap that the unhedged book is already through, so the interesting
   line -- a limit that a delta hedge does NOT clear -- is visible rather than
   described. Stated in the engine's internal unit -- dollars per 1.00 of
   annualised vol -- which is a hundred times the desk's "per vol point".
   $300,000 here is a $3,000-a-point cap. *)
let vega_cap = 300_000.0
let gamma_cap = 400.0
let notional_cap = 1_000_000.0

let limits =
  [
    Synthetic_book.limit "nvda-notional" (Limit.Instrument underlying)
      (Limit.Gross_notional (Notional.of_float notional_cap));
    Synthetic_book.limit "nvda-vega" (Limit.Instrument underlying)
      (Limit.Greek_limit (Greek.Vega, Notional.of_float vega_cap));
    Synthetic_book.limit "nvda-gamma" (Limit.Instrument underlying)
      (Limit.Greek_limit (Greek.Gamma, Notional.of_float gamma_cap));
  ]

(* The case a single portfolio vega gets wrong. A calendar spread is long one
   expiry and short another on the same name. Its parallel-shift vega -- the
   sum across expiries -- nets to nearly nothing, because the two legs'
   sensitivities cancel. But they are sensitivities to DIFFERENT volatilities:
   the 25-day implied and the 180-day implied move together and not
   identically, so the position is a real bet on the term structure and the
   total says it is flat. *)
let calendar_near_days = 25.0
let calendar_far_days = 180.0
let calendar_far_contracts = 50.0
let far_id = "NVDA-950C-far"
let near_id = "NVDA-950C-near"

let position ~id ~expiry_in_days =
  Options.Position.create ~underlying ~id
    ~strike:(Options.Strike.of_float strike)
    ~right:Options.Right.Call ~expiry_in_days ()

let state ~label (graph : Graph.t) : State.t =
  let s = Graph.snapshot graph in
  {
    State.label;
    delta_equivalent =
      Notional.to_float
        (Map.find_exn (Graph.Snapshot.exposure_by_instrument s) underlying);
    gamma = Graph.Snapshot.portfolio_gamma s;
    vega = Graph.Snapshot.portfolio_vega s;
  }

(* The hedge walk: options only, hedged, the limits, then the clock. *)
let walk ~implied_vol : State.t list * float * Breach.t list =
  let graph =
    Graph.create
      ~instruments:[ { Instrument.symbol = underlying; sector } ]
      ~limits
      ~options:[ position ~id:contract_id ~expiry_in_days:expiry_days ]
      ~rate ~confidence:0.95 ~return_window:10 ()
  in
  Exn.protect
    ~finally:(fun () -> Graph.destroy graph)
    ~f:(fun () ->
      Graph.set_price graph underlying (Price.of_float spot);
      Graph.set_contracts graph contract_id (Options.Contracts.of_float contracts);
      Graph.set_implied_vol graph contract_id (Options.Implied_vol.of_float implied_vol);
      Graph.stabilize graph;
      let options_only = state ~label:"options only" graph in
      (* Buy the shares the short calls are short. The count is the engine's
         own delta-equivalent exposure divided by the spot -- a number the risk
         engine produces, not a guess. *)
      let hedge_shares = -.options_only.State.delta_equivalent /. spot in
      Graph.set_qty graph underlying (Qty.of_float hedge_shares);
      Graph.stabilize graph;
      let hedged = state ~label:"+ the hedge" graph in
      let breaches =
        Graph.Snapshot.breaches (Graph.snapshot graph)
        |> List.sort ~compare:(fun a b ->
            Float.descending (Limits.utilisation a) (Limits.utilisation b))
      in
      let today = state ~label:"today" graph in
      Graph.advance_valuation_days graph clock_advance_days;
      Graph.stabilize graph;
      let later = state ~label:"+20 days" graph in
      ([ options_only; hedged; today; later ], hedge_shares, breaches))

let calendar () : Calendar.t =
  let graph =
    Graph.create
      ~instruments:[ { Instrument.symbol = underlying; sector } ]
      ~limits:[]
      ~options:
        [
          position ~id:far_id ~expiry_in_days:calendar_far_days;
          position ~id:near_id ~expiry_in_days:calendar_near_days;
        ]
      ~rate ~confidence:0.95 ~return_window:10 ()
  in
  Exn.protect
    ~finally:(fun () -> Graph.destroy graph)
    ~f:(fun () ->
      Graph.set_price graph underlying (Price.of_float spot);
      List.iter [ far_id; near_id ] ~f:(fun id ->
          Graph.set_implied_vol graph id
            (Options.Implied_vol.of_float (synthetic_implied_vol ~spot ~strike)));
      (* One contract of each, to read the per-contract vegas out of the engine
         and size the spread from them rather than from a guess. Vega scales
         roughly with the square root of time, so the far leg carries more of
         it per contract and the near leg has to be the larger position. *)
      Graph.set_contracts graph far_id (Options.Contracts.of_float 1.0);
      Graph.set_contracts graph near_id (Options.Contracts.of_float 0.0);
      Graph.stabilize graph;
      let far_vega = Graph.portfolio_vega graph in
      Graph.set_contracts graph far_id (Options.Contracts.of_float 0.0);
      Graph.set_contracts graph near_id (Options.Contracts.of_float 1.0);
      Graph.stabilize graph;
      let near_vega = Graph.portfolio_vega graph in
      let near_contracts = -.calendar_far_contracts *. far_vega /. near_vega in
      Graph.set_contracts graph far_id (Options.Contracts.of_float calendar_far_contracts);
      Graph.set_contracts graph near_id (Options.Contracts.of_float near_contracts);
      Graph.stabilize graph;
      let s = Graph.snapshot graph in
      let by_bucket = Graph.Snapshot.vega_by_bucket s in
      {
        Calendar.far =
          {
            Leg.id = far_id;
            days = calendar_far_days;
            contracts = calendar_far_contracts;
          };
        near = { Leg.id = near_id; days = calendar_near_days; contracts = near_contracts };
        portfolio_vega = Graph.Snapshot.portfolio_vega s;
        portfolio_gamma = Graph.Snapshot.portfolio_gamma s;
        buckets =
          List.map Options.Tenor_bucket.ordered ~f:(fun bucket ->
              ( Options.Tenor_bucket.to_string bucket,
                Option.value (Map.find by_bucket bucket) ~default:0.0 ));
      })

(* The hedge walk first and the calendar second, as the CLI ran them: each
   graph is destroyed before the next is created, so at no point do two of
   this module's graphs share Incremental's state. *)
let run () : t =
  let implied_vol = synthetic_implied_vol ~spot ~strike in
  let states, hedge_shares, breaches = walk ~implied_vol in
  let calendar = calendar () in
  {
    surface = { Surface.formula; floor; at_setup = implied_vol };
    setup =
      {
        Setup.underlying = Symbol.to_string underlying;
        id = contract_id;
        strike;
        right = Options.Right.to_string Options.Right.Call;
        expiry_days;
        contracts;
        multiplier = Options.default_multiplier;
        spot;
        rate;
        implied_vol;
      };
    states;
    hedge_shares;
    breaches;
    clock_advance_days;
    calendar;
  }
