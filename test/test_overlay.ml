(* The overlay: one document kind, four rules, two numbers. Every expected
   value is derived by hand beside its assertion; none was produced by running
   the code. The module is pure, so the clock is a record built here. *)

open Core
module Overlay = Ohcamel_desk.Overlay

let fail = Alcotest.fail
let t name f = Alcotest.test_case name `Quick f

(* The instant every case is measured from. *)
let now = Time_ns.of_string_with_utc_offset "2026-10-13T16:00:00Z"
let clock : Overlay.clock = { now }
let at_minutes_ago m = Time_ns.sub now (Time_ns.Span.of_min m)

let doc ?(version = 1) ?(kind = "overlay") ?(as_of = "2026-10-13T15:30:00Z") ?(seq = 7)
    ?(scale = "0.7071") () =
  sprintf
    {|{"schema_version":%d,"kind":"%s","as_of":"%s","computed_at":"2026-10-13T15:30:05Z","sequence":%d,"exposure_scale":%s,"inputs":{"vol_target":0.1},"reported":{"regime_p_high":0.12}}|}
    version kind as_of seq scale

let parsed s =
  match Overlay.parse_string s with
  | Ok o -> o
  | Error e -> failwithf "test document did not parse: %s" e ()

let judge ?(max_age = 60) ?(last = 6) s =
  Overlay.validate ~clock ~max_age_minutes:max_age ~last_sequence:last (parsed s)

let expect_rule rule = function
  | Overlay.Rejected (r, _) when Poly.equal r rule -> ()
  | Overlay.Rejected (_, why) -> failwithf "wrong rule: %s" why ()
  | Overlay.Accepted _ -> fail "accepted"

let why = function Overlay.Rejected (_, w) -> w | Overlay.Accepted _ -> ""
let close = Alcotest.float 1e-9

let state_to_string = function
  | Overlay.Fresh -> "Fresh"
  | Overlay.Held s -> "Held " ^ Time_ns.Span.to_string_hum s
  | Overlay.Stale -> "Stale"
  | Overlay.Absent -> "Absent"

let state_t = Alcotest.testable (Fmt.of_to_string state_to_string) Poly.equal

let suite =
  ( "overlay",
    [
      (* --- the governor: cap 0.02, so full size to 0.010, zero at 0.02. --- *)
      t "governor at half cap is one" (fun () ->
          (* dd 0.010 <= cap/2 = 0.010 -> 1.0 *)
          Alcotest.check close "" 1.0
            (Overlay.governor ~drawdown:(Some 0.010) ~dd_cap:(Some 0.02)));
      t "governor at three quarters" (fun () ->
          (* (0.02 - 0.015) / 0.01 = 0.5 *)
          Alcotest.check close "" 0.5
            (Overlay.governor ~drawdown:(Some 0.015) ~dd_cap:(Some 0.02)));
      t "governor at seven eighths" (fun () ->
          (* (0.02 - 0.0175) / 0.01 = 0.25 *)
          Alcotest.check close "" 0.25
            (Overlay.governor ~drawdown:(Some 0.0175) ~dd_cap:(Some 0.02)));
      t "governor at cap" (fun () ->
          Alcotest.check close "" 0.0
            (Overlay.governor ~drawdown:(Some 0.02) ~dd_cap:(Some 0.02)));
      t "governor beyond cap" (fun () ->
          Alcotest.check close "" 0.0
            (Overlay.governor ~drawdown:(Some 0.03) ~dd_cap:(Some 0.02)));
      t "governor with no cap or no drawdown is one" (fun () ->
          Alcotest.check close "" 1.0 (Overlay.governor ~drawdown:(Some 0.5) ~dd_cap:None);
          Alcotest.check close "" 1.0
            (Overlay.governor ~drawdown:None ~dd_cap:(Some 0.02));
          Alcotest.check close "" 1.0 (Overlay.governor ~drawdown:None ~dd_cap:None));
      t "governor: equity above its peak (negative drawdown) is one" (fun () ->
          Alcotest.check close "" 1.0
            (Overlay.governor ~drawdown:(Some (-0.01)) ~dd_cap:(Some 0.02)));
      t "governor fails closed on a number it cannot use" (fun () ->
          (* A known-but-unusable input is not "unknown": unknown is None and
             sizes freely (nothing to govern with), whereas NaN or inf says a
             figure exists and is garbage, and the governor's job is to stop
             sizing when the number it needs is unusable. Likewise a cap that
             is zero or negative cannot define a band. *)
          Alcotest.check close "nan drawdown" 0.0
            (Overlay.governor ~drawdown:(Some Float.nan) ~dd_cap:(Some 0.02));
          Alcotest.check close "inf drawdown" 0.0
            (Overlay.governor ~drawdown:(Some Float.infinity) ~dd_cap:(Some 0.02));
          Alcotest.check close "nan cap" 0.0
            (Overlay.governor ~drawdown:(Some 0.01) ~dd_cap:(Some Float.nan));
          Alcotest.check close "zero cap" 0.0
            (Overlay.governor ~drawdown:(Some 0.0) ~dd_cap:(Some 0.0)));
      (* --- effective --- *)
      t "effective multiplies the overlay by the governor" (fun () ->
          (* 0.7071 * 0.5 = 0.35355 *)
          Alcotest.check close "" 0.35355
            (Overlay.effective ~overlay:(Some 0.7071) ~fallback:0.5 ~governor:0.5));
      t "effective falls back when there is no overlay" (fun () ->
          (* 0.5 * 1 = 0.5 *)
          Alcotest.check close "" 0.5
            (Overlay.effective ~overlay:None ~fallback:0.5 ~governor:1.0));
      t "effective is clamped to [0, 1]" (fun () ->
          Alcotest.check close "above" 1.0
            (Overlay.effective ~overlay:(Some 1.5) ~fallback:0.5 ~governor:1.0);
          Alcotest.check close "below" 0.0
            (Overlay.effective ~overlay:(Some (-0.5)) ~fallback:0.5 ~governor:1.0));
      t "effective fails closed on a non-finite number" (fun () ->
          (* NaN * 1 = NaN, inf * 0 = NaN, inf * 1 = inf: none is a size. *)
          Alcotest.check close "nan overlay" 0.0
            (Overlay.effective ~overlay:(Some Float.nan) ~fallback:0.5 ~governor:1.0);
          Alcotest.check close "inf overlay, zero governor" 0.0
            (Overlay.effective ~overlay:(Some Float.infinity) ~fallback:0.5 ~governor:0.0);
          Alcotest.check close "nan fallback" 0.0
            (Overlay.effective ~overlay:None ~fallback:Float.nan ~governor:1.0);
          Alcotest.check close "nan governor" 0.0
            (Overlay.effective ~overlay:(Some 0.7) ~fallback:0.5 ~governor:Float.nan));
      (* --- the rules --- *)
      t "a good document is accepted" (fun () ->
          match judge (doc ()) with
          | Overlay.Accepted o -> Alcotest.check close "" 0.7071 o.exposure_scale
          | Overlay.Rejected (_, w) -> fail w);
      t "O1 schema_version" (fun () -> expect_rule `O1 (judge (doc ~version:2 ())));
      t "O2 future" (fun () ->
          (* as_of = now + 1 s *)
          expect_rule `O2 (judge (doc ~as_of:"2026-10-13T16:00:01Z" ())));
      t "O2 stale" (fun () ->
          (* 16:00:00 - 14:59:00 = 61 minutes > 60 *)
          let v = judge (doc ~as_of:"2026-10-13T14:59:00Z" ()) in
          expect_rule `O2 v;
          Alcotest.(check bool)
            "names 61 minutes" true
            (String.is_substring (why v) ~substring:"61 minutes"));
      t "O2 at exactly max age is accepted" (fun () ->
          (* 16:00 - 15:00 = 60 minutes <= 60 *)
          match judge (doc ~as_of:"2026-10-13T15:00:00Z" ()) with
          | Overlay.Accepted _ -> ()
          | Overlay.Rejected (_, w) -> fail w);
      t "O3 replay" (fun () ->
          expect_rule `O3 (judge ~last:7 (doc ~seq:7 ()));
          expect_rule `O3 (judge ~last:9 (doc ~seq:7 ())));
      t "O4 refuses infinity (1e999)" (fun () ->
          expect_rule `O4 (judge (doc ~scale:"1e999" ())));
      t "O4 refuses 1.2 and -0.1" (fun () ->
          expect_rule `O4 (judge (doc ~scale:"1.2" ()));
          expect_rule `O4 (judge (doc ~scale:"-0.1" ())));
      t "O4 accepts the closed ends 0 and 1" (fun () ->
          (* 0 >= 0 and 0 <= 1; 1 >= 0 and 1 <= 1: both bounds are inclusive,
             and integer spellings read as the same floats. *)
          List.iter [ "0"; "1"; "0.0"; "1.0" ] ~f:(fun s ->
              match judge (doc ~scale:s ()) with
              | Overlay.Accepted _ -> ()
              | Overlay.Rejected (_, w) -> fail w));
      t "the first failing rule wins" (fun () ->
          (* O1, O2, O3 and O4 all fail here; O1 is reported. *)
          expect_rule `O1
            (judge ~last:9 (doc ~version:2 ~as_of:"2026-10-13T16:00:01Z" ~scale:"2" ()));
          (* O2, O3, O4 fail: O2. *)
          expect_rule `O2
            (judge ~last:9 (doc ~as_of:"2026-10-13T16:00:01Z" ~scale:"2" ()));
          (* O3 and O4 fail: O3. *)
          expect_rule `O3 (judge ~last:9 (doc ~scale:"2" ())));
      (* --- parse --- *)
      t "parse keeps the document's pieces" (fun () ->
          let s = doc () in
          let o = parsed s in
          Alcotest.(check int) "seq" 7 o.sequence;
          Alcotest.(check string) "kind" "overlay" o.kind;
          Alcotest.(check string) "computed_at" "2026-10-13T15:30:05Z" o.computed_at;
          Alcotest.(check string) "raw" s o.raw;
          (* 15:30 is 30 minutes before the 16:00 clock *)
          Alcotest.(check bool) "as_of" true (Time_ns.equal o.as_of (at_minutes_ago 30.)));
      t "parse refuses kind signal" (fun () ->
          match Overlay.parse_string (doc ~kind:"signal" ()) with
          | Error _ -> ()
          | Ok _ -> fail "parsed a signal as an overlay");
      t "parse refuses a missing exposure_scale" (fun () ->
          match
            Overlay.parse_string
              {|{"schema_version":1,"kind":"overlay","as_of":"2026-10-13T15:30:00Z","computed_at":"x","sequence":7,"inputs":{},"reported":{}}|}
          with
          | Error _ -> ()
          | Ok _ -> fail "defaulted a missing exposure_scale");
      t "parse refuses a missing inputs" (fun () ->
          match
            Overlay.parse_string
              {|{"schema_version":1,"kind":"overlay","as_of":"2026-10-13T15:30:00Z","computed_at":"x","sequence":7,"exposure_scale":0.5,"reported":{}}|}
          with
          | Error e ->
              Alcotest.(check bool)
                "names inputs" true
                (String.is_substring e ~substring:"inputs")
          | Ok _ -> fail "defaulted a missing inputs");
      t "parse refuses a reported that is not an object" (fun () ->
          match
            Overlay.parse_string
              {|{"schema_version":1,"kind":"overlay","as_of":"2026-10-13T15:30:00Z","computed_at":"x","sequence":7,"exposure_scale":0.5,"inputs":{},"reported":"none"}|}
          with
          | Error e ->
              Alcotest.(check bool)
                "names reported" true
                (String.is_substring e ~substring:"reported")
          | Ok _ -> fail "accepted a string as reported");
      t "O1 refuses a record whose kind is not overlay" (fun () ->
          (* parse_string refuses such a kind first, so build the record by hand. *)
          let o = { (parsed (doc ())) with kind = "signal" } in
          expect_rule `O1 (Overlay.validate ~clock ~max_age_minutes:60 ~last_sequence:6 o));
      t "parse refuses an as_of that is not an instant" (fun () ->
          match Overlay.parse_string (doc ~as_of:"yesterday" ()) with
          | Error _ -> ()
          | Ok _ -> fail "accepted an unparseable as_of");
      t "an unparseable computed_at is informational" (fun () ->
          let s =
            {|{"schema_version":1,"kind":"overlay","as_of":"2026-10-13T15:30:00Z","computed_at":"later","sequence":7,"exposure_scale":0.5,"inputs":{},"reported":{}}|}
          in
          Alcotest.(check string) "kept" "later" (parsed s).computed_at);
      t "parse refuses text that is not JSON" (fun () ->
          match Overlay.parse_string "nan" with
          | Error _ -> ()
          | Ok _ -> fail "parsed non-JSON");
      (* --- state --- *)
      t "state Absent when nothing was received" (fun () ->
          Alcotest.check state_t "" Overlay.Absent
            (Overlay.state ~clock ~received_at:None ~max_age_minutes:60));
      t "state Fresh under a minute" (fun () ->
          Alcotest.check state_t "" Overlay.Fresh
            (Overlay.state ~clock
               ~received_at:(Some (Time_ns.sub now (Time_ns.Span.of_sec 59.)))
               ~max_age_minutes:60));
      t "state Held at one minute and at 30 minutes" (fun () ->
          Alcotest.check state_t "1m"
            (Overlay.Held (Time_ns.Span.of_min 1.))
            (Overlay.state ~clock
               ~received_at:(Some (at_minutes_ago 1.))
               ~max_age_minutes:60);
          Alcotest.check state_t "30m"
            (Overlay.Held (Time_ns.Span.of_min 30.))
            (Overlay.state ~clock
               ~received_at:(Some (at_minutes_ago 30.))
               ~max_age_minutes:60));
      t "state Held at exactly max age, Stale beyond" (fun () ->
          Alcotest.check state_t "60m"
            (Overlay.Held (Time_ns.Span.of_min 60.))
            (Overlay.state ~clock
               ~received_at:(Some (at_minutes_ago 60.))
               ~max_age_minutes:60);
          Alcotest.check state_t "61m" Overlay.Stale
            (Overlay.state ~clock
               ~received_at:(Some (at_minutes_ago 61.))
               ~max_age_minutes:60));
      t "state: a receipt from the future is Stale, not Fresh" (fun () ->
          Alcotest.check state_t "" Overlay.Stale
            (Overlay.state ~clock
               ~received_at:(Some (Time_ns.add now (Time_ns.Span.of_sec 5.)))
               ~max_age_minutes:60));
    ] )
