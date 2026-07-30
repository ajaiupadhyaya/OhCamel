(* Phase 2.

   FRED macro series (rates, etc.), feeding the rolling-beta / factor-exposure
   node.

   Polling is fine here and the README says so explicitly: these series update
   daily or slower, so a slow poll is matched to the data rather than papering
   over a design gap. The reactive requirement is about tick-driven quantities.

   ------------------------------------------------------------------------
   THE ENDPOINT

     https://api.stlouisfed.org/fred/series/observations
       ?series_id=DGS10&api_key=...&file_type=json
       &sort_order=desc&limit=N

   Requesting descending order with a limit fetches the most recent N
   observations instead of sixty years of history, then the list is reversed
   into chronological order. DGS10 alone goes back to 1962.

     {"observations":[
        {"realtime_start":"...","realtime_end":"...","date":"2024-05-01","value":"4.63"},
        {"realtime_start":"...","realtime_end":"...","date":"2024-05-27","value":"."}]}

   ------------------------------------------------------------------------
   TWO TRAPS, BOTH LOAD-BEARING

   1. FRED writes "." for a missing observation -- a market holiday, or a day
      the series was not published. It is a string in a field that is otherwise
      a number, so a lenient parser coerces it to 0.0, and a 0.0 in a yield
      series is not a missing value: it is a claim that the ten-year Treasury
      yielded nothing that day. One such point produces two enormous spurious
      changes in the difference series and can dominate a beta estimate outright.
      Missing observations are dropped, and dropping is the only defensible
      choice -- interpolating would invent a rate that was never published.

   2. The series is a LEVEL and beta needs a CHANGE. Yields are close to a unit
      root; regressing returns on the level of a yield is a spurious regression
      in the textbook sense, and it will happily produce a large t-statistic
      that means nothing. The level is differenced here, at the boundary, so
      graph.ml only ever sees changes.

   Units: differences of DGS10 are in PERCENTAGE POINTS, because that is how
   FRED publishes it -- 4.63 means 4.63%. So the resulting beta reads as
   "portfolio return per 1 percentage point move in the series", which is the
   conventional way to quote a rates beta. It is deliberately not rescaled to a
   fraction; doing so silently would make every beta a hundred times smaller
   than the number anyone expects to see. *)

open Core
open Async

let host = "api.stlouisfed.org"
let path = "/fred/series/observations"

(* ------------------------------------------------------------------------ *)
(* Observations                                                              *)
(* ------------------------------------------------------------------------ *)

module Observation = struct
  (* [value = None] is a genuine missing observation, kept distinct from a value
     of zero all the way through parsing so that nothing downstream has to guess
     which it was looking at. *)
  type t = { date : string; value : float option } [@@deriving sexp_of, equal]

  let missing_marker = "."

  let of_json json =
    let field key =
      match json with
      | `Assoc fields -> (
          match List.Assoc.find fields key ~equal:String.equal with
          | Some (`String s) -> Some s
          | _ -> None)
      | _ -> None
    in
    match field "date" with
    | None -> None
    | Some date ->
        let value =
          match field "value" with
          | None -> None
          | Some raw when String.equal (String.strip raw) missing_marker -> None
          | Some raw -> Option.try_with (fun () -> Float.of_string (String.strip raw))
        in
        Some { date; value }
end

(* Parse a whole response body. Pure, so the tests drive it with a captured
   payload and never open a socket. *)
let parse_observations (body : string) : Observation.t list Or_error.t =
  match Option.try_with (fun () -> Yojson.Safe.from_string body) with
  | None -> Or_error.error_string "fred: response was not valid JSON"
  | Some json -> (
      let observations =
        match json with
        | `Assoc fields -> List.Assoc.find fields "observations" ~equal:String.equal
        | _ -> None
      in
      match observations with
      | Some (`List items) -> Ok (List.filter_map items ~f:Observation.of_json)
      | _ ->
          (* FRED reports its own errors as JSON with an "error_message" field
             and an HTTP error status, so surface that rather than a generic
             shape complaint -- it usually says exactly what is wrong with the
             request. *)
          let message =
            match json with
            | `Assoc fields -> (
                match List.Assoc.find fields "error_message" ~equal:String.equal with
                | Some (`String m) -> m
                | _ -> "no observations field in response")
            | _ -> "no observations field in response"
          in
          Or_error.errorf "fred: %s" message)

(* Level series -> first differences, dropping missing observations first.

   Differencing across a gap is deliberate and is the least-bad option: if
   Friday and Tuesday are present but Monday was a holiday, the Friday-to-Tuesday
   change is a real move that happened, merely over a longer interval. The
   alternative -- discarding any change that spans a gap -- would throw away
   every long weekend in the sample. *)
let to_changes (observations : Observation.t list) : float array =
  let levels = List.filter_map observations ~f:(fun o -> o.Observation.value) in
  match levels with
  | [] | [ _ ] -> [||]
  | first :: rest ->
      let _, changes =
        List.fold rest ~init:(first, []) ~f:(fun (previous, acc) level ->
            (level, (level -. previous) :: acc))
      in
      Array.of_list (List.rev changes)

(* The whole transformation from response body to the array the graph consumes,
   with no network in it.

   The reversal is part of this function rather than of [fetch] on purpose: the
   request asks for newest-first, and turning that back into chronological order
   is precisely the step that would be silently wrong -- a reversed difference
   series has every sign flipped, which flips the sign of beta, which turns a
   long-duration book into a short-duration one on the display. Nothing about
   the output looks wrong. So it belongs where a test can reach it. *)
