(* Reproducibility pins for options_walk.ml, plus one hand derivation.

   THESE ARE PINS. The hedge count, the three "+20 days" cells, the 141.5%
   vega utilisation, the calendar's gamma and its two bucket vegas are the
   numbers README.md prints for `make options`, and what is asserted is that
   the walk, moved out of bin/main.ml, still produces them. Why a short 950
   call at 26.9% vol has THAT delta is options.ml's business, tested there
   against Hull. The hand derivation is the implied vol itself, which is a
   three-term formula and can be checked on paper. *)

open Core
module Options_walk = Ohcamel.Options_walk
module Options = Ohcamel.Options
module Limits = Ohcamel.Limits
open Ohcamel.Types

let walk = lazy (Options_walk.run ())
let f0 x = Printf.sprintf "%.0f" x
let f1 x = Printf.sprintf "%.1f" x

(* m = 950 / 900 - 1 = 0.0555...; m^2 = 0.0030864
   0.28 + 0.9 * 0.0030864 - 0.25 * 0.0555... = 0.28 + 0.0027778 - 0.0138889
                                              = 0.2688889, i.e. 26.9% *)
let test_the_synthetic_surface_at_the_setup () =
  let iv = Options_walk.synthetic_implied_vol ~spot:900.0 ~strike:950.0 in
  Alcotest.(check (float 1e-6)) "26.89% by hand" 0.2688889 iv;
  let w = Lazy.force walk in
  Alcotest.(check (float 0.0))
    "the setup carries the same number" iv
    w.Options_walk.setup.Options_walk.Setup.implied_vol;
  Alcotest.(check (float 0.0))
    "and so does the surface" iv w.Options_walk.surface.Options_walk.Surface.at_setup;
  Alcotest.(check (float 0.0))
    "the floor is 5%" 0.05 w.Options_walk.surface.Options_walk.Surface.floor;
  (* The floor never binds. 0.9 m^2 - 0.25 m + 0.28 is a parabola with its
     minimum at m = 0.25 / 1.8 = 0.13889, where it is
     0.28 - 0.25^2 / (4 * 0.9) = 0.28 - 0.017361 = 0.262639 -- above 0.05
     everywhere. The floor is a guard against a future edit to the
     coefficients, not a feature of this surface, and the page says so. *)
  Alcotest.(check (float 1e-6))
    "the surface's minimum, by hand" 0.262639
    (Options_walk.synthetic_implied_vol ~spot:900.0
       ~strike:(900.0 *. (1.0 +. (0.25 /. 1.8))))

let test_the_setup_is_the_readmes_book () =
  let s = (Lazy.force walk).Options_walk.setup in
  let open Options_walk.Setup in
  Alcotest.(check string) "NVDA" "NVDA" s.underlying;
  Alcotest.(check string) "the contract id" "NVDA-950C-30d" s.id;
  Alcotest.(check (float 0.0)) "strike" 950.0 s.strike;
  Alcotest.(check string) "a call" "C" s.right;
  Alcotest.(check (float 0.0)) "30 days" 30.0 s.expiry_days;
  Alcotest.(check (float 0.0)) "short fifty" (-50.0) s.contracts;
  Alcotest.(check (float 0.0)) "the listed multiplier" 100.0 s.multiplier;
  Alcotest.(check (float 0.0)) "spot" 900.0 s.spot;
  Alcotest.(check (float 0.0)) "rate" 0.04 s.rate

let test_the_hedge_is_1338_shares_and_flattens_delta () =
  let w = Lazy.force walk in
  Alcotest.(check string) "1,338 shares" "1338" (f0 w.Options_walk.hedge_shares);
  Alcotest.(check (list string))
    "four states, in the CLI's order"
    [ "options only"; "+ the hedge"; "today"; "+20 days" ]
    (List.map w.Options_walk.states ~f:(fun s -> s.Options_walk.State.label));
  match w.Options_walk.states with
  | [ before; after; today; _ ] ->
      let open Options_walk.State in
      Alcotest.(check bool)
        "short calls are short delta" true
        (Float.( < ) before.delta_equivalent 0.0);
      (* shares = -delta / spot, then exposure = shares * spot + delta. In IEEE
         arithmetic (a / b) * b is not always a, so "exactly zero" is asserted
         to a micro-dollar on a six-figure exposure rather than bit-for-bit. *)
      Alcotest.(check (float 1e-6))
        "delta-equivalent is zero after the hedge" 0.0 after.delta_equivalent;
      Alcotest.(check (float 0.0)) "gamma did not move" before.gamma after.gamma;
      Alcotest.(check (float 0.0)) "vega did not move" before.vega after.vega;
      Alcotest.(check (float 0.0))
        "today IS the hedged state" after.delta_equivalent today.delta_equivalent
  | _ -> Alcotest.fail "four states"

