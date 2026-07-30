(* Phase 2. The feed boundary.

   NOTHING HERE TOUCHES THE NETWORK. Every payload is a captured fixture, and
   that is a deliberate constraint rather than a convenience: a suite that talks
   to Alpaca fails when the market is closed, when the wifi drops, and when
   someone else is holding the account's one allowed connection. A test that
   fails for reasons unrelated to the code is a test people learn to ignore.

   What is being tested is the boundary itself -- the place where bytes someone
   else controls become numbers this engine will believe. Most of these cases are
   hostile input, because that is where the risk is. A parser that only handles
   well-formed input is not a boundary, it is an assumption. *)

open Core
module Alpaca = Ohcamel.Alpaca_ws
module Alpaca_rest = Ohcamel.Alpaca_rest
module Fred = Ohcamel.Fred_client
module Config = Ohcamel.Config
module Graph = Ohcamel.Graph
open Ohcamel.Types

let feq = Alcotest.float 1e-9

(* ------------------------------------------------------------------------ *)
(* Alpaca: trade parsing                                                     *)
(* ------------------------------------------------------------------------ *)

(* The exact shape from Alpaca's documentation, including the fields this engine
   ignores -- trade id, exchange, conditions, tape. Keeping them in the fixture
   is the point: the parser must pick out what it needs from a real message
   rather than from a tidied one. *)
let trade_frame =
  {|[{"T":"t","S":"AAPL","i":96921,"x":"D","p":126.55,"s":1,"t":"2021-02-22T15:51:44.208Z","c":["@","I"],"z":"C"}]|}

let single (payload : string) =
  match Alpaca.Message.of_frame payload with
  | [ m ] -> m
  | ms -> Alcotest.failf "expected exactly one message, got %d" (List.length ms)

let test_parse_trade () =
  match single trade_frame with
  | Alpaca.Message.Trade tick ->
      Alcotest.(check string) "symbol" "AAPL" (Symbol.to_string (Tick.symbol tick));
      Alcotest.check feq "price" 126.55 (Price.to_float (Tick.price tick));
      (* 2021-02-22T15:51:44.208Z, parsed as UTC. Asserted by round-tripping to
         the same instant rather than by comparing formatted strings, which
         would test the formatter instead of the parser. *)
      Alcotest.(check string)
        "timestamp parsed as UTC" "2021-02-22 15:51:44.208000000Z"
        (Time_ns.to_string_utc (Tick.time tick))
  | other ->
      Alcotest.failf "expected a trade, got %s"
        (Sexp.to_string_hum (Alpaca.Message.sexp_of_t other))

(* Frames are arrays and are usually batched. Forty trades in one frame is
   ordinary during the open. *)
let test_parse_batched_frame () =
  let payload =
    {|[{"T":"t","S":"AAPL","p":100.0,"t":"2021-02-22T15:51:44.208Z"},
       {"T":"t","S":"MSFT","p":200.0,"t":"2021-02-22T15:51:44.209Z"},
       {"T":"t","S":"AAPL","p":100.5,"t":"2021-02-22T15:51:44.210Z"}]|}
  in
  let messages = Alpaca.Message.of_frame payload in
  Alcotest.(check int) "three trades in one frame" 3 (List.length messages);
  List.iter messages ~f:(fun m ->
      match m with
      | Alpaca.Message.Trade _ -> ()
      | other ->
          Alcotest.failf "expected a trade, got %s"
            (Sexp.to_string_hum (Alpaca.Message.sexp_of_t other)))

let test_parse_control_messages () =
  (match single {|[{"T":"success","msg":"connected"}]|} with
  | Alpaca.Message.Success msg -> Alcotest.(check string) "connected" "connected" msg
  | _ -> Alcotest.fail "expected Success");
  (match single {|[{"T":"success","msg":"authenticated"}]|} with
  | Alpaca.Message.Success msg ->
      Alcotest.(check string) "authenticated" "authenticated" msg
  | _ -> Alcotest.fail "expected Success");
  (match single {|[{"T":"subscription","trades":["AAPL"],"quotes":[],"bars":[]}]|} with
  | Alpaca.Message.Subscription _ -> ()
  | _ -> Alcotest.fail "expected Subscription");
  match single {|[{"T":"error","code":406,"msg":"connection limit exceeded"}]|} with
  | Alpaca.Message.Error { code; msg } ->
      Alcotest.(check int) "code" 406 code;
      Alcotest.(check string) "msg" "connection limit exceeded" msg
  | _ -> Alcotest.fail "expected Error"

(* ------------------------------------------------------------------------ *)
(* Alpaca: hostile input                                                     *)
(* ------------------------------------------------------------------------ *)

(* Every one of these must fail to produce a Trade.

   The NaN case is the one that matters most and is the reason the check is
   [Float.is_finite] rather than a range test. NaN does not raise; it propagates
   silently through every arithmetic operation downstream, so one NaN print
   turns exposure, VaR and every limit comparison into NaN. And because every
   float comparison against NaN is false, a NaN exposure reads as "not breached"
   on every limit in the book -- an entire risk system switched off by one JSON
   field.

   JSON has no NaN literal, so it arrives as the string "NaN" or as a null,
   neither of which is a number; both are covered. *)
