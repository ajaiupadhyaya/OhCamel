(function () {
  "use strict";

  // The frame state every section shares (the change marks and the stale
  // sets) and the one connection that drives them. Both are catted in above:
  // web/shared.js and web/stream.js.
  var S = window.OhCamelShared;

  // ---- Figure 1 ----
  // The topology is fetched once, by the stream -- it cannot change after
  // construction -- and handed here when it arrives. The handle lights the
  // same names the ledger underlines, from the same frame, because it is the
  // same fact; the stale closure dims the same rows for the same reason.
  var graph = null;
  var NODE_OF_ROW = {
    gross: "gross_exposure", net: "net_exposure", equity: "equity", dd: "current_drawdown",
    var: "var_notional", es: "es_notional", pvar: "parametric_var", pvarewma: "parametric_var_ewma",
    beta: "portfolio_beta", gamma: "portfolio_gamma", vega: "portfolio_vega", dr: "diversification_ratio"
  };
  function nodeOfRow(key) {
    if (NODE_OF_ROW[key]) return NODE_OF_ROW[key];
    if (key.indexOf("pos:") === 0) return "exposure:" + key.slice(4);
    if (key.indexOf("sec:") === 0) return "sector:" + key.slice(4);
    return null;
  }
  function renderGraphFrame(s) {
    if (!graph) return;
    // Dimmed before it is lit, as it was when the feed's health drew first:
    // the two are separate state on the drawing, and the order is kept rather
    // than reasoned about. The list is the stale set this frame computed, so
    // the drawing dims exactly the names the rows mark.
    graph.dim(S.staleSymbols());
    graph.setValues(s.by_node || {});
    var over = 0; s.limits.forEach(function (l) { if (l.breached) over++; });
    graph.setNote("breaches", over + " of " + (s.limits.length + s.unevaluated.length));
    // What changed rides beside what ran: the drawing lights the first and
    // ghosts the difference, which is where a cutoff held.
    graph.light(s.recomputed || [], s.changed || null);
  }
  // Built when the stream's one /api/graph fetch answers. The stream then
  // replays the frame already in hand, so the drawing must exist before it
  // returns -- which is why this is a topology subscriber and not a fetch.
  function buildFigure(topology) {
    var box = document.getElementById("graphbox");
    if (!box || !window.OhCamelGraph) return;
    graph = window.OhCamelGraph.render(box, topology, { inspector: true });
    // The heat starts from the process's history, not from this tab's.
    fetch("/api/heat").then(function (r) { return r.json(); }).then(function (h) {
      if (graph && h && h.nodes) graph.heat(h.nodes);
    }).catch(function () { /* the heat builds from this tab's frames instead */ });
  }
  // ---- The footer: what /api/ops knows ----
  // The stream owns the poll and the masthead's #mode; these are the footer's
  // own fields, which belong to this page, so they are drawn from each answer
  // here.
  function opsNum(x) { return x === null || x === undefined ? "—" : Number(x).toLocaleString("en-US"); }
  function opsMb(bytes) { return bytes === null || bytes === undefined ? "—" : (bytes / 1048576).toFixed(1) + " MB"; }
  function renderOps(o) {
    var $ = function (id) { return document.getElementById(id); };
    var p = o.process || {}, st = o.stream || {}, h = o.history || {}, gc = o.gc || {};
    $("stabilizes").textContent = opsNum(p.stabilizes);
    $("frames").textContent = opsNum(st.frames_sent);
    $("subs").textContent = opsNum(st.subscribers);
    $("coalesce").textContent = st.coalesce_ms === undefined ? "—" : opsNum(Math.round(st.coalesce_ms)) + " ms";
    $("hist").textContent = h.capacity === undefined ? "—"
      : opsNum(h.appended) + " appended / " + opsNum(h.points) + " points / " + opsNum(h.capacity) + " capacity";
    // A pid is an identifier, not a quantity: no thousands separator, so it greps.
    $("pid").textContent = o.pid === undefined || o.pid === null ? "—" : String(o.pid);
    $("host").textContent = o.hostname || "—";
    // OCaml words are 8 bytes on every platform this runs on.
    $("gcheap").textContent = gc.heap_words === undefined ? "—" : opsMb(gc.heap_words * 8);
    $("gcmajor").textContent = opsNum(gc.major_collections);
    $("rss").textContent = opsMb(o.rss_bytes);
    var fs = o.feed_source || {}, f = $("feedstats");
    if (fs.kind === "alpaca" && fs.alpaca && fs.fred) {
      f.hidden = false;
      f.textContent = "Alpaca frames " + opsNum(fs.alpaca.frames) + " · trades " + opsNum(fs.alpaca.trades)
        + " · rejected " + opsNum(fs.alpaca.rejected) + " · reconnects " + opsNum(fs.alpaca.reconnects)
        + " · last error " + (fs.alpaca.last_error || "none")
        + " — FRED polls " + opsNum(fs.fred.polls) + " · last success " + (fs.fred.last_success || "never");
    } else {
      f.hidden = true;
    }
  }
  function money(x) {
    if (x === null || x === undefined) return null;
    var s = Math.abs(Math.round(x)).toLocaleString("en-US");
    return (x < 0 ? "-$" : "$") + s;
  }
  function pct(x, dp) {
    if (x === null || x === undefined) return null;
    return (x * 100).toFixed(dp === undefined ? 2 : dp) + "%";
  }
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = text;
    return e;
  }

  // The value cell, the risk cell and the stale sets are OhCamelShared's: they
  // are the frame's state rather than this section's, and three of the four
  // pages need them without needing anything else in this file.
  function row(table, key, label, note, text, cls, rowCls) {
    var tr = el("tr", rowCls || null);
    var node = nodeOfRow(key);
    if (node && S.staleNode(node)) tr.classList.add("rowstale");
    var k = el("td", "k");
    k.appendChild(document.createTextNode(label));
    if (note) k.appendChild(el("em", null, note));
    tr.appendChild(k);
    tr.appendChild(S.value(key, text, cls));
    table.appendChild(tr);
    return tr;
  }

  function renderPositions(s) {
    var quiet = s.quiet || [];
    var t = document.getElementById("pos");
    t.textContent = "";
    s.positions.forEach(function (p) {
      var tr = row(t, "pos:" + p.symbol, p.symbol, p.sector,
          money(p.exposure), p.exposure < 0 ? "neg" : null,
          S.staleSymbol(p.symbol) ? "rowstale" : null);
      // Stale on schedule is not a broken feed. The demo never ticks one name
      // so the stale path can be watched, and the frame says which one.
      if (quiet.indexOf(p.symbol) >= 0) {
        tr.classList.add("quiet");
        tr.firstChild.appendChild(el("em", "quietlbl", "quiet by design"));
      }
      S.riskCell(tr, "risk:" + p.symbol, p.risk_share, p.risk_over_money);
    });

    var st = document.getElementById("sectors");
    st.textContent = "";
    s.sectors.forEach(function (x) {
      // A sector total is only as good as its worst member.
      var tr = row(st, "sec:" + x.sector, x.sector, null,
          money(x.exposure), x.exposure < 0 ? "neg" : null,
          S.staleSector(x.sector) ? "rowstale" : null);
      S.riskCell(tr, "risk:sec:" + x.sector, x.risk_share);
    });
  }

  function renderBook(s) {
    var t = document.getElementById("book");
    t.textContent = "";
    row(t, "gross", "gross", "magnitudes", money(s.gross_exposure), null, "big");
    row(t, "net", "net", "signed", money(s.net_exposure), s.net_exposure < 0 ? "neg" : null);
    row(t, "equity", "equity", "cash + net", money(s.equity));
    row(t, "dd", "drawdown", "from peak", pct(s.current_drawdown));

    row(t, "var", "VaR 95", "1-day", money(s.value_at_risk_notional), null, "gap");
    row(t, "es", "ES 95", "mean of tail", money(s.expected_shortfall_notional));
    // Shown in dollars like the two above it, so the three are comparable --
    // the gap between the empirical and the normal estimate is the diagnostic,
    // and it is invisible if one of them is a percentage.
    row(t, "pvar", "parametric", "equal-weighted", s.parametric_var === null ? null
        : money(s.parametric_var * s.gross_exposure));
    // The same closed form over a decay-weighted covariance matrix. Sitting
    // directly under its equal-weighted twin because the pair is the reading,
    // not either one: they use the identical window and differ only in how
    // fast the past stops counting, so EWMA above equal-weighted means
    // volatility is rising faster than the flat window has absorbed, and EWMA
    // below it means a shock is ageing out of that window which the market has
    // already stopped pricing.
    row(t, "pvarewma", "parametric", "EWMA " + s.ewma_lambda.toFixed(2),
        s.parametric_var_ewma === null ? null
        : money(s.parametric_var_ewma * s.gross_exposure));
    row(t, "beta", "beta", s.factor, s.portfolio_beta === null ? null
        : s.portfolio_beta.toFixed(3));
    // Gamma and vega, shown only when the book actually holds options. A row
    // reading "0" on an equities book is not information, and a dashboard whose
    // every row is always present trains its reader to stop looking at rows.
    //
    // Vega is divided by 100 here and nowhere else: the engine and the wire
    // format both carry it per 1.00 of annualised vol, which is the unit the
    // limits are written in, and the desk's "per vol point" is a rendering
    // convention. Applying it upstream would make the API and the limit
    // thresholds disagree by a factor of a hundred.
    if (s.portfolio_gamma !== 0 || s.portfolio_vega !== 0) {
      row(t, "gamma", "gamma", "$ delta / 1.00 move", money(s.portfolio_gamma),
          s.portfolio_gamma < 0 ? "neg" : null, "gap");
      row(t, "vega", "vega", "$ / vol pt", money(s.portfolio_vega / 100),
          s.portfolio_vega < 0 ? "neg" : null);
      // Vega by tenor, shown only when more than one bucket is occupied.
      //
      // With a single expiry the total IS the bucket and a second row would be
      // the same number twice. With two or more, the total is a parallel-shift
      // approximation that can read zero on a book holding real term-structure
      // risk -- which is precisely when the breakdown is worth the space.
      var buckets = s.vega_by_bucket || {};
      var names = Object.keys(buckets);
      if (names.length > 1) {
        for (var bi = 0; bi < names.length; bi++) {
          row(t, "vega:" + names[bi], "\u00a0\u00a0" + names[bi], "of which",
              money(buckets[names[bi]] / 100),
              buckets[names[bi]] < 0 ? "neg" : null);
        }
      }
    }
    else if (window.OhCamelStream && window.OhCamelStream.mode() === "live") {
      // Said in the place the rows would be, rather than left to be
      // discovered: the live host has no options-chain source, so the options
      // branch of the graph is built and never fed.
      row(t, "optoff", "options", "DISABLED — no options-chain source", "off", null, "gap").id = "optoff";
    }
    // Sum of standalone position volatilities over portfolio volatility, so at
    // least 1.00. What the book is getting from being a portfolio rather than a
    // pile of positions -- and the number that falls toward 1.00 as
    // correlations converge, which is what a selloff does.
    row(t, "dr", "diversification", "vs. standalone", s.diversification_ratio === null
        ? null : s.diversification_ratio.toFixed(2) + "x");

    if (s.warming_up) {
      var tr = el("tr", "gap");
      var td = el("td", "k");
      td.colSpan = 3;
      td.style.color = "var(--unknown)";
      td.style.fontSize = "12px";
      td.textContent = "warming up — not enough return history to form a distribution";
      tr.appendChild(td);
      t.appendChild(tr);
    }
  }

  // A sparkline, drawn as inline SVG built by hand.
  //
  // No charting library, and not because one would be hard to add -- because
  // dashboard_html.ml is a single string compiled into the binary, and the
  // whole point of that is that the dashboard has no external dependency to
  // fetch, version, or fail to fetch. A CDN script tag would make this page
  // stop working on a machine with no route to the internet, which is exactly
  // the machine a risk dashboard is most likely to be pinned to.
  //
  // Sixty lines of SVG buys the two things a trail actually needs: the shape,
  // and the endpoints. It does not buy axes, zoom, tooltips or a legend, and
  // it is not trying to.
  function sparkline(values, opts) {
    var w = 260, h = 44, pad = 3;
    var pts = [];
    for (var i = 0; i < values.length; i++) {
      if (values[i] !== null && isFinite(values[i])) pts.push([i, values[i]]);
    }
    if (pts.length < 2) return null;
    var lo = pts[0][1], hi = pts[0][1];
    for (var j = 1; j < pts.length; j++) {
      if (pts[j][1] < lo) lo = pts[j][1];
      if (pts[j][1] > hi) hi = pts[j][1];
    }
    // A dead-flat series has no range to scale against. Drawing it through the
    // middle is the honest rendering: the line is flat because the number did
    // not move, not because the scale collapsed.
    var span = (hi - lo) || 1;
    var n = values.length - 1 || 1;
    var d = "";
    for (var k = 0; k < pts.length; k++) {
      var x = pad + (pts[k][0] / n) * (w - 2 * pad);
      var y = h - pad - ((pts[k][1] - lo) / span) * (h - 2 * pad);
      d += (k === 0 ? "M" : "L") + x.toFixed(1) + " " + y.toFixed(1);
    }
    var last = pts[pts.length - 1];
    var lx = pad + (last[0] / n) * (w - 2 * pad);
    var ly = h - pad - ((last[1] - lo) / span) * (h - 2 * pad);
    var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 " + w + " " + h);
    svg.setAttribute("width", w);
    svg.setAttribute("height", h);
    var path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", d);
    path.setAttribute("fill", "none");
    path.setAttribute("stroke", opts.stroke);
    path.setAttribute("stroke-width", "1.4");
    path.setAttribute("stroke-linejoin", "round");
    svg.appendChild(path);
    var dot = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    dot.setAttribute("cx", lx.toFixed(1));
    dot.setAttribute("cy", ly.toFixed(1));
    dot.setAttribute("r", "2");
    dot.setAttribute("fill", opts.stroke);
    svg.appendChild(dot);
    return { svg: svg, lo: lo, hi: hi };
  }

  function renderHistory(h) {
    var el = document.getElementById("history");
    el.textContent = "";
    // Two or fewer points is not a trail, and a one-pixel line implying a
    // track record would be worse than saying so.
    if (!h || h.points < 2) {
      var p = document.createElement("div");
      p.className = "k";
      p.style.fontSize = "12px";
      p.style.color = "var(--ink-faint)";
      p.textContent = "collecting — a trail appears once the book has moved twice";
      el.appendChild(p);
      return;
    }
    var series = [
      { key: "gross", label: "gross", stroke: "var(--ink)", fmt: money },
      { key: "net", label: "net", stroke: "var(--ink-soft)", fmt: money },
      { key: "var_notional", label: "VaR 95", stroke: "var(--mark)", fmt: money },
      { key: "drawdown", label: "drawdown", stroke: "var(--over)", fmt: pct }
    ];
    for (var i = 0; i < series.length; i++) {
      var s = series[i];
      var drawn = sparkline(h[s.key], { stroke: s.stroke });
      var rowEl = document.createElement("div");
      rowEl.style.display = "flex";
      rowEl.style.alignItems = "center";
      rowEl.style.justifyContent = "space-between";
      rowEl.style.gap = "10px";
      rowEl.style.marginBottom = "2px";
      var lbl = document.createElement("span");
      lbl.className = "k";
      lbl.style.whiteSpace = "nowrap";
      lbl.textContent = s.label;
      rowEl.appendChild(lbl);
      if (drawn) {
        rowEl.appendChild(drawn.svg);
        var range = document.createElement("span");
        range.className = "k";
        range.style.fontSize = "11px";
        range.style.color = "var(--ink-faint)";
        range.textContent = s.fmt(drawn.lo) + " – " + s.fmt(drawn.hi);
        rowEl.appendChild(range);
      } else {
        var none = document.createElement("span");
        none.className = "k";
        none.style.color = "var(--ink-faint)";
        none.textContent = "—";
        rowEl.appendChild(none);
      }
      el.appendChild(rowEl);
    }
    // Says how much of the session is on screen. "500 points" and "500 of
    // 40,000 changes" are different statements and only the second is honest.
    document.getElementById("histnote").textContent =
      "— last " + h.points + " of " + h.appended.toLocaleString("en-US")
      + " changes, in memory only, lost on restart";
  }

  // Fetched rather than pushed. The SSE frame carries the current state, which
  // is what every other panel needs; shipping the whole trail on every tick
  // would multiply the frame size by five hundred to redraw a line that moved
  // by one point. So the stream says WHEN, and this asks for the history.
  var historyInFlight = false;
  function refreshHistory() {
    if (historyInFlight) return;
    historyInFlight = true;
    fetch("/api/history")
      .then(function (r) { return r.json(); })
      .then(function (h) { renderHistory(h); })
      .catch(function () { /* the next change will try again */ })
      .then(function () { historyInFlight = false; });
  }

  // ---- the desk: the venue's account, from the frame's desk object ----
  // The frame carries sixteen fields and nothing a table needs a second
  // request for, except what /api/desk lists: the names the venue holds
  // outside the book, the switch whole, the blotter and the fills. Those are
  // fetched when something they show has moved -- the key built at the end of
  // renderDesk -- and not on every frame.
  var deskKey = null, deskWant = null, deskInFlight = false;
  function renderDesk(s) {
    var d = s.desk, t = document.getElementById("desk"), note = document.getElementById("desknote");
    if (!t || !note) return;
    var F = window.OhCamelFormat;
    if (!d) { t.textContent = ""; note.textContent = "— no desk in this process"; return; }
    note.textContent = d.status === "enabled"
      ? "— " + d.venue + (d.venue === "simulated" ? ", in this process" : (d.trading ? ", trading" : ", read side"))
      : "— disabled: " + (d.reason || "no reason given");
    var rows = [
      ["equity", d.equity === null ? "—" : F.money(d.equity)],
      ["cash", d.cash === null ? "—" : F.money(d.cash)],
      ["session P&L", d.session_pnl === null ? "—" : F.money(d.session_pnl)],
      // "on" beside a tripped or halted switch would read as orders going
      // out; the switch refuses every one, so the row says so.
      ["trading", !d.trading ? "off" : d.kill_switch === "clear" ? "on" : "on, but the switch refuses new orders"],
      ["kill switch", typeof d.kill_switch === "string" ? d.kill_switch : "—"],
      // With no order manager attached the server sends 0 for want of a count;
      // nothing is counting, so the page says it does not know.
      ["open orders", d.tickets === "none" || typeof d.open_orders !== "number" ? "—" : String(d.open_orders)],
      ["sessions recorded", String(d.sessions) + (d.journal === "memory" ? " · in memory" : "")],
      ["last sync", d.last_sync ? d.last_sync.replace("T", " ").slice(0, 19) + "Z" : (d.last_error ? "failed" : "never")],
      ["unmanaged", d.unmanaged === 0 ? "none" : d.unmanaged + " held outside the book"]
    ];
    t.textContent = "";
    rows.forEach(function (r) {
      var tr = document.createElement("tr");
      tr.appendChild(F.el("td", "k", r[0]));
      tr.appendChild(F.el("td", "v num", r[1]));
      t.appendChild(tr);
    });
    if (d.last_error) {
      var tr = document.createElement("tr");
      tr.appendChild(F.el("td", "k", "last error"));
      tr.appendChild(F.el("td", "v desk-error", d.last_error));
      t.appendChild(tr);
    }
    renderTicketControls(d, s);
    // /api/desk is asked again when anything it draws has moved: a sync (the
    // unmanaged count, last_sync), a journal write (the version: an order, a
    // fill, a session), the switch, or the open-order count. The last two live
    // in memory, not the journal, so the version alone would miss a trip; and
    // a sync writes nothing to the journal, so the version alone would miss
    // that too (A1's reason for its key). One request at a time; the key is
    // taken only once a fetch has drawn, so a failed one is asked again by a
    // later frame; an answer a newer frame overtook is dropped.
    deskWant = [d.unmanaged, d.last_sync, d.version, d.kill_switch, d.open_orders].join("|");
    if (deskWant === deskKey || deskInFlight) return;
    var asked = deskWant;
    deskInFlight = true;
    fetch("/api/desk").then(function (r) {
      // An error status carries {"error": ...}, not the desk: drawn, it would
      // say "no orders yet" about a desk that could not be read.
      if (!r.ok) throw new Error("status " + r.status);
      return r.json();
    }).then(function (b) {
      if (asked !== deskWant || !b) return;
      renderDeskBody(b);
      deskKey = asked;
    }).catch(function () { /* the frame's fields above still stand */ })
      .then(function () { deskInFlight = false; });
  }

  function renderDeskBody(b) {
    var u = document.getElementById("deskunmanaged");
    var held = b.unmanaged_positions;
    if (!Array.isArray(held) || held.length === 0) { u.hidden = true; u.textContent = ""; }
    else {
      // The text first, then shown, as A1's page did.
      u.textContent = "held by the venue, not in the book: " + held.map(function (p) {
        return p.symbol + " " + (typeof p.qty === "number" ? p.qty : "—");
      }).join(", ");
      u.hidden = false;
    }
    renderSwitch(b);
    renderBlotter(b.orders || { open: [], recent: [] });
    renderFills(b.fills || [], b.tca);
  }

  // Every number below can arrive as null -- the server sends null for a
  // figure it could not compute -- and an unknown is drawn as a dash, never
  // as 0 or as the word null.
  function known(x) { return typeof x === "number" && isFinite(x); }
  function fixed2(x) { return known(x) ? x.toFixed(2) : "—"; }
  function dollars(x) { return known(x) ? window.OhCamelFormat.money(x) : "—"; }

  // The header a cross-site form cannot set. The browser adds Sec-Fetch-Site
  // and Origin itself, and the live host refuses a request without them.
  function deskPost(path, body, protectedRoute) {
    var headers = { "Content-Type": "application/json" };
    if (protectedRoute) headers["X-OhCamel-Desk"] = "1";
    return fetch(path, { method: "POST", headers: headers, body: JSON.stringify(body) })
      .then(function (r) { return r.json().then(function (j) { return { status: r.status, body: j }; }); });
  }

  // A halt or a reset the server refused says why, in the server's sentence;
  // alert() shows it as text. Either way the desk is asked again, so the line
  // shows what the switch reads now rather than what was pressed.
  function switchPost(path, body) {
    deskPost(path, body, true).then(function (res) {
      deskKey = null;
      if (res.body && res.body.error) window.alert(res.body.error);
    }).catch(function (e) { deskKey = null; window.alert("no answer: " + e.message); });
  }

  function renderSwitch(b) {
    var box = document.getElementById("deskswitch"), sw = b.switch, F = window.OhCamelFormat;
    if (!box) return;
    if (!sw) { box.hidden = true; box.textContent = ""; return; }
    box.textContent = "";
    box.className = "desk-switch " + sw.state;
    var line = sw.state === "clear" ? "kill switch clear: orders may be sent"
      : sw.state === "tripped" ? "kill switch TRIPPED by " + sw.limit + ": new orders refused, open orders cancelled, positions untouched"
      : "desk HALTED by hand (" + sw.why + "): new orders refused, open orders cancelled, positions untouched";
    box.appendChild(F.el("span", "desk-switch-line", line));
    if (sw.state === "tripped" && known(sw.auto_reset_s))
      box.appendChild(F.el("span", "desk-switch-note", known(sw.resets_in_s)
        ? "· resets itself in " + Math.ceil(sw.resets_in_s) + " s — the demo only"
        : "· resets itself " + sw.auto_reset_s + " s after " + sw.limit + " clears — the demo only"));
    // The buttons only where tickets are accepted: the demo host answers 405
    // to both routes, and a button that can only be refused is not offered.
    if (b.tickets === "accepted") {
      var btn = document.createElement("button");
      btn.type = "button";
      if (sw.state === "clear") {
        btn.textContent = "halt the desk";
        btn.onclick = function () {
          var why = window.prompt("Why halt the desk? Every open order will be cancelled; positions stay.");
          if (why === null) return;
          switchPost("/api/desk/kill", { why: why });
        };
      } else {
        btn.textContent = "reset";
        btn.onclick = function () {
          if (!window.confirm("Reset the kill switch? New orders will be allowed again.")) return;
          switchPost("/api/desk/kill/reset", { confirm: "reset" });
        };
      }
      box.appendChild(btn);
    }
    // The line is built before it is shown, as A1's list is.
    box.hidden = false;
  }

  var ticketReady = false;
  function renderTicketControls(d, s) {
    var form = document.getElementById("ticket");
    if (!form) return;
    // Place is offered only where tickets are accepted; preview wherever an
    // order manager exists to answer it.
    var place = document.getElementById("tplace");
    place.hidden = d.tickets !== "accepted";
    document.getElementById("ticketnote").textContent = d.tickets === "accepted"
      ? "— the rules, then the limits, then the venue"
      : "— preview only on this host: the rules and the limits answer, and nothing is sent";
    if (!ticketReady) {
      ticketReady = true;
      var sym = document.getElementById("tsym");
      s.positions.forEach(function (p) {
        var o = document.createElement("option");
        o.value = p.symbol; o.textContent = p.symbol;
        sym.appendChild(o);
      });
      var type = document.getElementById("ttype"), limit = document.getElementById("tlimit");
      type.onchange = function () { limit.disabled = type.value !== "limit"; };
      // Enter in a field must not submit the form: a form with no action
      // reloads the page, and nothing here is meant to leave by that road.
      form.onsubmit = function (e) { e.preventDefault(); };
      document.getElementById("tpreview").onclick = function () { sendTicket(false); };
      place.onclick = function () {
        if (!window.confirm("Send this order to the venue? It passes the rules and the limits first.")) return;
        sendTicket(true);
      };
    }
    // Shown last, once its note and its symbols are in.
    form.hidden = d.status !== "enabled" || d.tickets === "none";
  }

  function ticketBody() {
    var body = {
      symbol: document.getElementById("tsym").value,
      side: document.getElementById("tside").value,
      qty: Number(document.getElementById("tqty").value),
      type: document.getElementById("ttype").value
    };
    if (body.type === "limit") body.limit_price = Number(document.getElementById("tlimit").value);
    return body;
  }

  function sendTicket(placing) {
    var out = document.getElementById("ticketout"), F = window.OhCamelFormat;
    out.textContent = placing ? "sending…" : "asking…";
    deskPost(placing ? "/api/desk/orders" : "/api/desk/preview", ticketBody(), placing).then(function (res) {
      var b = res.body || {}, p = (placing ? b.preview : b) || {};
      out.textContent = "";
      if (b.error) { out.appendChild(F.el("div", "ticket-bad", b.error)); return; }
      // A placement the venue refused, or one the desk declared failed, is no
      // working order as far as the desk knows: it reads as a failure, with the
      // server's reason as text beneath it.
      var venueRefused = placing && b.state === "rejected_by_venue";
      var failed = placing && b.state === "failed";
      var ok = placing
        ? b.state !== "rejected_pre_trade" && !venueRefused && !failed
        : p.passed === true;
      var head = placing
        ? (ok ? "sent: " + String(b.state).replace(/_/g, " ")
          : venueRefused ? "refused by the venue"
          : failed ? "failed"
          : "refused before the venue")
        : (ok ? "would pass the rules and the limits" : "would be refused");
      out.appendChild(F.el("div", ok ? "ticket-ok" : "ticket-bad", head));
      if ((venueRefused || failed) && b.reason) out.appendChild(F.el("div", "ticket-reason", b.reason));
      (p.reasons || []).forEach(function (r) { out.appendChild(F.el("div", "ticket-reason", r)); });
      if (p.gate) {
        out.appendChild(F.el("div", "ticket-gate",
          "gross " + dollars(p.gate.gross_before) + " → " + dollars(p.gate.gross_after) +
          " · equity " + dollars(p.gate.equity_before) + " → " + dollars(p.gate.equity_after) +
          ((p.gate.cleared || []).length ? " · clears " + p.gate.cleared.map(function (m) { return m.limit; }).join(", ") : "")));
      }
      if (placing) deskKey = null;
    }).catch(function (e) {
      out.textContent = "no answer: " + e.message;
      // A placed order whose answer was lost may still exist; the blotter is
      // asked again so it says.
      if (placing) deskKey = null;
    });
  }

  function renderBlotter(orders) {
    var t = document.getElementById("blotter"), F = window.OhCamelFormat;
    if (!t) return;
    t.textContent = "";
    var seen = {};
    var rows = (orders.open || []).concat(orders.recent || []).filter(function (o) {
      if (seen[o.client_order_id]) return false;
      seen[o.client_order_id] = true;
      return true;
    }).slice(0, 20);
    if (rows.length === 0) {
      var empty = document.createElement("tr");
      empty.appendChild(F.el("td", "k", "no orders yet"));
      t.appendChild(empty);
      return;
    }
    var head = document.createElement("tr");
    ["time", "symbol", "side", "qty", "type", "state", "filled", "avg", "why"].forEach(function (h) {
      head.appendChild(F.el("th", "", h));
    });
    t.appendChild(head);
    rows.forEach(function (o) {
      var tr = document.createElement("tr");
      tr.className = "order " + o.state;
      tr.appendChild(F.el("td", "k", String(o.created_at).slice(11, 19)));
      tr.appendChild(F.el("td", "k", o.symbol));
      tr.appendChild(F.el("td", "k", o.side));
      tr.appendChild(F.el("td", "v num", known(o.qty) ? String(o.qty) : "—"));
      tr.appendChild(F.el("td", "k", o.type === "limit" ? "limit " + fixed2(o.limit_price) : "market"));
      tr.appendChild(F.el("td", "k state", String(o.state).replace(/_/g, " ")));
      tr.appendChild(F.el("td", "v num", known(o.filled_qty) ? String(o.filled_qty) : "—"));
      tr.appendChild(F.el("td", "v num", fixed2(o.avg_fill_price)));
      // A reason is the rules', the gate's or the venue's own words; it is
      // set as text, never parsed as markup.
      tr.appendChild(F.el("td", "k reason", o.reason || (o.source === "demo" ? "the demo's trader" : "")));
      t.appendChild(tr);
    });
  }

  // A cost that rounds to nothing is 0.0, not the -0.0 toFixed gives it.
  function bps(x) {
    if (!known(x)) return "—";
    var t = x.toFixed(1);
    return t === "-0.0" ? "0.0" : t;
  }

  function renderFills(fills, tca) {
    var t = document.getElementById("fills"), note = document.getElementById("tcanote"), F = window.OhCamelFormat;
    if (!t) return;
    /* The note is the Execution page's; a page that has the fills without it
       renders the table and says nothing about what could not be costed. */
    t.textContent = "";
    if (fills.length === 0) { if (note) note.textContent = ""; return; }
    var head = document.createElement("tr");
    ["time", "symbol", "side", "qty", "price", "shortfall", "delay", "slippage", "½ spread", "vs model"].forEach(function (h) {
      head.appendChild(F.el("th", "", h));
    });
    t.appendChild(head);
    fills.forEach(function (f) {
      var tr = document.createElement("tr");
      [String(f.at).slice(11, 19), f.symbol, f.side].forEach(function (x) { tr.appendChild(F.el("td", "k", x)); });
      tr.appendChild(F.el("td", "v num", known(f.qty) ? String(f.qty) : "—"));
      tr.appendChild(F.el("td", "v num", fixed2(f.price)));
      [f.shortfall_bps, f.delay_bps, f.slippage_bps, f.half_spread_bps, f.versus_model_bps].forEach(function (x) {
        tr.appendChild(F.el("td", "v num", bps(x)));
      });
      t.appendChild(tr);
    });
    if (note) {
      var o = tca && tca.overall;
      note.textContent = (o
        ? "Basis points; positive is cost. " + o.count + " fills: shortfall mean " + bps(o.mean_shortfall_bps) +
          ", median " + bps(o.median_shortfall_bps) + ", quantity-weighted " + bps(o.weighted_shortfall_bps) + ". "
        : "") + (tca && tca.note ? tca.note : "");
    }
  }

  // This page's sections, from a frame the stream has already begun: the
  // masthead, the banners and the two clocks are its, and the stale sets and
  // the change marks are already computed when this runs.
  function render(s) {
    renderPositions(s);
    renderBook(s);
    renderDesk(s);
    renderGraphFrame(s);
    document.getElementById("nodes").textContent = s.nodes_recomputed.toLocaleString("en-US");
    if (s.stabilizes !== undefined && s.stabilizes !== null)
      document.getElementById("stabilizes").textContent = s.stabilizes.toLocaleString("en-US");
    var a = s.alerts || {};
    document.getElementById("alertstat").textContent =
      a.enabled ? ("alerts sent " + a.sent + (a.failed ? ", failed " + a.failed : ""))
                : "alerting disabled";
    refreshHistory();
  }

  window.OhCamelStream.onFrame(render);
  window.OhCamelStream.onOps(renderOps);
  window.OhCamelStream.onTopology(buildFigure);
})();
