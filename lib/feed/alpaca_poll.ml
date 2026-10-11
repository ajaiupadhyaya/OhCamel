(* The names past the stream's thirty (design D7, §4.3).

   Alpaca's free websocket carries at most Config.stream_cap symbols, and a book
   may declare up to Config.universe_cap. The rest are marked here: every
   [every], one GET of the multi-symbol latest-trades call per fifty names, each
   trade applied with Graph.apply_tick -- a real print, so it is feed evidence,
   unlike backfill's closing mark -- and the graph settled once per batch.

   It talks to data.alpaca.markets and nothing else, and it never raises into
   its caller: a batch that fails is logged when its kind of failure is new and
   tried again on the next tick, and the names it would have marked stay at what
   they had and go stale on the page, which is the truth about them. A name the
   response does not carry is left alone, never zeroed.

   Every request is bounded. Clock_ns.with_timeout gives up after
   [request_timeout], and the same deadline is handed to cohttp as
   [~interrupt] and closes the body's pipe, so an abandoned request is
   cancelled rather than left holding a socket -- the pattern of
   desk/alpaca_paper.ml's [within], which lib/ may not name. *)

open Core
open Async

let request_timeout = Time_ns.Span.of_sec 30.0

(* Alpaca's latest-trades call takes many symbols; fifty per request keeps the
   query string short. *)
let batch_size = 50

(* What went wrong with one batch, coarse enough that the same outage logged
   twice is the same class: a log line on each change of class, not on each
   failure, so a ten-second loop against a dead host writes one line, not six
   a minute. *)
module Failure = struct
  type t = { class_ : string; detail : string }
end

let fetch ~(credentials : Config.Credentials.t) ~(feed : string)
    (symbols : Types.Symbol.t list) :
    ((Types.Symbol.t * Types.Tick.t) list, Failure.t) Result.t Deferred.t =
  let uri = Alpaca_rest.latest_trades_uri ~symbols ~feed in
  (* The URI carries no credential -- the keys go in headers -- so it is safe to
     name in a log line. *)
  let where = Uri.path uri in
  let abandoned = Ivar.create () in
  let abandon = Ivar.read abandoned in
  match%map
    Clock_ns.with_timeout request_timeout
      (Monitor.try_with ~extract_exn:true ~rest:`Log (fun () ->
           let%bind response, body =
             Cohttp_async.Client.get ~interrupt:abandon
               ~headers:(Alpaca_rest.headers ~credentials)
               uri
           in
           upon abandon (fun () ->
               match body with `Pipe pipe -> Pipe.close_read pipe | _ -> ());
           let%map body = Cohttp_async.Body.to_string body in
           (Cohttp.Response.status response, body)))
  with
  | `Timeout ->
      Ivar.fill_if_empty abandoned ();
      Error
        {
          Failure.class_ = "timeout";
          detail =
            sprintf "GET %s did not answer within %s" where
              (Time_ns.Span.to_string_hum request_timeout);
        }
  | `Result (Error exn) ->
      Error
        {
          Failure.class_ = "request";
          detail = sprintf "GET %s failed: %s" where (Exn.to_string exn);
        }
  | `Result (Ok (`OK, body)) -> (
      match Yojson.Safe.from_string body with
      | json -> Ok (Alpaca_rest.parse_latest_trades json)
      | exception Yojson.Json_error msg ->
          Error
            {
              Failure.class_ = "not json";
              detail = sprintf "GET %s was not JSON: %s" where msg;
            })
  | `Result (Ok (status, body)) ->
      let status = Cohttp.Code.string_of_status status in
      Error
        {
          Failure.class_ = "http " ^ status;
          detail = sprintf "GET %s returned %s (%s)" where status (String.prefix body 200);
        }

let run ~(graph : Graph.t) ~(credentials : Config.Credentials.t)
    ~(runtime : Config.Runtime.t) ~(symbols : Types.Symbol.t list)
    ~(every : Time_ns.Span.t) ~(stats : Alpaca_ws.Stats.t) ~(on_event : string -> unit) :
    unit Deferred.t =
  let feed = runtime.Config.Runtime.alpaca_feed in
  let batches = List.chunks_of symbols ~length:batch_size in
  (* The last outcome's class: None before the first, "ok" after a success. *)
  let last = ref None in
  let note class_ line =
    if not (Option.equal String.equal !last (Some class_)) then on_event line;
    last := Some class_
  in
  let one_batch batch =
    match%map
      Monitor.try_with ~extract_exn:true (fun () ->
          match%map fetch ~credentials ~feed batch with
          | Error _ as error -> error
          | Ok ticks ->
              (* The websocket's own frame applier: an unknown symbol is
                 counted, each trade marks its cells, one stabilize. *)
              let (_ : Alpaca_ws.Message.t list) =
                Alpaca_ws.apply_frame ~graph ~stats
                  (List.map ticks ~f:(fun (_, tick) -> Alpaca_ws.Message.Trade tick))
              in
              Ok (List.length ticks))
    with
    | Ok (Ok marked) ->
        note "ok"
          (sprintf "poll: marking %d names by latest trade every %s (%d in this answer)"
             (List.length symbols)
             (Time_ns.Span.to_string_hum every)
             marked)
    | Ok (Error { Failure.class_; detail }) -> note class_ ("poll: " ^ detail)
    | Error exn -> note "raised" ("poll: raised " ^ Exn.to_string exn)
  in
  Clock_ns.every' every (fun () ->
      Deferred.List.iter batches ~how:`Sequential ~f:one_batch);
  Deferred.never ()
