/* The shell: which page am I. Nothing else belongs here -- a file catted into
   every page costs every page, so it stays six lines.

   The comparison is exact on pathname, so "/" is current only on "/" and not
   on every page the way a prefix test would make it. */
function markCurrentNavLink() {
  var here = window.location.pathname;
  var links = document.querySelectorAll("nav.sitenav a");
  for (var i = 0; i < links.length; i++) {
    if (links[i].getAttribute("href") === here) links[i].setAttribute("aria-current", "page");
  }
}
markCurrentNavLink();
