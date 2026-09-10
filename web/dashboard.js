(function () {
  "use strict";

  var prev = {};          // last rendered value per key, for change marks
  var firstFrame = true;  // do not mark everything as "moved" on load

  // ---- Figure 1, and the header's two clocks ----
  // The topology is fetched once: it cannot change after construction. The
  // handle lights the same names the ledger underlines, from the same frame,
  // because it is the same fact; the stale closure dims the same rows for the
  // same reason.
  var topology = null, graph = null, ops = null, pendingFrame = null;
  var staleNodes = new Set();          // downstream closure of every stale price, by node name
  var lastFrameAt = null, lastPrintAt = null;
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
    pendingFrame = s;
    // Set here rather than in render, so a frame that arrived before the
    // topology gets its denominator when the topology does.
    document.getElementById("ran").textContent =
      (s.recomputed ? s.recomputed.length : "—") + " of " + (topology ? topology.counts.named : "—");
    if (!graph) return;
    graph.setValues(s.by_node || {});
    var over = 0; s.limits.forEach(function (l) { if (l.breached) over++; });
    graph.setNote("breaches", over + " of " + (s.limits.length + s.unevaluated.length));
    graph.light(s.recomputed || []);
  }
  // A frame that arrived before the topology (or, on the live host, before
  // /api/ops) was set without it: stale rows by symbol rather than by closure,
  // no limit dimmed, no options line. Set the ledger again from that same frame
  // when it arrives, rather than waiting for the next frame, which a parked host
  // may not send for hours. The same frame twice marks nothing as moved.
  function resetLedger() {
    var s = pendingFrame;
    if (!s) return;
    renderHealth(s); renderPositions(s); renderBook(s); renderLimits(s);
  }
  function loadGraph() {
    var box = document.getElementById("graphbox");
    if (!box || !window.OhCamelGraph) return;
    fetch("/api/graph").then(function (r) { return r.json(); }).then(function (t) {
      topology = t;
      graph = window.OhCamelGraph.render(box, topology, { inspector: true });
      if (pendingFrame) { resetLedger(); renderGraphFrame(pendingFrame); }
    }).catch(function () { /* the figure stays empty; the ledger does not depend on it */ });
  }
  function loadOps() {
    fetch("/api/ops").then(function (r) { return r.json(); }).then(function (o) {
      var first = ops === null;
      ops = o;
      document.getElementById("mode").textContent =
        o.mode === "live" ? "live · Alpaca + FRED" : "demo · synthetic feed";
      if (first && o.mode === "live") resetLedger();
    }).catch(function () { /* the header keeps its default word */ });
  }
  // Time_ns.to_string_utc prints nanoseconds and a space; Date.parse wants
  // milliseconds and a T.
  function parseUtc(s) { return Date.parse(s.replace(" ", "T").replace(/(\.\d{1,3})\d*Z$/, "$1Z")); }
  function age(ms) { return ms < 60000 ? (ms / 1000).toFixed(1) + " s" : Math.round(ms / 60000) + " min"; }
  function clocks() {
    var now = Date.now();
    var lf = document.getElementById("lastframe"), lp = document.getElementById("lastprint");
    if (lastFrameAt !== null) {
      var f = now - lastFrameAt;
      lf.textContent = age(f) + (f > 60000 ? " parked" : "");
      lf.className = "v num" + (f > 60000 ? " parked" : "");
    }
    if (lastPrintAt !== null) {
      // A print is stamped by the host's clock and aged by this browser's; a
      // host a second ahead would otherwise show a print from the future.
      var p = Math.max(0, now - lastPrintAt), threshold = ((ops && ops.feed && ops.feed.staleness_threshold_s) || 90) * 1000;
      lp.textContent = age(p);
      lp.className = "v num" + (p > threshold ? " over" : p > threshold / 2 ? " warm" : "");
    }
  }
  // A display clock, not a poll: it reads two timestamps and asks the server nothing.
  setInterval(clocks, 250);
  loadOps(); loadGraph();

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

  // A value cell. Marks itself if this key's text differs from last frame --
  // which is the page's one piece of ornament and is entirely data-driven.
  function value(key, text, extraClass) {
    var td = el("td", "v num" + (extraClass ? " " + extraClass : ""));
    var shown = (text === null || text === undefined) ? "—" : text;
    td.textContent = shown;
    if (!firstFrame && prev[key] !== undefined && prev[key] !== shown) {
      td.classList.add("moved");
    }
    prev[key] = shown;
    return td;
  }

  var staleSet = {};       // symbols with no recent print
  var staleSectors = {};   // sectors holding at least one of them

  function row(table, key, label, note, text, cls, rowCls) {
    var tr = el("tr", rowCls || null);
    var node = nodeOfRow(key);
    if (node && staleNodes.has(node)) tr.classList.add("rowstale");
    var k = el("td", "k");
    k.appendChild(document.createTextNode(label));
    if (note) k.appendChild(el("em", null, note));
    tr.appendChild(k);
    tr.appendChild(value(key, text, cls));
    table.appendChild(tr);
    return tr;
  }

  // A row's share of portfolio VaR, and that share over its share of money.
  //
  // Both come from the encoder (risk_share, risk_over_money): invariant 2
  // applied to a division. This page used to sum the components and divide
  // here; it no longer does arithmetic on risk. A NEGATIVE share is a hedge:
  // it gets the ok colour and keeps its sign.
  function riskCell(tr, key, share, ratio) {
    var td = el("td", "risk");
    if (share === null || share === undefined) {
      td.textContent = "—";
    } else {
      var shown = (share * 100).toFixed(1) + "%";
      td.appendChild(document.createTextNode(shown));
      if (share < 0) td.classList.add("hedge");
      if (!firstFrame && prev[key] !== undefined && prev[key] !== shown) td.classList.add("moved");
      prev[key] = shown;
    }
    if (ratio !== undefined) td.appendChild(el("span", "rm", ratio === null ? "--" : ratio.toFixed(2) + "×"));
    tr.appendChild(td);
  }

  function renderPositions(s) {
    var quiet = s.quiet || [];
    var t = document.getElementById("pos");
    t.textContent = "";
    s.positions.forEach(function (p) {
      var tr = row(t, "pos:" + p.symbol, p.symbol, p.sector,
          money(p.exposure), p.exposure < 0 ? "neg" : null,
          staleSet[p.symbol] ? "rowstale" : null);
      // Stale on schedule is not a broken feed. The demo never ticks one name
      // so the stale path can be watched, and the frame says which one.
      if (quiet.indexOf(p.symbol) >= 0) {
        tr.classList.add("quiet");
        tr.firstChild.appendChild(el("em", "quietlbl", "quiet by design"));
      }
      riskCell(tr, "risk:" + p.symbol, p.risk_share, p.risk_over_money);
    });

    var st = document.getElementById("sectors");
    st.textContent = "";
    s.sectors.forEach(function (x) {
      // A sector total is only as good as its worst member.
      var tr = row(st, "sec:" + x.sector, x.sector, null,
          money(x.exposure), x.exposure < 0 ? "neg" : null,
          staleSectors[x.sector] ? "rowstale" : null);
      riskCell(tr, "risk:sec:" + x.sector, x.risk_share);
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
    else if (ops && ops.mode === "live") {
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

  function renderLimits(s) {
    var box = document.getElementById("limits");
    box.textContent = "";

    s.limits.forEach(function (l) {
      var d = el("div", "lim" + (l.breached ? " over" : ""));
      if (staleNodes.has("limit:" + l.name)) d.classList.add("stale");
      var top = el("div", "top");
      top.appendChild(el("span", "name", l.name));
      top.appendChild(el("span", "scope", l.scope));
      var p = el("span", "pct num");
      var key = "lim:" + l.name;
      var shown = (l.utilisation * 100).toFixed(0) + "%";
      var q = el("span", "q", shown);
      if (!firstFrame && prev[key] !== undefined && prev[key] !== shown) q.classList.add("moved");
      prev[key] = shown;
      p.appendChild(q);
      top.appendChild(p);
      d.appendChild(top);

      var bar = el("div", "bar");
      var fill = el("i");
      fill.style.width = Math.max(0, Math.min(100, l.utilisation * 100)) + "%";
      bar.appendChild(fill);
      d.appendChild(bar);

      var fmt = l.unit === "fraction" ? function (x) { return pct(x); } : money;
      d.appendChild(el("div", "detail",
        l.breached
          ? fmt(l.observed) + " over " + fmt(l.threshold) + " by " + fmt(l.excess)
          : fmt(l.observed) + " of " + fmt(l.threshold) + ", " + fmt(-l.excess) + " to spare"));
      box.appendChild(d);
    });

    s.unevaluated.forEach(function (name) {
      var d = el("div", "lim na");
      var top = el("div", "top");
      top.appendChild(el("span", "name", name));
      top.appendChild(el("span", "pct num", "n/a"));
      d.appendChild(top);
      d.appendChild(el("div", "detail", "input unavailable — not the same as passing"));
      box.appendChild(d);
    });
  }

  function renderHealth(s) {
    var h = s.feed;
    staleSet = {};
    staleSectors = {};
    h.stale.concat(h.never_seen).forEach(function (sym) { staleSet[sym] = true; });
    s.positions.forEach(function (p) {
      if (staleSet[p.symbol] && p.sector) staleSectors[p.sector] = true;
    });
    // Staleness follows the edges, not the column: the downstream closure of
    // each stale price, from the served topology, on the drawing and on the
    // ledger's rows alike. Until the topology has arrived the rows fall back
    // to the symbol match above.
    staleNodes = new Set();
    var staleSyms = h.stale.concat(h.never_seen);
    if (topology && window.OhCamelGraph) {
      staleSyms.forEach(function (sym) {
        var cell = "price[" + sym + "]";
        staleNodes.add(cell);
        window.OhCamelGraph.closure(topology, [cell], "down").forEach(function (n) { staleNodes.add(n); });
      });
    }
    if (graph) graph.dim(staleSyms);
    lastPrintAt = null;
    (h.symbols || []).forEach(function (st) {
      if (!st.last_tick) return;
      var t = parseUtc(st.last_tick);
      if (!isNaN(t) && (lastPrintAt === null || t > lastPrintAt)) lastPrintAt = t;
    });
    var feed = document.getElementById("feed");
    feed.textContent = "";
    var dot = el("span", "dot" + (h.healthy ? "" : " bad"));
    feed.appendChild(dot);

    var warn = document.getElementById("warn");
    var main = document.getElementById("main");

    if (h.healthy) {
      feed.appendChild(document.createTextNode("live"));
      warn.className = "";
      warn.textContent = "";
      main.classList.remove("stale");
    } else if (h.stale.length) {
      feed.appendChild(document.createTextNode(h.stale.length + " stale"));
      warn.className = "on";
      warn.textContent = "";
      warn.appendChild(el("b", null, "Prices are stale: " + h.stale.join(", ") + ". "));
      warn.appendChild(document.createTextNode(
        "Everything below was computed from old marks. A limit that is not breached on a stale price is not information."));
      main.classList.add("stale");
    } else {
      feed.appendChild(document.createTextNode("no prints"));
      warn.className = "on";
      warn.textContent = "";
      warn.appendChild(el("b", null, "No prints yet for " + h.never_seen.join(", ") + ". "));
      warn.appendChild(document.createTextNode(
        "The subscription may not have taken, or the market may be closed."));
      main.classList.remove("stale");
    }
  }

  // Phase 4 state. The page reports it and cannot change it: no route on the
  // server arms, trips or resets anything.
  function renderAlerts(s) {
    var a = s.alerts || { enabled: false, kill_switch: "off" };
    var ks = document.getElementById("ks");
    var halt = document.getElementById("halt");

    ks.className = "v ks " + (a.kill_switch === "tripped" ? "tripped"
                            : a.kill_switch === "armed" ? "armed" : "off");
    ks.textContent = !a.enabled ? "off"
      : a.kill_switch === "tripped" ? "HALTED"
      : a.kill_switch === "armed" ? "armed"
      : "on, no switch";

    if (a.kill_switch === "tripped") {
      halt.className = "on";
      halt.textContent = "";
      halt.appendChild(el("b", null, "NEW ORDERS HALTED"));
      halt.appendChild(document.createTextNode(
        "  \u2014 tripped by " + a.tripped_by + ". "));
      halt.appendChild(el("span", null,
        "This sets a flag and nothing else; no order is placed or cancelled by this system. "
        + "It stays set until the engine is restarted or the switch is reset."));
    } else {
      halt.className = "";
      halt.textContent = "";
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

  function render(s) {
    renderAlerts(s);
    renderHealth(s);
    renderPositions(s);
    renderBook(s);
    renderLimits(s);
    renderGraphFrame(s);
    lastFrameAt = Date.now();
    clocks();
    document.getElementById("nsym").textContent =
      s.positions.length + " / " + s.sectors.length + " sectors";
    document.getElementById("nodes").textContent = s.nodes_recomputed.toLocaleString("en-US");
    document.getElementById("asof").textContent = s.as_of.replace("T", " ").slice(0, 19) + "Z";
    var a = s.alerts || {};
    document.getElementById("alertstat").textContent =
      a.enabled ? ("alerts sent " + a.sent + (a.failed ? ", failed " + a.failed : ""))
                : "alerting disabled";
    refreshHistory();
    firstFrame = false;
  }

  var conn = document.getElementById("conn");
  var src = new EventSource("/api/stream");
  src.onmessage = function (e) {
    conn.textContent = "stream connected";
    try { render(JSON.parse(e.data)); }
    catch (err) { conn.textContent = "bad frame: " + err.message; }
  };
  src.onerror = function () {
    conn.textContent = "stream lost — the browser will retry";
    document.getElementById("feed").innerHTML = '<span class="dot idle"></span>disconnected';
  };
})();
