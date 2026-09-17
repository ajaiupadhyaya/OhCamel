(* The example book, parsed.

   deploy/Dockerfile bakes this file into the image as /app/book.sexp, and
   deploy/deploy.sh copies it to create book.sexp on a host that has none. So a
   typo in it is not a documentation bug: it is a container that refuses to
   start, on the one path nobody exercises locally. Nothing else parsed it.

   The second half of the case is the shipped default. A book copied from this
   file must not trade until someone edits it: the desk block is present so that
   enabling it is one word rather than a guess, and it says disabled. *)

open Core
module Book = Ohcamel.Config.Book
module Desk_spec = Ohcamel.Config.Book.Desk_spec

(* dune runs the suite in _build/default/test, and the stanza's dep puts the
   file one level up. *)
let path = "../book.example.sexp"

let test_the_example_book_parses_and_ships_inert () =
  let book =
    match Book.load path with
    | Ok book -> book
    | Error e ->
        Alcotest.failf
          "the example book does not parse, so a fresh deploy would refuse to start: %s"
          (Error.to_string_hum e)
  in
  (* The universe and the limits the rest of the documentation describes. *)
  Alcotest.(check int) "six instruments" 6 (List.length book.Book.positions);
  Alcotest.(check int) "seven limits" 7 (List.length book.Book.limits);
  (* Inert as shipped: no orders, and no alerting, until a person edits it. *)
  Alcotest.(check bool)
    "the desk ships disabled" true
    (Desk_spec.equal_trading book.Book.desk.Desk_spec.trading Desk_spec.Disabled);
  Alcotest.(check bool)
    "alerting ships off" false book.Book.alerts.Ohcamel.Config.Alerts.enabled;
  (* The kill switch lives inside alerting, so an armed switch under disabled
     alerting would be a configuration that does nothing. Shipped, both are
     off. *)
  Alcotest.(check bool)
    "the kill switch ships disarmed" false
    book.Book.alerts.Ohcamel.Config.Alerts.kill_switch_enabled

let suite =
  ( "example book",
    [
      Alcotest.test_case "the example book parses and ships inert" `Quick
        test_the_example_book_parses_and_ships_inert;
    ] )
