// web/graph.js -- the Incremental graph, drawn from what Incremental holds.
//
// Everything here is taken from /api/graph: the nodes, the edges, the ranks,
// the readers outside. The client lays out rows and strokes; it never decides
// what depends on what and it never recomputes a number. The values under the
// nodes come from a frame's by_node, the closures from the served edges, and
// the lit set from the frame's recomputed -- the same hook the tests pin.
(function () {
  "use strict";
  var F = window.OhCamelFormat || {};
  var money = F.money, pct = F.pct, el = F.el;
  var NS = "http://www.w3.org/2000/svg";
  function svgEl(tag, attrs, cls) {
    var e = document.createElementNS(NS, tag);
    for (var k in attrs) if (Object.prototype.hasOwnProperty.call(attrs, k)) e.setAttribute(k, attrs[k]);
    if (cls) e.setAttribute("class", cls);
    return e;
  }
  function text(x, y, s, cls) { var t = svgEl("text", { x: x, y: y }, cls); t.textContent = s; return t; }
  function byNameOrder(a, b) { return a.name < b.name ? -1 : a.name > b.name ? 1 : 0; }

  // ---- geometry: Task 11's rules, with a second line under every node ----
  var COL0 = 320, COL = 150, LINE = 24, GAP = 22, LEFT = 24, TOP = 34, CHAR = 6.6, OBS_GAP = 40, OBS_W = 240;
  // A band's caption takes a line of its own above the band. The prototype set
  // it 8 px over the band's first row, where it ran into last_tick[AAPL].
  var CAP = 14;
  // The width of one character on a node's second line (8.5 px monospace),
  // as CHAR is on its first (11 px).
  var VAL_CHAR = 5.1;
  var OPTION_SINGLETONS = ["gamma_map", "vega_map", "portfolio_gamma", "portfolio_vega", "vega_by_bucket"];
  // graph.ml's own sentences, for the five nodes whose comments are the argument.
  var SENTENCES = {
    "covariance": "hangs off aligned_returns and nothing else: a tick cannot reach it, only a new return can",
    "attribution": "reads the equal-weighted matrix, fixed at construction; which one is on the wire as attribution_covariance",
    "current_drawdown": "a Float.equal cutoff: at a new peak the value is 0.0 again, so limit:dd-cap is spared the recompute",
    "feed_health": "disconnected on purpose: nothing in the risk chain is downstream of now",
    "valuation_days": "the second clock, in days; wiring the Greeks to now would put the options book on a five-second timer"
  };
  // Column heads: the stage most of a column's symbol and spine nodes belong
  // to, a folded band voting once per name it holds. Limit and feed nodes sit
  // in their own bands and do not vote; a column holding only limits is headed
  // "limits". Ties go to the deeper stage.
  var STAGES = [
    ["breaches", ["breaches"]],
    ["attribution", ["attribution", "component_var_map", "component_var_sector_map", "diversification_ratio"]],
    ["estimators", ["aligned_returns", "covariance", "covariance_ewma", "portfolio_returns", "historical_var", "expected_shortfall", "parametric_var", "parametric_var_ewma", "var_notional", "es_notional", "portfolio_beta", "current_drawdown"]],
    ["aggregates", ["exposure_map", "sector:", "sector_map", "gross_exposure", "net_exposure", "weights", "equity", "gamma_map", "vega_map", "portfolio_gamma", "portfolio_vega", "vega_by_bucket"]],
    ["exposure", ["exposure:", "option_exposure:", "greeks:"]]
  ];
  var SCALAR = { usd: 1, fraction: 1, ratio: 1, count: 1, price: 1, qty: 1, time: 1 };
  function isFeed(n) { return n.name === "now" || n.name === "feed_health" || n.name.indexOf("feed:") === 0 || n.name.indexOf("last_tick[") === 0; }
  function stageOf(n) {
    if (n.family === "input") return "inputs";
    for (var i = 0; i < STAGES.length; i++) for (var j = 0; j < STAGES[i][1].length; j++) {
      var k = STAGES[i][1][j];
      if (k.slice(-1) === ":" ? n.name.indexOf(k) === 0 : n.name === k) return STAGES[i][0];
    }
    return "spine";
  }
  function familyKey(name) {
    var i = name.indexOf("["); if (i > 0) return name.slice(0, i) + "[S]";
    i = name.indexOf(":"); return i > 0 ? name.slice(0, i) + ":S" : name;
  }
  function colX(rank) { return rank === 0 ? LEFT : LEFT + COL0 + (rank - 1) * COL; }
  function unitFormat(unit, v, name) {
    if (v === null || v === undefined) return "—";
    switch (unit) {
      case "usd": return money(v);
      case "fraction": return pct(v);
      // beta is a coefficient and reads to three places; the other ratio,
      // diversification, is a multiple and reads as one.
      case "ratio": return name === "portfolio_beta" ? v.toFixed(3) : v.toFixed(2) + "×";
      case "price": return v.toFixed(2);
      case "qty": case "count": return Math.round(v).toLocaleString("en-US");
      case "time": return v.toFixed(1) + " d";
      default: return String(v);
    }
  }

  // ---- the contract's pure functions: closures and fragments ----
  function adjacency(topo, dir) {
    var m = {};
    topo.edges.forEach(function (e) {
      var from = dir === "down" ? e[0] : e[1], to = dir === "down" ? e[1] : e[0];
      (m[from] = m[from] || []).push(to);
    });
    return m;
  }
  // Strictly reachable from the seeds; the seeds themselves are not in the set
  // unless another seed reaches them -- the same rule as Graph.Topology.closure,
  // so the OCaml test and this agree on what "downstream of price[S]" is.
  function closure(topo, names, dir) {
    var next = adjacency(topo, dir), seen = new Set(), stack = names.slice();
    while (stack.length) {
      var n = stack.pop();
      (next[n] || []).forEach(function (m) { if (!seen.has(m)) { seen.add(m); stack.push(m); } });
    }
    return seen;
  }
  function filter(topo, spec) {
    spec = spec || {};
    var keep = new Set();
    topo.nodes.forEach(function (n) {
      if ((spec.nodes && spec.nodes.indexOf(n.name) >= 0) || (spec.families && spec.families.indexOf(n.family) >= 0)) keep.add(n.name);
    });
    return {
      nodes: topo.nodes.filter(function (n) { return keep.has(n.name); }),
      edges: topo.edges.filter(function (e) { return keep.has(e[0]) && keep.has(e[1]); }),
      outside: topo.outside, attribution_covariance: topo.attribution_covariance, counts: topo.counts
    };
  }

  // ---- layout ----
  // Bands in page order: the symbol rows, sector rows, the spine (singletons
  // stacked per column), one row per limit, and the feed branch under a gap.
  // Column 0 of a symbol row lays its cells side by side.
  //
  // Compact -- the default, because Task 11 drew this book one row per name
  // and no single name's path to breaches could be followed by eye -- folds
  // each per-symbol family into one band (`exposure:S × 6`) and unfolds the
  // name a frame ticked into a row directly under the bands, with its feed row
  // under the feed bands. Both of those rows keep their height while nothing
  // is open, so the frame that unfolds a name and the quiet frame that folds it
  // move nothing below the figure.
  function layout(topo, compact, open) {
    var byName = {}, symbols = [], sectors = [], limits = [];
    topo.nodes.forEach(function (n) {
      byName[n.name] = n;
      if (n.symbol && symbols.indexOf(n.symbol) < 0) symbols.push(n.symbol);
      if (n.sector && sectors.indexOf(n.sector) < 0) sectors.push(n.sector);
      if (n.limit) limits.push(n.name);
    });
    symbols.sort(); sectors.sort();
    // Only the option singletons this topology holds collapse, so a fragment
    // that has none of them does not carry the "no options" row.
    var collapsed = topo.counts && topo.counts.options === 0
      ? OPTION_SINGLETONS.filter(function (m) { return byName[m] !== undefined; }) : [];
    var drawn = [], alias = {}, bands = {};
    topo.nodes.forEach(function (n) {
      if (collapsed.indexOf(n.name) >= 0) { alias[n.name] = null; return; }
      if (compact && n.symbol && n.symbol !== open) {
        var key = familyKey(n.name);
        if (!bands[key]) { bands[key] = { name: key, family: n.family, rank: n.rank, unit: "state", observed: false, band: true, members: [], feed: isFeed(n) }; drawn.push(bands[key]); }
        bands[key].members.push(n.name); alias[n.name] = key; return;
      }
      alias[n.name] = n.name; drawn.push(n);
    });
    drawn.forEach(function (n) { n.label = n.band ? n.name + " × " + n.members.length : n.name; });
    // The first absence: a slot beside covariance_ewma with no edge into it.
    if (byName["covariance_ewma"]) drawn.push({ name: "covariance_ewma~garch", label: "garch", note: "— implemented, not wired in", rank: byName["covariance_ewma"].rank, absent: true, unit: "state", family: "singleton" });
    var edges = [], seenEdge = {};
    topo.edges.forEach(function (e) {
      var a = alias[e[0]], b = alias[e[1]];
      if (!a || !b || a === b) return;
      var k = a + "→" + b;
      if (!seenEdge[k]) { seenEdge[k] = true; edges.push([a, b]); }
    });
    var maxRank = 0; drawn.forEach(function (n) { if (n.rank > maxRank) maxRank = n.rank; });
    function row() { var r = []; for (var i = 0; i <= maxRank; i++) r.push([]); return r; }
    // Compact: row 0 of the symbol and feed bands holds the folded families,
    // row 1 the open name. Per name: one row per symbol, then the feed's
    // unsymbolled row (now, feed_health).
    var B = {
      symbols: compact ? [row(), row()] : symbols.map(row),
      sectors: sectors.map(row), spine: [row()], limits: limits.map(row),
      feed: compact ? [row(), row()] : symbols.map(row).concat([row()])
    };
    drawn.forEach(function (n) {
      var t;
      if (n.band) t = n.feed ? B.feed[0] : B.symbols[0];
      else if (isFeed(n)) t = compact ? B.feed[n.symbol ? 1 : 0] : B.feed[n.symbol ? symbols.indexOf(n.symbol) : symbols.length];
      else if (n.limit) t = B.limits[limits.indexOf(n.name)];
      else if (n.symbol) t = compact ? B.symbols[1] : B.symbols[symbols.indexOf(n.symbol)];
      else if (n.sector) t = B.sectors[sectors.indexOf(n.sector)];
      else t = B.spine[0];
      t[n.rank].push(n);
    });
    var sections = [["symbols", B.symbols], ["sectors", B.sectors], ["spine", B.spine], ["limits", B.limits], ["feed", B.feed]];
    var pos = {}, y = TOP, tops = {};
    sections.forEach(function (s) {
      var name = s[0], any = false, side0 = name === "symbols";
      var holds = s[1].some(function (r) { return r.some(function (col) { return col.length > 0; }); });
      if (!holds) return;
      s[1].forEach(function (r, ri) {
        var h = 0;
        r.forEach(function (col, rank) {
          col.sort(byNameOrder);
          h = Math.max(h, rank === 0 && side0 ? (col.length ? 1 : 0) : col.length);
        });
        var reserved = compact && ri === 1 && (name === "symbols" || name === "feed");
        if (!h && !reserved) return;
        h = Math.max(h, 1);
        if (!any) { if (name === "limits" || name === "feed") y += CAP; tops[name] = y; any = true; }
        r.forEach(function (col, rank) {
          col.forEach(function (n, i) {
            var side = rank === 0 && side0;
            pos[n.name] = { x: colX(rank) + (side ? i * 104 : 0), y: y + (side ? 0 : i * LINE), w: n.label.length * CHAR };
          });
        });
        y += h * LINE + 4;
      });
      if (any) y += GAP;
    });
    return { drawn: drawn, edges: edges, pos: pos, height: y, maxRank: maxRank, symbols: symbols, limits: limits, tops: tops, alias: alias, byName: byName, collapsed: collapsed };
  }

  // ---- render ----
  function render(container, topology, opts) {
    opts = opts || {};
    var filtered = !!opts.filter;
    var topo = filtered ? filter(topology, opts.filter) : topology;
    var byName = {};
    topo.nodes.forEach(function (n) { byName[n.name] = n; });
    // Task 11's decision is this one line. Its prototype drew the demo book one
    // row per name -- 82 nodes, 117 edges, 798 straight-line crossings -- and
    // the six exposure rows fanned into one bundle, so HAIRBALL: the folded
    // drawing is the default for every book, and `compact: false` asks for the
    // per-name one.
    var compact = opts.compact === undefined ? true : !!opts.compact;
    var inspector = !!opts.inspector;
    var st = { runs: {}, values: null, notes: {}, lit: [], litCount: 0, stale: [], staleSet: new Set(), open: null, hover: null, dead: false, L: null, svg: null, cap: null, insp: null };
    var maxRank = 0; topo.nodes.forEach(function (n) { if (n.rank > maxRank) maxRank = n.rank; });

    function nodeGroup(name) { return st.svg ? st.svg.querySelector('g.node[data-name="' + name + '"]') : null; }
    // A value under a scalar cell or singleton; a run count under everything
    // else. Per-symbol, per-sector and per-limit nodes carry only the count --
    // the ledger has their numbers, and one place per number is the rule. A
    // cell is set, never run, so under a cell that is not a number there is
    // nothing: "ran 0×" would read as a cell that never changed.
    function valueText(n) {
      if (n.absent) return "";
      if (st.notes[n.name] !== undefined) return st.notes[n.name];
      if (n.family === "input" && (n.band || !SCALAR[n.unit])) return "";
      if (n.band) { var s = 0; n.members.forEach(function (m) { s += st.runs[m] || 0; }); return "ran " + s + "×"; }
      if (SCALAR[n.unit] && (n.family === "input" || n.family === "singleton")) return st.values ? unitFormat(n.unit, st.values[n.name], n.name) : "—";
      return "ran " + (st.runs[n.name] || 0) + "×";
    }
    function captionText() { var c = topo.counts || {}; return "The Incremental graph, taken from Incremental. " + c.named + " named nodes, " + c.observed + " observed. This frame: " + st.litCount + " ran."; }
    function applyValues() {
      if (!st.svg) return;
      st.L.drawn.forEach(function (n) { var g = nodeGroup(n.name); if (!g) return; var v = g.querySelector("text.val"); if (v) v.textContent = valueText(n); });
    }
    function drawnSet(names) { var s = new Set(); names.forEach(function (m) { var a = st.L.alias[m]; if (a) s.add(a); }); return s; }
    function applyLit() {
      if (!st.svg) return;
      var lit = drawnSet(st.lit);
      Array.prototype.forEach.call(st.svg.querySelectorAll("g.node, path.edge"), function (e) { e.classList.remove("lit"); });
      void st.svg.getBoundingClientRect(); // restart the 0.75 s fade for a node lit on consecutive frames
      Array.prototype.forEach.call(st.svg.querySelectorAll("g.node"), function (g) { if (lit.has(g.getAttribute("data-name"))) g.classList.add("lit"); });
      Array.prototype.forEach.call(st.svg.querySelectorAll("path.edge"), function (p) { if (lit.has(p.getAttribute("data-to"))) p.classList.add("lit"); });
      if (st.cap) st.cap.querySelector(".cap-title").textContent = captionText();
    }
    function applyStale() {
      if (!st.svg) return;
      var stale = drawnSet(Array.from(st.staleSet)), over = new Set();
      st.stale.forEach(function (s) { var a = st.L.alias["feed:" + s]; if (a) over.add(a); });
      if (st.stale.length && st.L.alias["feed_health"]) over.add("feed_health");
      Array.prototype.forEach.call(st.svg.querySelectorAll("g.node"), function (g) { var name = g.getAttribute("data-name"); g.classList.toggle("stale", stale.has(name)); g.classList.toggle("over", over.has(name)); });
    }
    function inspectorLine(n) {
      var ins = topo.edges.filter(function (e) { return e[1] === n.name; }).map(function (e) { return e[0]; });
      var outs = topo.edges.filter(function (e) { return e[0] === n.name; }).map(function (e) { return e[1]; });
      var parts = [n.name, n.family.replace("_", " "), n.observed ? "observed" : "not observed", "cutoff " + n.cutoff,
        "inputs: " + (ins.length ? ins.join(", ") : "none"), "outputs: " + (outs.length ? outs.join(", ") : "none"),
        "upstream " + closure(topo, [n.name], "up").size + " / downstream " + closure(topo, [n.name], "down").size,
        "ran " + (st.runs[n.name] || 0) + "× since you opened this page"];
      if (SENTENCES[n.name]) parts.push(SENTENCES[n.name]);
      return parts.join(" · ");
    }
    // Hover strokes the node's upstream edges and its downstream edges. The
    // hovered node is remembered, so the redraw a newly ticked name forces in
    // compact mode does not wipe the inspector out from under the pointer.
    function hover(n) {
      st.hover = n;
      if (st.insp) st.insp.textContent = n ? inspectorLine(n) : "";
      if (!st.svg) return;
      var up = n ? drawnSet(Array.from(closure(topo, [n.name], "up")).concat([n.name])) : null;
      var down = n ? drawnSet(Array.from(closure(topo, [n.name], "down")).concat([n.name])) : null;
      Array.prototype.forEach.call(st.svg.querySelectorAll("path.edge"), function (p) {
        p.classList.toggle("up", !!up && up.has(p.getAttribute("data-to")));
        p.classList.toggle("down", !!down && down.has(p.getAttribute("data-from")));
      });
    }
    function drawList() {
      st.svg = null; st.cap = null; st.insp = null;
      var ol = el("ol", "graph-list");
      for (var r = 0; r <= maxRank; r++) {
        var names = topo.nodes.filter(function (n) { return n.rank === r; }).map(function (n) { return n.name; });
        if (!names.length) continue;
        var li = el("li", null, "rank " + r + " · ");
        names.forEach(function (name, i) { li.appendChild(el(st.lit.indexOf(name) >= 0 ? "b" : "span", null, name)); if (i < names.length - 1) li.appendChild(document.createTextNode(", ")); });
        ol.appendChild(li);
      }
      container.appendChild(ol);
      if (inspector) container.appendChild(el("div", "graph-note", "The drawing reads best on a laptop. Here are its node names by rank, with the ones this frame ran marked."));
    }
    function edgePath(a, b) { var x1 = a.x + a.w + 8, y1 = a.y - 4, x2 = b.x - 4, y2 = b.y - 4, mx = (x1 + x2) / 2; return "M" + x1 + "," + y1 + " C" + mx + "," + y1 + " " + mx + "," + y2 + " " + x2 + "," + y2; }
    function drawSvg() {
      var L = st.L = layout(topo, compact, st.open);
      // The observers column belongs to the whole graph; a filtered fragment
      // is a piece of the risk chain and does not carry it. A fragment also
      // starts at its own first column rather than at rank 0, so a fragment of
      // the estimators does not open on half a screen of nothing.
      var observers = !filtered, r0 = 0;
      if (filtered) { r0 = L.maxRank; L.drawn.forEach(function (n) { if (n.rank < r0) r0 = n.rank; }); }
      var dx = colX(r0) - LEFT;
      if (dx) Object.keys(L.pos).forEach(function (k) { L.pos[k].x -= dx; });
      var obsX = colX(L.maxRank) + COL + OBS_GAP, width = observers ? obsX + OBS_W : obsX - dx, height = L.height + (L.collapsed.length ? 18 : 0);
      var svg = st.svg = svgEl("svg", { width: width, height: height, viewBox: "0 0 " + width + " " + height, role: "img", "aria-label": "the dependency graph" });
      var prev = null, order = ["inputs"].concat(STAGES.map(function (s) { return s[0]; })).concat(["spine"]);
      for (var r = r0; r <= L.maxRank; r++) {
        var votes = {}, best = null;
        L.drawn.forEach(function (n) {
          if (n.rank !== r || n.absent || n.limit || isFeed(n) || (n.band && n.feed)) return;
          var s = stageOf(n); votes[s] = (votes[s] || 0) + (n.band ? n.members.length : 1);
        });
        order.forEach(function (s) { if (votes[s] && (!best || votes[s] > votes[best])) best = s; });
        if (!best) best = L.drawn.some(function (n) { return n.rank === r && n.limit; }) ? "limits" : "";
        if (!best) continue; // a column holding nothing that votes gets no head
        svg.appendChild(text(colX(r) - dx, 14, best === prev ? "·" : best, "head"));
        prev = best;
      }
      if (L.tops.limits !== undefined) svg.appendChild(text(LEFT, L.tops.limits - CAP - 4, "limits — one row each, at the rank of what it reads", "head"));
      if (L.tops.feed !== undefined) svg.appendChild(text(LEFT, L.tops.feed - CAP - 4, "deliberately disconnected", "head"));
      L.edges.forEach(function (e) {
        var a = L.pos[e[0]], b = L.pos[e[1]]; if (!a || !b) return;
        svg.appendChild(svgEl("path", { d: edgePath(a, b), "data-from": e[0], "data-to": e[1] }, "edge"));
      });
      L.drawn.forEach(function (n) {
        var p = L.pos[n.name];
        if (n.absent) {
          // Set the way a node is set -- its name, and under it what it is --
          // so the sentence stays inside its column instead of running into
          // the names of the next one.
          var ga = svgEl("g", { transform: "translate(" + p.x + "," + p.y + ")" }, "absent");
          ga.appendChild(svgEl("rect", { x: -4, y: -10, width: Math.max(p.w, n.note.length * VAL_CHAR) + 8, height: 24, rx: 2 }, "slot"));
          var a = svgEl("a", { href: "#s05" }); a.appendChild(text(0, 0, n.label, "name")); a.appendChild(text(0, 13, n.note, "val")); ga.appendChild(a);
          svg.appendChild(ga); return;
        }
        var g = svgEl("g", { "data-name": n.name, "data-family": n.family, transform: "translate(" + p.x + "," + p.y + ")" }, "node" + (n.band ? " band" : ""));
        if (n.family === "input" && !n.band) g.appendChild(svgEl("rect", { x: -13, y: -10, width: 8, height: 8 }, "cell"));
        g.appendChild(text(0, 0, n.label, "name"));
        g.appendChild(svgEl("line", { x1: 0, y1: 3, x2: p.w, y2: 3 }, "rule"));
        if (n.observed) g.appendChild(svgEl("circle", { cx: p.w + 5, cy: -3, r: 2.5 }, "obs"));
        g.appendChild(text(0, 13, valueText(n), "val"));
        if (inspector && !n.band) {
          g.addEventListener("mouseenter", function () { hover(n); });
          g.addEventListener("mouseleave", function () { hover(null); });
        }
        svg.appendChild(g);
      });
      if (L.collapsed.length) svg.appendChild(text(LEFT, height - 6, "greeks · option_exposure · gamma_map · vega_map · portfolio_gamma · portfolio_vega · vega_by_bucket — no options in this book; the five singletons exist and ran once", "head collapsed"));
      // The readers outside the graph, from the served list; the kill switch's
      // dotted edge to a bar is the second absence.
      if (observers) {
        var go = svgEl("g", {}, "observers"), oy = TOP, entries = {};
        go.appendChild(svgEl("line", { x1: obsX - 20, y1: 6, x2: obsX - 20, y2: height }, "rule"));
        go.appendChild(text(obsX, 14, "observers — outside the graph", "head"));
        (topo.outside || []).forEach(function (o) {
          if (o.name === "kill_switch") return;
          var label = o.name === "history" ? "history · reads " + o.reads.length + " · 500 points"
            : o.name === "stream" ? "stream · reads " + o.reads.length
            : "alerts · reads " + o.reads.join(", ") + (o.present ? "" : " · not attached");
          var g = svgEl("g", { "data-outside": o.name, transform: "translate(" + obsX + "," + oy + ")" }, "outside" + (o.present ? "" : " missing"));
          g.appendChild(text(0, 0, label, "name"));
          g.appendChild(svgEl("line", { x1: 0, y1: 3, x2: label.length * CHAR, y2: 3 }, "rule"));
          go.appendChild(g); entries[o.name] = oy;
          if (o.name === "alerts") o.reads.forEach(function (r) { var a = L.pos[L.alias[r]]; if (a) go.appendChild(svgEl("path", { d: edgePath(a, { x: obsX, y: oy }), "data-from": r, "data-to": "alerts" }, "reads")); });
          oy += LINE;
        });
        var ks = (topo.outside || []).filter(function (o) { return o.name === "kill_switch"; })[0];
        if (ks) {
          var ky = oy + 4, gk = svgEl("g", {}, "absent kill");
          gk.appendChild(svgEl("path", { d: "M" + (obsX + 20) + "," + ((entries.alerts !== undefined ? entries.alerts : oy - LINE) + 6) + " L" + (obsX + 20) + "," + ky }, "dotted"));
          gk.appendChild(svgEl("line", { x1: obsX + 12, y1: ky, x2: obsX + 28, y2: ky }, "bar"));
          gk.appendChild(text(obsX + 34, ky + 4, "kill switch — wired to " + (ks.wired_to === null ? "nothing" : ks.wired_to), "name"));
          go.appendChild(gk);
        }
        svg.appendChild(go);
      }
      container.appendChild(svg);
      if (inspector) {
        st.insp = el("div", "inspector", ""); container.appendChild(st.insp);
        st.cap = el("figcaption", "graph-cap");
        st.cap.appendChild(el("span", "cap-title", captionText()));
        st.cap.appendChild(el("span", "cap-prov", "LIVE · THIS HOST"));
        container.appendChild(st.cap);
        container.appendChild(el("div", "graph-note", "nodes recomputed (footer) is Incremental's process-wide count and includes watch nodes, plumbing, every stress fork and the startup probe; the number above is named node bodies, from the hook the tests pin."));
        if (compact) container.appendChild(el("div", "graph-note", "per-symbol families are drawn as one band each (" + L.symbols.length + " names); the name a frame ticked opens its own row, and a frame without a tick folds it again"));
      }
      applyLit(); applyStale();
      if (st.hover) hover(L.pos[st.hover.name] ? st.hover : null);
    }
    function draw() {
      if (st.dead) return;
      container.textContent = "";
      container.classList.add("ohcamel-graph");
      if (container.clientWidth && container.clientWidth < 700) { drawList(); return; }
      try { drawSvg(); }
      catch (e) {
        // The node list stands in wherever the drawing cannot be made; the
        // error still goes to the console rather than vanishing.
        if (window.console) console.error("OhCamelGraph: the drawing failed, so the node list is shown instead", e);
        container.textContent = "";
        drawList();
      }
    }
    function light(names) {
      if (st.dead) return;
      var lit = [], open = null;
      (names || []).forEach(function (x) {
        var name = typeof x === "string" ? x : x.name, n = typeof x === "string" ? 1 : (x.n || 1);
        lit.push(name); st.runs[name] = (st.runs[name] || 0) + n;
        // The row a frame opens is the name that ticked. A feed:S note riding
        // on the frame is the clock, and the clock opens nothing.
        var node = byName[name];
        if (open === null && node && node.symbol && !isFeed(node)) open = node.symbol;
      });
      st.lit = lit; st.litCount = lit.length;
      if (compact && open !== st.open) { st.open = open; draw(); applyValues(); return; }
      if (!st.svg) { draw(); return; }
      applyLit(); applyValues();
      if (st.hover && st.insp) st.insp.textContent = inspectorLine(st.hover);
    }
    function dim(staleSymbols) {
      var set = new Set();
      (staleSymbols || []).forEach(function (s) { var cell = "price[" + s + "]"; if (byName[cell]) { set.add(cell); closure(topo, [cell], "down").forEach(function (m) { set.add(m); }); } });
      st.stale = (staleSymbols || []).slice(); st.staleSet = set;
      if (!st.dead) applyStale();
      return set;
    }
    function setValues(byNode) { st.values = byNode || {}; if (!st.dead) applyValues(); }
    function setNote(name, t) { st.notes[name] = t; if (!st.dead) applyValues(); }
    function destroy() { st.dead = true; st.hover = null; container.textContent = ""; container.classList.remove("ohcamel-graph"); st.svg = null; st.cap = null; st.insp = null; }
    draw();
    return { light: light, dim: dim, setValues: setValues, setNote: setNote, destroy: destroy, redraw: draw };
  }

  window.OhCamelGraph = { render: render, filter: filter, closure: closure };
})();
