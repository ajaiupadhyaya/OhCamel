(* Unit tests for synthetic_book.ml.

   The book is six literals and nine limits, and the point of testing literals
   is that three printers, a stress suite, a crisis backtest and now a served
   process all read the same ones. The one number derived by hand here is the
   gross the crisis mode prints as a literal string ("$316,000 gross"), which
   is the only place the CLI states a fact about this book that the book itself
   does not compute. *)

open Core
module Synthetic_book = Ohcamel.Synthetic_book
module Graph = Ohcamel.Graph
open Ohcamel.Types

(*   AAPL 150 x  400 =  60,000
     MSFT 300 x  200 =  60,000
     NVDA 900 x   60 =  54,000
     JPM  200 x  250 =  50,000
     XOM  100 x -500 = -50,000  -> |.| 50,000
     CVX  140 x -300 = -42,000  -> |.| 42,000
                        -------
     gross             316,000 *)
let test_the_book_is_the_one_the_cli_prints () =
  Alcotest.(check int) "six names" 6 (List.length Synthetic_book.book);
  Alcotest.(check int) "six instruments" 6 (List.length Synthetic_book.instruments);
  Alcotest.(check int) "nine limits" 9 (List.length Synthetic_book.limits);
  Alcotest.(check (float 1e-9))
    "gross at the marks is the $316,000 the crisis mode states" 316_000.0
    (List.fold Synthetic_book.book ~init:0.0 ~f:(fun acc (_, _, mark, qty) ->
         acc +. Float.abs (mark *. qty)));
  Alcotest.(check (list string))
    "three sectors, and the short leg is ENERGY"
    [ "ENERGY"; "FINANCIALS"; "TECH" ]
    (List.map Synthetic_book.book ~f:(fun (_, s, _, _) -> Sector.to_string s)
    |> List.dedup_and_sort ~compare:String.compare);
  Alcotest.(check (float 1e-9)) "confidence" 0.95 Synthetic_book.confidence;
  Alcotest.(check int) "return window" 60 Synthetic_book.return_window

let with_seeded_graph ~seed ~f =
  let graph =
    Graph.create ~starting_cash:Synthetic_book.starting_cash
      ~instruments:Synthetic_book.instruments ~limits:Synthetic_book.limits
      ~confidence:Synthetic_book.confidence ~return_window:Synthetic_book.return_window ()
  in
  Exn.protect
    ~finally:(fun () -> Graph.destroy graph)
    ~f:(fun () ->
      List.iter Synthetic_book.book ~f:(fun (symbol, _, price, qty) ->
          Graph.set_price graph symbol (Price.of_float price);
          Graph.set_qty graph symbol (Qty.of_float qty));
      Synthetic_book.seed_returns ~rng:(Random.State.make [| seed |]) ~graph;
      Graph.stabilize graph;
      f graph)

(* Spec §07, amended: the demo host used to seed its windows and its factor as
   independent draws, so every beta was fitted to noise. What is asserted here
   is the structural fact, not a beta: the factor cell is set, every window is
   the full return_window long, and two graphs seeded from the same seed hold
   exactly the same arrays -- which is what makes the served host's stress
   table a reproducible figure rather than a fresh coin toss per restart. *)
let test_seed_returns_is_deterministic_and_sets_the_factor () =
  let read graph =
    ( Array.to_list (Graph.factor_returns graph),
      List.map Synthetic_book.book ~f:(fun (symbol, _, _, _) ->
          Array.to_list (Graph.returns graph symbol)) )
  in
  let factor_a, windows_a = with_seeded_graph ~seed:7 ~f:read in
  let factor_b, windows_b = with_seeded_graph ~seed:7 ~f:read in
  Alcotest.(check int) "the factor is one window long" 60 (List.length factor_a);
  List.iter windows_a ~f:(fun w ->
      Alcotest.(check int) "each name's window is full" 60 (List.length w));
  Alcotest.(check (list (float 0.0))) "same seed, same factor" factor_a factor_b;
  Alcotest.(check (list (list (float 0.0)))) "same seed, same windows" windows_a windows_b;
  Alcotest.(check bool)
    "with a factor set, the graph can estimate a beta at all" true
    (with_seeded_graph ~seed:7 ~f:(fun g -> Option.is_some (Graph.portfolio_beta g)))

let suite =
  ( "synthetic_book",
    [
      Alcotest.test_case "the book is the one the CLI prints" `Quick
        test_the_book_is_the_one_the_cli_prints;
      Alcotest.test_case "seed_returns is deterministic and sets the factor" `Quick
        test_seed_returns_is_deterministic_and_sets_the_factor;
    ] )
