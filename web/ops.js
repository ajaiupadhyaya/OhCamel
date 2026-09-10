(function () {
  "use strict";

  // /ops is the one page that asks on a timer. The dashboard is pushed a frame
  // when a value changes; a process's uptime, its heap and its build sha are
  // not graph changes and never will be. So this page polls /api/ops -- and
  // /api/health, for the per-symbol ages /api/ops deliberately does not
  // carry -- every ten seconds while the tab is visible, and draws the
  // DIFFERENCES between polls. Sixty polls is ten minutes, the window the
  // pulse strips show. Nothing here computes risk: every number is a served
  // field or the subtraction of two, and every unknown is a word in the
  // unknown colour, never a zero.
  var POLL_MS = 10000;
  var WINDOW = 60;         // polls kept per host
  var ARRIVAL_MS = 60000;  // the frame-arrival strip's axis
  var SPREAD_MS = 20000;   // the smoke suite's window, shaded on that axis

  document.title = "OhCamel — operations";

  // ---- helpers ------------------------------------------------------------
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = text;
    return e;
  }
  function svg(tag, attrs) {
    var e = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (var k in attrs) e.setAttribute(k, attrs[k]);
    return e;
  }
  function isNum(x) { return typeof x === "number" && isFinite(x); }
  function int(x) { return isNum(x) ? Math.round(x).toLocaleString("en-US") : null; }
  // The runtime counts words; the page multiplies by the word size and says so.
  function mb(words) { return isNum(words) ? (words * 8 / 1e6).toFixed(1) + " MB" : null; }

  // Core's Time_ns.to_string_utc: "2026-09-03 12:34:56.123456789Z". Date.parse
  // is not promised to read a space and nine fractional digits, so it is read
  // by hand; a string that does not match is null, never NaN.
  function parseUtc(s) {
    var m = /^(\d{4})-(\d\d)-(\d\d)[ T](\d\d):(\d\d):(\d\d)(?:\.(\d+))?Z$/.exec(s || "");
    if (!m) return null;
    var ms = m[7] ? parseInt((m[7] + "000").slice(0, 3), 10) : 0;
    return Date.UTC(+m[1], m[2] - 1, +m[3], +m[4], +m[5], +m[6], ms);
  }
  function stamp(s) { return typeof s === "string" && s.length >= 19 ? s.slice(0, 19) + "Z" : s; }
  // 3d 4h 12m, the spec's form; under an hour, minutes and seconds.
  function duration(s) {
    if (!isNum(s)) return null;
    var d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60);
    if (d > 0) return d + "d " + h + "h " + m + "m";
    if (h > 0) return h + "h " + m + "m";
    return m + "m " + Math.floor(s % 60) + "s";
  }
  // An age, re-rendered once a second as text with no transition.
  function age(ms) {
    if (!isNum(ms)) return null;
    if (ms < 10000) return (ms / 1000).toFixed(1) + " s";
    if (ms < 120000) return Math.round(ms / 1000) + " s";
    return Math.floor(ms / 60000) + " m " + Math.round(ms % 60000 / 1000) + " s";
  }

  // A group of rows in the ledger's idiom: td.k, an em note, td.v. A row is
  // [key, value, className, note, id]; a null value renders as the em dash,
  // which is the page's word for "not measured" and is never a zero.
  function group(container, label, rows) {
    var g = el("div", "grp");
    g.appendChild(el("span", "lbl", label));
    var t = el("table");
    rows.forEach(function (r) {
      var tr = el("tr");
      var k = el("td", "k", r[0]);
      if (r[3]) k.appendChild(el("em", null, r[3]));
      tr.appendChild(k);
      var v = el("td", "v num" + (r[2] ? " " + r[2] : ""),
                 r[1] === null || r[1] === undefined ? "—" : String(r[1]));
      if (r[4]) v.id = r[4];
      tr.appendChild(v);
      t.appendChild(tr);
    });
    g.appendChild(t);
    container.appendChild(g);
    return g;
  }
  function note(container, word, sentence) {
    var n = el("div", "note");
    n.appendChild(el("b", null, word));
    if (sentence) n.appendChild(document.createTextNode(" — " + sentence));
    container.appendChild(n);
    return n;
  }

  // ---- the three strips ---------------------------------------------------

  // Δ per poll as 1 px-gapped bars, scaled to the window's own maximum and to
  // nothing else: a run of zeros is a flat line and is the alarm, and needs no
  // threshold. A failed poll is one full-height --over bar, because "the page
  // could not ask" and "the engine answered zero" are different facts.
  function pulse(container, label, deltas, key) {
    var W = WINDOW * 4, H = 24, max = 0;
    deltas.forEach(function (d) { if (!d.failed && d[key] > max) max = d[key]; });
    var s = svg("svg", { viewBox: "0 0 " + W + " " + H, preserveAspectRatio: "none" });
    s.appendChild(svg("line", { x1: 0, y1: H - 0.5, x2: W, y2: H - 0.5, stroke: "var(--rule)", "stroke-width": 1 }));
    deltas.forEach(function (d, i) {
      var x = (WINDOW - deltas.length + i) * 4;
      if (d.failed) { s.appendChild(svg("rect", { x: x, y: 0, width: 3, height: H, fill: "var(--over)" })); return; }
      if (!(d[key] > 0)) return;
      var h = Math.max(1, Math.round(d[key] / max * (H - 2)));
      s.appendChild(svg("rect", { x: x, y: H - 1 - h, width: 3, height: h, fill: "var(--ink)" }));
    });
    var wrap = el("div", "strip");
    wrap.appendChild(s);
    var last = deltas.length ? deltas[deltas.length - 1] : null;
    wrap.appendChild(el("span", "cap",
      label + " · " + deltas.length + " of " + WINDOW + " polls · latest " +
      (last === null ? "—" : last.failed ? "poll failed" : "+" + int(last[key])) + " · max " + int(max)));
    container.appendChild(wrap);
  }

  // One dot per symbol on a 0..threshold axis, unlabelled. Staleness is age
  // against a threshold and nothing else; names stay off this strip even where
  // they are public, so the two hosts' strips read the same way. Past the
  // threshold the dot sits at the right end in --over; never seen is an open
  // ring in --unknown at the left, because it has no age to be placed by.
  function ageStrip(container, health, threshold_s, now) {
    var W = 240, H = 16, L = 6, R = W - 6;
    var s = svg("svg", { viewBox: "0 0 " + W + " " + H, preserveAspectRatio: "none" });
    s.appendChild(svg("line", { x1: L, y1: H / 2, x2: R, y2: H / 2, stroke: "var(--rule)", "stroke-width": 1 }));
    s.appendChild(svg("line", { x1: R, y1: 2, x2: R, y2: H - 2, stroke: "var(--ink-faint)", "stroke-width": 1 }));
    var symbols = health.symbols || [], over = 0, never = 0;
    symbols.forEach(function (sym) {
      var t = sym.never_seen ? null : parseUtc(sym.last_tick);
      if (t === null) {
        never++;
        s.appendChild(svg("circle", { cx: L, cy: H / 2, r: 3, fill: "none", stroke: "var(--unknown)", "stroke-width": 1.5 }));
        return;
      }
      var a = (now - t) / 1000, frac = Math.min(1, Math.max(0, a / threshold_s));
      if (a > threshold_s) over++;
      s.appendChild(svg("circle", { cx: L + frac * (R - L), cy: H / 2, r: 3, fill: a > threshold_s ? "var(--over)" : "var(--ink)" }));
    });
    var wrap = el("div", "strip");
    wrap.appendChild(s);
    wrap.appendChild(el("span", "cap",
      "age of last print, 0 → " + threshold_s + " s · " + symbols.length + " symbols · " +
      over + " past the threshold · " + never + " never seen"));
    container.appendChild(wrap);
  }

  // One tick per SSE frame at its browser arrival time on a 60 s axis, the
  // trailing 20 s shaded: the smoke suite's spread assertion, drawn. The
  // caption counts distinct frames in that 20 s and the seconds they spread
  // over, which is what deploy/smoke.sh prints; a frame is distinct when its
  // data differs, as the suite's `sort -u` counts it. The parked caption is
  // the live host's at night. On the demo host, whose feed ticks every 400 ms,
  // the same reading means the engine, not the market, has stopped -- and the
  // caption says which host it is on.
  function arrivalStrip(container, frames, now, state, mode) {
    var W = 300, H = 18, shadeX = W * (1 - SPREAD_MS / ARRIVAL_MS);
    var s = svg("svg", { viewBox: "0 0 " + W + " " + H, preserveAspectRatio: "none" });
    s.appendChild(svg("rect", { x: shadeX, y: 0, width: W - shadeX, height: H, fill: "var(--rule)", opacity: 0.55 }));
    s.appendChild(svg("line", { x1: 0, y1: H - 0.5, x2: W, y2: H - 0.5, stroke: "var(--rule)", "stroke-width": 1 }));
    var recent = [], seen = {};
    frames.forEach(function (f) {
      var x = W * (1 - (now - f.t) / ARRIVAL_MS);
      if (x < 0) return;
      s.appendChild(svg("line", { x1: x, y1: 3, x2: x, y2: H - 3, stroke: "var(--ink)", "stroke-width": 1 }));
      if (now - f.t <= SPREAD_MS) { recent.push(f); seen[f.data] = true; }
    });
    var distinct = Object.keys(seen).length;
    var spread = recent.length >= 2 ? ((recent[recent.length - 1].t - recent[0].t) / 1000).toFixed(1) : "0";
    var cap;
    if (recent.length === 0 && state === "parked" && mode === "live") {
      cap = "0 frames — nothing changed; the stream is parked, not dead; the counter above still advances from the 5 s clock";
    } else if (recent.length === 0 && state === "parked") {
      cap = "0 frames on the demo host, whose feed ticks every 400 ms — this is the engine not moving, not the market";
    } else {
      cap = distinct + " distinct frame" + (distinct === 1 ? "" : "s") + " spread over " + spread +
            " s of the last 20 · " + frames.length + " in 60 s";
    }
    var wrap = el("div", "strip");
    wrap.appendChild(s);
    wrap.appendChild(el("span", "cap", cap));
    container.appendChild(wrap);
  }

  // ---- one host's column -------------------------------------------------

  // Everything a column knows about its host between polls. `deltas` is the
  // last WINDOW polls as {nodes, frames, failed}; `first` is the earliest ops
  // object still trusted, which is what "book moving" divides by. A failed
  // poll leaves `last` alone, so the next good delta spans two intervals and
  // the bar beside the --over bar is taller than its neighbours: that is the
  // honest shape, not a smoothing problem.
  function hostState() {
    return { deltas: [], last: null, first: null, firstAt: 0, lastAt: 0 };
  }

  function recordPoll(st, ops, now) {
    if (ops === null) {
      st.deltas.push({ nodes: 0, frames: 0, failed: true });
    } else {
      if (st.last !== null) {
        st.deltas.push({
          nodes: Math.max(0, ops.process.nodes_recomputed - st.last.process.nodes_recomputed),
          frames: Math.max(0, ops.stream.frames_sent - st.last.stream.frames_sent),
          failed: false
        });
      }
      // First sight of this host, or a restart: uptime went backwards, so the
      // counters did too, and a rate that straddled the restart would be a lie.
      if (st.first === null || (st.last !== null && ops.uptime_s < st.last.uptime_s)) {
        st.first = ops; st.firstAt = now;
      }
      st.last = ops; st.lastAt = now;
    }
    while (st.deltas.length > WINDOW) st.deltas.shift();
  }

  // The column, redrawn whole on every poll: ~60 rows every ten seconds costs
  // nothing measurable, and it keeps this a function of (ops, health, state)
  // with no DOM to reconcile. `browser` is the EventSource this page holds --
  // passed for this host, null for the peer, whose stream carries no CORS
  // header on purpose and is therefore not this page's to read.
  function renderHost(rows, ops, health, st, now, browser) {
    rows.textContent = "";
    var b = ops.build, p = ops.process, s = ops.stream, h = ops.history, f = ops.feed;
    var fs = ops.feed_source, a = ops.alerts, gc = ops.gc, r = ops.reports;

    group(rows, "engine", [
      ["mode", ops.mode, ops.mode === "live" ? "live" : ""],
      ["up", duration(ops.uptime_s)],
      ["started", stamp(ops.started_at)],
      ["pid", ops.pid],
      ["container id", ops.hostname, "", "not the droplet"],
      ["OCaml", ops.ocaml_version],
      ["build", b.git_short, b.git_sha === "unknown" ? "unknown" : "", b.profile],
      ["built", stamp(b.built_at), b.built_at === "unknown" ? "unknown" : ""],
      ["platform", b.architecture + " " + b.system]
    ]);
    pulse(rows, "Δ nodes_recomputed per poll", st.deltas, "nodes");
    pulse(rows, "Δ frames_sent per poll", st.deltas, "frames");

    // Quiet names are stale by design (the demo's own CVX, never ticked on
    // purpose), and the server does not net them out of stale/never_seen --
    // it publishes what Feed_health says. So "over" here means more names are
    // stale than are accounted for by quiet: a difference of served fields,
    // no arithmetic beyond it. When every stale/never-seen name is a quiet
    // one, the rows stay plain and a caption says so, rather than showing a
    // permanent alarm beside "quiet by design" for the one demonstration the
    // page exists to draw honestly.
    var staleOver = f.stale + f.never_seen > f.quiet;
    group(rows, "feed", [
      ["source", fs.kind],
      ["threshold", f.staleness_threshold_s + " s"],
      ["symbols", f.symbols],
      ["healthy", f.healthy ? "yes" : "no", (!f.healthy && staleOver) ? "over" : ""],
      ["stale", f.stale, (f.stale > 0 && staleOver) ? "over" : ""],
      ["never seen", f.never_seen, f.never_seen > 0 ? "unknown" : ""],
      ["quiet by design", f.quiet, "", "counted, not named"]
    ]);
    if (!f.healthy && !staleOver) {
      note(rows, "by design", "stale names are the quiet ones");
    }
    if (health) {
      ageStrip(rows, health, f.staleness_threshold_s, now);
    } else {
      var miss = el("div", "strip");
      miss.appendChild(el("span", "cap unknown", "feed ages not drawn — /api/health did not answer"));
      rows.appendChild(miss);
    }

    // The two live-only groups. On the demo host both are null and neither is
    // drawn; a row of em dashes under "alpaca" on a synthetic feed would say
    // the feed had been asked and had nothing, which is not what happened.
    if (fs.alpaca) group(rows, "alpaca", [
      ["feed", fs.alpaca_feed],
      ["frames", int(fs.alpaca.frames)],
      ["trades", int(fs.alpaca.trades)],
      ["rejected", int(fs.alpaca.rejected), fs.alpaca.rejected > 0 ? "over" : ""],
      ["unknown symbol", int(fs.alpaca.unknown_symbol)],
      ["reconnects", int(fs.alpaca.reconnects), fs.alpaca.reconnects > 0 ? "over" : ""],
      ["last error", fs.alpaca.last_error, fs.alpaca.last_error ? "over" : ""]
    ]);
    if (fs.fred) group(rows, "fred", [
      ["series", fs.fred_series],
      ["polls", int(fs.fred.polls)],
      ["successes", int(fs.fred.successes)],
      ["observations", int(fs.fred.observations)],
      ["consecutive failures", int(fs.fred.consecutive_failures), fs.fred.consecutive_failures > 0 ? "over" : ""],
      ["last success", stamp(fs.fred.last_success)],
      ["last error", fs.fred.last_error, fs.fred.last_error ? "over" : ""]
    ]);

    var st8 = browser ? browser.state : null;
    group(rows, "stream", [
      ["state", browser ? st8 : "not opened",
        browser ? (st8 === "open" ? "live" : st8 === "reconnecting" ? "over" : "") : "unknown",
        browser ? "this browser's reading" : "cross-origin; the stream carries no CORS header on purpose"],
      ["frames in 60 s", browser ? browser.frames.length : null],
      ["last frame", browser ? age(browser.lastFrameAt === null ? null : now - browser.lastFrameAt) : null,
        "", "", browser ? "s-lastframe" : ""],
      ["frames_sent", int(s.frames_sent), "", "delivered to ≥ 1 subscriber; welcome frames excluded"],
      ["subscribers", s.subscribers, "", "open pipes only"],
      ["coalesce", s.coalesce_ms + " ms"],
      ["keepalive", s.keepalive_s + " s"]
    ]);
    if (browser) arrivalStrip(rows, browser.frames, now, st8, ops.mode);

    // history.appended per minute is the one rate a fork cannot inflate: the
    // buffer is filled by an observer on the served graph and nothing else.
    // It needs two polls to exist and says so until it has them.
    var mins = st.first !== null && st.last !== st.first ? (st.lastAt - st.firstAt) / 60000 : 0;
    var perMin = mins > 0 ? ((h.appended - st.first.history.appended) / mins).toFixed(1) : null;
    group(rows, "book moving", [
      ["appended / min", perMin, perMin === null ? "unknown" : "",
        perMin === null ? "needs two polls" : "over " + mins.toFixed(1) + " min"],
      ["appended", int(h.appended)],
      ["points", int(h.points) + " of " + int(h.capacity), "", "lost on restart"]
    ]);

    var named = ops.graph && ops.graph.named ? ops.graph.named : null;
    group(rows, "process", [
      ["nodes_recomputed", int(p.nodes_recomputed), "", "whole process: the startup probe and every fork included"],
      ["stabilizes", int(p.stabilizes)],
      ["nodes_created", int(p.nodes_created), "", "cumulative; not this graph's size"],
      ["var_sets", int(p.var_sets)],
      ["active observers", int(p.active_observers)],
      ["named nodes", named ? named.distinct + " distinct, " + int(named.total) + " runs" : "not in this build",
        named ? "" : "unknown", "the recompute log, which forks never reach"]
    ]);

    group(rows, "alerts", [
      ["enabled", a.enabled ? "yes" : "no"],
      ["kill switch", a.kill_switch, a.kill_switch === "tripped" ? "over" : ""],
      ["tripped by", a.tripped_by],
      ["tripped at", stamp(a.tripped_at)],
      ["halt new orders", a.halt_new_orders ? "yes" : "no", a.halt_new_orders ? "over" : ""],
      ["firing", a.firing.length ? a.firing.join(", ") : "none", a.firing.length ? "over" : "",
        "hysteresis: over the line, or not yet back under clear_below"],
      ["sent / failed", int(a.sent) + " / " + int(a.failed), a.failed > 0 ? "over" : ""],
      ["sinks", a.sinks.length ? a.sinks.join(", ") : "none"],
      ["trips on", a.trips_on.length ? a.trips_on.join(", ") : "none"],
      ["clear below", a.clear_below]
    ]);

    group(rows, "resources", [
      ["heap", mb(gc.heap_words), "", "words × 8"],
      ["top heap", mb(gc.top_heap_words)],
      ["minor / major", int(gc.minor_collections) + " / " + int(gc.major_collections), "", "collections"],
      ["compactions", int(gc.compactions)],
      ["RSS", isNum(ops.rss_bytes) ? (ops.rss_bytes / 1e6).toFixed(1) + " MB" : "not on this platform",
        isNum(ops.rss_bytes) ? "" : "unknown", "/proc/self/statm"]
    ]);

    group(rows, "reports", [
      ["static", r.static, r.static === "absent" ? "unknown" : ""],
      ["garch", r.garch, r.garch === "absent" ? "unknown" : ""]
    ]);
  }

  function renderHeader(ops) {
    document.getElementById("h-mode").textContent = ops.mode;
    var build = document.getElementById("h-build");
    build.textContent = ops.build.git_short;
    build.classList.toggle("unknown", ops.build.git_sha === "unknown");
    document.getElementById("h-up").textContent = duration(ops.uptime_s) || "—";
  }

  // ---- asking ---------------------------------------------------------------

  // One JSON read with a deadline. A failure is a null -- never a throw the
  // loop would have to catch twice, and never a stand-in object: a column that
  // cannot be read draws one --over bar and a sentence, not a zero.
  function getJson(url, ms) {
    var ctl = typeof AbortController === "function" ? new AbortController() : null;
    var timer = ctl ? setTimeout(function () { ctl.abort(); }, ms) : null;
    return fetch(url, { cache: "no-store", signal: ctl ? ctl.signal : undefined })
      .then(function (res) { if (!res.ok) throw new Error(String(res.status)); return res.json(); })
      .catch(function () { return null; })
      .then(function (v) { if (timer) clearTimeout(timer); return v; });
  }

  var thisState = hostState(), peerState = hostState();
  var thisRows = document.getElementById("this-rows");
  var peerRows = document.getElementById("peer-rows");
  var lastPollAt = null;
  document.getElementById("this-origin").textContent = location.origin;

  // ?peer= is this browser's override -- the one input to this page that is
  // not a served field. It is how the unreachable branch is tested and how the
  // owner points a laptop at any origin, and it reaches the DOM through
  // textContent only, so an attacker-supplied origin can put nothing on the
  // page but its own JSON's numbers.
  var override = new URLSearchParams(location.search).get("peer");

  // ---- the peer column: three fixed sentences, or a host ---------------------
  function renderPeer(ops, origin, peerOps, peerHealth, now) {
    document.getElementById("peer-origin").textContent = origin || "";
    peerRows.textContent = "";
    if (!origin) {
      if (ops === null) {
        note(peerRows, "unknown", "this host did not answer, and the peer is whatever this host says it is");
      } else if (ops.mode === "demo") {
        note(peerRows, "gated", "the live host is behind a password — open live.ohcamel…/ops to see both");
      } else {
        note(peerRows, "no peer configured", "OHCAMEL_PEER_ORIGIN is not set on this host");
      }
      return;
    }
    if (peerOps === null) {
      // Never "down": a 401, a CORS refusal, a wrong origin and a dead host
      // all read the same from a browser, and this page does not guess.
      recordPoll(peerState, null, now);
      note(peerRows, "unreachable from this browser",
           "this browser could not read " + origin + "/api/ops; from here a 401, a CORS refusal and a dead host are the same fact");
      pulse(peerRows, "Δ nodes_recomputed per poll", peerState.deltas, "nodes");
      return;
    }
    recordPoll(peerState, peerOps, now);
    renderHost(peerRows, peerOps, peerHealth, peerState, now, null);
  }

  // ---- the stream, this host only -------------------------------------------
  var browser = { state: "reconnecting", frames: [], lastFrameAt: null, es: null };
  function openStream() {
    var es = new EventSource("/api/stream");
    browser.es = es;
    es.onmessage = function (e) {
      var now = Date.now();
      browser.frames.push({ t: now, data: e.data });
      browser.lastFrameAt = now;
      while (browser.frames.length && now - browser.frames[0].t > ARRIVAL_MS) browser.frames.shift();
    };
    // The browser reconnects on its own after a dropped connection. readyState
    // 2 means it gave up, which happens on a non-200 and nowhere else; then it
    // is reopened here after one poll interval, so a proxy restart is survived
    // without a reload.
    es.onerror = function () { if (es.readyState === 2) setTimeout(openStream, POLL_MS); };
  }
  // open: the socket is up and a frame arrived inside the strip's 60 s.
  // parked: the socket is up and nothing has changed for longer than that --
  // the live host at night, and what separates it from dead is that /api/ops
  // still answers. reconnecting: the socket is not up.
  function streamState(now) {
    if (!browser.es || browser.es.readyState !== 1) return "reconnecting";
    return browser.lastFrameAt !== null && now - browser.lastFrameAt <= ARRIVAL_MS ? "open" : "parked";
  }

  // ---- the loop ---------------------------------------------------------------
  // This host first, because the peer's origin is what this host says it is;
  // then health and the peer's two routes together. Promise.all takes the
  // plain values as they are, so an absent peer costs no request.
  function poll() {
    getJson("/api/ops", 8000).then(function (ops) {
      var origin = override || (ops && ops.peer) || null;
      return Promise.all([
        ops, origin,
        getJson("/api/health", 8000),
        origin ? getJson(origin + "/api/ops", 8000) : null,
        origin ? getJson(origin + "/api/health", 8000) : null
      ]);
    }).then(function (r) {
      var ops = r[0], origin = r[1], health = r[2], peerOps = r[3], peerHealth = r[4];
      var now = Date.now();
      lastPollAt = now;
      recordPoll(thisState, ops, now);
      if (ops === null) {
        thisRows.textContent = "";
        note(thisRows, "unreachable", "/api/ops did not answer from its own origin; the strip keeps the poll that failed");
        pulse(thisRows, "Δ nodes_recomputed per poll", thisState.deltas, "nodes");
      } else {
        browser.state = streamState(now);
        renderHeader(ops);
        renderHost(thisRows, ops, health, thisState, now, browser);
      }
      renderPeer(ops, origin, peerOps, peerHealth, now);
    });
  }

  var timer = null;
  function start() { if (timer === null) { poll(); timer = setInterval(poll, POLL_MS); } }
  function stop() { if (timer !== null) { clearInterval(timer); timer = null; } }
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible") start(); else stop();
  });

  // The two ages are re-rendered as text once a second with no transition:
  // the header's "last poll" and this host's "last frame".
  setInterval(function () {
    var now = Date.now();
    document.getElementById("h-poll").textContent = lastPollAt === null ? "—" : age(now - lastPollAt) + " ago";
    var lf = document.getElementById("s-lastframe");
    if (lf) lf.textContent = browser.lastFrameAt === null ? "—" : age(now - browser.lastFrameAt);
  }, 1000);

  openStream();
  if (document.visibilityState !== "hidden") start();
})();
