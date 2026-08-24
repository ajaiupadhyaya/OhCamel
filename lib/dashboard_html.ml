(* Phase 3. The dashboard page, served from memory by server.ml.

   Embedded as a string rather than read from disk so the binary is
   self-contained: no asset path to get wrong, nothing to forget to deploy, and
   `ohcamel serve` works from any working directory.

   ------------------------------------------------------------------------
   THE DESIGN, AND WHY IT IS THIS AND NOT A TRADING TERMINAL PASTICHE

   Three decisions, each derived from something true about this engine rather
   than from what risk dashboards usually look like.

   1. THE LAYOUT IS THE GRAPH. Three columns, left to right: positions, then
      book aggregates, then limits. That is the dependency order in graph.ml --
      exposure feeds gross feeds VaR feeds the limits that read them. Reading
      the page left to right is reading the graph downstream. A conventional
      dashboard would group by "what the user wants to see"; this one groups by
      what depends on what, because that is the thing this project is about.

   2. CHANGED VALUES ARE MARKED. Every number that moved since the previous
      frame gets a brief underline. This is the architecture made visible: a
      tick in one name lights that instrument, its sector and the aggregates,
      and visibly does NOT light the others. It is real data, not decoration --
      the client diffs successive snapshots, so a mark means that value actually
      changed.

   3. THE NUMBERS LOSE AUTHORITY WHEN THE FEED DOES. If prices go stale, the
      risk figures desaturate and pick up a hatch. This is the one deliberately
      aggressive move on the page, and it is the whole thesis of the project
      expressed in CSS: a limit reading "not breached" from a twenty-minute-old
      mark is not information, and it should not look like information.

   Monospace is used for data and nothing else -- no monospace prose, no
   monospace headings. Labels are a plain grotesque, small and letter-spaced.
   With no web fonts available (the page must be self-contained, and a CDN
   request would be a dependency on the network for a page whose job is to work
   when the network is misbehaving), personality has to come from the setting
   rather than the faces, so it comes from scale, tracking and colour instead.

   Both colour schemes are authored, not one inverted. Light is a cool paper for
   reading over a long session; dark is for a dim room. Neither uses the
   green-on-black that would make this look like every other market display.

   Palette semantics, which are three states and not two: within limit, over
   limit, and CANNOT BE EVALUATED. That third state gets a real colour rather
   than a grey, because "unknown" quietly rendered as "fine" is the failure
   mode this whole engine is built against. *)

