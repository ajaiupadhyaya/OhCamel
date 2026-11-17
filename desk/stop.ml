(* INC-01's stop rules, decided at the close.

   Per strategy, in the book before it trades: a drawdown from peak on allocated
   capital, and a count of sessions since its first fill. Either one stops it. A
   stopped strategy is recorded with the rule that fired (strategy_state), said
   once in the alerts, sized no more, and flattened by the intake's adjust pass.
   The desk never un-stops: only the owner's (reinstated DATE), later than the
   stop, brings a strategy back, at the next start, and its record then begins
   again at that date, because the fall before it is what the stop answered.

   Nothing evaluated these until 2026-11-03, while 23 strategies traded with them
   in the book. The record is Record.sessions_of over the strategy's own fills,
   so a stop and the page read one drawdown -- since Task 1.8 with its dividends on
   their ex-dates and its fills' fees, so an ex-date for a monthly payer is not a
   fall toward a stop's line. A strategy already stopped stays stopped: [evaluate]
   skips it, whatever its record now reads. *)

open Core
module Spec = Ohcamel.Config.Book.Signals_spec

let check ~(spec : Spec.Stop.t) ~(record : Record.summary) ~sessions =
  if Float.( >= ) record.Record.max_drawdown spec.Spec.Stop.drawdown then
    `Stop
      ( "drawdown",
        sprintf "drawdown %.1f%% reached its line of %.1f%%"
          (100.0 *. record.Record.max_drawdown)
          (100.0 *. spec.Spec.Stop.drawdown) )
  else if sessions >= spec.Spec.Stop.sessions then
    `Stop
      ( "sessions",
        sprintf "%d sessions since its first fill, the book's count of %d" sessions
          spec.Spec.Stop.sessions )
  else `Continue

let close_of ~zone date =
  Time_ns.of_date_ofday ~zone date (Time_ns.Ofday.create ~hr:16 ())

(* A session's capital is the most the strategy had allocated in force at any
   moment from the previous close to this one. The day's P&L was earned on the
   position before any re-size inside it: when the governor cut a strategy to a
   fifth at 09:45, as it does when the book is losing, the allocation at the close
   made a 3% loss read 15%, and a stop could fire on that alone. The largest never
   inflates a return, and a session sized to 0 before its sale still counts. *)
let capital ~zone ~allocations ~previous date =
  let close = close_of ~zone date in
  let from = Option.map previous ~f:(close_of ~zone) in
  List.fold allocations ~init:(None, 0.0) ~f:(fun (held, most) (at, dollars) ->
      if Time_ns.( > ) at close then (held, most)
      else
        match from with
        | Some from when Time_ns.( <= ) at from -> (Some dollars, most)
        | Some _ | None -> (held, Float.max most dollars))
  |> fun (held, most) -> Float.max (Option.value held ~default:0.0) most

(* WHICH ARRIVAL QUOTE IS ONE (Task 1.8). A fill's cost is measured against the
   midpoint of the quote its order was sent against -- when that quote is one a
   trade could have met. It is None, and the fill's cost unknown, never zero and
   never a guess, when:

   - the order journaled none: since Task 1.7, a quote whose own stamp was more
     than 60 s older than its fetch (a shrinking order on a thin name), or any
     order sent with no quote;
   - the order was an auction's (market-on-open, market-on-close): before 1.7 a
     market-on-open order's "arrival" was the quote at proposal, read after 19:00
     ET from a thin book, and an auction fill was never offered at it;
   - the order was created outside 09:30-16:00 New York on a weekday, for the same
     reason: an after-hours quote is not an arrival for a fill in the session;
   - the quote is one-sided, crossed, locked or not finite (Tca.of_fill's rule). *)
let arrival_mid ~zone (f : Journal.Strategy_fill.t) =
  let regular_hours at =
    let date, ofday = Time_ns.to_date_ofday at ~zone in
    (match Date.day_of_week date with Day_of_week.Sat | Sun -> false | _ -> true)
    && Time_ns.Ofday.( >= ) ofday Desk_time.session_opens
    && Time_ns.Ofday.( < ) ofday Desk_time.session_closes
  in
  match (f.arrival, f.tif) with
  | None, _ | _, (Order.Tif.Opg | Order.Tif.Cls) -> None
  | Some (bid, ask), Order.Tif.Day ->
      let b = Ohcamel.Types.Price.to_float bid and a = Ohcamel.Types.Price.to_float ask in
      if
        Float.is_finite b && Float.is_finite a && Float.( > ) b 0.0 && Float.( > ) a b
        && regular_hours f.created_at
      then Some ((a +. b) /. 2.0)
      else None

let cash_dividend (a : Venue.Activity.t) =
  String.equal a.activity_type "DIV"
  && Option.equal String.equal a.status (Some "executed")
  && not (Option.equal String.equal a.sub_type (Some "SDIV"))

(* The venue's cash-dividend records are the dividends a strategy is credited
   from, on their ex-dates. The account's DIV activity is the same dividend's
   cash, posted on the payable date: never credited a second time. When one has
   posted for the name on the record's payable date and carries a per-share
   amount, that is the rate paid, and it is the one used. *)
let dividends_of ~(cash_dividends : Venue.Cash_dividend.t list)
    ~(activities : Venue.Activity.t list) : Record.dividend list =
  List.map cash_dividends ~f:(fun (c : Venue.Cash_dividend.t) ->
      let paid =
        List.find_map activities ~f:(fun (a : Venue.Activity.t) ->
            match (a.symbol, c.payable_date, a.per_share) with
            | Some s, Some pay, Some per_share
              when cash_dividend a
                   && Ohcamel.Types.Symbol.equal s c.symbol
                   && Date.equal a.date pay ->
                Some per_share
            | _ -> None)
      in
      {
        Record.ex_date = c.ex_date;
        symbol = c.symbol;
        per_share = Option.value paid ~default:c.rate;
      })

(* A dividend the venue paid that no cash-dividend record names (by name and
   payable date): its ex-date is not known, so it is credited to no strategy, and
   the close says so every night until a record arrives. *)
let unmatched_dividends ~(cash_dividends : Venue.Cash_dividend.t list)
    ~(activities : Venue.Activity.t list) =
  List.filter activities ~f:(fun (a : Venue.Activity.t) ->
      cash_dividend a
      && not
           (List.exists cash_dividends ~f:(fun (c : Venue.Cash_dividend.t) ->
                Option.equal Ohcamel.Types.Symbol.equal a.symbol (Some c.symbol)
                && Option.equal Date.equal c.payable_date (Some a.date))))

(* A FEE activity is a fill's when it names the fill's order; Alpaca's documented
   shape gives a fee no order id, so a fee that names none is the account's,
   recorded and assigned to no strategy. Fees are positive here (a FEE's
   net_amount is negative). *)
let fees_by_order (activities : Venue.Activity.t list) =
  List.fold activities ~init:(String.Map.empty, 0.0, 0)
    ~f:(fun (by_order, unassigned, count) (a : Venue.Activity.t) ->
      if
        String.equal a.activity_type "FEE"
        && Option.equal String.equal a.status (Some "executed")
      then
        let fee = -.a.net_amount in
        match a.order_id with
        | Some id ->
            ( Map.update by_order id ~f:(fun x -> Option.value x ~default:0.0 +. fee),
              unassigned,
              count )
        | None -> (by_order, unassigned +. fee, count + 1)
      else (by_order, unassigned, count))

let sessions_under ~rule ?dividends ?activities ~journal ~zone ~strategy ~since () =
  match
    Journal.source_fill_details journal ~prefix:(Rebalance.Source.prefix ~strategy)
  with
  | [] -> []
  | first :: _ as rows -> (
      let first_date = Time_ns.to_date first.Journal.Strategy_fill.at ~zone in
      let closes = Hashtbl.Poly.create () in
      List.iter (Journal.closes_since journal ~since:first_date) ~f:(fun (date, s, c) ->
          Hashtbl.set closes ~key:(date, Ohcamel.Types.Symbol.to_string s) ~data:c);
      let allocations = Journal.allocations journal ~strategy in
      let all_dates = Journal.session_dates journal in
      let previous =
        match all_dates with
        | [] -> Date.Map.empty
        | _ :: later ->
            Date.Map.of_alist_reduce
              ~f:(fun a _ -> a)
              (List.map2_exn (List.drop_last_exn all_dates) later ~f:(fun p d -> (d, p)))
      in
      let allocated date =
        capital ~zone ~allocations ~previous:(Map.find previous date) date
      in
      let activities =
        match (rule, activities) with
        | `Before_1_8, _ -> []
        | `Current, Some given -> given
        | `Current, None -> Journal.activities journal
      in
      let fees, _, _ = fees_by_order activities in
      let order_qty =
        List.fold rows ~init:String.Map.empty ~f:(fun acc (f : Journal.Strategy_fill.t) ->
            match f.venue_order_id with
            | Some id ->
                Map.update acc id ~f:(fun x ->
                    Option.value x ~default:0.0 +. Float.abs f.qty)
            | None -> acc)
      in
      let fee_of (f : Journal.Strategy_fill.t) =
        match
          Option.bind f.venue_order_id ~f:(fun id ->
              Option.both (Map.find fees id) (Map.find order_qty id))
        with
        | Some (fee, total) when Float.( > ) total 0.0 -> fee *. Float.abs f.qty /. total
        | _ -> 0.0
      in
      let dividends =
        match (rule, dividends) with
        | `Before_1_8, _ -> []
        | `Current, Some given -> given
        | `Current, None ->
            dividends_of ~cash_dividends:(Journal.cash_dividends journal) ~activities
      in
      let record =
        Record.sessions_of
          ~dates:(List.filter all_dates ~f:(fun date -> Date.( >= ) date first_date))
          ~close:(fun date s ->
            Hashtbl.find closes (date, Ohcamel.Types.Symbol.to_string s))
          ~allocated ~dividends
          (List.map rows ~f:(fun (f : Journal.Strategy_fill.t) ->
               {
                 Record.date = Time_ns.to_date f.at ~zone;
                 symbol = f.symbol;
                 qty = f.qty;
                 price = f.price;
                 arrival_mid =
                   (match rule with
                   | `Before_1_8 -> None
                   | `Current -> arrival_mid ~zone f);
                 fee = (match rule with `Before_1_8 -> 0.0 | `Current -> fee_of f);
               }))
      in
      match since with
      | None -> record
      | Some since ->
          List.filter record ~f:(fun (s : Record.session) -> Date.( >= ) s.date since))

let sessions ~journal ~zone ~strategy ~since =
  sessions_under ~rule:`Current ~journal ~zone ~strategy ~since ()

