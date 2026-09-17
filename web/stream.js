// web/stream.js -- the one connection, and the page furniture it fills.
//
// ONE CONNECTION, NOT ONE PER PAGE. /api/stream is a subscription the engine
// counts, and the ops page prints that count as "open subscribers". A second
// EventSource from the same browser would make that number say two where one
// reader is looking, and a number that does not mean what it says is worse
// than no number. So this file owns the single source, and every section on
// the page subscribes to it. It is catted into the four engine pages; the ops
// page keeps its own masthead and its own connection, and this file is not on
// it -- deliberately, because that page's stream shows what a subscriber
// looks like from the inside.
//
// It also owns everything in web/header.html, for the same reason the partial
// exists: a page carrying the masthead's ids and no writer for them would
// show eight dashes that never change, which reads as an engine with nothing
// to say rather than as a page missing a script. And it keeps the /api/graph
// fetch, because the staleness closure in shared.js needs the topology on
// every page, whether or not that page draws Figure 1.
//
// Every element lookup here returns early when the element is absent: three
// of the four pages carry none of the Desk's sections, and two of them may not
// carry the footer either.
(function () {
  "use strict";

  var S = window.OhCamelShared;
  function $(id) { return document.getElementById(id); }
  function set(id, text) { var e = $(id); if (!e) return; e.textContent = text; }

  var frameSubs = [], opsSubs = [], topologySubs = [];
  var last = null; // the last frame, for a replay
  var ops = null; // the last /api/ops answer, or null before it has answered
  var topology = null;
  var lastFrameAt = null, lastPrintAt = null;
  var started = false;

  // One broken section is a broken section, not a dead page: a renderer that
  // throws takes its own panel down and the rest of the frame still draws.
  // The error is not swallowed -- it goes to the console, because a section
  // that silently stopped updating is the hardest kind of page to debug.
  function fan(subs, arg) {
    for (var i = 0; i < subs.length; i++) {
      try {
        subs[i](arg);
      } catch (e) {
        if (window.console) console.error("OhCamelStream: a subscriber threw", e);
      }
    }
  }

  // ---- the masthead's own fields ----

  // Phase 4 state. The kernel's alerts are reported in #ks; #halt is the
  // desk's switch, and it is empty -- no box at all -- until one is tripped.
  function renderAlerts(s) {
    var a = s.alerts || { enabled: false, kill_switch: "off" };
    var ks = $("ks"), halt = $("halt"), F = window.OhCamelFormat;

    if (ks) {
      ks.className = "v ks " + (a.kill_switch === "tripped" ? "tripped"
                              : a.kill_switch === "armed" ? "armed" : "off");
      ks.textContent = !a.enabled ? "off"
        : a.kill_switch === "tripped" ? "HALTED"
        : a.kill_switch === "armed" ? "armed"
        : "on, no switch";
    }

    if (!halt) return;
    if (a.kill_switch === "tripped") {
      halt.className = "on";
      halt.textContent = "";
      halt.appendChild(F.el("b", null, "NEW ORDERS HALTED"));
      halt.appendChild(document.createTextNode("  — tripped by " + a.tripped_by + ". "));
      halt.appendChild(F.el("span", null,
        "New orders are refused and every open order is cancelled; positions are not touched. "
        + "It stays set until the engine is restarted or the switch is reset."));
    } else {
      halt.className = "";
      halt.textContent = "";
    }
  }

  // The feed's health: the dot and the word in #feed, the sentence in #warn,
  // and the class on #main that takes the authority off every number under it.
  // The three are one condition and are written together.
  function renderFeed(s) {
    var h = s.feed, F = window.OhCamelFormat;
    if (!h) return;
    lastPrintAt = null;
    (h.symbols || []).forEach(function (st) {
      if (!st.last_tick) return;
      var t = parseUtc(st.last_tick);
      if (!isNaN(t) && (lastPrintAt === null || t > lastPrintAt)) lastPrintAt = t;
    });

    var feed = $("feed");
    if (feed) {
      feed.textContent = "";
      feed.appendChild(F.el("span", "dot" + (h.healthy ? "" : " bad")));
    }
    var warn = $("warn"), main = $("main");
    if (warn) { warn.className = ""; warn.textContent = ""; }

    if (h.healthy) {
      if (feed) feed.appendChild(document.createTextNode("live"));
      if (main) main.classList.remove("stale");
    } else if (h.stale.length) {
      if (feed) feed.appendChild(document.createTextNode(h.stale.length + " stale"));
      if (warn) {
        warn.className = "on";
        warn.appendChild(F.el("b", null, "Prices are stale: " + h.stale.join(", ") + ". "));
        warn.appendChild(document.createTextNode(
          "Everything below was computed from old marks. A limit that is not breached on a stale price is not information."));
      }
      if (main) main.classList.add("stale");
    } else {
      if (feed) feed.appendChild(document.createTextNode("no prints"));
      if (warn) {
        warn.className = "on";
        warn.appendChild(F.el("b", null, "No prints yet for " + h.never_seen.join(", ") + ". "));
        warn.appendChild(document.createTextNode(
          "The subscription may not have taken, or the market may be closed."));
      }
      if (main) main.classList.remove("stale");
    }
  }

  // The masthead's three counts. #ran is set from the frame and the topology
  // together, so a frame that arrived before /api/graph answered gets its
  // denominator when the topology does -- which is what the replay below is
  // for.
  function renderCounts(s) {
    set("ran", (s.recomputed ? s.recomputed.length : "—") + " of " + (topology ? topology.counts.named : "—"));
    set("nsym", s.positions.length + " / " + s.sectors.length + " sectors");
    set("asof", s.as_of.replace("T", " ").slice(0, 19) + "Z");
  }

  // ---- the two clocks ----
  // Time_ns.to_string_utc prints nanoseconds and a space; Date.parse wants
  // milliseconds and a T.
  function parseUtc(s) { return Date.parse(s.replace(" ", "T").replace(/(\.\d{1,3})\d*Z$/, "$1Z")); }
  function age(ms) { return ms < 60000 ? (ms / 1000).toFixed(1) + " s" : Math.round(ms / 60000) + " min"; }
  function clocks() {
    var now = Date.now();
    var lf = $("lastframe"), lp = $("lastprint");
    if (lf && lastFrameAt !== null) {
      var f = now - lastFrameAt;
      lf.textContent = age(f) + (f > 60000 ? " parked" : "");
      lf.className = "v num" + (f > 60000 ? " parked" : "");
    }
    if (lp && lastPrintAt !== null) {
      // A print is stamped by the host's clock and aged by this browser's; a
      // host a second ahead would otherwise show a print from the future.
      var p = Math.max(0, now - lastPrintAt);
      var threshold = ((ops && ops.feed && ops.feed.staleness_threshold_s) || 90) * 1000;
      lp.textContent = age(p);
      lp.className = "v num" + (p > threshold ? " over" : p > threshold / 2 ? " warm" : "");
    }
  }

  // ---- the frame, and the replay ----

  function deliver(s, replayed) {
    last = s;
    if (S) S.beginFrame(s);
    renderAlerts(s);
    renderFeed(s);
    renderCounts(s);
    if (!replayed) lastFrameAt = Date.now();
    clocks();
    fan(frameSubs, s);
    if (S) S.endFrame();
  }

  // A frame that arrived before the topology (or, on the live host, before
  // /api/ops) was drawn without them: stale rows by symbol rather than by
  // closure, no limit dimmed, no options line. So that same frame is fanned
  // out again when the missing thing arrives, rather than waiting for the next
  // frame, which a parked host may not send for hours. The same frame twice
  // marks nothing as moved, because no value's text differs from itself, and
  // it does not restamp the last-frame clock, which is still aging the frame
  // the engine actually sent.
  function replay() { if (last) deliver(last, true); }

  // ---- the topology, fetched once ----
  function loadTopology() {
    fetch("/api/graph").then(function (r) { return r.json(); }).then(function (t) {
      topology = t;
      if (S) S.setTopology(t);
      // The subscribers first -- Figure 1 builds itself here -- and then the
      // replay, so the drawing exists to receive the frame it is replayed.
      fan(topologySubs, t);
      replay();
    }).catch(function () { /* no closure and no figure; the rows fall back to the symbol match */ });
  }

  // ---- the footer's process figures, every 30 s while the tab is visible ----
  // The only poll on the page. Everything on the stream is there because it is
  // a graph change; a process's pid, uptime and heap are not, so they are
  // asked for on a timer. Stopped while the tab is hidden, as /ops does,
  // because a hundred background tabs asking every 30 s is a load the engine
  // did not sign up for.
  function loadOps() {
    fetch("/api/ops").then(function (r) { return r.json(); }).then(function (o) {
      var first = ops === null;
      ops = o;
      set("mode", o.mode === "live" ? "live · Alpaca + FRED" : "demo · synthetic feed");
      fan(opsSubs, o);
      // The mode is not on the frame, and the ledger says different things on
      // the two hosts, so the first answer redraws the frame already in hand.
      if (first && o.mode === "live") replay();
    }).catch(function () { /* the masthead keeps its word and the footer its dashes */ });
  }
  var opsTimer = null;
  function startOps() { if (opsTimer === null) { loadOps(); opsTimer = setInterval(loadOps, 30000); } }
  function stopOps() { if (opsTimer !== null) { clearInterval(opsTimer); opsTimer = null; } }

  // ---- the connection ----
  // The browser retries a dropped stream by itself, unless the retry gets an
  // error status: during a redeploy the proxy answers 502 for a few seconds,
  // and EventSource then closes for good while the page says it will retry.
  // So a closed source is replaced here, after a pause that grows to 30 s.
  var retryMs = 1000;
  function connect() {
    var src = new EventSource("/api/stream");
    src.onmessage = function (e) {
      retryMs = 1000;
      set("conn", "stream connected");
      var s;
      try { s = JSON.parse(e.data); }
      catch (err) { set("conn", "bad frame: " + err.message); return; }
      deliver(s, false);
    };
    src.onerror = function () {
      var feed = $("feed");
      if (feed) feed.innerHTML = '<span class="dot idle"></span>disconnected';
      if (src.readyState === EventSource.CLOSED) {
        set("conn", "stream lost — reconnecting in " + Math.round(retryMs / 1000) + " s");
        setTimeout(connect, retryMs);
        retryMs = Math.min(30000, retryMs * 2);
      } else {
        set("conn", "stream lost — the browser will retry");
      }
    };
  }

  // Called once, as the last statement of the last script on the page, so
  // every section has registered before the first frame can arrive.
  function start() {
    if (started) return;
    started = true;
    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "visible") startOps(); else stopOps();
    });
    // A display clock, not a poll: it reads two timestamps and asks the server nothing.
    setInterval(clocks, 250);
    if (document.visibilityState !== "hidden") startOps();
    loadTopology();
    connect();
  }

  window.OhCamelStream = {
    onFrame: function (fn) { frameSubs.push(fn); },
    onOps: function (fn) { opsSubs.push(fn); },
    onTopology: function (fn) { topologySubs.push(fn); },
    // "demo" or "live", from /api/ops, and null before it has answered. Not
    // from the frame: the frame is the book, and the mode is the host.
    mode: function () { return ops ? ops.mode : null; },
    start: start
  };
})();
