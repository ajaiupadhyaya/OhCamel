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

  // Moved from desk.js, pointed at #ledger. The dimming rule and the
  // title it puts on a dimmed limit are unchanged; el/pct/money are
  // OhCamelFormat's, and the stale mark and the change mark are
  // OhCamelShared's, per Task 2's contract.
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

  /* Gamma and vega. Zero is a real answer here -- a book with no options has no
     convexity -- so these print 0.00 rather than a dash. */
  function renderGreeks(s) {
    var t = document.getElementById("greeks");
    if (!t) return;
    t.textContent = "";
    [["portfolio gamma", s.portfolio_gamma], ["portfolio vega", s.portfolio_vega]]
      .forEach(function (r) {
        var tr = document.createElement("tr");
        cell(tr, r[0], "lbl");
        cell(tr, r[1] === null || r[1] === undefined ? null : Number(r[1]).toFixed(2), "v num");
        t.appendChild(tr);
      });
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
      cell(tr, names.length === 0 ? "—" : names.join(", "), names.length ? "over" : "");
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