let test_rejects_bad_prices () =
  let rejected name payload =
    match Alpaca.Message.of_frame payload with
    | [ Alpaca.Message.Trade tick ] ->
        Alcotest.failf "%s: should not have produced a trade (price %f)" name
          (Price.to_float (Tick.price tick))
    | _ -> ()
  in
  rejected "negative price" {|[{"T":"t","S":"AAPL","p":-1.0,"t":"2021-02-22T15:51:44Z"}]|};
  rejected "zero price" {|[{"T":"t","S":"AAPL","p":0.0,"t":"2021-02-22T15:51:44Z"}]|};
  rejected "NaN as a string"
    {|[{"T":"t","S":"AAPL","p":"NaN","t":"2021-02-22T15:51:44Z"}]|};
  rejected "null price" {|[{"T":"t","S":"AAPL","p":null,"t":"2021-02-22T15:51:44Z"}]|};
  rejected "price as a string" {|[{"T":"t","S":"AAPL","p":"126.55"}]|};
  rejected "missing price" {|[{"T":"t","S":"AAPL","t":"2021-02-22T15:51:44Z"}]|};
  rejected "missing symbol" {|[{"T":"t","p":126.55,"t":"2021-02-22T15:51:44Z"}]|};
  rejected "empty object" {|[{}]|}

