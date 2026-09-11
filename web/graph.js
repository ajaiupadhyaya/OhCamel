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

  // ---- geometry ----
  // A node is two lines, its name and under it a value or a run count. A
  // column is as wide as the widest thing it can ever hold, plus one gutter
  // for the edges -- one width for every column drew the figure 1824 px wide
  // on a six-name book, and it has to fit a laptop's first screen.
  var LINE = 24, GAP = 12, GUT = 26, LEFT = 24, TOP = 30, CHAR = 6.6, DOT = 9;
  // A caption that finds no room beside its band takes a line of its own above it.
  var CAP = 14;
  // The width of one character on a node's second line (8.5 px monospace), as
  // CHAR is on its first (11 px), and of a head (10 px, tracked .14em).
  var VAL_CHAR = 5.1, HEAD_CHAR = 7.4;
  // The observers hang under breaches, in its column, this wide.
  var OBS_W = 130;
  // Below this the drawing stops shrinking to its container and scrolls in it.
  var MIN_FIT = 1100;
  // The widest a node's second line gets, in characters: a dollar value, a
  // percentage, a run count that has been climbing all afternoon, and on a
  // band the count of its stale members after that.
  var VAL_CHARS = { usd: 11, fraction: 7, ratio: 6, price: 8, qty: 7, time: 7, count: 7 };
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
  function shownValue(n) { return SCALAR[n.unit] && (n.family === "input" || n.family === "singleton"); }
  function nodeWidth(n) {
    var name = n.label.length * CHAR + (n.observed ? DOT : 0);
    var val = n.absent ? n.note.length : n.band ? 18 : shownValue(n) ? (VAL_CHARS[n.unit] || 8) : 10;
    return Math.max(name, val * VAL_CHAR);
  }
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
  // Bands in page order: the symbol rows with the sectors beside them, the
  // spine (singletons stacked per column), the limits, and the feed branch
  // under a gap. A band is rows laid one under the next, plus floats -- the
  // sectors -- that belong to no row and stack in their column below whatever
  // the rows put there, so exposure reads into sector along one line.
  //
  // Compact -- the default, because Task 11 drew this book one row per name
  // and no single name's path to breaches could be followed by eye -- folds
  // each per-symbol family into one band (`exposure:S × 6`) and unfolds the
  // name a frame ticked into a row directly under the bands, with its feed row
  // under the feed bands. Both of those rows keep their height while nothing
  // is open, and every column is measured for every name a frame could open,
  // so the frame that unfolds a name and the quiet frame that folds it move
  // nothing, below the figure or beside it.
  //
  // The limits share one band, each limit in the column of its rank, stacked
  // in configured order -- a staircase of one row per limit was a third of the
  // figure's height for nine limits.
  function layout(topo, compact, open, observers) {
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
    // The first absence: a slot beside covariance_ewma with no edge into it,
    // on a line of its own in that column.
    if (byName["covariance_ewma"]) drawn.push({ name: "covariance_ewma~garch", label: "garch —", note: "implemented, not wired in", rank: byName["covariance_ewma"].rank, absent: true, unit: "state", family: "singleton" });
    var edges = [], seenEdge = {};
    topo.edges.forEach(function (e) {
      var a = alias[e[0]], b = alias[e[1]];
      if (!a || !b || a === b) return;
      var k = a + "→" + b;
      if (!seenEdge[k]) { seenEdge[k] = true; edges.push([a, b]); }
    });
    var maxRank = 0; drawn.forEach(function (n) { if (n.rank > maxRank) maxRank = n.rank; });
    // Column widths, from what is drawn and, folded, from every name a frame
    // could open; the observers widen the last column they hang in.
    var colW = [];
    function widen(n) { colW[n.rank] = Math.max(colW[n.rank] || 0, nodeWidth(n)); }
    drawn.forEach(widen);
    if (compact) topo.nodes.forEach(function (n) { if (n.symbol && collapsed.indexOf(n.name) < 0) widen({ label: n.name, observed: n.observed, rank: n.rank, unit: n.unit, family: n.family }); });
    if (observers) colW[maxRank] = Math.max(colW[maxRank] || 0, OBS_W);
    var colXs = [], x = LEFT;
    for (var r = 0; r <= maxRank; r++) { colXs.push(x); if (colW[r]) x += colW[r] + GUT; }
    function colX(rank) { return colXs[rank]; }
    function cols() { var c = []; for (var i = 0; i <= maxRank; i++) c.push([]); return c; }
    // Compact: row 0 of the symbol and feed bands holds the folded families,
    // row 1 the open name. Per name: one row per symbol, then the feed's
    // unsymbolled row (now, feed_health).
    var B = {
      symbols: { rows: compact ? [cols(), cols()] : symbols.map(cols), floats: cols() },
      spine: { rows: [cols()], floats: cols() },
      limits: { rows: [cols()], floats: cols() },
      feed: { rows: compact ? [cols(), cols()] : symbols.map(cols).concat([cols()]), floats: cols() }
    };
    drawn.forEach(function (n) {
      var t;
      if (n.band) t = n.feed ? B.feed.rows[0] : B.symbols.rows[0];
      else if (isFeed(n)) t = compact ? B.feed.rows[n.symbol ? 1 : 0] : B.feed.rows[n.symbol ? symbols.indexOf(n.symbol) : symbols.length];
      else if (n.limit) t = B.limits.rows[0];
      else if (n.symbol) t = compact ? B.symbols.rows[1] : B.symbols.rows[symbols.indexOf(n.symbol)];
      else if (n.sector) t = B.symbols.floats;
      else t = B.spine.rows[0];
      t[n.rank].push(n);
    });
    function byConfigured(a, b) { return limits.indexOf(a.name) - limits.indexOf(b.name); }
    function any(c) { return c.some(function (col) { return col.length > 0; }); }
    function ranksHeld(band) {
      var rs = [];
      band.rows.concat([band.floats]).forEach(function (row) { row.forEach(function (col, rank) { if (col.length) rs.push(rank); }); });
      return rs;
    }
    // Each band caption sits beside its band where the band leaves room --
    // the limits start two columns in, the feed stops three columns in -- and
    // takes a line above the band only where it does not.
    var CAPTIONS = { limits: ["limits —", "at the rank of what each reads"], feed: ["deliberately disconnected"] };
    var pos = {}, y = TOP, tops = {}, lines = {}, caps = [];
    ["symbols", "spine", "limits", "feed"].forEach(function (name) {
      var band = B[name];
      if (!band.rows.some(any) && !any(band.floats)) return;
      var line = 0, used = [], cap = CAPTIONS[name], capAt = null, minLines = 1;
      if (cap) {
        var held = ranksHeld(band), lo = Math.min.apply(null, held), hi = Math.max.apply(null, held);
        var capW = Math.max.apply(null, cap.map(function (s) { return s.length * HEAD_CHAR; }));
        if (name === "limits" && colX(lo) - LEFT - 16 >= capW) capAt = LEFT;
        else if (name === "feed" && hi < maxRank) capAt = colX(hi + 1);
        if (capAt === null) { y += CAP; caps.push({ lines: cap, x: LEFT, y: y - CAP - 4 }); }
        else { caps.push({ lines: cap, x: capAt, y: y }); minLines = cap.length; }
      }
      band.rows.forEach(function (row, ri) {
        row.forEach(function (col) { col.sort(name === "limits" ? byConfigured : byNameOrder); });
        var h = 0, claims = row.map(function (col) { return col.length > 0; });
        row.forEach(function (col) { h = Math.max(h, col.length); });
        // The open row is as tall as the folded row above it, whether or not a name is open.
        if (compact && ri === 1 && (name === "symbols" || name === "feed")) band.rows[0].forEach(function (col, rank) {
          var k = col.filter(function (n) { return n.band; }).length;
          if (k) { h = Math.max(h, k); claims[rank] = true; }
        });
        if (!h) return;
        row.forEach(function (col, rank) { col.forEach(function (n, i) { pos[n.name] = { x: colX(rank), y: y + (line + i) * LINE, w: n.label.length * CHAR }; }); });
        claims.forEach(function (c, rank) { if (c) used[rank] = line + h; });
        line += h;
      });
      band.floats.forEach(function (col, rank) {
        col.sort(byNameOrder);
        var s = used[rank] || 0;
        col.forEach(function (n, i) { pos[n.name] = { x: colX(rank), y: y + (s + i) * LINE, w: n.label.length * CHAR }; });
        if (col.length) line = Math.max(line, s + col.length);
      });
      line = Math.max(line, minLines);
      tops[name] = y; lines[name] = line;
      y += line * LINE + GAP;
    });
    // The last band's last line, its value line, and a margin.
    var height = y - GAP - LINE + 22;
    return { drawn: drawn, edges: edges, pos: pos, height: height, maxRank: maxRank, symbols: symbols, limits: limits, tops: tops, lines: lines, caps: caps, alias: alias, byName: byName, collapsed: collapsed, colX: colX, colW: colW };
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
    var st = { runs: {}, values: null, notes: {}, lit: [], litCount: 0, stale: [], staleSet: new Set(), partial: {}, open: null, hover: null, dead: false, L: null, svg: null, cap: null, insp: null };
    var maxRank = 0; topo.nodes.forEach(function (n) { if (n.rank > maxRank) maxRank = n.rank; });

    // Escaped: a limit's name comes from the owner's book, and a quote in one
    // would otherwise throw here on every frame.
    function nodeGroup(name) { return st.svg ? st.svg.querySelector('g.node[data-name="' + (window.CSS && CSS.escape ? CSS.escape(name) : name) + '"]') : null; }
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
    // A band with some stale members and some fresh ones says how many after
    // its value, in the cannot-evaluate ink (the feed's own band in --over).
    function writeVal(g, n) {
      var v = g.querySelector("text.val"); if (!v) return;
      var base = valueText(n), p = n.band && st.partial[n.name];
      v.textContent = base;
      if (p && p.n) {
        var t = svgEl("tspan", {}, "partial" + (p.over ? " over" : ""));
        t.textContent = (base ? " · " : "") + p.n + " stale";
        v.appendChild(t);
      }
    }
    function applyValues() {
      if (!st.svg) return;
      st.L.drawn.forEach(function (n) { var g = nodeGroup(n.name); if (g) writeVal(g, n); });
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
    // A band dims only when every name it holds is dimmed. Dimming price[S] × 5
    // because one of five is quiet would say four fresh prices are stale, which
    // is more than the engine knows.
    function applyStale() {
      if (!st.svg) return;
      var over = new Set(st.stale.map(function (s) { return "feed:" + s; }));
      if (st.stale.length) over.add("feed_health");
      st.partial = {};
      st.L.drawn.forEach(function (n) {
        var g = nodeGroup(n.name); if (!g) return;
        var dim, red;
        if (n.band) {
          var k = n.members.filter(function (m) { return st.staleSet.has(m); }).length;
          var o = n.members.filter(function (m) { return over.has(m); }).length;
          dim = k > 0 && k === n.members.length; red = o > 0 && o === n.members.length;
          st.partial[n.name] = { n: dim || red ? 0 : Math.max(k, o), over: o > k };
          writeVal(g, n);
        } else { dim = st.staleSet.has(n.name); red = over.has(n.name); }
        g.classList.toggle("stale", dim); g.classList.toggle("over", red);
      });
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
      // The observers belong to the whole graph; a filtered fragment is a
      // piece of the risk chain and does not carry them. A column holding
      // nothing takes no width, so a fragment of the estimators starts at its
      // own first column rather than on half a screen of nothing.
      var observers = !filtered;
      var L = st.L = layout(topo, compact, st.open, observers), colX = L.colX, r0 = L.maxRank;
      L.drawn.forEach(function (n) { if (n.rank < r0) r0 = n.rank; });
      var ox = colX(L.maxRank), bp = L.pos[L.alias["breaches"]], obsBottom = 0;
      if (observers) obsBottom = (bp ? bp.y : TOP) + 172;
      var width = Math.round(colX(L.maxRank) + (L.colW[L.maxRank] || 0) + 16);
      var height = Math.max(L.height, obsBottom) + (L.collapsed.length ? 16 : 0);
      var svg = st.svg = svgEl("svg", { width: width, height: height, viewBox: "0 0 " + width + " " + height, role: "img", "aria-label": "the dependency graph" });
      // Natural size at most; shrinks with its container down to MIN_FIT, and
      // below that keeps MIN_FIT and scrolls inside it.
      svg.style.width = "100%"; svg.style.height = "auto";
      svg.style.maxWidth = width + "px"; svg.style.minWidth = Math.min(width, MIN_FIT) + "px";
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
        svg.appendChild(text(colX(r), 14, best === prev ? "·" : best, "head"));
        prev = best;
      }
      L.caps.forEach(function (c) {
        var t = svgEl("text", { x: c.x, y: c.y }, "head");
        c.lines.forEach(function (s, i) { var sp = svgEl("tspan", { x: c.x, dy: i ? 13 : 0 }); sp.textContent = s; t.appendChild(sp); });
        svg.appendChild(t);
      });
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
      if (L.collapsed.length) svg.appendChild(text(LEFT, height - 5,"greeks · option_exposure · gamma_map · vega_map · portfolio_gamma · portfolio_vega · vega_by_bucket — no options in this book; the five singletons exist and ran once", "head collapsed"));
      // The readers outside the graph, from the served list, hung under
      // breaches below a rule: the chain ends at breaches, and what reads it
      // from outside is set beneath it. The kill switch's dotted edge to a bar
      // is the second absence.
      if (observers) {
        var by = bp ? bp.y : TOP, sep = by + 22, go = svgEl("g", {}, "observers"), oy = sep + 44, entries = {};
        go.appendChild(svgEl("line", { x1: ox - 6, y1: sep, x2: ox + OBS_W, y2: sep }, "rule"));
        go.appendChild(text(ox, sep + 13, "observers", "head"));
        go.appendChild(text(ox, sep + 25, "outside the graph", "head"));
        var outside = topo.outside || [], ks = outside.filter(function (o) { return o.name === "kill_switch"; })[0];
        function entry(o, name, sub) {
          var g = svgEl("g", { "data-outside": o.name, transform: "translate(" + ox + "," + oy + ")" }, "outside" + (o.present === false ? " missing" : ""));
          g.appendChild(text(0, 0, name, "name"));
          g.appendChild(svgEl("line", { x1: 0, y1: 3, x2: name.length * CHAR, y2: 3 }, "rule"));
          g.appendChild(text(0, 13, sub, "val"));
          go.appendChild(g); entries[o.name] = oy; oy += LINE;
        }
        outside.forEach(function (o) {
          if (o.name !== "alerts") return;
          entry(o, "alerts", "reads " + o.reads.join(", ") + (o.present ? "" : " · not attached"));
          o.reads.forEach(function (r) { var a = L.pos[L.alias[r]]; if (a) go.appendChild(svgEl("path", { d: "M" + (ox + 4) + "," + (a.y + 18) + " L" + (ox + 4) + "," + (entries.alerts - 13), "data-from": r, "data-to": "alerts" }, "reads")); });
          if (ks) {
            var ky = oy + 6, gk = svgEl("g", {}, "absent kill");
            gk.appendChild(svgEl("path", { d: "M" + (ox + 6) + "," + (entries.alerts + 17) + " L" + (ox + 6) + "," + ky }, "dotted"));
            gk.appendChild(svgEl("line", { x1: ox - 2, y1: ky, x2: ox + 14, y2: ky }, "bar"));
            gk.appendChild(text(ox + 20, ky + 4, "kill switch —", "name"));
            gk.appendChild(text(ox + 20, ky + 17, "wired to " + (ks.wired_to === null ? "nothing" : ks.wired_to), "val"));
            go.appendChild(gk); oy = ky + LINE + 10;
          }
        });
        outside.forEach(function (o) {
          if (o.name === "history") entry(o, "history", "reads " + o.reads.length + " · 500 points");
          else if (o.name === "stream") entry(o, "stream", "reads " + o.reads.length);
        });
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
