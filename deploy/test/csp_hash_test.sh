#!/usr/bin/env bash
#
# The public site's Content-Security-Policy (deploy/Caddyfile.snippets, the
# ohcamel_quant snippet) allows inline script ONLY by hash. quant/web/index.html
# carries a small inline script (the pre-paint theme snippet), and Vite copies
# it into dist/index.html byte for byte -- so the hash can be checked here,
# from the source, with no npm and no build, in CI's lint job.
#
#   deploy/test/csp_hash_test.sh                               # the source
#   deploy/test/csp_hash_test.sh quant/web/dist/index.html     # a built page (CI's quant job)
#
# Fails, printing the hash to paste, if any executable inline <script> in
# quant/web/index.html is not allowed by the CSP's script-src. Editing that
# snippet by even one space changes its hash; without this test the first
# sign would be a site whose theme toggle silently stops working, visible
# only in a browser console. deploy/smoke.sh repeats the check against the
# served page and the served header after every deploy.

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."

pass=0
fail=0
ok() { printf '  ok    %s\n' "$1"; pass=$((pass + 1)); }
no() { printf '  FAIL  %s -- %s\n' "$1" "$2"; fail=$((fail + 1)); }

html="${1:-quant/web/index.html}"
snippets=deploy/Caddyfile.snippets

if [ ! -f "$html" ]; then
	no "$html" "missing -- the web app moved; update this test"
	printf '\n  %d passed, %d failed\n\n' "$pass" "$fail"
	exit 1
fi

csp=$(grep -E '^\s*header @quant_spa Content-Security-Policy ' "$snippets" | head -1)
if [ -z "$csp" ]; then
	no "$snippets" "no 'header @quant_spa Content-Security-Policy' line found"
	printf '\n  %d passed, %d failed\n\n' "$pass" "$fail"
	exit 1
fi

# One line per executable inline script: its sha256, base64, as CSP spells it.
hashes=$(python3 - "$html" <<'PY'
import base64, hashlib, re, sys
body = open(sys.argv[1], encoding="utf-8").read()
for m in re.finditer(r"<script\b([^>]*)>(.*?)</script>", body, re.S):
    attrs, code = m.group(1), m.group(2)
    if "src=" in attrs or re.search(r'type="?application/(ld\+)?json', attrs):
        continue
    print("sha256-" + base64.b64encode(hashlib.sha256(code.encode("utf-8")).digest()).decode())
PY
)

if [ -z "$hashes" ]; then
	ok "no inline scripts in $html (the hash in the CSP can be dropped)"
fi
while IFS= read -r h; do
	[ -n "$h" ] || continue
	case "$csp" in
	*"'$h'"*) ok "inline script $h is allowed by the CSP" ;;
	*) no "inline script in $html" "not allowed by the CSP; add '$h' to script-src in $snippets (and drop the stale hash)" ;;
	esac
done <<<"$hashes"

# One directive's sources ("" when absent).
directive() { printf '%s' "$csp" | tr ';' '\n' | sed -n "s/^ *$1 //p" | head -1; }

# The policy's other load-bearing promises, so an edit cannot quietly widen it.
case "$csp" in
*"default-src 'self'"*) ok "default-src 'self'" ;;
*) no "default-src" "the SPA policy must be default-src 'self'" ;;
esac
case "$(directive script-src)" in
"") no "script-src" "missing" ;;
*"'unsafe-inline'"*) no "script-src" "'unsafe-inline' in script-src defeats the hash" ;;
*) ok "script-src carries no 'unsafe-inline'" ;;
esac
case "$csp" in
*"frame-ancestors 'none'"*) ok "frame-ancestors 'none'" ;;
*) no "frame-ancestors" "missing frame-ancestors 'none'" ;;
esac

# Harden H4: exactly what the bundle needs, measured in a browser against the
# built SPA (every route) and a Plotly surface: uPlot styles through the CSSOM
# (no inline style needed), KaTeX's markup carries style="" attributes
# (style-src-attr), Plotly's rules go through insertRule into one EMPTY <style>
# (style-src-elem allows only the empty string's hash), Plotly 4's surface
# compiles nothing (no 'unsafe-eval'), nothing spawns a worker, and the SSE
# stream is same-origin (connect-src 'self').
EMPTY_STYLE="'sha256-47DEQpj8HBSa+/TImW+5JCeuQeRkm5NMpJWZG3hSuFU='"
case "$csp" in
*"'unsafe-eval'"*) no "script-src" "'unsafe-eval' is not needed by the bundle (Plotly 4 surface runs without it)" ;;
*) ok "no 'unsafe-eval'" ;;
esac
[ "$(directive style-src-elem)" = "'self' $EMPTY_STYLE" ] && ok "style-src-elem 'self' + the empty <style> hash only" ||
	no "style-src-elem" "want 'self' $EMPTY_STYLE, got '$(directive style-src-elem)'"
[ "$(directive style-src-attr)" = "'unsafe-inline'" ] && ok "style-src-attr 'unsafe-inline' (KaTeX markup)" ||
	no "style-src-attr" "want 'unsafe-inline', got '$(directive style-src-attr)'"
[ "$(directive connect-src)" = "'self'" ] && ok "connect-src 'self' (API + SSE)" ||
	no "connect-src" "want 'self', got '$(directive connect-src)'"
[ "$(directive worker-src)" = "'none'" ] && ok "worker-src 'none'" || no "worker-src" "want 'none', got '$(directive worker-src)'"
[ "$(directive img-src)" = "'self' data:" ] && ok "img-src 'self' data:" || no "img-src" "want 'self' data:, got '$(directive img-src)'"
[ "$(directive object-src)" = "'none'" ] && ok "object-src 'none'" || no "object-src" "want 'none'"

# The quant site's other security headers: nosniff, Referrer-Policy and
# X-Frame-Options come from ohcamel_common, which the public host imports
# before ohcamel_quant; HSTS is set on the production host only.
common=$(sed -n '/^(ohcamel_common)/,/^}/p' "$snippets")
for h in "X-Content-Type-Options nosniff" "X-Frame-Options DENY" "Referrer-Policy no-referrer"; do
	case "$common" in *"$h"*) ok "ohcamel_common: $h" ;; *) no "ohcamel_common" "missing '$h'" ;; esac
done
public=$(sed -n '/^{\$OHCAMEL_DEMO_HOST} {/,/^}/p' deploy/Caddyfile)
case "$public" in
*"import ohcamel_common"*"import ohcamel_quant"*) ok "public host imports ohcamel_common and ohcamel_quant" ;;
*) no "deploy/Caddyfile" "the public host must import ohcamel_common and ohcamel_quant" ;;
esac
case "$public" in
*'Strict-Transport-Security "max-age=31536000'*) ok "public host sends HSTS (max-age one year)" ;;
*) no "deploy/Caddyfile" "the public host must send Strict-Transport-Security" ;;
esac

printf '\n  %d passed, %d failed\n\n' "$pass" "$fail"
[ "$fail" -eq 0 ] || exit 1
