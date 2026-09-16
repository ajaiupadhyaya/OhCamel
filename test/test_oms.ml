(* The order manager's preview, on the gate's book:

     AAPL  400 x 150 = 60,000   TECH
     MSFT  100 x 300 = 30,000   TECH
     XOM  -200 x 100 = -20,000  ENERGY

   Limits aapl-cap 80,000, tech-cap 100,000, book-cap 200,000. Every name has
   printed just now, the session is open, twenty-day volume is a fixed
   1,000,000 shares (1% of it is 10,000), and the order cap is 25,000. *)

open Core
open Ohcamel.Types
module Graph = Ohcamel.Graph
module Desk_spec = Ohcamel.Config.Book.Desk_spec
module D = Ohcamel_desk

let aapl = Symbol.of_string "AAPL"
let msft = Symbol.of_string "MSFT"
let xom = Symbol.of_string "XOM"
let money = Notional.of_float
let limit name scope n = { Limit.name; scope; kind = Limit.Gross_notional (money n) }

(* [book_is_current] is true unless a case says otherwise: every case but one
   gates an order against a book the account holds. The trailing unit is what
   lets that default be taken, as test_gate.ml's [with_book] takes its own:
   an optional argument before the last labelled one is never erased. *)
let with_oms ?(book_is_current = true) ~f () =
  let graph =
    Graph.create ~starting_cash:(money 1_000_000.0)
      ~instruments:
        [
          { Instrument.symbol = aapl; sector = Sector.of_string "TECH" };
          { Instrument.symbol = msft; sector = Sector.of_string "TECH" };
          { Instrument.symbol = xom; sector = Sector.of_string "ENERGY" };
        ]
      ~limits:
        [
          limit "aapl-cap" (Limit.Instrument aapl) 80_000.0;
          limit "tech-cap" (Limit.Sector (Sector.of_string "TECH")) 100_000.0;
          limit "book-cap" Limit.Portfolio 200_000.0;
        ]
      ~confidence:0.95 ~return_window:10 ()
  in
  Exn.protect
    ~finally:(fun () -> Graph.destroy graph)
    ~f:(fun () ->
      List.iter
        [ (aapl, 150.0, 400.0); (msft, 300.0, 100.0); (xom, 100.0, -200.0) ]
        ~f:(fun (s, p, q) ->
          Graph.set_qty graph s (Qty.of_float q);
          Graph.set_returns graph s
            [| -0.02; -0.01; 0.0; 0.01; 0.02; -0.02; -0.01; 0.0; 0.01; 0.02 |];
          Graph.apply_tick graph
            { Tick.symbol = s; price = Price.of_float p; time = Time.now () });
      Graph.set_now graph (Time.now ());
      Graph.stabilize graph;
      let journal = Or_error.ok_exn (D.Journal.open_ ~path:":memory:") in
      let venue =
        D.Sim_venue.create ~opened_at:(Time_ns.now ())
          ~marks:(fun s -> Some (Graph.price graph s))
          ~now:Time_ns.now
          ~half_spread_bps:(fun _ -> 5.0)
          ~cash:(money 1_000_000.0) ~positions:[] ()
      in
      let oms =
        D.Oms.create ~graph ~journal
          ~spec:{ Desk_spec.default with Desk_spec.trading = Desk_spec.Enabled }
          ~read:(Some (D.Sim_venue.read venue))
          ~trade:(Ok (D.Sim_venue.trade ~auto:false venue))
          ~halt:(D.Halt.create D.Halt.Source.none)
          ~accepts_tickets:false ~adv:(D.Oms.Adv.Fixed 1_000_000.0) ~now:Time_ns.now
          ~rng:(Random.State.make [| 3 |]) ~on_change:ignore ~on_event:ignore
          ~after_fill:ignore
          ~book_is_current:(fun () -> book_is_current)
          ()
      in
      D.Oms.set_market oms ~session_open:true ~adv20:[];
      f oms journal)

let ticket ?(kind = D.Order.Kind.Market) symbol side qty =
  { D.Ticket.symbol; side; qty; kind }

let rules (p : D.Oms.Preview.t) =
  List.map p.D.Oms.Preview.failures ~f:(fun x -> x.D.Rules.Failure.rule)

let created (p : D.Oms.Preview.t) =
  match p.D.Oms.Preview.verdict with
  | Some v ->
      List.map v.Ohcamel.Gate.Verdict.created ~f:(fun m -> m.Ohcamel.Gate.Move.limit)
  | None -> []

