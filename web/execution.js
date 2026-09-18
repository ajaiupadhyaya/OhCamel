/* Execution: four tables from /api/desk, computed nowhere.

   Every server string -- an order's state, a venue's reason, the engine's own
   note about what it could not cost -- is written with textContent. */
(function () {
  "use strict";
  var F = window.OhCamelFormat;

  function cell(row, text, cls) {
    var td = document.createElement("td");
    td.textContent = text === null || text === undefined || text === "" ? "—" : String(text);
    if (cls) td.className = cls;
    row.appendChild(td);
    return td;
  }

  function head(table, names) {
    var tr = document.createElement("tr");
    names.forEach(function (n) { tr.appendChild(F.el("th", "", n)); });
    table.appendChild(tr);
  }

  function empty(table, sentence) {
    var tr = document.createElement("tr");
    cell(tr, sentence, "lbl");
    table.appendChild(tr);
  }

  /* What is still working. An order with no venue id has been journaled and not
     yet acknowledged -- a state worth showing rather than hiding, because it is
     the one the lookup schedule is about to resolve. */
  function renderOpen(d) {
    var t = document.getElementById("openorders");
    if (!t) return;
    t.textContent = "";
    var open = (d.orders && d.orders.open) || [];
    if (open.length === 0) { empty(t, "nothing is working"); return; }
    head(t, ["sent", "symbol", "side", "qty", "type", "state", "filled", "venue id"]);
    open.forEach(function (o) {
      var r = document.createElement("tr");
      cell(r, o.created_at, "lbl");
      cell(r, o.symbol);
      cell(r, o.side);
      cell(r, o.qty, "num");
      cell(r, o.type);
      cell(r, o.state);
      cell(r, o.filled_qty, "num");
      cell(r, o.venue_order_id === null ? "not acknowledged" : o.venue_order_id);
      t.appendChild(r);
    });
  }

  /* The costs. A summary over no fills prints dashes, not zeros: the mean of
     nothing is not nought, and a zero here would read as a free fill. */
  function summaryRow(t, label, s) {
    if (!s) return;
    var r = document.createElement("tr");
    cell(r, label, "lbl");
    cell(r, s.count, "num");
    [s.mean_shortfall_bps, s.median_shortfall_bps, s.weighted_shortfall_bps, s.mean_versus_model_bps]
      .forEach(function (v) {
        cell(r, v === null || v === undefined ? null : Number(v).toFixed(1), "num");
      });
    t.appendChild(r);
  }

  function renderTca(d) {
    var t = document.getElementById("tca");
    var note = document.getElementById("tcanote");
    if (!t) return;
    t.textContent = "";
    var tca = d.tca;
    if (!tca) {
      if (note) note.textContent = "no desk in this process, so there are no costs to report";
      return;
    }
    head(t, ["", "fills", "mean", "median", "weighted", "vs model"]);
    summaryRow(t, "overall", tca.overall);
    var by = tca.by_symbol || {};
    Object.keys(by).sort().forEach(function (symbol) { summaryRow(t, symbol, by[symbol]); });
    if (note) note.textContent = tca.note || "";
  }

  function renderSessions(d) {
    var t = document.getElementById("sessions");
    if (!t) return;
    t.textContent = "";
    var rows = d.recent_sessions || [];
    if (rows.length === 0) { empty(t, "no session has closed yet"); return; }
    head(t, ["date", "equity", "gross", "net"]);
    rows.forEach(function (s) {
      var r = document.createElement("tr");
      cell(r, s.date, "lbl");
      cell(r, F.money(s.equity_close), "num");
      cell(r, F.money(s.gross_close), "num");
      cell(r, F.money(s.net_close), "num");
      t.appendChild(r);
    });
  }

  function renderForecasts(d) {
    var t = document.getElementById("forecasts");
    if (!t) return;
    t.textContent = "";
    var rows = d.last_forecasts || [];
    if (rows.length === 0) { empty(t, "no forecast has been recorded yet"); return; }
    head(t, ["estimator", "confidence", "VaR fraction", "VaR", "ES"]);
    rows.forEach(function (f) {
      var r = document.createElement("tr");
      cell(r, f.estimator, "lbl");
      cell(r, F.pct(f.confidence, 0), "num");
      cell(r, F.pct(f.var_fraction), "num");
      cell(r, F.money(f.var_notional), "num");
      cell(r, F.money(f.es_notional), "num");
      t.appendChild(r);
    });
  }

  function draw(d) {
    renderOpen(d);
    renderTca(d);
    renderSessions(d);
    renderForecasts(d);
  }

  /* The /api/desk fetch discipline, copied from dashboard.js's renderDesk
     rather than shared with it -- the Desk page keeps its own copy for the
     blotter and the fills it still draws, and two ten-line copies are cheaper
     than a module that would have to be catted into both. One request in
     flight; the key is taken only once a fetch has drawn, so a failed one is
     asked again by a later frame; an answer a newer frame overtook is
     dropped. The key is the same five fields dashboard.js watches: a sync
     (unmanaged, last_sync), a journal write (version: an order, a fill, a
     session) or the switch and the open-order count, which live in memory
     and not the journal. */
  var deskKey = null, deskWant = null, deskInFlight = false;
  function poll(s) {
    var d = s.desk;
    if (!d) return;
    deskWant = [d.unmanaged, d.last_sync, d.version, d.kill_switch, d.open_orders].join("|");
    if (deskWant === deskKey || deskInFlight) return;
    var asked = deskWant;
    deskInFlight = true;
    fetch("/api/desk").then(function (r) {
      if (!r.ok) throw new Error("status " + r.status);
      return r.json();
    }).then(function (b) {
      if (asked !== deskWant || !b) return;
      draw(b);
      deskKey = asked;
    }).catch(function () { /* the next frame that moves the key tries again */ })
      .then(function () { deskInFlight = false; });
  }

  if (window.OhCamelStream) OhCamelStream.onFrame(poll);
})();
