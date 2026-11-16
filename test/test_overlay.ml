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
let hours = Time_ns.Span.of_hr

(* [Overlay.effective] with the book's default max age, 4 session hours, and
   an age of zero unless a case gives one. *)
let effective ?(age = Some Time_ns.Span.zero) ~overlay ~fallback ~governor () =
  Overlay.effective ~overlay ~age ~max_age:(hours 4.0) ~fallback ~governor

let ny = Timezone.find_exn "America/New_York"

let ny_at s =
  Time_ns.of_date_ofday ~zone:ny
    (Date.of_string (String.prefix s 10))
    (Time_ns.Ofday.of_string (String.drop_prefix s 11))

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
          (* A known-but-unusable input gives 0: NaN or inf says a figure
             exists and is garbage, and the governor's job is to stop sizing
             when the number it needs is unusable. Likewise a cap that is zero
             or negative cannot define a band. (A drawdown the desk cannot
             read at all is not asked of this function: Intake.reading
             governs it with [unread_governor], at the fallback.) *)
          Alcotest.check close "nan drawdown" 0.0
            (Overlay.governor ~drawdown:(Some Float.nan) ~dd_cap:(Some 0.02));
          Alcotest.check close "inf drawdown" 0.0
            (Overlay.governor ~drawdown:(Some Float.infinity) ~dd_cap:(Some 0.02));
          Alcotest.check close "nan cap" 0.0
            (Overlay.governor ~drawdown:(Some 0.01) ~dd_cap:(Some Float.nan));
          Alcotest.check close "zero cap" 0.0
            (Overlay.governor ~drawdown:(Some 0.0) ~dd_cap:(Some 0.0)));
      t "a cut book earns its size back once its peak leaves the window" (fun () ->
          (* Task 1.4, the brief's path (test_risk_metrics.ml derives its
             drawdowns): close 1 at 100, then 92 for 251 closes. With cap
             0.10 the band is 0.05 to 0.10.
               close 250: dd 0.08 -> (0.10 - 0.08) / 0.05 = 0.4
               close 252: dd 0.0  -> 1.0, full size again
             From the all-time peak the drawdown stays 0.08 and the governor
             0.4 for as long as the book holds 92 -- it could never earn its
             size back by holding still, only by making a new high. *)
          let path = Array.append [| 100.0 |] (Array.create ~len:251 92.0) in
          let governor_at k =
            Overlay.governor
              ~drawdown:
                (Some
                   (Ohcamel.Risk_metrics.drawdown ~window:250
                      (Array.sub path ~pos:0 ~len:k)))
              ~dd_cap:(Some 0.10)
          in
          Alcotest.check close "close 250: cut to 0.4" 0.4 (governor_at 250);
          Alcotest.check close "close 252: back to 1.0" 1.0 (governor_at 252);
          Alcotest.check close "from the all-time peak it stays cut" 0.4
            (Overlay.governor
               ~drawdown:(Some (Ohcamel.Risk_metrics.current_drawdown ~equity:path))
               ~dd_cap:(Some 0.10)));
      (* --- effective --- *)
      t "effective multiplies the overlay by the governor" (fun () ->
          (* 0.7071 * 0.5 = 0.35355 *)
          Alcotest.check close "" 0.35355
            (effective ~overlay:(Some 0.7071) ~fallback:0.5 ~governor:0.5 ()));
      t "effective falls back when there is no overlay" (fun () ->
          (* 0.5 * 1 = 0.5 *)
          Alcotest.check close "" 0.5
            (effective ~overlay:None ~fallback:0.5 ~governor:1.0 ()));
      t "effective is clamped to [0, 1]" (fun () ->
          Alcotest.check close "above" 1.0
            (effective ~overlay:(Some 1.5) ~fallback:0.5 ~governor:1.0 ());
          Alcotest.check close "below" 0.0
            (effective ~overlay:(Some (-0.5)) ~fallback:0.5 ~governor:1.0 ()));
      t "effective fails closed on a non-finite number" (fun () ->
          (* NaN * 1 = NaN, inf * 0 = NaN, inf * 1 = inf: none is a size. *)
          Alcotest.check close "nan overlay" 0.0
            (effective ~overlay:(Some Float.nan) ~fallback:0.5 ~governor:1.0 ());
          Alcotest.check close "inf overlay, zero governor" 0.0
            (effective ~overlay:(Some Float.infinity) ~fallback:0.5 ~governor:0.0 ());
          Alcotest.check close "nan fallback" 0.0
            (effective ~overlay:None ~fallback:Float.nan ~governor:1.0 ());
          Alcotest.check close "nan governor" 0.0
            (effective ~overlay:(Some 0.7) ~fallback:0.5 ~governor:Float.nan ()));
      (* --- the overlay's age, in session hours (Task 1.5); max age 4 h --- *)
      t "a 5-hour-old overlay of 0.9 with fallback 0.5 sizes at 0.5" (fun () ->
          Alcotest.check close "" 0.5
            (effective ~overlay:(Some 0.9)
               ~age:(Some (hours 5.0))
               ~fallback:0.5 ~governor:1.0 ()));
      t "a 3-hour-old one sizes at 0.9" (fun () ->
          Alcotest.check close "" 0.9
            (effective ~overlay:(Some 0.9)
               ~age:(Some (hours 3.0))
               ~fallback:0.5 ~governor:1.0 ()));
      t "exactly the max age still stands; a minute past it does not" (fun () ->
          Alcotest.check close "4 h" 0.9
            (effective ~overlay:(Some 0.9)
               ~age:(Some (hours 4.0))
               ~fallback:0.5 ~governor:1.0 ());
          Alcotest.check close "4 h 1 min" 0.5
            (effective ~overlay:(Some 0.9)
               ~age:(Some Time_ns.Span.(hours 4.0 + of_min 1.0))
               ~fallback:0.5 ~governor:1.0 ()));
      t "an overlay of unknown age sizes at the fallback" (fun () ->
          (* Unknown is not permission: 0.9 of no known age is 0.5 x 0.8. *)
          Alcotest.check close "" 0.4
            (effective ~overlay:(Some 0.9) ~age:None ~fallback:0.5 ~governor:0.8 ()));
      t "an age that runs backwards is no age" (fun () ->
          Alcotest.check close "" 0.5
            (effective ~overlay:(Some 0.9)
               ~age:(Some (Time_ns.Span.neg (hours 1.0)))
               ~fallback:0.5 ~governor:1.0 ()));
      t "an old overlay below the fallback is never raised to it" (fun () ->
          (* min(0.2, 0.5) = 0.2: the fallback is a ceiling on what a dead
             overlay may size at, never a floor that buys. *)
          Alcotest.check close "" 0.2
            (effective ~overlay:(Some 0.2)
               ~age:(Some (hours 5.0))
               ~fallback:0.5 ~governor:1.0 ());
          Alcotest.check close "factor" 0.2
            (Overlay.factor ~overlay:(Some 0.2) ~age:None ~max_age:(hours 4.0)
               ~fallback:0.5));
      t "an unread drawdown governs at the fallback, never above the last read" (fun () ->
          Alcotest.check close "none read yet: the fallback" 0.5
            (Overlay.unread_governor ~fallback:0.5 ~last:None);
          Alcotest.check close "last 1.0: the fallback" 0.5
            (Overlay.unread_governor ~fallback:0.5 ~last:(Some 1.0));
          Alcotest.check close "last 0.3: 0.3" 0.3
            (Overlay.unread_governor ~fallback:0.5 ~last:(Some 0.3));
          Alcotest.check close "last 0 (at the cap): 0" 0.0
            (Overlay.unread_governor ~fallback:0.5 ~last:(Some 0.0));
          Alcotest.check close "a nan fallback: 0" 0.0
            (Overlay.unread_governor ~fallback:Float.nan ~last:None));
      (* --- session hours: 09:30-16:00 New York, weekdays --- *)
      t "session hours count only the regular session" (fun () ->
          let span a b =
            Ohcamel_desk.Desk_time.session_span ~zone:ny ~from:(ny_at a) ~until:(ny_at b)
              ()
          in
          let hrs = Alcotest.float 1e-9 in
          Alcotest.check hrs "Mon 09:00 to 18:00: the whole session, 6.5 h" 6.5
            (Time_ns.Span.to_hr (span "2026-11-16 09:00" "2026-11-16 18:00"));
          Alcotest.check hrs "Fri 15:50 to Mon 09:45: 10 + 15 minutes" (25.0 /. 60.0)
            (Time_ns.Span.to_hr (span "2026-11-13 15:50" "2026-11-16 09:45"));
          Alcotest.check hrs "Fri 16:00 to Mon 09:30: none" 0.0
            (Time_ns.Span.to_hr (span "2026-11-13 16:00" "2026-11-16 09:30"));
          Alcotest.check hrs "Mon 11:00 to Tue 11:00: 5 + 1.5" 6.5
            (Time_ns.Span.to_hr (span "2026-11-16 11:00" "2026-11-17 11:00"));
          Alcotest.check hrs "backwards: none" 0.0
            (Time_ns.Span.to_hr (span "2026-11-16 12:00" "2026-11-16 11:00"));
          (* A summer day, on the same wall clock: the zone, not an offset. *)
          Alcotest.check hrs "Mon 10:00 to 12:00 in July" 2.0
            (Time_ns.Span.to_hr (span "2026-07-13 10:00" "2026-07-13 12:00")));
      t "a Friday-close overlay is not stale at Monday's open" (fun () ->
          (* Received 15:50 Friday; at 09:45 Monday it is 66 wall-clock hours
             old and 25 session minutes old: it stands. At 13:45 Monday it is
             4 h 10 min old in session: the fallback. *)
          let age now =
            Some
              (Ohcamel_desk.Desk_time.session_span ~zone:ny
                 ~from:(ny_at "2026-11-13 15:50") ~until:(ny_at now) ())
          in
          Alcotest.check close "Monday 09:45" 0.9
            (effective ~overlay:(Some 0.9) ~age:(age "2026-11-16 09:45") ~fallback:0.5
               ~governor:1.0 ());
          Alcotest.check close "Monday 13:45" 0.5
            (effective ~overlay:(Some 0.9) ~age:(age "2026-11-16 13:45") ~fallback:0.5
               ~governor:1.0 ()));
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
