// web/charts.js -- the report figures, drawn by hand as inline SVG.
//
// Strokes and fills are classes the stylesheet colours from the page's tokens,
// so both schemes work without a second drawing. No gridlines and no legends
// beyond a word at a line's end: the sparkline's idiom, grown to seven forms.
// Nothing here computes a risk number. Every value arrives on the wire; the
// only arithmetic is placing it.
(function () {
  "use strict";
  var NS = "http://www.w3.org/2000/svg";
  var F = window.OhCamelFormat || {};

  function s(tag, attrs, cls, text) {
    var e = document.createElementNS(NS, tag);
    for (var k in attrs) if (Object.prototype.hasOwnProperty.call(attrs, k)) e.setAttribute(k, attrs[k]);
    if (cls) e.setAttribute("class", cls);
    if (text !== undefined && text !== null) e.textContent = text;
    return e;
  }
  // A chart is as wide as its container up to its natural width, and keeps its
  // proportions -- the figures sit in a reading column and must not overflow a phone.
  function frame(container, w, h, label) {
    var svg = s("svg", { viewBox: "0 0 " + w + " " + h, role: "img", "aria-label": label }, "chart");
    svg.style.width = "100%"; svg.style.maxWidth = w + "px"; svg.style.height = "auto";
    container.appendChild(svg);
    return svg;
  }
  function lin(d0, d1, r0, r1) { return function (v) { return r0 + (v - d0) / (d1 - d0 || 1) * (r1 - r0); }; }
  function logs(d0, d1, r0, r1) { var a = Math.log(d0), b = Math.log(d1); return function (v) { return r0 + (Math.log(v) - a) / (b - a) * (r1 - r0); }; }
  function r1(x) { return Math.round(x * 10) / 10; }
  function pts(xy) { return xy.map(function (p) { return r1(p[0]) + "," + r1(p[1]); }).join(" "); }

  // ---- scaling: two series over three book sizes ----
  // Log y, because the claim is one line climbing twenty-fold while the other
  // does not move, and a linear axis would press the flat one into the floor.
  function scaling(container, rows) {
    var W = 560, H = 210, L = 30, R = 120, T = 18, B = 30;
    var svg = frame(container, W, H, "nodes in the graph and nodes per tick at three book sizes");
    var x = function (i) { return L + i * (W - L - R) / Math.max(1, rows.length - 1); };
    var y = logs(10, 2000, H - B, T);
    svg.appendChild(s("line", { x1: L, y1: H - B, x2: W - R, y2: H - B }, "axis"));
    rows.forEach(function (r, i) {
      svg.appendChild(s("text", { x: x(i), y: H - B + 16, "text-anchor": "middle" }, "tick", r.instruments + " names"));
    });
    [["nodes_in_graph", "nodes in graph", "ink", -9], ["nodes_per_tick", "nodes per tick", "second", 16]].forEach(function (series) {
      var key = series[0], xy = rows.map(function (r, i) { return [x(i), y(Math.max(10, r[key]))]; });
      svg.appendChild(s("polyline", { points: pts(xy) }, "ln " + series[2]));
      rows.forEach(function (r, i) {
        svg.appendChild(s("circle", { cx: r1(xy[i][0]), cy: r1(xy[i][1]), r: 3 }, "pt " + series[2]));
        var v = key === "nodes_per_tick" ? r[key].toFixed(1) : r[key].toLocaleString("en-US");
        svg.appendChild(s("text", { x: r1(xy[i][0]), y: r1(xy[i][1] + series[3]), "text-anchor": "middle" }, "val", v));
      });
      var last = xy[xy.length - 1];
      svg.appendChild(s("text", { x: r1(last[0] + 14), y: r1(last[1] + 4) }, "end " + series[2], series[1]));
    });
    return svg;
  }

  // ---- attribution: money and risk, paired, per name ----
  // Risk is signed: a hedge's bar runs left of zero in the hedge colour, and
  // that is the "no stray abs" test drawn.
  function attribution(container, positions) {
    var rows = positions.filter(function (p) { return p.weight !== null && p.weight !== undefined; });
    var W = 620, rowH = 30, T = 8, H = T + rows.length * rowH + 8, L = 64, BAR = 300;
    var svg = frame(container, W, H, "each position's share of the money and share of the risk");
    var lo = 0, hi = 0;
    rows.forEach(function (p) {
      hi = Math.max(hi, Math.abs(p.weight), p.risk_share || 0);
      lo = Math.min(lo, p.risk_share || 0);
    });
    var x = lin(lo, hi || 1, L, L + BAR), zero = x(0);
    svg.appendChild(s("line", { x1: r1(zero), y1: T - 2, x2: r1(zero), y2: H - 6 }, "axis"));
    rows.forEach(function (p, i) {
      var yy = T + i * rowH;
      svg.appendChild(s("text", { x: 0, y: yy + 14 }, "name", p.symbol));
      var m = Math.abs(p.weight);
      svg.appendChild(s("rect", { x: r1(zero), y: yy + 4, width: r1(Math.max(0.5, x(m) - zero)), height: 6 }, "bx money"));
      if (p.risk_share === null || p.risk_share === undefined) {
        svg.appendChild(s("text", { x: r1(zero + 4), y: yy + 22 }, "val unknown", "warming up"));
      } else {
        var rs = p.risk_share, x0 = Math.min(zero, x(rs)), w = Math.abs(x(rs) - zero);
        svg.appendChild(s("rect", { x: r1(x0), y: yy + 13, width: r1(Math.max(0.5, w)), height: 6 }, "bx risk" + (rs < 0 ? " hedge" : "")));
      }
      var txt = F.pct(m, 1) + " of money · " + (p.risk_share === null || p.risk_share === undefined ? "—" : F.pct(p.risk_share, 1)) + " of risk";
      svg.appendChild(s("text", { x: L + BAR + 18, y: yy + 12 }, "val", txt));
      if (p.risk_over_money !== null && p.risk_over_money !== undefined) {
        svg.appendChild(s("text", { x: L + BAR + 18, y: yy + 24 }, "val soft", "risk/money " + p.risk_over_money.toFixed(2) + "×"));
      }
    });
    return svg;
  }

  // ---- tenor buckets: six fixed slots, signed ----
  // The empty tenors are drawn empty, and the parallel-shift total is a flat
  // rule beside them: a total of zero hiding two opposite bars is the figure.
  function tenor(container, buckets, total) {
    var W = 560, H = 206, L = 20, T = 24, B = 56, slotW = (W - L - 110) / buckets.length;
    var svg = frame(container, W, H, "vega by tenor bucket, and the parallel-shift total");
    var m = 1; buckets.forEach(function (b) { m = Math.max(m, Math.abs(b.vega)); });
    var y = lin(-m, m, H - B, T), zero = y(0);
    svg.appendChild(s("line", { x1: L, y1: r1(zero), x2: W - 20, y2: r1(zero) }, "axis"));
    buckets.forEach(function (b, i) {
      var cx = L + i * slotW + slotW / 2, v = b.vega;
      if (v !== 0) svg.appendChild(s("rect", { x: r1(cx - 12), y: r1(Math.min(zero, y(v))), width: 24, height: r1(Math.abs(y(v) - zero)) }, "bx " + (v < 0 ? "loss" : "ink")));
      svg.appendChild(s("text", { x: r1(cx), y: H - B + 34, "text-anchor": "middle" }, "tick", b.bucket));
      if (v !== 0) svg.appendChild(s("text", { x: r1(cx), y: r1(v < 0 ? y(v) + 14 : y(v) - 5), "text-anchor": "middle" }, "val", F.money(v / 100)));
    });
    var tx = W - 90;
    svg.appendChild(s("line", { x1: tx, y1: r1(zero), x2: tx + 60, y2: r1(zero) }, "ln ink thick"));
    svg.appendChild(s("text", { x: tx + 30, y: r1(zero - 8), "text-anchor": "middle" }, "val", F.money(total / 100)));
    svg.appendChild(s("text", { x: tx + 30, y: H - B + 34, "text-anchor": "middle" }, "tick", "total"));
    svg.appendChild(s("text", { x: L, y: H - 6 }, "tick", "vega, $ per vol point, by days remaining"));
    return svg;
  }

  // ---- an exceedance strip: one tick per breach ----
  function strip(container, n, hits, opts) {
    opts = opts || {};
    var W = 940, H = 16;
    var svg = frame(container, W, H, n + " forecasts, " + hits.length + " exceptions");
    var x = lin(0, Math.max(1, n - 1), 0.5, W - 0.5);
    svg.appendChild(s("line", { x1: 0, y1: 12, x2: W, y2: 12 }, "axis"));
    if (opts.boundary !== undefined) svg.appendChild(s("line", { x1: r1(x(opts.boundary)), y1: 0, x2: r1(x(opts.boundary)), y2: H }, "boundary"));
    hits.forEach(function (h) { svg.appendChild(s("line", { x1: r1(x(h)), y1: 4, x2: r1(x(h)), y2: 12 }, "hit")); });
    return svg;
  }

  // ---- a crisis timeline ----
  // The book's daily return as bars, each estimator's -VaR as a line, the
  // selected estimator's exceptions as dots, and its worst 21-session burst
  // shaded. Every window shares one y-scale, so 2008 and 2022 compare.
  var ESTIMATOR_CLASS = { historical: "ink", parametric: "second", ewma: "mark" };
  function timeline(container, win, rows, selected, yMax) {
    yMax = yMax || 0.08;
    var W = 940, H = 170, T = 10, B = 22;
    var svg = frame(container, W, H, win.name + ": the book's daily returns against three VaR forecasts");
    var n = win.realised.length, x = lin(0, Math.max(1, n - 1), 1, W - 1), y = lin(-yMax, yMax, H - B, T);
    var sel = rows.filter(function (r) { return r.estimator.kind === selected; })[0];
    if (sel && sel.burst) {
      var bx0 = x(sel.burst.start), bx1 = x(Math.min(n - 1, sel.burst.start + 20));
      svg.appendChild(s("rect", { x: r1(bx0), y: T, width: r1(Math.max(2, bx1 - bx0)), height: H - B - T }, "shade"));
      svg.appendChild(s("text", { x: r1(Math.min(bx0, W - 200)), y: T + 10 }, "tick", sel.burst.count + " exceptions in 21 sessions"));
    }
    svg.appendChild(s("line", { x1: 0, y1: r1(y(0)), x2: W, y2: r1(y(0)) }, "axis"));
    var d = "";
    win.realised.forEach(function (v, i) {
      if (v === null) return;
      var c = Math.max(-yMax, Math.min(yMax, v));
      d += "M" + r1(x(i)) + "," + r1(y(0)) + "V" + r1(y(c));
    });
    svg.appendChild(s("path", { d: d }, "returns"));
    rows.forEach(function (r) {
      if (!r.var) return;
      var line = r.var.map(function (v, i) { return v === null ? null : [x(i), y(Math.max(-yMax, -v))]; }).filter(Boolean);
      svg.appendChild(s("polyline", { points: pts(line) }, "ln " + (ESTIMATOR_CLASS[r.estimator.kind] || "ink") + (r.estimator.kind === selected ? "" : " faint")));
    });
    if (sel) sel.hits.forEach(function (h) {
      var v = win.realised[h];
      if (v === null) return;
      svg.appendChild(s("circle", { cx: r1(x(h)), cy: r1(y(Math.max(-yMax, v))), r: 2.5 }, "hitdot"));
    });
    // Year boundaries, from the window's own dates.
    var lastYear = null;
    win.dates.forEach(function (dt, i) {
      var yr = dt.slice(0, 4);
      if (yr !== lastYear) {
        if (lastYear !== null) {
          svg.appendChild(s("line", { x1: r1(x(i)), y1: H - B, x2: r1(x(i)), y2: H - B + 4 }, "axis"));
          svg.appendChild(s("text", { x: r1(x(i) + 3), y: H - 6 }, "tick", yr));
        }
        lastYear = yr;
      }
    });
    svg.appendChild(s("text", { x: W - 2, y: r1(y(yMax)) + 10, "text-anchor": "end" }, "tick", "+" + Math.round(yMax * 100) + "%"));
    svg.appendChild(s("text", { x: W - 2, y: r1(y(-yMax)) - 3, "text-anchor": "end" }, "tick", "−" + Math.round(yMax * 100) + "%"));
    return svg;
  }

  // ---- GARCH: persistence, mean ± sd, at six sample sizes ----
  // The whisker at 60 spanning most of the axis is the verdict drawn.
  function garch(container, rows, truth, window, quoted) {
    var W = 560, H = 230, L = 40, R = 20, T = 14, B = 34;
    var svg = frame(container, W, H, "fitted persistence against sample size");
    var x = logs(45, 2600, L, W - R), y = lin(0, 1.2, H - B, T);
    svg.appendChild(s("line", { x1: L, y1: H - B, x2: W - R, y2: H - B }, "axis"));
    [0, 0.5, 1].forEach(function (v) { svg.appendChild(s("text", { x: L - 8, y: r1(y(v)) + 3, "text-anchor": "end" }, "tick", v.toFixed(1))); });
    svg.appendChild(s("line", { x1: L, y1: r1(y(truth)), x2: W - R, y2: r1(y(truth)) }, "truth"));
    svg.appendChild(s("text", { x: W - R, y: r1(y(truth)) - 5, "text-anchor": "end" }, "tick", "true " + truth.toFixed(2)));
    svg.appendChild(s("line", { x1: r1(x(window)), y1: T, x2: r1(x(window)), y2: H - B }, "boundary"));
    svg.appendChild(s("text", { x: r1(x(window)) + 5, y: T + 10 }, "tick", "the engine's window, " + window));
    function draw(rs, cls, dx) {
      rs.forEach(function (r) {
        var cx = x(r.n) + dx, lo = y(Math.max(0, r.persistence_mean - r.persistence_sd)), hi = y(Math.min(1.2, r.persistence_mean + r.persistence_sd));
        svg.appendChild(s("line", { x1: r1(cx), y1: r1(lo), x2: r1(cx), y2: r1(hi) }, "whisker " + cls));
        svg.appendChild(s("circle", { cx: r1(cx), cy: r1(y(r.persistence_mean)), r: 3.5 }, "pt " + cls));
      });
    }
    if (quoted) draw(quoted, "quoted", rows.length ? -5 : 0);
    draw(rows, "ink", quoted ? 5 : 0);
    (quoted || rows).forEach(function (r) { svg.appendChild(s("text", { x: r1(x(r.n)), y: H - B + 16, "text-anchor": "middle" }, "tick", String(r.n))); });
    svg.appendChild(s("text", { x: (L + W - R) / 2, y: H - 4, "text-anchor": "middle" }, "tick", "observations per fit (log scale)"));
    return svg;
  }

  // ---- stress: signed P&L per scenario, in suite order ----
  function stress(container, scenarios, worst) {
    var W = 760, rowH = 24, H = scenarios.length * rowH + 10, NAME = 150, MID = NAME + 190, HALF = 170;
    var svg = frame(container, W, H, "profit and loss under each scenario");
    var m = 1; scenarios.forEach(function (sc) { m = Math.max(m, Math.abs(sc.pnl)); });
    var x = lin(-m, m, MID - HALF, MID + HALF);
    svg.appendChild(s("line", { x1: MID, y1: 0, x2: MID, y2: H }, "axis"));
    scenarios.forEach(function (sc, i) {
      var yy = i * rowH + 4, v = sc.pnl, x0 = Math.min(MID, x(v));
      svg.appendChild(s("text", { x: 0, y: yy + 12 }, "name" + (sc.name === worst ? " worst" : ""), sc.name));
      svg.appendChild(s("rect", { x: r1(x0), y: yy + 3, width: r1(Math.max(0.5, Math.abs(x(v) - MID))), height: 10 }, "bx " + (v < 0 ? "loss" : "ink")));
      var label = F.money(v) + "  " + (sc.pnl_fraction === null ? "" : F.pct(sc.pnl_fraction, 1));
      svg.appendChild(s("text", { x: v < 0 ? MID + 8 : MID - 8, y: yy + 12, "text-anchor": v < 0 ? "start" : "end" }, "val", label));
      var br = (sc.new_breaches || []).map(function (b) { return b.name; });
      if (br.length) svg.appendChild(s("text", { x: MID + HALF + 16, y: yy + 12 }, "val over", "breaks " + br.join(", ")));
    });
    return svg;
  }

  window.OhCamelCharts = { scaling: scaling, attribution: attribution, tenor: tenor, strip: strip, timeline: timeline, garch: garch, stress: stress };
})();