let test_nvda_vega_is_breached_at_141_5_percent () =
  let w = Lazy.force walk in
  let named name =
    List.find_exn w.Options_walk.breaches ~f:(fun b ->
        String.equal (Breach.limit b).Limit.name name)
  in
  Alcotest.(check bool) "nvda-vega breached" true (Breach.breached (named "nvda-vega"));
  Alcotest.(check string)
    "141.5%" "141.5"
    (f1 (Limits.utilisation (named "nvda-vega") *. 100.0));
  Alcotest.(check bool)
    "the notional cap is comfortably clear" false
    (Breach.breached (named "nvda-notional"));
  Alcotest.(check bool)
    "sorted by utilisation, descending" true
    (List.is_sorted w.Options_walk.breaches ~compare:(fun a b ->
         Float.descending (Limits.utilisation a) (Limits.utilisation b)))

let test_twenty_days_later () =
  let w = Lazy.force walk in
  Alcotest.(check (float 0.0))
    "the clock advanced twenty days" 20.0 w.Options_walk.clock_advance_days;
  let later = List.last_exn w.Options_walk.states in
  let open Options_walk.State in
  Alcotest.(check string) "$657,690 over-hedged" "657690" (f0 later.delta_equivalent);
  Alcotest.(check string) "gamma -25.2" "-25.2" (f1 later.gamma);
  Alcotest.(check string) "vega $-1,502 per point" "-1502" (f0 (later.vega /. 100.0))

let test_the_calendar_spread () =
  let c = (Lazy.force walk).Options_walk.calendar in
  let open Options_walk.Calendar in
  Alcotest.(check (pair string (pair (float 0.0) (float 0.0))))
    "long 50 far calls, 180 days out"
    ("NVDA-950C-far", (180.0, 50.0))
    Options_walk.Leg.(c.far.id, (c.far.days, c.far.contracts));
  Alcotest.(check string)
    "near leg, 25 days out" "NVDA-950C-near" c.near.Options_walk.Leg.id;
  Alcotest.(check (float 0.0)) "25 days" 25.0 c.near.Options_walk.Leg.days;
  Alcotest.(check bool)
    "the near leg is short" true
    (Float.( < ) c.near.Options_walk.Leg.contracts 0.0);
  Alcotest.(check string) "gamma -72.5" "-72.5" (f1 c.portfolio_gamma);
  Alcotest.(check bool)
    "the parallel-shift vega is zero" true
    (Float.( < ) (Float.abs c.portfolio_vega) 1e-3);
  Alcotest.(check (list string))
    "all six buckets, in tenor order"
    (List.map Options.Tenor_bucket.ordered ~f:Options.Tenor_bucket.to_string)
    (List.map c.buckets ~f:fst);
  let bucket name = List.Assoc.find_exn c.buckets ~equal:String.equal name in
  Alcotest.(check string)
    "1w-1m: $-12,559 per point" "-12559"
    (f0 (bucket "1w-1m" /. 100.0));
  Alcotest.(check string) "3-6m: $12,559 per point" "12559" (f0 (bucket "3-6m" /. 100.0));
  Alcotest.(check bool)
    "the buckets sum to the (zero) total" true
    (Float.( < )
       (Float.abs (List.sum (module Float) c.buckets ~f:snd -. c.portfolio_vega))
       1e-3);
  Alcotest.(check int)
    "the other four are empty" 4
    (List.count c.buckets ~f:(fun (_, vega) -> Float.( = ) vega 0.0))

let suite =
  ( "options_walk",
    [
      Alcotest.test_case "the synthetic surface at the setup" `Quick
        test_the_synthetic_surface_at_the_setup;
      Alcotest.test_case "the setup is the README's book" `Quick
        test_the_setup_is_the_readmes_book;
      Alcotest.test_case "THE HEDGE IS 1,338 SHARES AND FLATTENS DELTA" `Quick
        test_the_hedge_is_1338_shares_and_flattens_delta;
      Alcotest.test_case "nvda-vega is breached at 141.5%" `Quick
        test_nvda_vega_is_breached_at_141_5_percent;
      Alcotest.test_case "twenty days later" `Quick test_twenty_days_later;
      Alcotest.test_case "the calendar spread" `Quick test_the_calendar_spread;
    ] )
