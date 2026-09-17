// web/shared.js -- the frame state every section shares.
//
// Four pages draw from one frame, and three things about that frame are not a
// section's to own. Which symbols have no recent print, which sectors hold one
// and which nodes are downstream of one: the positions table marks rows from
// the first two, the ledger dims limits from the third, and Figure 1 dims the
// same names for the same reason. Computed once per frame, here, rather than
// three times from three copies that would drift the moment one page's
// renderer changed. And the change marks -- the brief underline on a value
// that moved -- need the previous frame's text per key and a flag saying this
// is the first frame; a page whose sections each kept their own `prev` would
// mark nothing on one section and everything on another.
//
// The stream calls beginFrame before it fans a frame out and endFrame after,
// so every section sees the same stale sets and the same first-frame flag.
// There are no element lookups in this file: it builds cells and answers
// questions, and the pages it is catted into do the looking up.
(function () {
  "use strict";

  var prev = {}; // last rendered text per key, for change marks
  var firstFrame = true; // do not mark everything as "moved" on load

  var topology = null; // the served graph, once /api/graph has answered
  var staleSyms = []; // symbols with no recent print, in the frame's order
  var staleSet = {}; // the same, as a lookup
  var staleSectors = {}; // sectors holding at least one of them
  var staleNodes = new Set(); // downstream closure of every stale price, by node name

  // The topology cannot change after construction, so this is set once, by the
  // stream, and the closure below is empty until it is. A page that drew the
  // closure from a topology it did not have would dim nothing and look
  // healthy, which is why the fallback is the symbol match rather than a claim.
  function setTopology(t) {
    topology = t;
  }

  function beginFrame(s) {
    var h = (s && s.feed) || {};
    staleSyms = (h.stale || []).concat(h.never_seen || []);
    staleSet = {};
    staleSectors = {};
    staleSyms.forEach(function (sym) { staleSet[sym] = true; });
    // A sector total is only as good as its worst member.
    ((s && s.positions) || []).forEach(function (p) {
      if (staleSet[p.symbol] && p.sector) staleSectors[p.sector] = true;
    });
    // Staleness follows the edges, not the column: the downstream closure of
    // each stale price, from the served topology. Until the topology has
    // arrived -- or on a page that does not carry graph.js, and so has no
    // closure to walk it with -- this stays empty and the rows fall back to
    // the symbol match above.
    staleNodes = new Set();
    if (topology && window.OhCamelGraph) {
      staleSyms.forEach(function (sym) {
        var cell = "price[" + sym + "]";
        staleNodes.add(cell);
        window.OhCamelGraph.closure(topology, [cell], "down").forEach(function (n) {
          staleNodes.add(n);
        });
      });
    }
  }

  // Called after the fan-out, not before it: every section must see
  // firstFrame true for the whole of the first frame, or the first section to
  // draw would clear the flag and the rest would mark their every value as
  // moved on load.
  function endFrame() {
    firstFrame = false;
  }

  // The change mark itself. A node's text differs from the text this key
  // carried last frame, so it moved -- which is the page's one piece of
  // ornament and is entirely data-driven. The first frame marks nothing,
  // because nothing has moved yet, and a key seen for the first time marks
  // nothing either, because a row that has just appeared did not move.
  function mark(node, key, shown) {
    if (!firstFrame && prev[key] !== undefined && prev[key] !== shown) {
      node.classList.add("moved");
    }
    prev[key] = shown;
  }

  // A value cell.
  function value(key, text, extraClass) {
    var td = window.OhCamelFormat.el("td", "v num" + (extraClass ? " " + extraClass : ""));
    var shown = text === null || text === undefined ? "—" : text;
    td.textContent = shown;
    mark(td, key, shown);
    return td;
  }

  // A row's share of portfolio VaR, and that share over its share of money.
  //
  // Both come from the encoder (risk_share, risk_over_money): invariant 2
  // applied to a division. The page does no arithmetic on risk. A NEGATIVE
  // share is a hedge: it gets the ok colour and keeps its sign.
  function riskCell(tr, key, share, ratio) {
    var F = window.OhCamelFormat;
    var td = F.el("td", "risk");
    if (share === null || share === undefined) {
      td.textContent = "—";
    } else {
      var shown = (share * 100).toFixed(1) + "%";
      td.appendChild(document.createTextNode(shown));
      if (share < 0) td.classList.add("hedge");
      mark(td, key, shown);
    }
    if (ratio !== undefined) td.appendChild(F.el("span", "rm", ratio === null ? "--" : ratio.toFixed(2) + "×"));
    tr.appendChild(td);
  }

  window.OhCamelShared = {
    beginFrame: beginFrame,
    endFrame: endFrame,
    setTopology: setTopology,
    // There is deliberately no topology() accessor. The stream hands the
    // topology to every page that wants it through onTopology, and a second
    // door onto the same fact invites a later section to poll for it instead
    // of subscribing -- which is the one of the two that can run before it
    // has arrived and quietly draw an empty closure.
    value: value,
    riskCell: riskCell,
    mark: mark,
    staleSymbol: function (sym) { return !!staleSet[sym]; },
    staleSector: function (sec) { return !!staleSectors[sec]; },
    staleNode: function (name) { return staleNodes.has(name); },
    // The list, for a caller that dims by symbol rather than asking per row.
    // A copy: Figure 1 keeps what it is handed.
    staleSymbols: function () { return staleSyms.slice(); }
  };
})();
