(* An intraday rebalance's waves (desk/waves.ml), by hand.

   The book's positions: A, B and C held long 100 each, nothing else. A sell
   of a held name shrinks its position; a buy of a name not held grows one.
   Every leg's price is 1.00, so a leg's notional is its quantity. *)

open Core
open Ohcamel.Types
module D = Ohcamel_desk
module Leg = D.Rebalance.Leg

let held = [ "A"; "B"; "C" ]

let position s =
  if List.mem held (Symbol.to_string s) ~equal:String.equal then 100.0 else 0.0

let leg name side qty =
  {
    Leg.symbol = Symbol.of_string name;
    weight = 0.0;
    price = 1.0;
    target = 0;
    current = 0;
    side;
    qty;
  }

let sell name = leg name D.Order.Side.Sell 10
let buy name qty = leg name D.Order.Side.Buy qty

let split ?(position = position) legs =
  List.map (D.Waves.split ~legs ~position ~max_legs:6) ~f:(fun wave ->
      List.map wave ~f:(fun (l : Leg.t) -> Symbol.to_string l.Leg.symbol))

let check what expected legs =
  Alcotest.(check (list (list string))) what expected (split legs)

(* Three sells of held names (shrinking) and four buys of new ones (growing),
   of 40, 30, 20 and 10: [[3]; [4]] -- the shrinking wave, then the growing
   legs largest first. *)
let test_seven () =
  check "the three that shrink, then the four that grow, largest first"
    [ [ "A"; "B"; "C" ]; [ "G"; "F"; "E"; "D" ] ]
    [ sell "A"; buy "D" 10; sell "B"; buy "E" 20; sell "C"; buy "F" 30; buy "G" 40 ]

(* Two shrinking and twelve growing with notionals 12, 11, ..., 1 (N12 is
   12 shares, N1 is 1): [[2]; [the 6 largest]; [the 6 smallest]]. Given in
   ascending order, so the sort is what puts them right. *)
let test_fourteen () =
  let growing = List.init 12 ~f:(fun i -> buy (sprintf "N%02d" (i + 1)) (i + 1)) in
  check "two, then 12..7, then 6..1"
    [
      [ "A"; "B" ];
      [ "N12"; "N11"; "N10"; "N09"; "N08"; "N07" ];
      [ "N06"; "N05"; "N04"; "N03"; "N02"; "N01" ];
    ]
    ((sell "A" :: growing) @ [ sell "B" ])

(* Nothing shrinks: no empty first wave, one wave only. *)
let test_no_shrinking () =
  check "one wave, largest first"
    [ [ "F"; "E"; "D" ] ]
    [ buy "D" 1; buy "E" 2; buy "F" 3 ]

(* Eight shrinking and one growing: a long strategy selling eight names it
   holds 100 of each, H1 1 share to H8 8, and buying 5 of a new one. The
   shrinking legs are chunked as the growing ones are, largest first:
   [[H8..H3]; [H2; H1]; [N]] -- where one unchunked wave of eight would be
   refused at six, and the strategy could never exit. Given in ascending
   order, so the sort is what puts them right. *)
let test_eight_shrinking () =
  let position s =
    if String.is_prefix (Symbol.to_string s) ~prefix:"H" then 100.0 else 0.0
  in
  let sells =
    List.init 8 ~f:(fun i -> leg (sprintf "H%d" (i + 1)) D.Order.Side.Sell (i + 1))
  in
  Alcotest.(check (list (list string)))
    "six largest sells, the two smallest, then the buy"
    [ [ "H8"; "H7"; "H6"; "H5"; "H4"; "H3" ]; [ "H2"; "H1" ]; [ "N" ] ]
    (split ~position (sells @ [ buy "N" 5 ]))

let suite =
  ( "waves",
    [
      Alcotest.test_case "Waves.split seven" `Quick test_seven;
      Alcotest.test_case "Waves.split fourteen" `Quick test_fourteen;
      Alcotest.test_case "Waves.split no shrinking" `Quick test_no_shrinking;
      Alcotest.test_case "Waves.split eight shrinking" `Quick test_eight_shrinking;
    ] )
