(* One sub-ledger check for the desk (desk/subledger.ml), pure: the
   strategies' own positions per name, summed with the hand's, against the
   account's. The order manager's half -- which names it judges, the alert
   at the sync and the close, and what a break refuses -- is in
   test_rebalance.ml. *)

open Core
open Ohcamel.Types
module Subledger = Ohcamel_desk.Subledger

let spy = Symbol.of_string "SPY"
let qqq = Symbol.of_string "QQQ"
let aapl = Symbol.of_string "AAPL"

let break_ =
  Alcotest.testable
    (fun ppf b -> Sexp.pp_hum ppf (Subledger.sexp_of_break b))
    Subledger.equal_break

let check ?(tolerance = Subledger.tolerance) own account =
  Subledger.check ~own ~account ~tolerance

(* The brief's pair: A long 100 SPY, B short 50, and the account holds the
   net, +50 -- no break, where the old per-strategy rule refused B forever
   because the account never "held" its -50. The account at +40 is one
   break, naming the sum (50) and the account (40). *)
let test_two_strategies_on_opposite_sides_sum_to_the_account () =
  let own = [ ("alpha", spy, 100.0); ("beta", spy, -50.0) ] in
  Alcotest.(check (list break_)) "+50 is the net: no break" [] (check own [ (spy, 50.0) ]);
  Alcotest.(check (list break_))
    "+40: one break, {SPY; 50; 40}"
    [ { Subledger.symbol = spy; strategies = 50.0; account = 40.0 } ]
    (check own [ (spy, 40.0) ])

(* With that break, an order that increases SPY exposure is refused, naming
   both figures; an order in QQQ is not, nor is one that reduces SPY. *)
let test_a_break_refuses_an_increase_in_its_name_only () =
  let breaks = check [ ("alpha", spy, 100.0); ("beta", spy, -50.0) ] [ (spy, 40.0) ] in
  (match Subledger.refuses breaks ~symbol:spy ~increases:true with
  | None -> Alcotest.fail "an increase in SPY went through a break"
  | Some why ->
      List.iter [ "SPY"; "sum to 50"; "holds 40" ] ~f:(fun substring ->
          Alcotest.(check bool)
            (sprintf "%S names %S" why substring)
            true
            (String.is_substring why ~substring)));
  Alcotest.(check (option string))
    "an increase in QQQ: no break there" None
    (Subledger.refuses breaks ~symbol:qqq ~increases:true);
  Alcotest.(check (option string))
    "a reduction in SPY goes" None
    (Subledger.refuses breaks ~symbol:spy ~increases:false)

(* Hand tickets are one more sub-ledger: the owner bought 30 SPY beside the
   pair, the account holds 80, and it sums. A name only the hand holds, or
   only the account, is judged as well -- the caller decides which names to
   pass (Oms.subledger passes only names a strategy trades). *)
let test_the_hand_is_one_more_sub_ledger () =
  Alcotest.(check (list break_))
    "A +100, B -50, hand +30, account +80: whole" []
    (check
       [ ("alpha", spy, 100.0); ("beta", spy, -50.0); (Subledger.hand, spy, 30.0) ]
       [ (spy, 80.0) ]);
  Alcotest.(check (list break_))
    "a name the account holds and no one's sub-ledger does"
    [ { Subledger.symbol = aapl; strategies = 0.0; account = 400.0 } ]
    (check [] [ (aapl, 400.0) ]);
  Alcotest.(check (list break_))
    "a name a sub-ledger holds and the account does not"
    [ { Subledger.symbol = qqq; strategies = 7.0; account = 0.0 } ]
    (check [ ("alpha", qqq, 7.0) ] [])

(* Half a share: float noise is no break, one whole share is, and an unknown
   on either side is never a sum that holds. *)
let test_the_tolerance_is_half_a_share_and_an_unknown_breaks () =
  Alcotest.(check (float 0.0)) "the default" 0.5 Subledger.tolerance;
  Alcotest.(check (list break_))
    "1e-9 off: noise" []
    (check [ ("alpha", spy, 100.0 +. 1e-9) ] [ (spy, 100.0) ]);
  Alcotest.(check int)
    "one share off: a break" 1
    (List.length (check [ ("alpha", spy, 101.0) ] [ (spy, 100.0) ]));
  Alcotest.(check int)
    "a NaN account: a break" 1
    (List.length (check [ ("alpha", spy, 100.0) ] [ (spy, Float.nan) ]));
  Alcotest.(check int)
    "a NaN position: a break" 1
    (List.length (check [ ("alpha", spy, Float.nan) ] [ (spy, 100.0) ]))

(* Whose sub-ledger an order's source names. *)
let test_a_source_names_its_owner () =
  Alcotest.(check (list string))
    "signal:<slug>:<sequence> is the strategy's; anything else the hand's"
    [ "exp_a01_spy"; "farm_tsmom_rates"; "hand"; "hand"; "hand"; "hand" ]
    (List.map
       [
         "signal:exp_a01_spy:12";
         "signal:farm_tsmom_rates:3";
         "ticket";
         "manual";
         "signal::4";
         "signal:noseq";
       ] ~f:(fun source -> Subledger.owner ~source))

let suite =
  ( "subledger",
    [
      Alcotest.test_case "two strategies on opposite sides sum to the account" `Quick
        test_two_strategies_on_opposite_sides_sum_to_the_account;
      Alcotest.test_case "a break refuses an increase in its name only" `Quick
        test_a_break_refuses_an_increase_in_its_name_only;
      Alcotest.test_case "the hand is one more sub-ledger" `Quick
        test_the_hand_is_one_more_sub_ledger;
      Alcotest.test_case "the tolerance is half a share and an unknown breaks" `Quick
        test_the_tolerance_is_half_a_share_and_an_unknown_breaks;
      Alcotest.test_case "a source names its owner" `Quick test_a_source_names_its_owner;
    ] )
