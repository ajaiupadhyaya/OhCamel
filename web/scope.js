// web/scope.js -- Figure 2: the book's limits, drawn as a targeting computer.
//
// The design is docs/superpowers/specs/2026-09-24-figure-2-the-scope-design.md.
// Every limit still ahead is a gate in a tunnel, at a depth set by its
// headroom; the fullest is the target and the readout is its room to spare; a
// breached limit has passed the screen and is a pair of red rails; a limit
// that cannot be evaluated has no depth, and is one dashed gate beyond the
// vanishing point, because a limit drawn as far away reads as safe.
//
// Two halves. layout() is arithmetic on the frame and touches no DOM, so
// web/test/scope.test.js can run it under node. draw() turns its model into
// SVG and the lamps. render(s) is what desk.js calls, once per frame.
(function (root) {
  "use strict";

  var K = 5; // perspective: a gate at depth d is drawn at 1 / (1 + K d)
  var MAX_RAILS = 6; // breaches past this are counted, not drawn
  var UNKNOWN_DEPTH = 1.25; // beyond the vanishing point of an idle limit

  function clamp(x, lo, hi) { return Math.max(lo, Math.min(hi, x)); }
  function finite(x) { return typeof x === "number" && isFinite(x); }
  function depthOf(u) { return clamp(1 - u, 0, 1); }
  function scaleOf(d) { return 1 / (1 + K * d); }

  function pad(n, width) {
    var s = String(n);
    while (s.length < width) s = "0" + s;
    return s;
  }
  // The readout's magnitude in the limit's own unit: whole dollars, or basis
  // points for a fraction, because a drawdown is not a dollar figure.
  function magnitude(l) {
    var x = Math.abs(l.excess);
    return l.unit === "fraction"
      ? { n: Math.round(x * 10000), digits: pad(Math.round(x * 10000), 4), unit: "bp" }
      : { n: Math.round(x), digits: pad(Math.round(x), 6), unit: "dollars" };
  }
  function spoken(l) {
    var m = magnitude(l);
    return m.unit === "bp" ? m.n + " bp" : "$" + m.n.toLocaleString("en-US");
  }
  function pctOf(l) {
    return finite(l.utilisation) ? Math.round(l.utilisation * 100) + "%" : "—";
  }
  // How far over, for a rail's label. A breach just past the line would round
  // to "+0%", which reads as no breach at all, so under ten percent it keeps a
  // decimal.
  function overPct(u) {
    var o = (u - 1) * 100;
    return (o < 10 ? o.toFixed(1) : String(Math.round(o))) + "%";
  }
  // Sorting key for "fullest": a non-finite utilisation on a breach is the
  // worst there is (a zero threshold with something against it).
  function fullness(l) {
    if (finite(l.utilisation)) return l.utilisation;
    return l.breached ? Infinity : 1;
  }
  function byFullnessThenName(a, b) {
    var d = fullness(b) - fullness(a);
    if (d !== 0 && !isNaN(d)) return d;
    return a.name < b.name ? -1 : a.name > b.name ? 1 : 0;
  }

  function layout(limits, unevaluated, isStale) {
    limits = limits || [];
    unevaluated = unevaluated || [];
    var staleOf = function (name) { return !!isStale("limit:" + name); };

    var ahead = limits.filter(function (l) { return !l.breached; }).sort(byFullnessThenName);
    var over = limits.filter(function (l) { return l.breached; }).sort(byFullnessThenName);
    var target = ahead.length ? ahead[0] : null;

    // Far to near, the order they are drawn in, so a nearer gate is on top.
    var gates = ahead.slice().reverse().map(function (l) {
      var d = finite(l.utilisation) ? depthOf(l.utilisation) : 0;
      return {
        name: l.name, pct: pctOf(l), depth: d, scale: scaleOf(d),
        stale: staleOf(l.name), target: l === target
      };
    });

    var rails = over.slice(0, MAX_RAILS).map(function (l) {
      return {
        name: l.name, stale: staleOf(l.name),
        label: finite(l.utilisation)
          ? l.name + " +" + overPct(l.utilisation)
          : l.name + " over"
      };
    });

    var readout;
    if (target) {
      var mt = magnitude(target);
      readout = { digits: mt.digits, unit: mt.unit, over: false, stale: staleOf(target.name),
                  caption: target.name + " · " + mt.unit + " to spare" };
    } else if (over.length) {
      var mo = magnitude(over[0]);
      readout = { digits: mo.digits, unit: mo.unit, over: true, stale: staleOf(over[0].name),
                  caption: over[0].name + " · " + mo.unit + " over" };
    } else {
      readout = { digits: "------", unit: null, over: false, stale: false,
                  caption: unevaluated.length ? "no limit can be evaluated" : "this book has no limits" };
    }
    if (readout.stale) readout.caption += " · stale";

    var lamps = limits.map(function (l) {
      return { name: l.name, state: l.breached ? "over" : "ahead", pct: pctOf(l), stale: staleOf(l.name) };
    }).concat(unevaluated.map(function (n) {
      return { name: n, state: "unknown", pct: "?", stale: false };
    }));

    var parts = [];
    if (target) parts.push("Nearest limit " + target.name + ", " + spoken(target) + " to spare.");
    else if (over.length) parts.push("Every evaluable limit is breached; the worst, " + over[0].name + ", is " + spoken(over[0]) + " over.");
    else parts.push(readout.caption.charAt(0).toUpperCase() + readout.caption.slice(1) + ".");
    if (over.length && target) parts.push("Breached: " + over.map(function (l) { return l.name; }).join(", ") + ".");
    if (unevaluated.length) parts.push("Cannot be evaluated: " + unevaluated.join(", ") + ".");

    return {
      gates: gates, target: target ? target.name : null, readout: readout,
      rails: rails, railsHidden: Math.max(0, over.length - MAX_RAILS),
      unknown: unevaluated.slice(), lamps: lamps, summary: parts.join(" ")
    };
  }

  // ---------------------------------------------------------------- drawing
  // Everything below needs a document; none of it runs under node.

  var NS = "http://www.w3.org/2000/svg";
  var VB_W = 600, VB_H = 340;
  var X0 = 24, Y0 = 16, X1 = 576, Y1 = 246; // the screen's frame
  var CX = 300, CY = 131, HW = 276, HH = 115; // centre and half-size
  var TWEEN_MS = 250;

  function svg(tag, attrs, parent) {
    var e = document.createElementNS(NS, tag);
    for (var k in attrs) if (Object.prototype.hasOwnProperty.call(attrs, k)) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }
  function text(str, attrs, parent) {
    var e = svg("text", attrs, parent);
    e.textContent = str;
    return e;
  }
  function placeGate(r, s) {
    r.setAttribute("x", (CX - HW * s).toFixed(2));
    r.setAttribute("y", (CY - HH * s).toFixed(2));
    r.setAttribute("width", (2 * HW * s).toFixed(2));
    r.setAttribute("height", (2 * HH * s).toFixed(2));
  }

  // Seven segments, lit and unlit: a real display shows its dark segments
  // faintly, and the readout is drawn rather than typeset because the page
  // loads no fonts.
  var SEGS = {
    "0": "abcdef", "1": "bc", "2": "abged", "3": "abgcd", "4": "fgbc",
    "5": "afgcd", "6": "afgedc", "7": "abc", "8": "abcdefg", "9": "abcdfg", "-": "g"
  };
  function digit(ch, x, y, parent) {
    var w = 20, h = 34, t = 3.6, half = h / 2, v = half - t * 1.5;
    var geo = {
      a: [x + t, y, w - 2 * t, t], b: [x + w - t, y + t, t, v], c: [x + w - t, y + half + t / 2, t, v],
      d: [x + t, y + h - t, w - 2 * t, t], e: [x, y + half + t / 2, t, v], f: [x, y + t, t, v],
      g: [x + t, y + half - t / 2, w - 2 * t, t]
    };
    var lit = SEGS[ch] || "";
    Object.keys(geo).forEach(function (k) {
      var g = geo[k];
      svg("rect", { x: g[0], y: g[1], width: g[2], height: g[3], rx: 1.2,
                    "class": lit.indexOf(k) >= 0 ? "seg on" : "seg" }, parent);
    });
  }

  var shown = {}; // name -> the scale each gate is drawn at now
  var breachedBefore = null; // null until the first frame, so nothing blinks on load
  var raf = null;
  var reduced = function () {
    return !!(root.matchMedia && root.matchMedia("(prefers-reduced-motion: reduce)").matches);
  };

  function draw(screen, lampList, m) {
    if (raf !== null) { root.cancelAnimationFrame(raf); raf = null; }
    screen.textContent = "";
    var s = svg("svg", { viewBox: "0 0 " + VB_W + " " + VB_H, role: "img", "aria-label": m.summary }, screen);

    // The tunnel's rails to the vanishing point.
    var persp = svg("g", { "class": "persp" }, s);
    [[X0, Y0], [X1, Y0], [X0, Y1], [X1, Y1], [162, Y0], [438, Y0], [162, Y1], [438, Y1]].forEach(function (p) {
      svg("line", { x1: p[0], y1: p[1], x2: CX, y2: CY }, persp);
    });
    svg("rect", { x: X0, y: Y0, width: X1 - X0, height: Y1 - Y0, rx: 14, "class": "frame" }, s);

    // The unknown gate: fixed, dashed, beyond everything with a number.
    if (m.unknown.length) {
      var us = scaleOf(UNKNOWN_DEPTH);
      placeGate(svg("rect", { "class": "gate unknown" }, s), us);
      text("?", { x: CX + HW * us + 5, y: CY + 4, "class": "unknown-q" }, s);
    }

    // The gates, far to near, each starting from where it was last drawn.
    var fast = reduced();
    var moving = m.gates.map(function (g) {
      var cls = "gate" + (g.target ? " target" : "") + (g.stale ? " stale" : "");
      var r = svg("rect", { "class": cls }, s);
      var from = shown[g.name] === undefined ? g.scale : shown[g.name];
      placeGate(r, fast ? g.scale : from);
      return { r: r, from: from, to: g.scale, name: g.name };
    });
    var t = m.gates.filter(function (g) { return g.target; })[0];
    if (t) {
      text(t.name + " " + t.pct, { x: CX - HW * t.scale + 6, y: CY - HH * t.scale + 22,
                                   "class": "gate-label" + (t.stale ? " stale" : "") }, s);
    }

    // The rails: worst at the edge, each further breach a step inward.
    var nowBreached = {};
    m.rails.forEach(function (rl, i) {
      nowBreached[rl.name] = true;
      var fresh = breachedBefore !== null && !breachedBefore[rl.name];
      var g = svg("g", { "class": "rail" + (rl.stale ? " stale" : "") + (fresh ? " fresh" : "") }, s);
      var dx = 14 + i * 12;
      svg("line", { x1: X0 + dx, y1: Y0, x2: X0 + dx, y2: Y1 }, g);
      svg("line", { x1: X1 - dx, y1: Y0, x2: X1 - dx, y2: Y1 }, g);
      text(rl.label, { x: X0 + 14 + m.rails.length * 12 + 4, y: Y1 - 10 - i * 14, "class": "rail-label" }, g);
    });
    if (m.railsHidden) {
      text("+" + m.railsHidden + " more over", { x: X0 + 14 + m.rails.length * 12 + 4,
        y: Y1 - 10 - m.rails.length * 14, "class": "rail-label" }, s);
    }
    breachedBefore = nowBreached;

    // The readout.
    var n = m.readout.digits.length, dw = 20, gap = 8;
    var total = n * dw + (n - 1) * gap;
    var ro = svg("g", { "class": "readout" + (m.readout.over ? " over" : "") + (m.readout.stale ? " stale" : "") }, s);
    var boxW = Math.max(240, total + 44);
    svg("rect", { x: CX - boxW / 2, y: 258, width: boxW, height: 52, rx: 14, "class": "readout-box" }, ro);
    for (var i = 0; i < n; i++) digit(m.readout.digits.charAt(i), CX - total / 2 + i * (dw + gap), 267, ro);
    text(m.readout.caption, { x: CX, y: 328, "text-anchor": "middle", "class": "caption" }, s);

    // Glide each gate to its new depth; nothing moves unless a value did.
    shown = {};
    m.gates.forEach(function (g) { shown[g.name] = g.scale; });
    if (!fast && moving.some(function (x) { return x.from !== x.to; })) {
      var start = null;
      var step = function (now) {
        if (start === null) start = now;
        var k = Math.min(1, (now - start) / TWEEN_MS);
        var e = 1 - Math.pow(1 - k, 3);
        moving.forEach(function (x) { placeGate(x.r, x.from + (x.to - x.from) * e); });
        raf = k < 1 ? root.requestAnimationFrame(step) : null;
      };
      raf = root.requestAnimationFrame(step);
    }

    // The lamps: the exact figures, in the book's order.
    lampList.textContent = "";
    m.lamps.forEach(function (l) {
      var li = document.createElement("li");
      li.className = l.state + (l.stale ? " stale" : "") + (l.name === m.target ? " target" : "");
      var sq = document.createElement("span"); sq.className = "lamp"; sq.setAttribute("aria-hidden", "true");
      var nm = document.createElement("span"); nm.className = "name"; nm.textContent = l.name;
      var pc = document.createElement("span"); pc.className = "pct num";
      pc.textContent = l.state === "unknown" ? "?" : l.pct;
      li.appendChild(sq); li.appendChild(nm); li.appendChild(pc);
      lampList.appendChild(li);
    });
  }

  function render(s) {
    var screen = document.getElementById("scopescreen");
    var lamps = document.getElementById("scopelamps");
    if (!screen || !lamps || !s) return;
    var S = root.OhCamelShared;
    var stale = S && S.staleNode ? S.staleNode : function () { return false; };
    draw(screen, lamps, layout(s.limits, s.unevaluated, stale));
  }

  var api = { layout: layout, render: render, depthOf: depthOf, scaleOf: scaleOf, MAX_RAILS: MAX_RAILS };
  root.OhCamelScope = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