let page =
  {html|<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OhCamel — risk</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Crect width='16' height='16' fill='%2316181a'/%3E%3Crect x='3' y='9' width='2' height='4' fill='%23e8ebe9'/%3E%3Crect x='7' y='5' width='2' height='8' fill='%23e8ebe9'/%3E%3Crect x='11' y='7' width='2' height='6' fill='%23b8860b'/%3E%3C/svg%3E">
<style>
  :root {
    --ground: #f1f3f1;
    --panel: #fbfcfb;
    --ink: #16181a;
    --ink-soft: #5c6360;
    --ink-faint: #969c98;
    --rule: #d5dad6;
    --over: #a8321c;
    --over-wash: #f7e6e2;
    --unknown: #3f4f96;
    --unknown-wash: #e6e9f6;
    --live: #1c6b4a;
    --mark: #b8860b;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --ground: #14171a;
      --panel: #1b1f22;
      --ink: #e8ebe9;
      --ink-soft: #98a09c;
      --ink-faint: #626a66;
      --rule: #2c3236;
      --over: #e8674a;
      --over-wash: #3a1f19;
      --unknown: #8b9ae0;
      --unknown-wash: #1f2540;
      --live: #4fbb8a;
      --mark: #d9a441;
    }
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; }
  body {
    background: var(--ground);
    color: var(--ink);
    font: 400 14px/1.45 ui-sans-serif, -apple-system, "Segoe UI", Roboto, sans-serif;
    -webkit-font-smoothing: antialiased;
    min-height: 100vh;
    display: flex;
    flex-direction: column;
  }
  main { flex: 1; }
  .num {
    font-family: ui-monospace, "SF Mono", SFMono-Regular, Menlo, Consolas, monospace;
    font-variant-numeric: tabular-nums;
    letter-spacing: -0.01em;
  }
  .lbl {
    font-size: 10px;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: var(--ink-faint);
    font-weight: 500;
  }

  /* ---- status bar ---- */
  header {
    display: flex; flex-wrap: wrap; gap: 8px 22px; align-items: baseline;
    padding: 14px 20px;
    border-bottom: 1px solid var(--rule);
    background: var(--panel);
    position: sticky; top: 0; z-index: 10;
  }
  h1 {
    margin: 0; font-size: 15px; font-weight: 620; letter-spacing: -0.01em;
  }
  h1 span { color: var(--ink-faint); font-weight: 400; margin-left: 8px; font-size: 12px; }
  .stat { display: flex; gap: 7px; align-items: baseline; }
  .stat .v { font-size: 13px; }
  .dot {
    width: 7px; height: 7px; border-radius: 50%;
    background: var(--live); display: inline-block; margin-right: 6px;
    vertical-align: middle;
  }
  .dot.bad { background: var(--over); }
  .dot.idle { background: var(--ink-faint); }
  header .spacer { margin-left: auto; }

  /* ---- the stale banner: the one loud thing on the page ---- */
  #warn { display: none; }
  #warn.on {
    display: block;
    padding: 10px 20px;
    background: var(--over-wash);
    color: var(--over);
    border-bottom: 1px solid var(--over);
    font-size: 13px;
  }
  #warn b { font-weight: 620; }

  /* ---- kill switch ----
     The loudest thing the page can show, and the only inverted block on it.
     A halt is a decision that is currently in force; it should not be possible
     to glance at this page and miss one. */
  #halt { display: none; }
  #halt.on {
    display: block;
    padding: 12px 20px;
    background: var(--over);
    color: #fff;
    font-size: 13px;
    font-weight: 500;
  }
  #halt b { font-weight: 700; letter-spacing: 0.04em; }
  #halt span { opacity: .85; }
  .ks { font-size: 13px; }
  .ks.armed { color: var(--live); }
  .ks.tripped { color: var(--over); font-weight: 620; }
  .ks.off { color: var(--ink-faint); }

  /* ---- three columns, in dependency order ---- */
  main {
    display: grid;
    grid-template-columns: minmax(240px, 1fr) minmax(240px, 1fr) minmax(300px, 1.3fr);
    gap: 1px;
    background: var(--rule);
    border-bottom: 1px solid var(--rule);
  }
  section { background: var(--panel); padding: 18px 20px 22px; min-width: 0; }
  section > .lbl { display: block; margin-bottom: 14px; }
  section > .lbl i {
    font-style: normal; color: var(--ink-faint); letter-spacing: 0;
    text-transform: none; font-size: 10px; opacity: .75;
  }
  @media (max-width: 900px) { main { grid-template-columns: 1fr; } }

  /* ---- rows ---- */
  table { width: 100%; border-collapse: collapse; }
  td { padding: 5px 0; vertical-align: baseline; }
  td.k { color: var(--ink-soft); font-size: 13px; white-space: nowrap; }
  td.k em { font-style: normal; color: var(--ink-faint); font-size: 11px; margin-left: 6px; }
  td.v { text-align: right; white-space: nowrap; font-size: 13px; }
  /* The risk-share column. Deliberately quieter than the exposure beside it:
     it is a second reading of the same row, not a competing headline. The
     column exists because "where the money is" and "where the risk is" are
     different questions, and putting the two answers on one line is the only
     way the difference is legible at a glance. */
  td.risk { text-align: right; white-space: nowrap; font-size: 12px;
            color: var(--ink-faint); width: 4.5em; padding-left: 14px; }
  td.risk.hedge { color: var(--ok); }
  tr.total td { border-top: 1px solid var(--rule); padding-top: 9px; }
  tr.gap td { padding-top: 16px; }
  .neg { color: var(--over); }

  /* the headline figures */
  .big td.v { font-size: 20px; letter-spacing: -0.02em; }
  .big td.k { font-size: 13px; }

  /* ---- change marks: the signature ----
     A value that moved gets a brief rule under it. Real information: the client
     diffs successive frames, so a mark means that number actually changed. */
  .q { display: inline-block; border-bottom: 1.5px solid transparent; padding-bottom: 1px; }
  @keyframes fade { from { border-bottom-color: var(--mark); } to { border-bottom-color: transparent; } }
  .q.moved { animation: fade .75s ease-out forwards; }
  @media (prefers-reduced-motion: reduce) { .q.moved { animation: none; } }

  /* ---- limits ---- */
  .lim { padding: 9px 0; border-bottom: 1px solid var(--rule); }
  .lim:last-child { border-bottom: 0; }
  .lim .top { display: flex; align-items: baseline; gap: 10px; }
  .lim .name { font-size: 13px; font-weight: 520; }
  .lim .scope { font-size: 11px; color: var(--ink-faint); }
  .lim .pct { margin-left: auto; font-size: 13px; }
  .lim .detail { font-size: 11px; color: var(--ink-faint); margin-top: 3px; }
  .bar { height: 3px; background: var(--rule); margin-top: 6px; position: relative; }
  .bar i { position: absolute; inset: 0 auto 0 0; background: var(--ink-soft); display: block; }
  .lim.over .name, .lim.over .pct { color: var(--over); }
  .lim.over .bar i { background: var(--over); }
  .lim.na .name, .lim.na .pct { color: var(--unknown); }
  .lim.na { background: var(--unknown-wash); margin: 0 -20px; padding-left: 20px; padding-right: 20px; }

  /* ---- stale: data that should not be trusted stops looking trustworthy ----
     Scoped by DEPENDENCY, not by page. A stale print on one symbol compromises
     the aggregates and every limit computed from them -- but it says nothing
     about the other five instruments, whose own exposures are still exactly
     right. Dimming those too would be the same overreach the engine avoids
     internally, where a tick in one name leaves the others untouched. */
  tr.rowstale .num, tr.rowstale td.k { opacity: .38; }
  main.stale section:nth-child(2) .num,
  main.stale section:nth-child(3) .num,
  main.stale section:nth-child(3) .detail { opacity: .38; }
  main.stale section:nth-child(2), main.stale section:nth-child(3) {
    background-image: repeating-linear-gradient(
      -45deg, transparent 0 9px, color-mix(in srgb, var(--over) 7%, transparent) 9px 10px);
  }

  footer {
    padding: 12px 20px 26px; color: var(--ink-faint); font-size: 11px;
    display: flex; flex-wrap: wrap; gap: 6px 20px;
  }
  footer .num { font-size: 11px; }
  a { color: inherit; }