let stopped journal name =
  match Journal.strategy_state journal ~strategy:name with
  | Some (`Stopped, _, _, _) -> true
  | Some (`Active, _, _, _) | None -> false

let evaluate_one ~journal ~zone ~at ~on_event (s : Spec.Strategy.t) =
  match s.stop with
  | None -> None
  | Some _ when stopped journal s.name -> None
  | Some spec -> (
      let record = sessions ~journal ~zone ~strategy:s.name ~since:s.reinstated in
      match
        check ~spec ~record:(Record.summarise record) ~sessions:(List.length record)
      with
      | `Continue -> None
      | `Stop (rule, detail) ->
          Journal.set_strategy_state journal ~strategy:s.name ~state:`Stopped
            ~rule:(Some rule) ~at ~detail;
          let line =
            sprintf
              "%s STOPPED: %s. It is sized no more and its position is flattened; only a \
               (reinstated DATE) in the book brings it back"
              s.name detail
          in
          Journal.record_alert journal
            { Journal.Alert.at; kind = "incubation_stopped"; limit_name = s.name; line };
          on_event ("stop      " ^ line);
          Some s.name)

(* Each strategy is guarded on its own, so a read that fails for one leaves every
   other evaluated, and is said. *)
let evaluate ~journal ~strategies ~zone ~at ~on_event =
  List.filter_map strategies ~f:(fun (s : Spec.Strategy.t) ->
      match Or_error.try_with (fun () -> evaluate_one ~journal ~zone ~at ~on_event s) with
      | Ok stopped -> stopped
      | Error e ->
          on_event
            (sprintf "stop      %s could not be evaluated: %s" s.name
               (Error.to_string_hum e));
          None)

let reinstate ~journal ~zone ~strategies ~on_event =
  List.iter strategies ~f:(fun (s : Spec.Strategy.t) ->
      match (s.reinstated, Journal.strategy_state journal ~strategy:s.name) with
      | Some date, Some (`Stopped, _, at, _) -> (
          match Desk_time.parse at with
          | Some stopped_at when Date.( > ) date (Time_ns.to_date stopped_at ~zone) ->
              let detail = sprintf "reinstated by the book on %s" (Date.to_string date) in
              Journal.set_strategy_state journal ~strategy:s.name ~state:`Active
                ~rule:None ~at:(Time_ns.now ()) ~detail;
              on_event (sprintf "stop      %s %s" s.name detail)
          | Some _ | None -> ())
      | _ -> ())
