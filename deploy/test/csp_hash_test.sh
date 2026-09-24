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

# The policy's other load-bearing promises, so an edit cannot quietly widen it.
case "$csp" in
*"default-src 'self'"*) ok "default-src 'self'" ;;
*) no "default-src" "the SPA policy must be default-src 'self'" ;;
esac
case "$csp" in
*"'unsafe-inline'; style-src"* | *"script-src 'self' 'unsafe-inline'"*) no "script-src" "'unsafe-inline' in script-src defeats the hash" ;;
*) ok "script-src carries no 'unsafe-inline'" ;;
esac
case "$csp" in
*"frame-ancestors 'none'"*) ok "frame-ancestors 'none'" ;;
*) no "frame-ancestors" "missing frame-ancestors 'none'" ;;
esac

printf '\n  %d passed, %d failed\n\n' "$pass" "$fail"
[ "$fail" -eq 0 ] || exit 1