let test_a_preview_that_takes_tech_over_its_cap_names_it_and_creates_nothing () =
  with_oms
    ~f:(fun oms journal ->
      (* 100 x 150 = 15,000: under the order cap and 1% of volume, so every
         rule passes. TECH 75,000 + 30,000 = 105,000 > 100,000. *)
      let p = D.Oms.preview oms (ticket aapl D.Order.Side.Buy 100) in
      Alcotest.(check bool) "refused" false (D.Oms.Preview.passed p);
      Alcotest.(check (list string)) "by no rule" [] (rules p);
      Alcotest.(check (list string)) "by tech-cap" [ "tech-cap" ] (created p);
      Alcotest.(check bool)
        "and the reasons name it" true
        (List.exists (D.Oms.Preview.reasons p)
           ~f:(String.is_substring ~substring:"tech-cap"));
      Alcotest.(check int)
        "nothing was journaled" 0
        (List.length (D.Journal.recent_orders journal ~limit:10)))
    ()

let test_the_rules_and_the_limits_both_speak () =
  with_oms
    ~f:(fun oms _ ->
      (* 200 x 150 = 30,000 > 25,000: the notional rule, and no other -- 200
         shares is under 1% of 1,000,000. AAPL 600 x 150 = 90,000 > 80,000
         and TECH 90,000 + 30,000 = 120,000 > 100,000: two limits, in the
         order they are configured. One rule and two limits: three reasons. *)
      let p = D.Oms.preview oms (ticket aapl D.Order.Side.Buy 200) in
      Alcotest.(check (list string)) "the rule" [ "notional" ] (rules p);
      Alcotest.(check (list string)) "the limits" [ "aapl-cap"; "tech-cap" ] (created p);
      Alcotest.(check int)
        "every reason, rules first" 3
        (List.length (D.Oms.Preview.reasons p)))
    ()

let test_a_preview's_json_has_exactly_these_keys () =
  with_oms
    ~f:(fun oms _ ->
      let json =
        D.Oms.Preview.to_json (D.Oms.preview oms (ticket xom D.Order.Side.Buy 10))
      in
      Alcotest.(check (list string))
        "ten, in order"
        [
          "passed";
          "symbol";
          "side";
          "qty";
          "type";
          "limit_price";
          "decision_price";
          "rules";
          "gate";
          "reasons";
        ]
        (Yojson.Safe.Util.keys json);
      (* XOM -200 -> -190: every limit further inside its line *)
      Alcotest.(check bool)
        "passed" true
        Yojson.Safe.Util.(to_bool (member "passed" json));
      Alcotest.(check (float 1e-9))
        "priced at the mark" 100.0
        Yojson.Safe.Util.(to_number (member "decision_price" json)))
    ()

(* The rules refuse an order on a book that is not the account's: before the
   first read is applied, or once the last is older than two sync intervals,
   the graph may hold the book file's quantities and cash, and the gate would
   judge a book nobody holds. XOM 10 x 100 = 1,000 passes every other rule and
   every limit (XOM -200 -> -190), so the trading rule speaks alone. *)
let test_a_book_that_is_not_the_account's_is_refused_by_the_trading_rule () =
  with_oms ~book_is_current:false
    ~f:(fun oms _ ->
      let p = D.Oms.preview oms (ticket xom D.Order.Side.Buy 10) in
      Alcotest.(check bool) "refused" false (D.Oms.Preview.passed p);
      Alcotest.(check (list string)) "by the trading rule alone" [ "trading" ] (rules p);
      Alcotest.(check bool)
        "saying why" true
        (List.exists (D.Oms.Preview.reasons p)
           ~f:(String.is_substring ~substring:"recent read of the account"));
      (* The frame's [trading] is [can_trade], so it reads off while the rule
         would refuse. *)
      Alcotest.(check bool)
        "and the frame's trading reads off" false (D.Oms.can_trade oms))
    ()

let suite =
  ( "oms",
    [
      Alcotest.test_case
        "a preview that takes TECH over its cap names it and creates nothing" `Quick
        test_a_preview_that_takes_tech_over_its_cap_names_it_and_creates_nothing;
      Alcotest.test_case "the rules and the limits both speak" `Quick
        test_the_rules_and_the_limits_both_speak;
      Alcotest.test_case "a preview's JSON has exactly these keys" `Quick
        test_a_preview's_json_has_exactly_these_keys;
      Alcotest.test_case "a book that is not the account's is refused by the trading rule"
        `Quick test_a_book_that_is_not_the_account's_is_refused_by_the_trading_rule;
    ] )