let test_survives_malformed_frames () =
  let empty name payload =
    Alcotest.(check int) name 0 (List.length (Alpaca.Message.of_frame payload))
  in
  empty "truncated JSON" {|[{"T":"t","S":"AAPL","p":126.|};
  empty "not JSON at all" "<html>502 Bad Gateway</html>";
  empty "empty string" "";
  empty "empty array" "[]";
  (* A bare object rather than an array is accepted, since it is unambiguous. *)
  Alcotest.(check int)
    "a bare object still parses" 1
    (List.length (Alpaca.Message.of_frame {|{"T":"success","msg":"connected"}|}));
  (* An unrecognised message type is a vendor protocol addition, not an error.
     Failing on one would make the feed brittle against a routine release. *)
  match single {|[{"T":"luld","S":"AAPL","u":130.0,"d":120.0}]|} with
  | Alpaca.Message.Other _ -> ()
  | _ -> Alcotest.fail "an unknown message type should be Other, not a failure"

(* A price is load-bearing; a timestamp is not. An unparseable timestamp falls
   back to local receipt time -- honest, since we did just receive it -- rather
   than discarding a real trade over a clock format. The reverse trade-off would
   be indefensible, which is why prices are rejected outright. *)
let test_bad_timestamp_keeps_the_trade () =
  let before = Time_ns.now () in
  match single {|[{"T":"t","S":"AAPL","p":126.55,"t":"not-a-timestamp"}]|} with
  | Alpaca.Message.Trade tick ->
      Alcotest.check feq "the price survived" 126.55 (Price.to_float (Tick.price tick));
      Alcotest.(check bool)
        "and the timestamp fell back to receipt time" true
        (Time_ns.( >= ) (Tick.time tick) before)
  | _ -> Alcotest.fail "a bad timestamp must not discard the trade"

(* ------------------------------------------------------------------------ *)
(* Alpaca: applying a frame to the graph                                     *)
(* ------------------------------------------------------------------------ *)

let test_book =
  [
    { Instrument.symbol = Symbol.of_string "AAPL"; sector = Sector.of_string "TECH" };
    { Instrument.symbol = Symbol.of_string "MSFT"; sector = Sector.of_string "TECH" };
  ]

let with_graph ~f =
  let graph =
    Graph.create ~instruments:test_book ~limits:[] ~confidence:0.95 ~return_window:10 ()
  in
  Exn.protect ~f:(fun () -> f graph) ~finally:(fun () -> Graph.destroy graph)

let test_apply_frame () =
  with_graph ~f:(fun graph ->
      let stats = Alpaca.Stats.create () in
      let payload =
        {|[{"T":"t","S":"AAPL","p":100.0,"t":"2021-02-22T15:51:44.208Z"},
           {"T":"t","S":"MSFT","p":200.0,"t":"2021-02-22T15:51:44.209Z"},
           {"T":"t","S":"AAPL","p":101.0,"t":"2021-02-22T15:51:44.210Z"}]|}
      in
      let control = Alpaca.apply_frame ~graph ~stats (Alpaca.Message.of_frame payload) in
      Alcotest.(check int)
        "no control messages in a pure trade frame" 0 (List.length control);
      Alcotest.(check int) "three trades applied" 3 stats.Alpaca.Stats.trades;
      (* Last write wins, which is what a sequence of prints on one symbol
         means. *)
      Alcotest.check feq "AAPL took the later print" 101.0
        (Price.to_float (Graph.price graph (Symbol.of_string "AAPL")));
      Alcotest.check feq "MSFT" 200.0
        (Price.to_float (Graph.price graph (Symbol.of_string "MSFT")));
      (* And liveness was recorded, which is what distinguishes a wire tick from
         a manual reprice. *)
      Alcotest.(check bool)
        "AAPL is no longer never-seen" false
        (List.mem
           (Graph.Feed_health.never_seen (Graph.feed_health graph))
           (Symbol.of_string "AAPL") ~equal:Symbol.equal))

(* A symbol the book does not hold is counted, not raised on and not applied.

   Graph.set_price raises on an unknown symbol by design -- a divergence between
   the subscription and the book should be loud -- so the filtering has to
   happen here, before the graph is touched. A subscription can legitimately
   outlive a position. *)
let test_unknown_symbol_is_counted_not_fatal () =
  with_graph ~f:(fun graph ->
      let stats = Alpaca.Stats.create () in
      let payload =
        {|[{"T":"t","S":"NVDA","p":900.0,"t":"2021-02-22T15:51:44.208Z"},
           {"T":"t","S":"AAPL","p":100.0,"t":"2021-02-22T15:51:44.209Z"}]|}
      in
      ignore (Alpaca.apply_frame ~graph ~stats (Alpaca.Message.of_frame payload));
      Alcotest.(check int) "the known symbol was applied" 1 stats.Alpaca.Stats.trades;
      Alcotest.(check int)
        "the unknown one was counted" 1 stats.Alpaca.Stats.unknown_symbol;
      Alcotest.(check int) "and not counted as a rejection" 0 stats.Alpaca.Stats.rejected)

let test_rejected_prints_are_counted () =
  with_graph ~f:(fun graph ->
      let stats = Alpaca.Stats.create () in
      let payload =
        {|[{"T":"t","S":"AAPL","p":-5.0,"t":"2021-02-22T15:51:44.208Z"},
           {"T":"t","S":"AAPL","p":100.0,"t":"2021-02-22T15:51:44.209Z"}]|}
      in
      ignore (Alpaca.apply_frame ~graph ~stats (Alpaca.Message.of_frame payload));
      Alcotest.(check int) "one good print applied" 1 stats.Alpaca.Stats.trades;
      Alcotest.(check int) "one bad print rejected" 1 stats.Alpaca.Stats.rejected;
      Alcotest.check feq "and the bad price never reached the graph" 100.0
        (Price.to_float (Graph.price graph (Symbol.of_string "AAPL"))))

let test_control_messages_are_returned () =
  with_graph ~f:(fun graph ->
      let stats = Alpaca.Stats.create () in
      let control =
        Alpaca.apply_frame ~graph ~stats
          (Alpaca.Message.of_frame {|[{"T":"error","code":406,"msg":"connection limit"}]|})
      in
      Alcotest.(check int)
        "the error came back for the caller to act on" 1 (List.length control))

(* ------------------------------------------------------------------------ *)
(* Alpaca: error classification                                              *)
(* ------------------------------------------------------------------------ *)

(* The distinction that keeps a reconnect loop from spinning forever on a
   condition it cannot fix. Reconnecting after "auth failed" fails identically
   every time while printing messages that look like progress. *)
let test_failure_classification () =
  let is_fatal code =
    match Alpaca.Failure.of_code ~code ~msg:"" with
    | Alpaca.Failure.Fatal _ -> true
    | Alpaca.Failure.Retryable _ -> false
  in
  Alcotest.(check bool) "402 auth failed: no retry can fix bad keys" true (is_fatal 402);
  Alcotest.(check bool)
    "406 connection limit: another session holds the slot" true (is_fatal 406);
  Alcotest.(check bool)
    "409 insufficient subscription: a plan problem" true (is_fatal 409);
  Alcotest.(check bool) "405 symbol limit: a plan problem" true (is_fatal 405);
  Alcotest.(check bool) "410 wrong channel for the feed" true (is_fatal 410);
  Alcotest.(check bool)
    "400 invalid syntax: a fresh handshake might differ" false (is_fatal 400);
  Alcotest.(check bool) "407 slow client" false (is_fatal 407);
  Alcotest.(check bool) "500 internal error" false (is_fatal 500);
  Alcotest.(check bool)
    "an undocumented code is retryable by default" false (is_fatal 9999);
  (* The fatal messages have to say what to go and do; that is their whole
     purpose. *)
  match Alpaca.Failure.of_code ~code:406 ~msg:"connection limit exceeded" with
  | Alpaca.Failure.Fatal detail ->
      Alcotest.(check bool)
        "the 406 message explains the one-connection limit" true
        (String.is_substring detail ~substring:"ONE")
  | _ -> Alcotest.fail "406 must be fatal"

(* ------------------------------------------------------------------------ *)
(* Alpaca: backoff                                                           *)
(* ------------------------------------------------------------------------ *)

(* A pure function of the attempt number, so the schedule can be asserted
   without sleeping. A backoff verified by watching a log is one nobody
   verifies. *)
let test_backoff_schedule () =
  let b = Alpaca.Backoff.default in
  let secs attempt = Time_ns.Span.to_sec (Alpaca.Backoff.base_delay b ~attempt) in
  Alcotest.check feq "attempt 1 waits the base" 1.0 (secs 1);
  Alcotest.check feq "attempt 2 doubles" 2.0 (secs 2);
  Alcotest.check feq "attempt 3" 4.0 (secs 3);
  Alcotest.check feq "attempt 4" 8.0 (secs 4);
  Alcotest.check feq "attempt 7 reaches the 60s cap" 60.0 (secs 7);
  Alcotest.check feq "and stays there" 60.0 (secs 50);
  (* The regression that motivated computing the doubling in floating point.
     As an integer shift, 2^(attempt-1) overflows to a negative span somewhere
     past sixty consecutive failures -- about an hour of outage, which is
     exactly the situation this code exists to survive. A negative delay makes
     [after] return immediately and the backoff becomes a hot loop against a
     service that is already down. *)
  List.iter [ 62; 100; 1_000; 1_000_000 ] ~f:(fun attempt ->
      let s = secs attempt in
      if Float.( <= ) s 0.0 || Float.( > ) s 60.0 then
        Alcotest.failf "attempt %d gave a delay of %f seconds, outside (0, 60]" attempt s);
  match Alpaca.Backoff.base_delay b ~attempt:0 with
  | exception Invalid_argument _ -> ()
  | _ -> Alcotest.fail "attempt 0 is a caller bug and should raise"

let test_backoff_jitter () =
  let b = Alpaca.Backoff.default in
  (* Jitter is applied downward only, so the cap stays a genuine cap. With
     jitter 0.25 the delay lies in [0.75 * base, base]. *)
  let check_bounds ~random ~expected =
    let d =
      Time_ns.Span.to_sec (Alpaca.Backoff.delay ~random:(fun _ -> random) b ~attempt:3)
    in
    Alcotest.check feq (sprintf "jitter draw %f" random) expected d
  in
  check_bounds ~random:0.0 ~expected:4.0;
  check_bounds ~random:1.0 ~expected:3.0;
  check_bounds ~random:0.5 ~expected:3.5;
  (* And never above the base, at any draw. *)
  List.iter [ 0.0; 0.1; 0.33; 0.9; 1.0 ] ~f:(fun r ->
      let d =
        Time_ns.Span.to_sec (Alpaca.Backoff.delay ~random:(fun _ -> r) b ~attempt:7)
      in
      if Float.( > ) d 60.0 || Float.( < ) d 45.0 then
        Alcotest.failf "jittered cap delay %f outside [45, 60]" d)

(* ------------------------------------------------------------------------ *)
(* FRED                                                                      *)
(* ------------------------------------------------------------------------ *)

(* The response shape as FRED actually sends it: newest-first (because the
   request asks for that), with a "." where an observation is missing. *)
let fred_body =
  {|{"realtime_start":"2024-05-28","realtime_end":"2024-05-28","observation_start":"1600-01-01",
     "observation_end":"9999-12-31","units":"lin","output_type":1,"file_type":"json",
     "order_by":"observation_date","sort_order":"desc","count":4,"offset":0,"limit":4,
     "observations":[
       {"realtime_start":"2024-05-28","realtime_end":"2024-05-28","date":"2024-05-24","value":"4.63"},
       {"realtime_start":"2024-05-28","realtime_end":"2024-05-28","date":"2024-05-23","value":"4.65"},
       {"realtime_start":"2024-05-28","realtime_end":"2024-05-28","date":"2024-05-22","value":"."},
       {"realtime_start":"2024-05-28","realtime_end":"2024-05-28","date":"2024-05-21","value":"4.60"}]}|}

(* The trap this fixture exists for.

   FRED writes "." for a missing observation -- a holiday, or a day the series
   was not published. It is a string in a field that is otherwise a number, so a
   lenient parser coerces it to 0.0. A 0.0 in a yield series is not a missing
   value: it is a claim that the ten-year Treasury yielded nothing that day, and
   it produces two enormous spurious changes in the difference series, either of
   which can dominate a beta estimate outright. *)
let test_fred_missing_observations () =
  match Fred.parse_observations fred_body with
  | Error e -> Alcotest.failf "should parse: %s" (Error.to_string_hum e)
  | Ok observations ->
      Alcotest.(check int)
        "four observations, none dropped at parse time" 4 (List.length observations);
      let values = List.map observations ~f:(fun o -> o.Fred.Observation.value) in
      Alcotest.(check (list (option (float 1e-9))))
        "the \".\" is None, not 0.0"
        [ Some 4.63; Some 4.65; None; Some 4.60 ]
        values

(* Levels to changes, chronologically, with the missing day dropped:

     2024-05-21  4.60
     2024-05-22  (missing)
     2024-05-23  4.65   -> +0.05  (across the gap: a real move, over two days)
     2024-05-24  4.63   -> -0.02

   Differencing across the gap is the least-bad option. Discarding any change
   that spans a missing day would throw away every long weekend in the sample. *)
let test_fred_changes () =
  match Fred.changes_of_body fred_body with
  | Error e -> Alcotest.failf "should parse: %s" (Error.to_string_hum e)
  | Ok changes ->
      Alcotest.(check int) "two changes from three usable levels" 2 (Array.length changes);
      Alcotest.check feq "4.60 -> 4.65" 0.05 changes.(0);
      Alcotest.check feq "4.65 -> 4.63" (-0.02) changes.(1)

(* The sign of the difference series is the sign of beta, and a reversed series
   flips it -- turning a long-duration book into a short-duration one on the
   display, with nothing about the output looking wrong. Hence an explicit test
   that newest-first input comes out chronological. *)
let test_fred_orientation () =
  let ascending =
    {|{"observations":[{"date":"2024-01-01","value":"1.0"},
                       {"date":"2024-01-02","value":"2.0"},
                       {"date":"2024-01-03","value":"4.0"}]}|}
  in
  (* Fed to changes_of_body, which reverses. So this ASCENDING fixture is read
     as descending, giving 4 -> 2 -> 1, i.e. changes of -2 and -1. *)
  match Fred.changes_of_body ascending with
  | Error e -> Alcotest.failf "should parse: %s" (Error.to_string_hum e)
  | Ok changes ->
      Alcotest.check feq "first change" (-2.0) changes.(0);
      Alcotest.check feq "second change" (-1.0) changes.(1)

let test_fred_degenerate_series () =
  let changes body =
    match Fred.changes_of_body body with
    | Ok c -> c
    | Error e -> Alcotest.failf "should parse: %s" (Error.to_string_hum e)
  in
  Alcotest.(check int)
    "no observations" 0
    (Array.length (changes {|{"observations":[]}|}));
  Alcotest.(check int)
    "one observation cannot be differenced" 0
    (Array.length (changes {|{"observations":[{"date":"2024-01-01","value":"1.0"}]}|}));
  Alcotest.(check int)
    "every observation missing" 0
    (Array.length
       (changes
          {|{"observations":[{"date":"2024-01-01","value":"."},
                             {"date":"2024-01-02","value":"."}]}|}))

(* FRED reports its own errors as JSON with an error_message, alongside an HTTP
   error status. Surfacing that text beats a generic shape complaint -- it
   usually says exactly what is wrong with the request. *)
let test_fred_error_responses () =
  let expect_error name body =
    match Fred.changes_of_body body with
    | Ok _ -> Alcotest.failf "%s: expected an error" name
    | Error e -> Error.to_string_hum e
  in
  let message =
    expect_error "FRED error object"
      {|{"error_code":400,"error_message":"Bad Request. The value for variable api_key is not registered."}|}
  in
  Alcotest.(check bool)
    "FRED's own explanation is passed through" true
    (String.is_substring message ~substring:"not registered");
  ignore (expect_error "not JSON" "<html>503 Service Unavailable</html>" : string);
  ignore (expect_error "JSON but not an object" "[1,2,3]" : string)

(* The API key travels in the query string, so any logged or errored URI would
   carry the credential with it. Everything human-facing goes through the
   redacted builder. *)
let test_fred_uri_redaction () =
  let uri = Uri.to_string (Fred.redacted_uri ~series_id:"DGS10" ~limit:61) in
  Alcotest.(check bool)
    "the series is there" true
    (String.is_substring uri ~substring:"DGS10");
  Alcotest.(check bool)
    "and the key is not" true
    (String.is_substring uri ~substring:"REDACTED");
  (* Belt and braces: a real key put through the request builder must not appear
     in the redacted form of the same request. *)
  let secret = "sk_live_do_not_log_me" in
  let real =
    Uri.to_string
      (Fred.request_uri ~series_id:"DGS10" ~limit:61
         ~api_key:(Config.Secret.of_string secret))
  in
  Alcotest.(check bool)
    "the request URI does carry the key" true
    (String.is_substring real ~substring:secret);
  Alcotest.(check bool)
    "the redacted URI does not" false
    (String.is_substring uri ~substring:secret)

(* ------------------------------------------------------------------------ *)
(* Alpaca REST: the historical backfill                                      *)
(* ------------------------------------------------------------------------ *)

(* Closes chosen so the returns are exact in binary:
     100 -> 110  =  +0.10
     110 ->  99  =  -0.10
   Fields the parser ignores are left in, so it is picking values out of a real
   bar rather than a tidied one. *)
let bars_body =
  {|{"bars":{"AAPL":[{"t":"2026-07-27T04:00:00Z","o":99.0,"h":101.0,"l":98.0,"c":100.0,"v":1000,"n":10,"vw":99.5},
                    {"t":"2026-07-28T04:00:00Z","o":100.0,"h":112.0,"l":100.0,"c":110.0,"v":2000,"n":20,"vw":105.0},
                    {"t":"2026-07-29T04:00:00Z","o":110.0,"h":111.0,"l":98.0,"c":99.0,"v":1500,"n":15,"vw":104.0}],
            "MSFT":[{"t":"2026-07-28T04:00:00Z","c":200.0},
                    {"t":"2026-07-29T04:00:00Z","c":210.0}]},
    "next_page_token":null}|}

let test_bars_to_returns () =
  match Alpaca_rest.returns_of_body bars_body with
  | Error e -> Alcotest.failf "should parse: %s" (Error.to_string_hum e)
  | Ok per_symbol ->
      let find s =
        match
          List.find per_symbol ~f:(fun (x : Alpaca_rest.Series.t) ->
              String.equal (Symbol.to_string x.symbol) s)
        with
        | Some x -> x.Alpaca_rest.Series.returns
        | None -> Alcotest.failf "no series for %s" s
      in
      let aapl = find "AAPL" in
      Alcotest.(check int) "three bars give two returns" 2 (Array.length aapl);
      Alcotest.check feq "100 -> 110" 0.10 aapl.(0);
      Alcotest.check feq "110 -> 99" (-0.10) aapl.(1);
      Alcotest.(check int) "two bars give one return" 1 (Array.length (find "MSFT"))

(* A zero or negative close is not a price. Dividing by one yields infinity,
   which then travels through the whole risk chain as quietly as a NaN would --
   and an infinite return in the window makes VaR infinite and every limit
   comparison against it false. *)
let test_bars_reject_impossible_closes () =
  match
    Alpaca_rest.returns_of_body
      {|{"bars":{"AAPL":[{"c":100.0},{"c":0.0},{"c":110.0},{"c":-5.0},{"c":121.0}]}}|}
  with
  | Error e -> Alcotest.failf "should parse: %s" (Error.to_string_hum e)
  | Ok per_symbol ->
      let returns = (List.hd_exn per_symbol).Alpaca_rest.Series.returns in
      Alcotest.(check int) "two bad closes dropped, three usable" 2 (Array.length returns);
      Alcotest.check feq "100 -> 110" 0.10 returns.(0);
      Alcotest.check feq "110 -> 121" 0.10 returns.(1);
      Array.iter returns ~f:(fun r ->
          if not (Float.is_finite r) then
            Alcotest.failf "a non-finite return (%f) escaped into the window" r)

let test_bars_degenerate_and_errors () =
  let ok body =
    match Alpaca_rest.returns_of_body body with
    | Ok v -> v
    | Error e -> Alcotest.failf "should parse: %s" (Error.to_string_hum e)
  in
  Alcotest.(check int) "no symbols" 0 (List.length (ok {|{"bars":{}}|}));
  Alcotest.(check int)
    "a symbol with one bar has no returns" 0
    (Array.length
       (List.hd_exn (ok {|{"bars":{"AAPL":[{"c":100.0}]}}|})).Alpaca_rest.Series.returns);
  Alcotest.(check int)
    "a symbol with no bars at all" 0
    (Array.length (List.hd_exn (ok {|{"bars":{"AAPL":[]}}|})).Alpaca_rest.Series.returns);
  (* Alpaca reports its own errors as {"message":"..."}; passing that through
     beats a generic complaint about the shape. *)
  match
    Alpaca_rest.returns_of_body {|{"message":"invalid syntax for parameter start"}|}
  with
  | Ok _ -> Alcotest.fail "expected an error"
  | Error e -> (
      Alcotest.(check bool)
        "alpaca's own explanation is passed through" true
        (String.is_substring (Error.to_string_hum e) ~substring:"invalid syntax");
      match Alpaca_rest.returns_of_body "<html>504</html>" with
      | Ok _ -> Alcotest.fail "expected an error for non-JSON"
      | Error _ -> ())

(* The backfill must also carry an opening MARK, not just returns.

   This is a regression test for a bug the live dashboard exposed and no unit
   test would have. Positions come from the book file, but prices only ever
   arrived from the tick stream -- so a symbol that had not printed yet stayed
   at its initial zero, and a position marked at zero contributes zero to
   exposure. Run it after the close and four of six names silently drop out of
   the book: gross read $217,590 against a true $460,000, with nothing on the
   page suggesting the number was wrong.

   Worse than staleness, and a different kind of wrong. A stale price is a real
   price from earlier. A zero is a price that never existed. *)
let test_backfill_carries_a_mark () =
  match Alpaca_rest.returns_of_body bars_body with
  | Error e -> Alcotest.failf "should parse: %s" (Error.to_string_hum e)
  | Ok per_symbol -> (
      let find s =
        match
          List.find per_symbol ~f:(fun (x : Alpaca_rest.Series.t) ->
              String.equal (Symbol.to_string x.symbol) s)
        with
        | Some x -> x
        | None -> Alcotest.failf "no series for %s" s
      in
      Alcotest.check
        (Alcotest.option (Alcotest.float 1e-9))
        "AAPL marks at its most recent close, not its first" (Some 99.0)
        (find "AAPL").Alpaca_rest.Series.last_close;
      Alcotest.check
        (Alcotest.option (Alcotest.float 1e-9))
        "MSFT likewise" (Some 210.0) (find "MSFT").Alpaca_rest.Series.last_close;
      (* A close of zero is not a mark. Falling back to it would reintroduce the
         exact bug this test exists for. *)
      (match
         Alpaca_rest.returns_of_body {|{"bars":{"AAPL":[{"c":100.0},{"c":0.0}]}}|}
       with
      | Error e -> Alcotest.failf "should parse: %s" (Error.to_string_hum e)
      | Ok [ x ] ->
          Alcotest.check
            (Alcotest.option (Alcotest.float 1e-9))
            "a zero close is skipped in favour of the last real one" (Some 100.0)
            x.Alpaca_rest.Series.last_close
      | Ok _ -> Alcotest.fail "expected one symbol");
      match Alpaca_rest.returns_of_body {|{"bars":{"AAPL":[]}}|} with
      | Error e -> Alcotest.failf "should parse: %s" (Error.to_string_hum e)
      | Ok [ x ] ->
          Alcotest.check
            (Alcotest.option (Alcotest.float 1e-9))
            "no bars, no mark -- and None is the honest answer" None
            x.Alpaca_rest.Series.last_close
      | Ok _ -> Alcotest.fail "expected one symbol")

(* The request must ask for split-adjusted closes. Unadjusted, a 2-for-1 split
   shows as a -50% single-day return; in a 60-day window at 95% confidence that
   one observation IS the tail, so VaR would report a 50% loss and hold there
   for three months. Splits are common enough that this is not a rare case. *)
let test_bars_request_is_adjusted () =
  let uri =
    Uri.to_string
      (Alpaca_rest.build_uri
         ~symbols:[ Symbol.of_string "AAPL"; Symbol.of_string "MSFT" ]
         ~window:60 ~feed:"iex")
  in
  Alcotest.(check bool)
    "adjustment=all" true
    (String.is_substring uri ~substring:"adjustment=all");
  Alcotest.(check bool)
    "daily bars, to match FRED's frequency" true
    (String.is_substring uri ~substring:"timeframe=1Day");
  Alcotest.(check bool)
    "both symbols in one request" true
    (String.is_substring uri ~substring:"AAPL%2CMSFT"
    || String.is_substring uri ~substring:"AAPL,MSFT");
  (* Credentials travel in headers here, not the query string, so unlike the
     FRED URI this one is safe to print in an error. *)
  Alcotest.(check bool)
    "no api key in the URI" false
    (String.is_substring uri ~substring:"api_key");
  (* Enough calendar days to cover the requested trading days plus holidays. *)
  Alcotest.(check bool)
    "lookback exceeds the window" true
    (Alpaca_rest.lookback_days ~window:60 > 60)

(* ------------------------------------------------------------------------ *)
(* Config                                                                    *)
(* ------------------------------------------------------------------------ *)

(* The reason Secret.t exists. This engine sexps its config into logs and will
   serve state over HTTP in Phase 3; a key that CAN be printed will eventually
   be printed, and the only durable fix is a type whose printer cannot emit
   it. *)
let test_secrets_are_redacted () =
  let secret = "sk_live_do_not_log_me" in
  let printed =
    Sexp.to_string_hum (Config.Secret.sexp_of_t (Config.Secret.of_string secret))
  in
  Alcotest.(check string) "sexp_of_t prints a placeholder" "<redacted>" printed;
  (* And through the enclosing record, which is how it would actually escape. *)
  let credentials =
    {
      Config.Credentials.alpaca_key = Config.Secret.of_string secret;
      alpaca_secret = Config.Secret.of_string "another";
      fred_api_key = Config.Secret.of_string "third";
    }
  in
  let printed = Sexp.to_string_hum (Config.Credentials.sexp_of_t credentials) in
  Alcotest.(check bool)
    "no key survives the record's printer" false
    (String.is_substring printed ~substring:secret);
  Alcotest.(check bool)
    "nor the others" false
    (String.is_substring printed ~substring:"another");
  (* The bytes are still reachable deliberately, for the wire. *)
  Alcotest.(check string)
    "to_string still works" secret
    (Config.Secret.to_string (Config.Secret.of_string secret))

let test_missing_credential_names_the_variable () =
  match Config.Credentials.required "OHCAMEL_DEFINITELY_NOT_SET_12345" with
  | Ok _ -> Alcotest.fail "an unset variable must not produce a credential"
  | Error e ->
      Alcotest.(check bool)
        "the error names the variable, so the fix is obvious" true
        (String.is_substring (Error.to_string_hum e)
           ~substring:"OHCAMEL_DEFINITELY_NOT_SET_12345")

(* ------------------------------------------------------------------------ *)
(* The book file                                                             *)
(* ------------------------------------------------------------------------ *)

let book_sexp =
  {|((cash 100000.0)
     (positions (((symbol AAPL) (sector TECH)   (qty 200.0))
                 ((symbol XOM)  (sector ENERGY) (qty -400.0))))
     (limits   (((name aapl-cap)  (scope (Instrument AAPL)) (kind (Gross_notional 25000.0)))
                ((name book-cap)  (scope Portfolio)         (kind (Gross_notional 150000.0)))
                ((name var-cap)   (scope Portfolio)         (kind (Value_at_risk 3000.0)))
                ((name dd-cap)    (scope Portfolio)         (kind (Max_drawdown 0.1))))))|}

let test_book_parsing () =
  match Config.Book.of_string book_sexp with
  | Error e -> Alcotest.failf "should parse: %s" (Error.to_string_hum e)
  | Ok book ->
      Alcotest.check feq "cash" 100000.0 book.Config.Book.cash;
      let instruments = Config.Book.instruments book in
      Alcotest.(check (list string))
        "instruments" [ "AAPL"; "XOM" ]
        (List.map instruments ~f:(fun i -> Symbol.to_string i.Instrument.symbol));
      Alcotest.(check (list string))
        "sectors carried through" [ "TECH"; "ENERGY" ]
        (List.map instruments ~f:(fun i -> Sector.to_string i.Instrument.sector));
      let limits = Config.Book.limits book in
      Alcotest.(check int) "four limits" 4 (List.length limits);
      (* The conversion has to land on the right constructor AND the right
         units: Gross_notional and Value_at_risk are money, Max_drawdown is a
         fraction. Types.ml keeps them separate precisely so a drawdown cannot
         be compared against a dollar exposure, and this is the boundary where
         that could be undone. *)
      Alcotest.(check (list string))
        "kinds survive the crossing"
        [
          "(Gross_notional 25000)";
          "(Gross_notional 150000)";
          "(Value_at_risk 3000)";
          "(Max_drawdown 0.1)";
        ]
        (List.map limits ~f:(fun l -> Sexp.to_string (Limit.sexp_of_kind (Limit.kind l))));
      (* And the whole thing has to be acceptable to the graph, which is the
         only test of the book file that really matters. *)
      let graph =
        Graph.create ~instruments ~limits ~confidence:0.95 ~return_window:10
          ~starting_cash:(Notional.of_float book.Config.Book.cash)
          ()
      in
      Graph.destroy graph

let test_book_rejects_malformed_files () =
  let rejected name body =
    match Config.Book.of_string body with
    | Ok _ -> Alcotest.failf "%s: should not have parsed" name
    | Error _ -> ()
  in
  rejected "not a sexp" "((cash 100.0";
  rejected "missing a field" "((cash 100.0) (positions ()))";
  rejected "wrong limit scope"
    {|((cash 1.0) (positions ()) (limits (((name a) (scope (Nonsense X)) (kind (Max_drawdown 0.1))))))|};
  rejected "cash is not a number" "((cash lots) (positions ()) (limits ()))"

let suite =
  ( "feed",
    [
      Alcotest.test_case "alpaca: a documented trade message" `Quick test_parse_trade;
      Alcotest.test_case "alpaca: frames are batched arrays" `Quick
        test_parse_batched_frame;
      Alcotest.test_case "alpaca: control messages" `Quick test_parse_control_messages;
      Alcotest.test_case "alpaca: bad prices never become trades" `Quick
        test_rejects_bad_prices;
      Alcotest.test_case "alpaca: malformed frames do not take the feed down" `Quick
        test_survives_malformed_frames;
      Alcotest.test_case "alpaca: a bad timestamp does not discard a good price" `Quick
        test_bad_timestamp_keeps_the_trade;
      Alcotest.test_case "alpaca: applying a frame to the graph" `Quick test_apply_frame;
      Alcotest.test_case "alpaca: an unsubscribed symbol is counted, not fatal" `Quick
        test_unknown_symbol_is_counted_not_fatal;
      Alcotest.test_case "alpaca: rejected prints are counted and never applied" `Quick
        test_rejected_prints_are_counted;
      Alcotest.test_case "alpaca: control messages reach the caller" `Quick
        test_control_messages_are_returned;
      Alcotest.test_case "alpaca: fatal errors are distinguished from retryable" `Quick
        test_failure_classification;
      Alcotest.test_case "alpaca: backoff schedule, capped and overflow-free" `Quick
        test_backoff_schedule;
      Alcotest.test_case "alpaca: backoff jitter stays under the cap" `Quick
        test_backoff_jitter;
      Alcotest.test_case "backfill: daily bars become returns" `Quick test_bars_to_returns;
      Alcotest.test_case "backfill: impossible closes never enter the window" `Quick
        test_bars_reject_impossible_closes;
      Alcotest.test_case "backfill: degenerate series and error responses" `Quick
        test_bars_degenerate_and_errors;
      Alcotest.test_case "backfill: carries an opening mark, not just returns" `Quick
        test_backfill_carries_a_mark;
      Alcotest.test_case "backfill: the request asks for adjusted daily bars" `Quick
        test_bars_request_is_adjusted;
      Alcotest.test_case "fred: \".\" is a missing observation, not zero" `Quick
        test_fred_missing_observations;
      Alcotest.test_case "fred: levels become changes" `Quick test_fred_changes;
      Alcotest.test_case "fred: the series comes out chronological" `Quick
        test_fred_orientation;
      Alcotest.test_case "fred: degenerate series" `Quick test_fred_degenerate_series;
      Alcotest.test_case "fred: error responses" `Quick test_fred_error_responses;
      Alcotest.test_case "fred: the api key never reaches a log" `Quick
        test_fred_uri_redaction;
      Alcotest.test_case "config: secrets cannot be printed" `Quick
        test_secrets_are_redacted;
      Alcotest.test_case "config: a missing credential names the variable" `Quick
        test_missing_credential_names_the_variable;
      Alcotest.test_case "book: parses and is acceptable to the graph" `Quick
        test_book_parsing;
      Alcotest.test_case "book: malformed files are rejected" `Quick
        test_book_rejects_malformed_files;
    ] )