</style>
</head>
<body>

<header>
  <h1>OhCamel<span>reactive risk &amp; limits</span></h1>
  <div class="stat"><span class="lbl">feed</span><span class="v" id="feed"><span class="dot idle"></span>connecting</span></div>
  <div class="stat"><span class="lbl">book</span><span class="v num" id="nsym">—</span></div>
  <div class="stat spacer"><span class="lbl">nodes recomputed</span><span class="v num" id="nodes">—</span></div>
  <div class="stat"><span class="lbl">alerts</span><span class="v ks off" id="ks">off</span></div>
  <div class="stat"><span class="lbl">as of</span><span class="v num" id="asof">—</span></div>
</header>

<div id="halt"></div>
<div id="warn"></div>

<main id="main">
  <section>
    <span class="lbl">positions <i>— exposure, and share of risk</i></span>
    <table id="pos"></table>
    <table id="sectors" style="margin-top:18px"></table>
  </section>

  <section>
    <span class="lbl">book <i>— aggregates</i></span>
    <table id="book"></table>
  </section>

  <section>
    <span class="lbl">limits <i>— downstream</i></span>
    <div id="limits"></div>
  </section>
</main>

<footer>
  <span>updates arrive when a value changes, not on a timer</span>
  <span id="conn">—</span>
  <span id="alertstat">—</span>
