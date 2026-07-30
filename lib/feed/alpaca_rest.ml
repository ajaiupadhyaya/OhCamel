(* Phase 2. Historical bars, fetched once at startup.

   The WebSocket feed delivers prices. It does not deliver history, and without
   history there is no return distribution -- so VaR, expected shortfall,
   parametric VaR and beta all sit at None and the dashboard reads "WARMING UP"
   forever. A risk engine whose risk numbers never arrive is not a risk engine.

   Waiting for the window to fill from the live stream is not an option either:
   a sixty-observation daily window would take three months.

   ------------------------------------------------------------------------
   WHY DAILY

   Beta compares the book's returns against the FRED factor series, and FRED
   publishes DGS10 daily. Two series at different frequencies produce a beta
   that is arithmetically well-defined and economically meaningless -- regressing
   minute returns on daily rate changes is comparing quantities that do not
   describe the same interval. Daily bars make the two match.

   This also means the return window does NOT move intraday, which is correct
   rather than a limitation. What moves intraday is the book: prices change the
   weights, weights change the portfolio return series, and VaR follows. The
   distribution is yesterday's; the exposure to it is right now's.

   ------------------------------------------------------------------------
   ADJUSTMENT IS NOT OPTIONAL

   The request asks for adjustment=all. Unadjusted closes across a 2-for-1 split
   show a -50% single-day return, which for a 60-day window at 95% confidence
   becomes the entire tail: VaR would report a 50% loss and stay there for three
   months. Splits are common enough that this would not be a rare failure. *)

open Core
open Async

let host = "data.alpaca.markets"
let path = "/v2/stocks/bars"

(* Enough calendar days to be confident of covering the requested number of
   TRADING days, with room for holidays. Roughly 252 trading days a year, so a
   1.6x margin over the window plus a fixed cushion is comfortable; the tail is
   trimmed to the window afterwards, so over-fetching costs one larger response
   and nothing else. *)
let lookback_days ~window = (window * 2) + 30

(* Alpaca wants an RFC-3339 date. Sliced out of the UTC timestamp rather than
   formatted through a calendar library, because [Time_ns.to_string_utc] is
   fixed-width ("2026-07-30 16:35:12.930764000Z") and the first ten characters
   are exactly the date. Avoids pulling in a timezone database to compute
   something the string already contains. *)
let date_string (t : Time_ns.t) = String.sub (Time_ns.to_string_utc t) ~pos:0 ~len:10

let headers ~(credentials : Config.Credentials.t) =
  Cohttp.Header.of_list
    [
      ( "APCA-API-KEY-ID",
        Config.Secret.to_string credentials.Config.Credentials.alpaca_key );
      ( "APCA-API-SECRET-KEY",
        Config.Secret.to_string credentials.Config.Credentials.alpaca_secret );
    ]

let build_uri ~symbols ~window ~feed =
  let start =
    date_string
      (Time_ns.sub (Time_ns.now ())
         (Time_ns.Span.of_day (float_of_int (lookback_days ~window))))
  in
  Uri.make ~scheme:"https" ~host ~path
    ~query:
      [
        ( "symbols",
          [ String.concat ~sep:"," (List.map symbols ~f:Types.Symbol.to_string) ] );
        ("timeframe", [ "1Day" ]);
        ("start", [ start ]);
        (* See the note at the top: unadjusted closes turn a split into a -50%
           return that dominates the tail for the life of the window. *)
        ("adjustment", [ "all" ]);
        ("feed", [ feed ]);
        ("limit", [ "10000" ]);
      ]
    ()

(* ------------------------------------------------------------------------ *)
(* Parsing                                                                   *)
(* ------------------------------------------------------------------------ *)

(* {"bars":{"AAPL":[{"t":"2024-01-02T05:00:00Z","o":..,"c":187.15,...}, ...]}} *)
let closes_of_bars (json : Yojson.Safe.t) : float list =
  match json with
  | `List bars ->
      List.filter_map bars ~f:(fun bar ->
          match bar with
          | `Assoc fields -> (
              match List.Assoc.find fields "c" ~equal:String.equal with
              | Some (`Float c) -> Some c
              | Some (`Int c) -> Some (float_of_int c)
              | _ -> None)
          | _ -> None)
  | _ -> []

