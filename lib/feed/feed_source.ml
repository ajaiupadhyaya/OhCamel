(* The live feed's counters, packaged for /api/ops.

   server.ml must not learn an Alpaca or FRED type. It is linked into every
   mode, including the six that have no credentials, and a broker's record
   reaching the wire module would put the feed's vocabulary in the middle of
   the wire format. So the object is assembled HERE, on the feed side of that
   line, and run_live hands Server.create the partially-applied result as a
   closure.

   The closure reads the two records at call time, never at creation. Both
   are mutable and climb for the life of the process; a snapshot taken at
   startup would report, forever, a socket that never received a frame --
   which is the failure /ops exists to show, arriving disguised as a
   measurement.

   The key set is the one server.ml's synthetic branch emits with nulls, so
   the page never has to ask which host it is reading before asking what the
   feed is doing. *)
let live ~(alpaca_feed : string) ~(fred_series : string) ~(alpaca : Alpaca_ws.Stats.t)
    ~(fred : Fred_client.Stats.t) () : Yojson.Safe.t =
  `Assoc
    [
      ("kind", `String "alpaca");
      ("alpaca_feed", `String alpaca_feed);
      ("fred_series", `String fred_series);
      ("alpaca", Alpaca_ws.Stats.to_json alpaca);
      ("fred", Fred_client.Stats.to_json fred);
    ]