</footer>

<script>
(function () {
  "use strict";

  var prev = {};          // last rendered value per key, for change marks
  var firstFrame = true;  // do not mark everything as "moved" on load

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
    var k = el("td", "k");
    k.appendChild(document.createTextNode(label));
    if (note) k.appendChild(el("em", null, note));
    tr.appendChild(k);
    tr.appendChild(value(key, text, cls));
    table.appendChild(tr);
    return tr;
  }

  // A row's share of portfolio VaR, appended as a third cell.
  //
  // Rendered as a percentage of the total rather than in dollars, because the
  // question it answers is comparative -- this name against the others -- and
  // dollars invite it to be read against the exposure on the same line, which
  // is a different quantity in different units.
  //
  // A NEGATIVE share is a position that moves against the book and therefore
  // reduces portfolio risk. It gets the ok colour and a minus sign rather than
  // being shown as a magnitude: a hedge and a risk contributor are opposite
  // facts and must not look the same.
  function riskCell(tr, key, share, total) {
    var td = el("td", "risk");
    if (share === null || share === undefined || total === null || !total) {
      td.textContent = "—";
    } else {
      var f = share / total;
      td.textContent = (f * 100).toFixed(1) + "%";
      if (f < 0) td.classList.add("hedge");
      var shown = td.textContent;
      if (!firstFrame && prev[key] !== undefined && prev[key] !== shown) {
        td.classList.add("moved");
      }
      prev[key] = shown;
    }
    tr.appendChild(td);
  }

  function renderPositions(s) {
    // Component VaR is an Euler decomposition, so the shares sum to the
    // portfolio total exactly -- which is what makes a percentage of the total
    // a meaningful number. Summed here rather than taken from a separate field
    // so the denominator is provably the same numbers as the numerators.
    var total = null;
    if (s.positions.length && s.positions[0].component_var !== null) {
      total = 0;
      s.positions.forEach(function (p) { total += p.component_var; });
    }

    var t = document.getElementById("pos");
    t.textContent = "";
    s.positions.forEach(function (p) {
      var tr = row(t, "pos:" + p.symbol, p.symbol, p.sector,
          money(p.exposure), p.exposure < 0 ? "neg" : null,
          staleSet[p.symbol] ? "rowstale" : null);
      riskCell(tr, "risk:" + p.symbol, p.component_var, total);
    });

    var st = document.getElementById("sectors");
    st.textContent = "";
    s.sectors.forEach(function (x) {
      // A sector total is only as good as its worst member.
      var tr = row(st, "sec:" + x.sector, x.sector, null,
          money(x.exposure), x.exposure < 0 ? "neg" : null,
          staleSectors[x.sector] ? "rowstale" : null);
      riskCell(tr, "risk:sec:" + x.sector, x.component_var, total);
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
    row(t, "pvar", "parametric", "normal", s.parametric_var === null ? null
        : money(s.parametric_var * s.gross_exposure));
    row(t, "beta", "beta", s.factor, s.portfolio_beta === null ? null
        : s.portfolio_beta.toFixed(3));
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

  function render(s) {
    renderAlerts(s);
    renderHealth(s);
    renderPositions(s);
    renderBook(s);
    renderLimits(s);
    document.getElementById("nsym").textContent =
      s.positions.length + " / " + s.sectors.length + " sectors";
    document.getElementById("nodes").textContent = s.nodes_recomputed.toLocaleString("en-US");
    document.getElementById("asof").textContent = s.as_of.replace("T", " ").slice(0, 19) + "Z";
    var a = s.alerts || {};
    document.getElementById("alertstat").textContent =
      a.enabled ? ("alerts sent " + a.sent + (a.failed ? ", failed " + a.failed : ""))
                : "alerting disabled";
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
</script>
</body>
</html>
|html}