let changes_of_body (body : string) : float array Or_error.t =
  Or_error.map (parse_observations body) ~f:(fun newest_first ->
      to_changes (List.rev newest_first))

(* ------------------------------------------------------------------------ *)
(* Fetching                                                                  *)
(* ------------------------------------------------------------------------ *)

(* The API key travels in the query string, which means any logged or errored
   URI would carry the credential with it. Two builders, and only [request_uri]
   ever sees the real key: everything human-facing goes through
   [redacted_uri]. *)
let build_uri ~series_id ~limit ~api_key =
  Uri.make ~scheme:"https" ~host ~path
    ~query:
      [
        ("series_id", [ series_id ]);
        ("api_key", [ api_key ]);
        ("file_type", [ "json" ]);
        (* Most recent first, so a limit fetches the recent tail rather than the
           start of a sixty-year history. Reversed into chronological order
           below. *)
        ("sort_order", [ "desc" ]);
        ("limit", [ Int.to_string limit ]);
      ]
    ()

let request_uri ~series_id ~limit ~(api_key : Config.Secret.t) =
  build_uri ~series_id ~limit ~api_key:(Config.Secret.to_string api_key)

let redacted_uri ~series_id ~limit = build_uri ~series_id ~limit ~api_key:"REDACTED"

let fetch ~(credentials : Config.Credentials.t) ~(runtime : Config.Runtime.t) :
    float array Or_error.t Deferred.t =
  let series_id = runtime.Config.Runtime.fred_series_id in
  (* One more observation than the window, because differencing consumes one. *)
  let limit = runtime.Config.Runtime.return_window + 1 in
  let uri =
    request_uri ~series_id ~limit ~api_key:credentials.Config.Credentials.fred_api_key
  in
  match%map
    Monitor.try_with ~extract_exn:true (fun () ->
        let%bind response, body = Cohttp_async.Client.get uri in
        let%map body = Cohttp_async.Body.to_string body in
        (Cohttp.Response.status response, body))
  with
  | Error exn ->
      (* Deliberately reports the redacted URI. An exception message carrying the
         API key would be written to a log, and a secret in a log is a secret
         that has leaked. *)
      Or_error.errorf "fred: request to %s failed: %s"
        (Uri.to_string (redacted_uri ~series_id ~limit))
        (Exn.to_string exn)
  | Ok (status, body) -> (
      match status with
      | `OK -> changes_of_body body
      | status ->
          Or_error.errorf "fred: %s returned %s"
            (Uri.to_string (redacted_uri ~series_id ~limit))
            (Cohttp.Code.string_of_status status))

(* ------------------------------------------------------------------------ *)
(* Polling                                                                   *)
(* ------------------------------------------------------------------------ *)

module Stats = struct
  type t = {
    mutable polls : int;
    mutable successes : int;
    mutable observations : int;
    mutable consecutive_failures : int;
    mutable last_success : Types.Time.t option;
    mutable last_error : string option;
  }

  let create () =
    {
      polls = 0;
      successes = 0;
      observations = 0;
      consecutive_failures = 0;
      last_success = None;
      last_error = None;
    }

  let to_string t =
    sprintf "polls=%d ok=%d observations=%d consecutive_failures=%d%s" t.polls t.successes
      t.observations t.consecutive_failures
      (match t.last_error with None -> "" | Some e -> sprintf " last_error=%S" e)
end

(* Poll forever, writing each successful fetch into the graph's factor cell.

   A failed poll is NOT fatal and does not clear the series. FRED is a
   free public API with no availability guarantee, the data changes daily at
   most, and yesterday's rate history is a far better input than no rate
   history: clearing it would drop portfolio_beta to None and make a transient
   HTTP error look like a factor that stopped existing.

   What it does instead is count, so that "beta has not moved in a while" and
   "FRED has been failing for six hours" are distinguishable in the stats
   line. *)
let run ?(on_event = fun (_ : string) -> ()) ~(graph : Graph.t)
    ~(credentials : Config.Credentials.t) ~(runtime : Config.Runtime.t) ~(stats : Stats.t)
    () =
  let rec poll () =
    stats.Stats.polls <- stats.Stats.polls + 1;
    let%bind result = fetch ~credentials ~runtime in
    (match result with
    | Ok changes ->
        stats.Stats.successes <- stats.Stats.successes + 1;
        stats.Stats.observations <- Array.length changes;
        stats.Stats.consecutive_failures <- 0;
        stats.Stats.last_success <- Some (Types.Time.now ());
        Graph.set_factor_returns graph changes;
        Graph.stabilize graph;
        on_event
          (sprintf "%s: %d changes" runtime.Config.Runtime.fred_series_id
             (Array.length changes))
    | Error error ->
        stats.Stats.consecutive_failures <- stats.Stats.consecutive_failures + 1;
        stats.Stats.last_error <- Some (Error.to_string_hum error);
        on_event
          (sprintf "%s FAILED (%d in a row, keeping the last good series): %s"
             runtime.Config.Runtime.fred_series_id stats.Stats.consecutive_failures
             (Error.to_string_hum error)));
    let%bind () =
      after
        (Time_ns.Span.to_span_float_round_nearest
           runtime.Config.Runtime.fred_poll_interval)
    in
    poll ()
  in
  poll ()