(* Simple returns from a close series: r(i) = c(i+1)/c(i) - 1.

   Non-positive closes are dropped before differencing rather than divided by. A
   zero close is not a real price, and dividing by it yields infinity, which
   then propagates through the whole risk chain as silently as a NaN would. *)
let returns_of_closes (closes : float list) : float array =
  let usable = List.filter closes ~f:(fun c -> Float.is_finite c && Float.( > ) c 0.0) in
  match usable with
  | [] | [ _ ] -> [||]
  | first :: rest ->
      let _, returns =
        List.fold rest ~init:(first, []) ~f:(fun (previous, acc) close ->
            (close, ((close /. previous) -. 1.0) :: acc))
      in
      Array.of_list (List.rev returns)

(* Whole body -> per-symbol return series. Pure, so the tests drive it with a
   captured payload and never open a socket. *)
let returns_of_body (body : string) : (Types.Symbol.t * float array) list Or_error.t =
  match Option.try_with (fun () -> Yojson.Safe.from_string body) with
  | None -> Or_error.error_string "alpaca_rest: response was not valid JSON"
  | Some json -> (
      let field key =
        match json with
        | `Assoc fields -> List.Assoc.find fields key ~equal:String.equal
        | _ -> None
      in
      match field "bars" with
      | Some (`Assoc per_symbol) ->
          Ok
            (List.map per_symbol ~f:(fun (symbol, bars) ->
                 (Types.Symbol.of_string symbol, returns_of_closes (closes_of_bars bars))))
      | _ ->
          (* Alpaca reports its own errors as {"message":"..."}; passing that
             through beats a generic shape complaint. *)
          let message =
            match field "message" with
            | Some (`String m) -> m
            | _ -> "no bars object in response"
          in
          Or_error.errorf "alpaca_rest: %s" message)

(* ------------------------------------------------------------------------ *)
(* Fetching                                                                  *)
(* ------------------------------------------------------------------------ *)

let fetch_daily_returns ~(credentials : Config.Credentials.t)
    ~(symbols : Types.Symbol.t list) ~(window : int) ~(feed : string) :
    (Types.Symbol.t * float array) list Or_error.t Deferred.t =
  if List.is_empty symbols then return (Ok [])
  else
    let uri = build_uri ~symbols ~window ~feed in
    match%map
      Monitor.try_with ~extract_exn:true (fun () ->
          let%bind response, body =
            Cohttp_async.Client.get ~headers:(headers ~credentials) uri
          in
          let%map body = Cohttp_async.Body.to_string body in
          (Cohttp.Response.status response, body))
    with
    (* The URI carries no credential -- the keys go in headers -- so it is safe
       to name in an error. Worth stating, because the FRED client's URI is not
       safe and has to be redacted. *)
    | Error exn ->
        Or_error.errorf "alpaca_rest: request to %s failed: %s" (Uri.to_string uri)
          (Exn.to_string exn)
    | Ok (`OK, body) -> returns_of_body body
    | Ok (status, body) ->
        Or_error.errorf "alpaca_rest: %s returned %s (%s)" (Uri.to_string uri)
          (Cohttp.Code.string_of_status status)
          (String.prefix body 200)

(* Backfill every instrument's return window, then settle once.

   Returns the per-symbol observation counts so the caller can say what actually
   arrived. A symbol with an empty window is not an error -- a recently listed
   ticker genuinely has no history -- but it does mean the common window across
   the book is zero, and therefore that every risk number stays None. Saying
   which symbol is short is the difference between diagnosing that in a minute
   and in an afternoon. *)
let backfill ~(graph : Graph.t) ~(credentials : Config.Credentials.t)
    ~(runtime : Config.Runtime.t) : (Types.Symbol.t * int) list Or_error.t Deferred.t =
  let symbols = Graph.symbols graph in
  let window = runtime.Config.Runtime.return_window in
  match%map
    fetch_daily_returns ~credentials ~symbols ~window
      ~feed:runtime.Config.Runtime.alpaca_feed
  with
  | Error _ as error -> error
  | Ok per_symbol ->
      List.iter per_symbol ~f:(fun (symbol, returns) ->
          if Graph.knows_symbol graph symbol then Graph.set_returns graph symbol returns);
      Graph.stabilize graph;
      Ok
        (List.map symbols ~f:(fun symbol ->
             (symbol, Array.length (Graph.returns graph symbol))))
