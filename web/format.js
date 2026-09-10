// web/format.js -- the three formatters every script on the page shares.
// The bodies are dashboard.js's, copied verbatim; dashboard.js keeps its own
// private copies until the reconcile pass removes them.
(function () {
  "use strict";
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
  window.OhCamelFormat = { money: money, pct: pct, el: el };
})();
