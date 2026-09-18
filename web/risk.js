/* The risk page. Reads, renders, computes nothing.

   Every string that came from the process -- a limit's name from book.sexp, a
   scenario's description from stress.ml, a breach's own sentence -- is written
   with textContent. */
(function () {
  "use strict";
  var F = window.OhCamelFormat, S = window.OhCamelShared;

  function cell(row, text, cls) {
    var td = document.createElement("td");
    td.textContent = text === null || text === undefined || text === "" ? "—" : String(text);
    if (cls) td.className = cls;
    row.appendChild(td);
    return td;
  }

  function head(table, names) {
    var tr = document.createElement("tr");
    names.forEach(function (n) {
      var th = document.createElement("th");
      th.textContent = n;
      tr.appendChild(th);
    });
    table.appendChild(tr);
  }

  // Moved from desk.js, pointed at #ledger. The dimming rule is unchanged:
  // a limit whose node has gone stale takes the stale class, and nothing
  // else -- it carries no title. el/pct/money are OhCamelFormat's, and the
  // stale mark and the change mark are OhCamelShared's, per Task 2's
  // contract.
  function renderLedger(s) {
    var box = document.getElementById("ledger");
    if (!box) return;
    box.textContent = "";

    s.limits.forEach(function (l) {
      var d = F.el("div", "lim" + (l.breached ? " over" : ""));
      if (S.staleNode("limit:" + l.name)) d.classList.add("stale");
      var top = F.el("div", "top");
      top.appendChild(F.el("span", "name", l.name));
      top.appendChild(F.el("span", "scope", l.scope));
      var p = F.el("span", "pct num");
      var key = "lim:" + l.name;
      var shown = (l.utilisation * 100).toFixed(0) + "%";
      var q = F.el("span", "q", shown);
      // The same mark the value cells get, from the same map: this one is a
      // span inside a bar rather than a whole cell, so it asks for the mark
      // directly instead of going through value().
      S.mark(q, key, shown);
      p.appendChild(q);
      top.appendChild(p);
      d.appendChild(top);

      var bar = F.el("div", "bar");
      var fill = F.el("i");
      fill.style.width = Math.max(0, Math.min(100, l.utilisation * 100)) + "%";
      bar.appendChild(fill);
      d.appendChild(bar);

      var fmt = l.unit === "fraction" ? function (x) { return F.pct(x); } : F.money;
      d.appendChild(F.el("div", "detail",
        l.breached
          ? fmt(l.observed) + " over " + fmt(l.threshold) + " by " + fmt(l.excess)
          : fmt(l.observed) + " of " + fmt(l.threshold) + ", " + fmt(-l.excess) + " to spare"));
      box.appendChild(d);
    });

    s.unevaluated.forEach(function (name) {
      var d = F.el("div", "lim na");
      var top = F.el("div", "top");
      top.appendChild(F.el("span", "name", name));
      top.appendChild(F.el("span", "pct num", "n/a"));
      d.appendChild(top);
      d.appendChild(F.el("div", "detail", "input unavailable — not the same as passing"));
      box.appendChild(d);
    });
  }

  /* The factor the process was started with, and the book's beta to it. A beta
     the process has not estimated is a dash: a book with too little history has
     no beta, and printing 0.000 would read as "uncorrelated". */
  function renderFactor(s) {
    var t = document.getElementById("factor");
    if (!t) return;
    t.textContent = "";
    var b = s.portfolio_beta;
    [["series", s.factor], ["portfolio beta", b === null || b === undefined ? null : Number(b).toFixed(3)]]
      .forEach(function (r) {
        var tr = document.createElement("tr");
        cell(tr, r[0], "lbl");
        cell(tr, r[1], "v num");
        t.appendChild(tr);
      });
  }

  /* Gamma and vega, in the Desk's units and under the Desk's labels, so the
     two pages never show one name with numbers a hundred apart: gamma as
     dollars of delta per 1.00 move, vega divided by 100 into dollars per vol
     point (the wire carries it per 1.00 of vol; desk.js says why the division
     happens only on the page). Zero is a real answer on the demo -- a book with
     no options has no convexity -- so it prints $0 rather than a dash. On the
     live host a zero pair is not an answer but an unfed branch, so this says
     what the Desk says in the same place. */
  function greek(t, label, unit, text, neg) {
    var tr = document.createElement("tr");
    var k = F.el("td", "k", label);
    k.appendChild(F.el("em", null, unit));
    tr.appendChild(k);
    cell(tr, text, "v num" + (neg ? " neg" : ""));
    t.appendChild(tr);
  }
  function renderGreeks(s) {
    var t = document.getElementById("greeks");
    if (!t) return;
    t.textContent = "";
    var g = s.portfolio_gamma, v = s.portfolio_vega;
    var known = function (x) { return x !== null && x !== undefined; };
    if (window.OhCamelStream && OhCamelStream.mode() === "live" && g === 0 && v === 0) {
      greek(t, "options", "DISABLED — no options-chain source", "off", false);
      return;
    }
    greek(t, "gamma", "$ delta / 1.00 move", known(g) ? F.money(g) : null, g < 0);
    greek(t, "vega", "$ / vol pt", known(v) ? F.money(v / 100) : null, v < 0);
  }

  var running = false;

  function renderStress(d) {
    var box = document.getElementById("stress");
    var note = document.getElementById("stressnote");
    if (!box) return;
    box.textContent = "";
    var table = document.createElement("table");
    head(table, ["scenario", "P&L", "of equity", "equity after", "drawdown after", "new breaches"]);
    (d.scenarios || []).forEach(function (sc) {
      var tr = document.createElement("tr");
      cell(tr, sc.name, "lbl");
      cell(tr, F.money(sc.pnl), "num");
      cell(tr, F.pct(sc.pnl_fraction), "num");
      cell(tr, F.money(sc.equity_after), "num");
      cell(tr, F.pct(sc.drawdown_after), "num");
      var names = (sc.new_breaches || []).map(function (b) { return b.name; });
      // An empty list is known, not unknown: "none", as the Desk's blotter says
      // "no orders yet", and the dash stays the page's word for unknown.
      cell(tr, names.length === 0 ? "none" : names.join(", "), names.length ? "over" : "");
      if (sc.description) tr.title = sc.description;
      table.appendChild(tr);
    });
    box.appendChild(table);
    if (note) {
      note.textContent =
        "As of " + (d.as_of || "—") + ". Every scenario runs on a fork of this book, so the " +
        "numbers are this book's and not an average book's, and the live graph is untouched.";
    }
  }

  function runStress() {
    if (running) return;
    running = true;
    var button = document.getElementById("stressrun");
    var note = document.getElementById("stressnote");
    if (button) button.disabled = true;
    if (note) note.textContent = "running the suite on a fork…";
    fetch("/api/stress", { headers: { Accept: "application/json" } })
      .then(function (r) {
        if (!r.ok) throw new Error("status " + r.status);
        return r.json();
      })
      .then(renderStress)
      .catch(function (e) {
        if (note) note.textContent = "the suite did not answer: " + e.message;
      })
      .then(function () {
        running = false;
        if (button) button.disabled = false;
      });
  }

  if (window.OhCamelStream) {
    OhCamelStream.onFrame(function (s) {
      renderLedger(s);
      renderFactor(s);
      renderGreeks(s);
    });
  }
  var run = document.getElementById("stressrun");
  if (run) run.addEventListener("click", runStress);
})();
