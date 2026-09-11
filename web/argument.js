// web/argument.js -- the README's sections, filled from what this process computed.
//
// Three sources and three tags. LIVE is this host's current frame (the stream,
// or /api/stress). COMPUTED is /api/reports and /api/reports/garch, which
// this process produced at startup. QUOTED is the README, set in the soft ink.
// Every table that is both computed and quoted prints one line saying whether
// the two agree, compared cell by cell against web/quoted.json.
(function () {
  "use strict";
  var F = window.OhCamelFormat, C = window.OhCamelCharts, G = window.OhCamelGraph;
  if (!F || !C || !document.getElementById("argument")) return;
  var el = F.el;
  function $(id) { return document.getElementById(id); }
  function set(id, t) { var e = $(id); if (e) e.textContent = t; }
  function clear(e) { while (e && e.firstChild) e.removeChild(e.firstChild); return e; }
  function num(x, dp) { return x === null || x === undefined ? "—" : x.toFixed(dp); }
  function p4(x) { return x === null || x === undefined ? "—" : x < 0.00005 ? "<0.0001" : x.toFixed(4); }
  function comma(x) { return x === null || x === undefined ? "—" : Math.round(x).toLocaleString("en-US"); }

  var quoted = null;
  try { quoted = JSON.parse($("quoted").textContent); } catch (e) { quoted = null; }
  var st = { mode: null, estimator: "parametric", reports: null, ticks: [], bars: [], snapshot: null, stressAt: 0 };

  // ---- provenance ----
  function stamp() {
    var r = st.reports, at = r ? r.computed_at.replace(/^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}).*$/, "$2Z") : "";
    Array.prototype.forEach.call(document.querySelectorAll("#argument .prov[data-prov]"), function (e) {
      var kind = e.getAttribute("data-prov");
      if (kind === "live") e.textContent = "live · this host";
      // On the live origin the reports still ran on the CLI's synthetic book and
      // seeds, and the tag must not let them pass for the book in the ledger.
      else if (kind === "computed") e.textContent = "computed · at startup" + (at ? " · " + at : "") + (st.mode === "live" ? " · synthetic book, not this host's" : "");
    });
  }

  // ---- computed against quoted ----
  // Each check is [label, computed, quoted, digits]; a cell agrees when both
  // round to the same string at the README's own precision.
  function agreement(target, checks, what) {
    var e = $(target); if (!e) return;
    if (!quoted) { e.textContent = "the README's table is not embedded in this page, so nothing is compared"; return; }
    var diffs = [];
    checks.forEach(function (c) {
      var a = c[1], b = c[2], d = c[3];
      var sa = a === null || a === undefined ? "—" : typeof a === "number" ? (d === null ? String(a) : a.toFixed(d)) : String(a);
      var sb = b === null || b === undefined ? "—" : typeof b === "number" ? (d === null ? String(b) : b.toFixed(d)) : String(b);
      if (sa !== sb) diffs.push(c[0] + " " + sb + " → " + sa);
    });
    var platform = st.reports ? st.reports.platform.system + ", " + st.reports.platform.architecture : "this host";
    e.className = "cvq" + (diffs.length ? " differs" : " agrees");
    e.textContent = diffs.length
      ? "Computed on this host (" + platform + "): differs from the README's " + what + " in " + diffs.length + " of " + checks.length + " cells — " + diffs.slice(0, 6).join("; ") + (diffs.length > 6 ? "; …" : "")
      : "Computed on this host (" + platform + "): agrees with the README's " + what + " in all " + checks.length + " cells.";
  }

  // ---- §01 ----
  // The recomputation sets this page has seen, as two shapes: a tick, and a
  // bar (the frame that runs covariance). An empty follow-up frame is neither.
  function framesLine() {
    function typical(xs) { if (!xs.length) return null; var s = xs.slice().sort(function (a, b) { return a - b; }); return s[Math.floor(s.length / 2)]; }
    var t = typical(st.ticks), b = typical(st.bars);
    if (t === null) return;
    set("frames-line", "Measured while you watched: " + (st.ticks.length + st.bars.length) + " frames since this page opened. A tick recomputed a median of " + t + " of " + (st.named || "the") + " named nodes" +
      (b !== null ? ", and a bar " + b + ", because a bar is the only thing that reaches covariance."
        : st.mode === "live" ? ". No bar runs on this host: its return windows are set once, at backfill, so covariance does not run again."
        : ", and no bar has closed yet (one every 15 s)."));
  }
  function renderScaling(r) {
    C.scaling(clear($("c-scaling")), r.scaling.rows);
    if (quoted) {
      var checks = [];
      r.scaling.rows.forEach(function (row) {
        var q = quoted.scaling.rows.filter(function (x) { return x.instruments === row.instruments; })[0];
        if (!q) return;
        checks.push([row.instruments + " names: nodes in graph", row.nodes_in_graph, q.nodes_in_graph, null]);
        checks.push([row.instruments + " names: if polled", row.if_polled, q.if_polled, null]);
      });
      agreement("q-scaling", checks, "node counts");
      var e = $("q-scaling");
      e.textContent += " Nodes per tick come from this probe's own seed and are not compared: " +
        r.scaling.rows.map(function (x) { return x.nodes_per_tick.toFixed(1); }).join(" / ") + " here, 25.6 / 25.2 / 26.0 in the README.";
    }
  }

  // ---- §02, from the stream ----
  function renderAttribution(s) {
    var box = $("c-attr"); if (!box) return;
    C.attribution(clear(box), s.positions);
    var d = s.diversification_ratio;
    set("attr-div", d === null || d === undefined ? "Diversification ratio: warming up." :
      "Diversification ratio " + d.toFixed(2) + "×: the book carries " + Math.round(100 / d) + "% of the volatility its positions would if they all moved together.");
    set("attr-note", "Shares of the parametric VaR, which decomposes. The headline VaR in the ledger is historical and does not. The decomposition reads the " +
      (s.attribution_covariance === "ewma" ? "EWMA" : "equal-weighted") + " covariance" +
      (s.euler_residual === null || s.euler_residual === undefined ? "." : ", and on this frame the Euler residual, the sum of the components minus the portfolio's volatility, is " + s.euler_residual.toExponential(1) + " (the tests allow 1e-9)."));
  }

  // ---- §03 ----
  function renderOptions(o) {
    set("opt-synthetic", "SYNTHETIC. The implied vol comes from a smile invented for this demonstration in lib/options_walk.ml, which no served graph reads: " + o.surface.formula + ". Neither deployed book holds options.");
    set("opt-contracts", String(Math.abs(o.setup.contracts))); set("opt-strike", String(o.setup.strike));
    set("opt-days", String(o.setup.expiry_days)); set("opt-spot", String(o.setup.spot));
    var t = clear($("t-walk"));
    var head = el("tr"); ["", "delta-equivalent", "gamma", "vega / vol pt"].forEach(function (h, i) { head.appendChild(el("th", i ? null : "l", h)); }); t.appendChild(head);
    o.states.forEach(function (x) {
      var tr = el("tr");
      tr.appendChild(el("td", "l", x.label)); tr.appendChild(el("td", null, F.money(x.delta_equivalent)));
      tr.appendChild(el("td", null, x.gamma.toFixed(1))); tr.appendChild(el("td", null, F.money(x.vega / 100)));
      t.appendChild(tr);
    });
    set("opt-hedge", "The hedge is " + comma(o.hedge_shares) + " shares, and the engine computed the ratio. The last row advances the valuation clock " + o.clock_advance_days + " days with nothing traded: that is theta's effect, not a theta number, because the engine never publishes theta.");
    var lims = clear($("opt-limits"));
    o.breaches.forEach(function (b) { lims.appendChild(el("div", "lim-line" + (b.breached ? " over" : ""), F.pct(b.utilisation, 1) + " · " + b.text)); });
    var cal = o.calendar;
    C.tenor(clear($("c-tenor")), cal.buckets, cal.portfolio_vega);
  }
  function renderOptionsLive(s) {
    if (s.portfolio_gamma === undefined) return;
    set("opt-live", "Live: the book in the ledger above holds " + (Math.abs(s.portfolio_gamma || 0) < 1e-9 && Math.abs(s.portfolio_vega || 0) < 1e-9 ? "no options (gamma 0, vega 0)." : "options: gamma " + num(s.portfolio_gamma, 1) + ", vega " + F.money((s.portfolio_vega || 0) / 100) + " per vol point."));
  }

  // ---- §04 and §06: the battery tables ----
  function durationCell(row) {
    if (row.duration_p === null) return ["does not apply", "na"];
    var b = row.duration_shape;
    return [p4(row.duration_p) + "  b=" + num(b, 2) + (b !== null && b >= 19.995 ? "*" : ""), null];
  }
  function batteryTable(tableId, rows, opts) {
    var t = clear($(tableId));
    var cols = [opts.first, "estimator", "n", "exceptions", "Kupiec p", "indep p", "joint p", "duration p"].concat(opts.burst ? ["burst"] : []).concat(["Basel", "verdict"]);
    var head = el("tr"); cols.forEach(function (c, i) { head.appendChild(el("th", i < 2 ? "l" : null, c)); }); t.appendChild(head);
    rows.forEach(function (row) {
      var tr = el("tr", row.rejected ? "rej" : null);
      var dur = durationCell(row);
      [[row.label, "l"], [row.estimator.label, "l"], [String(row.observations)], [row.exceptions + " / " + row.expected_exceptions.toFixed(1)],
        [p4(row.kupiec_p)], [p4(row.independence_p)], [p4(row.conditional_coverage_p)], dur]
        .concat(opts.burst ? [[row.burst ? String(row.burst.count) : "—"]] : [])
        .concat([[row.zone], [row.rejected ? "REJECTED" : "ok", "verdict"]])
        .forEach(function (c) { tr.appendChild(el("td", c[1] || null, c[0])); });
      t.appendChild(tr);
      if (opts.strips) {
        var sr = el("tr", "striprow"), td = el("td"); td.colSpan = cols.length;
        C.strip(td, row.observations, row.hits, row.label === "vol-regime" ? { boundary: 600 - 60 } : {});
        sr.appendChild(td); t.appendChild(sr);
      }
    });
  }
  function batteryChecks(rows, qrows, keyName) {
    var checks = [];
    rows.forEach(function (row) {
      var q = qrows.filter(function (x) { return x[keyName] === row.label && x.estimator === row.estimator.label; })[0];
      if (!q) { checks.push([row.label + "/" + row.estimator.label, "present", "absent", null]); return; }
      var k = row.label + "/" + row.estimator.label + " ";
      checks.push([k + "exceptions", row.exceptions, q.exceptions, null]);
      checks.push([k + "Kupiec p", row.kupiec_p, q.kupiec_p, 4]);
      checks.push([k + "indep p", row.independence_p, q.independence_p, 4]);
      checks.push([k + "joint p", row.conditional_coverage_p, q.joint_p, 4]);
      checks.push([k + "duration p", row.duration_p, q.duration_p, 4]);
      checks.push([k + "zone", row.zone, q.zone, null]);
      checks.push([k + "verdict", row.rejected ? "REJECTED" : "ok", q.verdict, null]);
      if (keyName === "window") checks.push([k + "burst", row.burst ? row.burst.count : null, q.burst, null]);
    });
    return checks;
  }
  function renderBattery(v) {
    var c = v.config;
    set("bt-config", Math.round(c.confidence * 100) + "% VaR · " + c.window + "-session window, strictly prior · α " + c.alpha + " · EWMA λ " + c.ewma_lambda + " · seed " + c.seed + " · " +
      v.synthetic.series.map(function (x) { return x.name + " (" + x.description + ")"; }).join(" · "));
    batteryTable("t-battery", v.synthetic.rows, { first: "series", strips: true });
    if (quoted) agreement("q-battery", batteryChecks(v.synthetic.rows, quoted.battery.rows, "series"), "battery table");
    set("bt-severe", v.synthetic.most_severe ? v.synthetic.most_severe.label + "\n" + v.synthetic.most_severe.text : "Nothing was rejected.");
  }

  // ---- §06 ----
  function renderCrisis(v) {
    var cr = v.crisis, book = cr.book;
    set("cr-book", "The book: " + book.positions.map(function (p) { return p.symbol + " " + (p.qty < 0 ? "short " : "") + Math.abs(p.qty); }).join(" · ") +
      " at today's synthetic marks, gross " + F.money(book.gross_notional) + ". Weights stay constant through each window: what today's book would have done, not what anyone held then.");
    function draw() {
      var box = clear($("c-crisis"));
      cr.windows.forEach(function (w) {
        var wrap = el("div", "tl");
        wrap.appendChild(el("div", "tl-head", w.name + " · " + w.first + " → " + w.last + " · " + w.sessions + " sessions, " + w.forecasts + " forecasts · worst day " + F.pct(w.worst_day, 2) + " · best " + F.pct(w.best_day, 2)));
        C.timeline(wrap, w, cr.rows.filter(function (r) { return r.label === w.name; }), st.estimator, 0.08);
        box.appendChild(wrap);
      });
      var sel = clear($("cr-select"));
      sel.appendChild(document.createTextNode("Exceptions shown for: "));
      [["historical", "historical"], ["parametric", "parametric"], ["ewma", "ewma(0.94)"]].forEach(function (k, i) {
        if (i) sel.appendChild(document.createTextNode(" · "));
        var a = el("a", "est est-" + k[0] + (st.estimator === k[0] ? " on" : ""), k[1]); a.href = "#s06";
        a.addEventListener("click", function (ev) { ev.preventDefault(); st.estimator = k[0]; draw(); });
        sel.appendChild(a);
      });
    }
    draw();
    batteryTable("t-crisis", cr.rows, { first: "window", burst: true });
    if (quoted) agreement("q-crisis", batteryChecks(cr.rows, quoted.crisis.rows, "window"), "crisis table");
    set("cr-rejected", cr.rejected + " of " + cr.rows.length + " configurations rejected at 5%.");
  }

  // ---- §05 ----
  function renderGarch(g) {
    if (g.status === "absent" || !g.truth) {
      set("garch-prov", "not run by this process");
      set("q-garch", "This process did not run the study, so only the README's figures could be shown.");
      return true;
    }
    var done = g.status === "done", failed = g.status === "failed";
    set("garch-prov", done ? "computed · on a second domain · " + (g.computed_in_ms / 1000).toFixed(1) + " s" : g.status === "failed" ? "the study failed: " + g.error : g.status + " · " + g.done + " of " + g.of + " fits, on a second domain");
    C.garch(clear($("c-garch")), done ? g.rows : [], g.truth.persistence, g.window, quoted ? quoted.garch.rows : null);
    var t = clear($("t-garch"));
    var head = el("tr"); ["n", "README: persistence", "this process: persistence", "this process: α", "this process: β"].forEach(function (h, i) { head.appendChild(el("th", i ? null : "l", h)); }); t.appendChild(head);
    var qrows = quoted ? quoted.garch.rows : [];
    (qrows.length ? qrows : g.rows).forEach(function (q) {
      var c = done ? g.rows.filter(function (x) { return x.n === q.n; })[0] : null, tr = el("tr");
      tr.appendChild(el("td", "l", String(q.n)));
      tr.appendChild(el("td", "quoted", qrows.length ? q.persistence_mean.toFixed(3) + " ± " + q.persistence_sd.toFixed(3) : "—"));
      tr.appendChild(el("td", c ? null : "na", c ? c.persistence_mean.toFixed(3) + " ± " + c.persistence_sd.toFixed(3) : failed ? "failed" : "computing"));
      tr.appendChild(el("td", c ? null : "na", c ? c.alpha_mean.toFixed(3) + " ± " + c.alpha_sd.toFixed(3) : ""));
      tr.appendChild(el("td", c ? null : "na", c ? c.beta_mean.toFixed(3) + " ± " + c.beta_sd.toFixed(3) : ""));
      t.appendChild(tr);
    });
    if (done && quoted) {
      var checks = [];
      g.rows.forEach(function (r) {
        var q = quoted.garch.rows.filter(function (x) { return x.n === r.n; })[0]; if (!q) return;
        ["alpha_mean", "alpha_sd", "beta_mean", "beta_sd", "persistence_mean", "persistence_sd"].forEach(function (k) { checks.push(["n=" + r.n + " " + k, r[k], q[k], 3]); });
      });
      agreement("q-garch", checks, "GARCH table");
    } else set("q-garch", failed ? "This process's study failed (" + g.error + "), so only the README's run is drawn." : "The README's run is drawn now. This process's fills in when its study finishes.");
    var v = done ? g.verdict_row : (quoted ? quoted.garch.rows[0] : null);
    if (v) set("garch-verdict", "At this engine's " + g.window + "-observation window the persistence comes back at " + v.persistence_mean.toFixed(3) + " ± " + v.persistence_sd.toFixed(3) +
      " against a true " + g.truth.persistence.toFixed(2) + (done ? "" : " (the README's figure, while this process computes its own)") +
      ". The spread is the size of the estimate, and the mean is biased low: sixty observations cannot tell a 34-day half-life from a 2-day one. The fit becomes defensible from n = 250. So the engine ships the equal-weighted and EWMA estimators, which are parameterised rather than fitted and have no sampling distribution to be wrong about.");
    return done || g.status === "failed" || g.status === "absent";
  }
  function pollGarch() {
    fetch("/api/reports/garch").then(function (r) { return r.json(); }).then(function (g) {
      if (!renderGarch(g)) setTimeout(pollGarch, 5000);
    }).catch(function () { setTimeout(pollGarch, 15000); });
  }

  // ---- §07 ----
  function loadStress() {
    var now = Date.now();
    if (now - st.stressAt < 5000) return;
    st.stressAt = now;
    fetch("/api/stress").then(function (r) { return r.json(); }).then(function (x) {
      var b = x.before;
      set("st-before", "Starting book: gross " + F.money(b.gross_exposure) + ", equity " + F.money(b.equity) + ", VaR " + (b.value_at_risk_notional === null ? "warming up" : F.money(b.value_at_risk_notional)) +
        ", " + (b.breached.length ? b.breached.length + " limit" + (b.breached.length > 1 ? "s" : "") + " breached (" + b.breached.join(", ") + ")" : "no limit breached") + ".");
      C.stress(clear($("c-stress")), x.scenarios, x.worst);
      var w = x.scenarios.filter(function (s) { return s.name === x.worst; })[0];
      if (w) set("st-worst", "Worst case, " + w.name + ": " + w.description + " " + w.shocks.map(function (k) { return k.text; }).join(", ") + ". P&L " + F.money(w.pnl) +
        ", equity " + F.money(w.equity_before) + " → " + F.money(w.equity_after) + ", drawdown " + F.pct(w.drawdown_before, 2) + " → " + F.pct(w.drawdown_after, 2) +
        (w.new_breaches.length ? ". New breaches: " + w.new_breaches.map(function (n) { return n.text; }).join("; ") : ". No new breach") +
        (w.unestimated_betas.length ? ". No beta for " + w.unestimated_betas.join(", ") : "") + ".");
      set("st-prov", "live · this host's book · as of " + x.as_of.replace(/^.* (\d{2}:\d{2}:\d{2}).*$/, "$1Z"));
      set("st-cost", "· " + x.scenarios.length + " forks in " + x.duration_ms.toFixed(0) + " ms; the process-wide counter in the footer moved by " + x.counter_cost.toLocaleString("en-US") + " for a reason that is not a tick. Not the README's table, which ran on the CLI's seeded book.");
    }).catch(function () { set("st-before", "The scenario suite did not answer."); });
  }

  // ---- §08 ----
  function renderVerified(v) {
    set("vf-tests", "Quoted, dated " + v.dated + ": " + v.tests + " hermetic tests, pinned by a test that counts the registered suites · coverage " + v.coverage_pct.toFixed(1) + "% = " + comma(v.coverage_covered) + " / " + comma(v.coverage_lines) + " points (bisect_ppx counts instrumented points, not lines). Neither is computed by this host.");
  }
  function renderBuild(o) {
    var b = o.build, up = o.uptime_s, u = up < 3600 ? Math.round(up / 60) + " min" : up < 86400 ? (up / 3600).toFixed(1) + " h" : (up / 86400).toFixed(1) + " d";
    set("vf-build", "This host runs build " + (b.git_short === "unknown" ? "unknown (a local build)" : b.git_short) + ", built " + b.built_at + ", OCaml " + o.ocaml_version + ", " + b.profile + ", " + b.architecture + "/" + b.system + ", up " + u + ". The smoke suite compares this sha to the droplet's checkout on every deploy.");
  }

  // ---- fragments of Figure 1 ----
  function fragments() {
    if (!G) return;
    fetch("/api/graph").then(function (r) { return r.json(); }).then(function (topo) {
      st.named = topo.counts && topo.counts.named;
      [["f-attr", ["weights", "covariance", "attribution", "component_var_map", "component_var_sector_map", "diversification_ratio"]],
       ["f-est", ["aligned_returns", "covariance", "covariance_ewma", "portfolio_returns", "historical_var", "expected_shortfall", "parametric_var", "parametric_var_ewma"]],
       ["f-garch", ["aligned_returns", "covariance_ewma", "parametric_var_ewma"]]].forEach(function (f) {
        var box = $(f[0]); if (!box) return;
        try { G.render(box, topo, { filter: { nodes: f[1] }, compact: true, inspector: false }); } catch (e) { /* the prose stands without it */ }
      });
    }).catch(function () {});
  }

  function renderReports(r) {
    st.reports = r;
    stamp();
    renderScaling(r);
    renderOptions(r.options);
    renderBattery(r.validation);
    renderCrisis(r.validation);
    renderVerified(r.verified);
  }

  // The stream's frames, handed over by dashboard.js after it renders one.
  function frame(s) {
    if (s.recomputed && s.recomputed.length) {
      var bar = s.recomputed.some(function (x) { return x.name === "covariance"; });
      (bar ? st.bars : st.ticks).push(s.recomputed.length);
      if (st.ticks.length > 2000) st.ticks.shift();
      if (st.bars.length > 2000) st.bars.shift();
      framesLine();
    }
    st.snapshot = s;
    renderAttribution(s);
    renderOptionsLive(s);
  }

  // /api/ops is dashboard.js's to poll, every 30 s; it hands each answer here.
  function ops(o) { st.mode = o.mode; renderBuild(o); stamp(); }
  // GARCH is polled once the static reports are in, so its agreement line can
  // name the platform they were computed on.
  fetch("/api/reports").then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); }).then(renderReports)
    .catch(function () { set("q-scaling", "This process did not compute its reports, so there is nothing to show beside the README's."); })
    .then(pollGarch);
  fragments();
  loadStress();
  $("st-again").addEventListener("click", function (ev) { ev.preventDefault(); loadStress(); });

  window.OhCamelArgument = { frame: frame, ops: ops };
})();
