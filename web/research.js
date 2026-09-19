/* Research: /api/research and /api/research/evidence, drawn and computed nowhere.

   Two reads. /api/research is the desk's record: the strategies this host's
   book registers, each one's latest judgement, how many files the intake is
   holding, and R8's sentence. /api/research/evidence is EXP-A01's two
   manifests, byte for byte as committed. The page joins them by name -- a
   registered strategy's row shows the verdict of the manifest whose slug is
   that name -- and otherwise prints what it was given: every number is the
   manifest's own, with toFixed for reading and no arithmetic behind it.

   Every server string reaches the page through F.el or a text node, never as
   markup, and every lookup of an element or a field returns early, or prints
   a dash, when it is absent. No colour here says "good": a pass is set in the
   ink, a fail in the over colour, and the page has no green. */
(function () {
  "use strict";
  var F = window.OhCamelFormat;
  if (!F) return;
  function $(id) { return document.getElementById(id); }
  function el(tag, cls, text) { return F.el(tag, cls || null, text); }

  // ---- reading, never computing ----

  function isObj(x) { return x !== null && typeof x === "object" && !Array.isArray(x); }
  function field(o, k) { return isObj(o) && Object.prototype.hasOwnProperty.call(o, k) ? o[k] : undefined; }
  function list(x) { return Array.isArray(x) ? x : []; }
  function isNum(x) { return typeof x === "number" && isFinite(x); }
  function str(x) { return typeof x === "string" ? x : null; }
  // A number, rounded to be read. -0.000 is 0.000: the sign of something that
  // rounds to nothing is not information.
  function fixed(x, dp) {
    if (!isNum(x)) return null;
    var t = x.toFixed(dp === undefined ? 3 : dp);
    return /^-0\.?0*$/.test(t) ? t.slice(1) : t;
  }
  // With its sign, as a return is read: +0.526, -0.272, and 0.000 unsigned.
  function signed(x, dp) {
    var t = fixed(x, dp);
    return t !== null && x > 0 && /[1-9]/.test(t) ? "+" + t : t;
  }
  function dash(t) { return t === null || t === undefined || t === "" ? "—" : String(t); }

  function row(table, cells, cls) {
    var tr = el("tr", cls);
    cells.forEach(function (c) {
      var td = el("td", c[1] || null, dash(c[0]));
      tr.appendChild(td);
    });
    table.appendChild(tr);
    return tr;
  }
  function head(table, names) {
    var tr = el("tr");
    names.forEach(function (n) { tr.appendChild(el("th", null, n)); });
    table.appendChild(tr);
  }
  function kv(dl, key, value) {
    dl.appendChild(el("dt", null, key));
    dl.appendChild(el("dd", "num", dash(value)));
  }

  // ---- the verdict, set plainly ----

  // A manifest's verdict is "pass", "pass, fragile" or "fail"; a judgement's
  // is "accepted", "advisory" or "rejected". Only the failing words take a
  // colour, the over colour. "advisory" takes the unknown colour the rest of
  // the site gives a thing that is shown and not acted on. Nothing takes the
  // live green: a verdict here is a record, and a coloured pass would read as
  // a recommendation this page does not make.
  function verdictClass(v) {
    if (typeof v !== "string") return "";
    if (v.indexOf("fail") === 0 || v === "rejected") return "rs-fail";
    if (v === "advisory") return "rs-advisory";
    return "";
  }

  // ---- the state: two answers, each kept until a newer one replaces it ----

  var research = null, researchError = null;
  var evidence = null, evidenceError = null;

  function manifests() { return list(field(evidence, "manifests")).filter(isObj); }
  function manifestFor(name) {
    var m = manifests().filter(function (x) { return field(x, "slug") === name; });
    return m.length ? m[0] : null;
  }

  // ---- 1. the strategies ----

  // In words, because "advisory" alone does not say what it does. The two
  // values are the book's; anything else is printed as it came.
  function sizingWords(s) {
    if (s === "advisory") return "advisory: its signals are shown, never sized";
    if (s === "live") return "live: an accepted signal is sized";
    return s;
  }

  function drawStrategies() {
    var line = $("intake"), t = $("strategies");
    if (!line || !t) return;
    t.textContent = "";
    line.className = "rs-line";
    if (!isObj(research)) {
      line.textContent = researchError ? "could not read /api/research: " + researchError : "asking /api/research…";
      return;
    }
    // A failed read keeps the last good answer on the page, and says so.
    var stale = researchError ? "the last read of /api/research failed (" + researchError + "), so this is the one before it. " : "";
    var strategies = list(field(research, "strategies")).filter(isObj);
    var intake = str(field(research, "intake")), reason = str(field(research, "reason"));
    var lastPass = str(field(research, "last_pass"));
    if (!strategies.length) {
      // The demo's own sentence, as served, says why; a host that registers
      // none for another reason says that instead.
      line.textContent = stale + (reason || "no strategy is registered on this host, so no signal file is read");
    } else if (intake === "running") {
      line.textContent = stale + "the intake is running: it reads the signal directory once a minute and judges R1–R7"
        + (lastPass ? "; its last pass was at " + lastPass : "; it has not finished a pass yet");
    } else {
      line.textContent = stale + "the intake is off" + (reason ? ": " + reason : "");
    }
    // Registered strategies first, then any manifest this host does not
    // register -- so on the demo the table is the two verdicts, each marked
    // as not registered here.
    var rows = strategies.map(function (s) { return { s: s, name: str(field(s, "name")), m: manifestFor(field(s, "name")) }; });
    manifests().forEach(function (m) {
      var slug = str(field(m, "slug"));
      if (!rows.some(function (r) { return r.name === slug; })) rows.push({ s: null, name: slug, m: m });
    });
    if (!rows.length) return;
    head(t, ["strategy", "backtest verdict", "symbols", "sizing", "capital fraction", "max age"]);
    rows.forEach(function (r) {
      var v = r.m ? str(field(r.m, "verdict")) : null;
      var tr = el("tr");
      tr.appendChild(el("td", "num", dash(r.name)));
      tr.appendChild(el("td", "rs-v " + verdictClass(v), r.m ? dash(v) : "no manifest"));
      if (r.s) {
        tr.appendChild(el("td", "num", list(field(r.s, "symbols")).filter(function (x) { return typeof x === "string"; }).join(", ") || "—"));
        tr.appendChild(el("td", "rs-l", dash(sizingWords(str(field(r.s, "sizing"))))));
        tr.appendChild(el("td", "num", dash(isNum(field(r.s, "capital_fraction")) ? String(field(r.s, "capital_fraction")) : null)));
        var age = field(r.s, "max_age");
        tr.appendChild(el("td", "num", isNum(age) ? age + " sessions" : "—"));
      } else {
        tr.appendChild(el("td", "num", dash(str(field(r.m, "symbol")))));
        tr.appendChild(el("td", "rs-l rs-quiet", "not registered on this host"));
        tr.appendChild(el("td", "num", "—"));
        tr.appendChild(el("td", "num", "—"));
      }
      t.appendChild(tr);
    });
  }

  // ---- 2. the latest signal ----

  // The judgement's verdict with what decided it: the rule a rejection
  // failed, the rule or the sizing that made it advisory, and for an accepted
  // one the rebalance it became. The detail sentence is the desk's own.
  function verdictLine(latest) {
    var v = str(field(latest, "verdict")), rule = str(field(latest, "rule"));
    if (v === "advisory" && rule === "sizing") return "advisory: its strategy's sizing is advisory";
    if (v === "advisory" && rule) return "advisory at " + rule;
    if (v === "rejected" && rule === "strategy") return "rejected: it names a symbol outside its strategy's own";
    if (v === "rejected" && rule) return "rejected at " + rule;
    if (v === "rejected") return "rejected";
    return dash(v);
  }

  function weights(latest) {
    var ts = list(field(latest, "targets")).filter(isObj);
    if (!ts.length) return field(latest, "targets") === null ? "the document could not be read back" : "none";
    // A weight is printed as served, not rounded: 0.333 and 0.33 are
    // different targets.
    return ts.map(function (t) {
      var w = field(t, "weight");
      return dash(str(field(t, "symbol"))) + " " + (isNum(w) ? String(w) : "—");
    }).join(", ");
  }

  function validation(latest) {
    var v = field(latest, "validation");
    if (!isObj(v)) return null;
    var parts = [dash(str(field(v, "status")))];
    var m = str(field(v, "manifest"));
    if (m) parts.push(m);
    var g = str(field(v, "gates_version"));
    if (g) parts.push("gates " + g);
    return parts.join(" · ");
  }

  function drawSignals() {
    var box = $("signals"), def = $("deferred");
    if (!box) return;
    box.textContent = "";
    if (def) def.textContent = "";
    if (!isObj(research)) {
      box.appendChild(el("p", "rs-line", researchError ? "not read: /api/research did not answer" : "asking /api/research…"));
      return;
    }
    var strategies = list(field(research, "strategies")).filter(isObj);
    if (!strategies.length) {
      box.appendChild(el("p", "rs-line", "no strategy is registered on this host, so there is no signal to show"));
    }
    strategies.forEach(function (s) {
      var div = el("div", "rs-sig");
      div.appendChild(el("div", "rs-sig-name num", dash(str(field(s, "name")))));
      var latest = field(s, "latest");
      if (!isObj(latest)) {
        div.appendChild(el("p", "rs-line", "no signal judged yet"));
        box.appendChild(div);
        return;
      }
      var v = str(field(latest, "verdict"));
      div.appendChild(el("p", "rs-verdict-line " + verdictClass(v), verdictLine(latest)));
      var detail = str(field(latest, "detail"));
      if (detail) div.appendChild(el("p", "rs-note", detail));
      var dl = el("dl", "rs-kv");
      kv(dl, "as of", str(field(latest, "as_of")));
      kv(dl, "sequence", isNum(field(latest, "sequence")) ? String(field(latest, "sequence")) : null);
      kv(dl, "weights", weights(latest));
      kv(dl, "validation", validation(latest));
      kv(dl, "received", str(field(latest, "received_at")));
      // Present for an accepted judgement only: "pending" until the
      // rebalance is proposed, then what came of it, in the desk's words.
      var rb = str(field(latest, "rebalance"));
      if (rb) kv(dl, "rebalance", rb);
      div.appendChild(dl);
      box.appendChild(div);
    });
    if (def && str(field(research, "intake")) === "running") {
      var n = field(research, "deferred");
      def.textContent = !isNum(n) ? "" : n === 0 ? "no file is deferred"
        : n + (n === 1 ? " file is" : " files are") + " deferred at R3, waiting for a session on or after its as_of";
    }
  }

  // ---- 3. the evidence ----

  // How each gate states its line. The numbers on the right of each line are
  // the manifest's own, read from the gate's detail -- threshold,
  // min_positive, lower_percentile, fragile_above, bps_levels -- because the
  // battery writes there the value it compared against, and a copy here could
  // drift from research/src/ohcamel_research/battery/gates.py's THRESHOLDS.
  //
  // What the manifest does not carry is written here, and it is not a number
  // that could drift: the direction of each comparison, and the zero that
  // two gates compare against. docs/CHARTER.md's gates table says "holdout
  // positive" and "lower 5th percentile > 0"; battery/gates.py's
  // holdout_gate and bootstrap_gate compare `cum > 0.0` and `lo > 0.0`; its
  // dsr_gate and psr_gate pass at or above their threshold, and
  // regimes_gate at or above min_positive. A detail that lacks its number
  // prints "not in the manifest" rather than a remembered one.
  function need(x) { return x === undefined || x === null ? "not in the manifest" : String(x); }
  var GATES = {
    holdout_positive: {
      label: function (d) {
        var span = str(field(d, "first")) && str(field(d, "last")) ? field(d, "first") + " → " + field(d, "last") : null;
        var bars = isNum(field(d, "bars")) ? field(d, "bars") + " bars" : null;
        return "holdout positive, its compounded return" + (span || bars ? " (" + [span, bars].filter(Boolean).join(", ") + ")" : "");
      },
      value: function (g) { return signed(field(g, "value")); },
      line: function () { return "> 0"; }
    },
    dsr: {
      label: function (d) {
        var n = field(d, "trial_count"), u = str(field(d, "unit"));
        return "Deflated Sharpe Ratio" + (isNum(n) ? " (" + n + " " + (u ? u.replace(/_/g, "-") : "trials") + ")" : "");
      },
      value: function (g) { return fixed(field(g, "value")); },
      line: function (d) { return "≥ " + need(field(d, "threshold")); }
    },
    psr: {
      label: function () { return "Probabilistic Sharpe Ratio"; },
      value: function (g) { return fixed(field(g, "value")); },
      line: function (d) { return "≥ " + need(field(d, "threshold")); }
    },
    bootstrap_sharpe_lower5: {
      label: function (d) {
        var p = field(d, "lower_percentile"), n = field(d, "n_samples"), b = field(d, "block");
        var extra = [isNum(n) ? n + " resamples" : null, isNum(b) ? "mean block " + b : null, isNum(field(d, "seed")) ? "seed " + field(d, "seed") : null].filter(Boolean);
        return "stationary-block bootstrap, lower " + (isNum(p) ? p : "?") + "th percentile of annualised Sharpe"
          + (extra.length ? " (" + extra.join(", ") + ")" : "");
      },
      value: function (g) { return fixed(field(g, "value")); },
      line: function (d) { var p = field(d, "lower_percentile"); return "lower " + (isNum(p) ? p : "?") + "th percentile > 0"; }
    },
    regimes_positive: {
      label: function () { return "regimes positive"; },
      value: function (g, d) {
        var v = field(g, "value"), covered = list(field(d, "covered"));
        return isNum(v) ? v + (covered.length ? " of " + covered.length : "") : null;
      },
      line: function (d) { return "≥ " + need(field(d, "min_positive")); }
    },
    pbo: {
      label: function (d) { var r = str(field(d, "rule")); return "PBO" + (r ? ", " + r : ""); },
      value: function (g) { return fixed(field(g, "value")); },
      line: function (d) { return "reported; above " + need(field(d, "fragile_above")) + " is named"; }
    },
    cost_sweep: {
      label: function () { return "cost sweep, bps round trip"; },
      value: function () { return "below"; },
      line: function (d) {
        var lv = list(field(d, "bps_levels")).filter(isNum);
        return lv.length ? "reported at " + lv.join(" / ") + " bps" : "reported";
      }
    }
  };

  // pass, fail, or reported: the battery's passed is true, false or null,
  // and a null is a figure the charter wants shown and not gated. PBO's
  // "high" is the manifest's own flag, named as the charter asks, and set in
  // the over colour because it is a warning; its row is not a failed gate.
  function result(g, d) {
    var p = field(g, "passed");
    if (p === true) return ["pass", ""];
    if (p === false) return ["fail", "rs-fail"];
    if (field(g, "name") === "pbo" && field(d, "high") === true) return ["reported: high", "rs-fail"];
    return ["reported", "rs-quiet"];
  }

  // The result beside the gate's name, before the figures behind it: on a
  // phone the table scrolls sideways, and what must be on its first screen is
  // which gates failed.
  function gateTable(m) {
    var t = el("table", "rs-t rs-gates");
    head(t, ["gate", "result", "value", "threshold"]);
    list(field(m, "gates")).filter(isObj).forEach(function (g) {
      var name = str(field(g, "name")), d = field(g, "detail");
      var spec = name !== null && Object.prototype.hasOwnProperty.call(GATES, name) ? GATES[name] : null;
      if (!isObj(d)) d = {};
      var label = spec ? spec.label(d) : dash(name);
      var value = spec ? spec.value(g, d) : fixed(field(g, "value"));
      var line = spec ? spec.line(d) : (field(d, "threshold") !== undefined ? String(field(d, "threshold")) : "not in the manifest");
      var r = result(g, d);
      row(t, [[label, "rs-l"], [r[0], "rs-v " + r[1]], [value, "num"], [line, "num"]], field(g, "passed") === false ? "rs-failed" : null);
    });
    return t;
  }

  function gate(m, name) {
    var g = list(field(m, "gates")).filter(function (x) { return field(x, "name") === name; });
    return g.length && isObj(field(g[0], "detail")) ? field(g[0], "detail") : null;
  }

  // The regimes the gate counted, each with its bars and its compounded
  // return, in the manifest's own order.
  function regimesTable(d) {
    var by = field(d, "by_regime"), bars = field(d, "bar_counts");
    if (!isObj(by)) return null;
    var names = list(field(d, "covered")).filter(function (x) { return typeof x === "string"; });
    if (!names.length) names = Object.keys(by);
    var t = el("table", "rs-t");
    head(t, ["regime", "bars", "return"]);
    names.forEach(function (n) {
      var v = field(by, n);
      row(t, [[n, "num"], [isNum(field(bars, n)) ? String(field(bars, n)) : null, "num"], [signed(v), "num"]]);
    });
    return t;
  }

  // Every level the sweep ran, the joined series the gates read beside the
  // holdout alone. The levels are the detail's; each series is looked up by
  // the key the battery wrote for that level ("5.0" for 5), found by value
  // rather than by guessing how Python printed it.
  function sweepTable(d) {
    var levels = list(field(d, "bps_levels")).filter(isNum);
    var series = ["sharpe_by_bps", "holdout_sharpe_by_bps", "total_return_by_bps", "holdout_total_return_by_bps"].map(function (k) { return field(d, k); });
    if (!levels.length) return null;
    function at(obj, level) {
      if (!isObj(obj)) return undefined;
      var keys = Object.keys(obj).filter(function (k) { return Number(k) === level; });
      return keys.length ? obj[keys[0]] : undefined;
    }
    var t = el("table", "rs-t");
    head(t, ["bps", "Sharpe, joined", "Sharpe, holdout", "return, joined", "return, holdout"]);
    levels.forEach(function (lv) {
      row(t, [[String(lv), "num"],
        [fixed(at(series[0], lv)), "num"], [fixed(at(series[1], lv)), "num"],
        [signed(at(series[2], lv)), "num"], [signed(at(series[3], lv)), "num"]]);
    });
    return t;
  }

  function params(p) {
    if (!isObj(p)) return null;
    return Object.keys(p).filter(function (k) { return k !== "symbol"; }).map(function (k) { return k + " " + p[k]; }).join(", ");
  }
  function windowText(w) {
    return isObj(w) && str(field(w, "start")) && str(field(w, "end")) ? field(w, "start") + " → " + field(w, "end") : null;
  }

  function manifestBlock(m) {
    var art = el("article", "rs-manifest");
    var slug = str(field(m, "slug")), v = str(field(m, "verdict"));
    // 1. Who it is, and its verdict.
    var h = el("h3", "rs-name");
    h.appendChild(el("span", "num", dash(slug)));
    var sub = [str(field(m, "symbol")), str(field(m, "strategy")), params(field(m, "selected_params"))].filter(Boolean).join(" · ");
    if (sub) h.appendChild(el("span", "rs-sub", sub));
    art.appendChild(h);
    var vp = el("p", "rs-verdict " + verdictClass(v));
    vp.appendChild(el("b", null, dash(v)));
    art.appendChild(vp);
    var vl = str(field(m, "verdict_line"));
    if (vl) art.appendChild(el("p", "rs-verdict-said", vl));
    // 2. The gates.
    art.appendChild(el("div", "twrap")).appendChild(gateTable(m));

    // 3. Everything else, below the verdict.
    var reg = gate(m, "regimes_positive");
    var rt = reg && regimesTable(reg);
    if (rt) {
      art.appendChild(el("div", "rs-h", "regimes: the compounded return of the joined series inside each"));
      art.appendChild(el("div", "twrap")).appendChild(rt);
    }
    var boot = gate(m, "bootstrap_sharpe_lower5");
    if (boot && (isNum(field(boot, "p50")) || isNum(field(boot, "p95")))) {
      art.appendChild(el("p", "rs-note", "bootstrap percentiles of annualised Sharpe: 50th "
        + dash(fixed(field(boot, "p50"))) + ", 95th " + dash(fixed(field(boot, "p95")))));
    }

    var sw = gate(m, "cost_sweep");
    var st = sw && sweepTable(sw);
    art.appendChild(el("div", "rs-h", "cost sweep: annualised Sharpe and compounded return at each level"));
    if (st) {
      art.appendChild(el("div", "twrap")).appendChild(st);
      var covers = str(field(sw, "covers"));
      if (covers) art.appendChild(el("p", "rs-note", covers));
    } else {
      art.appendChild(el("p", "rs-note", "the manifest carries no cost sweep"));
    }

    // Turnover and capacity: None in a manifest is "none measured", never 0,
    // which would read as "never traded" or "no capacity".
    var dl = el("dl", "rs-kv");
    var to = field(m, "turnover"), cap = field(m, "capacity");
    kv(dl, "turnover, one-way, a year", isNum(to) ? fixed(to, 2) : "none measured");
    kv(dl, "capacity", isNum(cap) ? F.money(cap) : "none measured");
    art.appendChild(el("div", "rs-h", "turnover and capacity"));
    art.appendChild(dl);

    // The deflation: its unit, and how many trial Sharpes each source gave.
    var dsr = field(m, "dsr");
    art.appendChild(el("div", "rs-h", "the Deflated Sharpe Ratio's trials"));
    if (isObj(dsr)) {
      var dd = el("dl", "rs-kv");
      kv(dd, "unit", str(field(dsr, "unit")));
      kv(dd, "trials", isNum(field(dsr, "trial_count")) ? String(field(dsr, "trial_count")) : null);
      var src = field(dsr, "trial_sharpe_sources");
      if (isObj(src)) Object.keys(src).forEach(function (k) { kv(dd, k, isNum(src[k]) ? String(src[k]) : null); });
      kv(dd, "series", str(field(dsr, "returns_series")));
      art.appendChild(dd);
    } else {
      art.appendChild(el("p", "rs-note", "the manifest carries no DSR block"));
    }

    // PBO, with both of its figures.
    var pbo = gate(m, "pbo");
    art.appendChild(el("div", "rs-h", "PBO: both figures, and the rule that picks between them"));
    if (pbo) {
      var pd = el("dl", "rs-kv");
      kv(pd, "rule", str(field(pbo, "rule")));
      kv(pd, "the port's", fixed(field(pbo, "pbo_port")));
      kv(pd, "fdq's", field(pbo, "pbo_fdq") === null ? "none: " + dash(str(field(pbo, "pbo_fdq_error"))) : fixed(field(pbo, "pbo_fdq")));
      kv(pd, "splits", isNum(field(pbo, "splits")) ? String(field(pbo, "splits")) : null);
      kv(pd, "overfit by one only", isNum(field(pbo, "splits_port_overfit_only")) || isNum(field(pbo, "splits_fdq_overfit_only"))
        ? "port " + dash(field(pbo, "splits_port_overfit_only")) + ", fdq " + dash(field(pbo, "splits_fdq_overfit_only")) : null);
      kv(pd, "configurations", isNum(field(pbo, "configurations")) ? String(field(pbo, "configurations")) : null);
      kv(pd, "window", windowText(field(pbo, "window")));
      art.appendChild(pd);
    } else {
      art.appendChild(el("p", "rs-note", "the manifest carries no PBO gate"));
    }

    // Where it came from.
    var pv = el("dl", "rs-kv");
    kv(pv, "selection window", windowText(field(m, "selection_window")));
    kv(pv, "holdout window", windowText(field(m, "holdout_window")));
    kv(pv, "gates version", str(field(m, "gates_version")));
    kv(pv, "ran at", str(field(m, "ran_at")));
    art.appendChild(el("div", "rs-h", "the run"));
    art.appendChild(pv);

    var notes = list(field(m, "notes")).filter(function (x) { return typeof x === "string"; });
    if (notes.length) {
      var det = el("details", "rs-notes");
      det.appendChild(el("summary", null, "the manifest's " + notes.length + " notes"));
      var ul = el("ul");
      notes.forEach(function (n) { ul.appendChild(el("li", null, n)); });
      det.appendChild(ul);
      art.appendChild(det);
    }
    return art;
  }

  function drawEvidence() {
    var box = $("evidence");
    if (!box) return;
    box.textContent = "";
    if (!isObj(evidence)) {
      box.appendChild(el("p", "rs-line", evidenceError ? "could not read /api/research/evidence: " + evidenceError : "asking /api/research/evidence…"));
      return;
    }
    var ms = manifests();
    if (!ms.length) { box.appendChild(el("p", "rs-line", "the evidence holds no manifest")); return; }
    ms.forEach(function (m) { box.appendChild(manifestBlock(m)); });
  }

  // ---- 5. R8, exactly as served ----
  function drawR8() {
    var p = $("r8");
    if (!p) return;
    p.textContent = dash(str(field(research, "r8")));
  }

  function drawResearch() { drawStrategies(); drawSignals(); drawR8(); }

  // ---- the reads ----

  function get(path) {
    return fetch(path).then(function (r) {
      if (!r.ok) throw new Error("status " + r.status);
      return r.json();
    });
  }

  // The evidence is a fact about the build, so it is asked for once, and
  // again only if that failed.
  var evidenceInFlight = false;
  function loadEvidence() {
    if (isObj(evidence) || evidenceInFlight) return;
    evidenceInFlight = true;
    get("/api/research/evidence").then(function (b) {
      evidence = isObj(b) ? b : null;
      evidenceError = evidence ? null : "the answer was not an object";
    }).catch(function (e) {
      evidenceError = e && e.message ? e.message : "no answer";
    }).then(function () {
      evidenceInFlight = false;
      drawEvidence();
      drawStrategies(); // the verdict column reads the evidence
    });
  }

  // The record is asked for at load, whenever a frame says the journal moved
  // (the desk's version: a judgement is a journal write), and once a minute
  // while the tab is visible -- the intake's pass is once a minute, and its
  // count of deferred files lives in memory, where no journal write shows it.
  // One request in flight. A reason to ask that arrives while one is out is
  // kept, and asked once more when it lands, so a judgement recorded during
  // a read is not left for the timer.
  var researchInFlight = false, askAgain = false;
  function loadResearch() {
    loadEvidence();
    if (researchInFlight) { askAgain = true; return; }
    researchInFlight = true;
    askAgain = false;
    get("/api/research").then(function (b) {
      if (isObj(b)) { research = b; researchError = null; }
      else researchError = "the answer was not an object";
    }).catch(function (e) {
      researchError = e && e.message ? e.message : "no answer";
    }).then(function () {
      researchInFlight = false;
      drawResearch();
      if (askAgain) loadResearch();
    });
  }

  var version = null;
  function onFrame(s) {
    var d = s && s.desk;
    if (!isObj(d) || !isNum(d.version) || d.version === version) return;
    var first = version === null;
    version = d.version;
    if (!first) loadResearch();
  }

  var timer = null;
  function startTimer() { if (timer === null) timer = setInterval(loadResearch, 60000); }
  function stopTimer() { if (timer !== null) { clearInterval(timer); timer = null; } }
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible") { loadResearch(); startTimer(); } else stopTimer();
  });
  if (document.visibilityState !== "hidden") startTimer();

  drawEvidence();
  loadResearch();
  if (window.OhCamelStream) OhCamelStream.onFrame(onFrame);
})();
