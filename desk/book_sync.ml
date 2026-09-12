(* The book, taken from the venue.

   Design §3.3: the book file declares the universe, and the venue says what
   is held. [plan] is the whole decision and is pure; [apply] writes it into
   the graph's cells.

   EVERY universe name is set, including to zero. A sync that wrote only the
   names the venue reported would leave a position the venue has since closed
   at its old quantity, carrying exposure and risk the account no longer has --
   the fiction of a price that never existed, arriving through the other input.

   A venue position outside the universe cannot enter the graph, whose shape is
   fixed at construction, and it is not dropped either: it comes back as
   unmanaged, for the page to name. *)

open Core
open Ohcamel.Types
module Graph = Ohcamel.Graph

module Plan = struct
  type t = {
    quantities : (Symbol.t * Qty.t) list;
    cash : Notional.t;
    unmanaged : Venue.Position.t list;
  }
  [@@deriving sexp_of, compare, equal]
end

let plan ~(universe : Symbol.t list) ~(positions : Venue.Position.t list)
    ~(account : Venue.Account.t) : Plan.t =
  let held =
    List.map positions ~f:(fun p -> (p.Venue.Position.symbol, p.Venue.Position.qty))
    |> Symbol.Map.of_alist_reduce ~f:Qty.add
  in
  let universe = List.dedup_and_sort universe ~compare:Symbol.compare in
  let members = Symbol.Set.of_list universe in
  {
    Plan.quantities =
      List.map universe ~f:(fun s ->
          (s, Option.value (Map.find held s) ~default:Qty.zero));
    cash = account.Venue.Account.cash;
    unmanaged =
      List.filter positions ~f:(fun p -> not (Set.mem members p.Venue.Position.symbol))
      |> List.sort ~compare:(fun a b ->
          Symbol.compare a.Venue.Position.symbol b.Venue.Position.symbol);
  }

(* Every cell, then one stabilize: the graph never sees a book that is half
   the last sync's and half this one's. *)
let apply (graph : Graph.t) (plan : Plan.t) : unit =
  List.iter plan.Plan.quantities ~f:(fun (symbol, qty) -> Graph.set_qty graph symbol qty);
  Graph.set_cash graph plan.Plan.cash;
  Graph.stabilize graph
