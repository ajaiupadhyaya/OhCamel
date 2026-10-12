#!/usr/bin/env bash
#
# Every file the engine's build embeds reaches its build context.
#
#   deploy/test/engine_context_test.sh
#
# deploy/Dockerfile builds the engine from the repository root with the root
# .dockerignore, then `COPY . .` and `dune build --profile release
# bin/main.exe`. The dune files in the context reach outside their own
# directory for files they embed at build time -- web/*, docs/crisis/*.csv,
# docs/CHARTER.md (lib/dune), EXP-A01's manifests (desk/dune) -- and a file
# the ignore list denies is simply absent in the builder: `dune build` stops
# with "No rule found for <path>" and the image never builds. A local build
# never notices, because the checkout has the file. That is how the Stage 0
# merge broke the image job (run 38183520647): lib/dune began embedding
# docs/CHARTER.md while .dockerignore still denied all of docs/ but the crisis
# CSVs.
#
# What it does: reads every tracked `dune` file the context itself carries,
# takes each `../<path>` in a (deps ...), (cat ...) or (glob_files ...) --
# relative to that dune file's directory, as dune resolves it -- expands
# globs against `git ls-files`, and evaluates each resulting file against
# .dockerignore with Docker's own rules (patterns in order, the last match
# wins, `!` re-includes, `*` stops at '/', `**` crosses it, and a pattern
# that matches a directory matches everything under it). Any embedded file
# the context would not carry is a FAIL naming it, as is a glob that expands
# to nothing.
#
# Needs git and python3; no Docker. Picked up by ci.yml's deploy/test/*.sh
# step with no change there.

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."

git ls-files >/dev/null 2>&1 || { echo "engine_context_test: not a git checkout"; exit 1; }

listing=$(mktemp "${TMPDIR:-/tmp}/engine-context-test.XXXXXX")
trap 'rm -f "$listing"' EXIT
git ls-files >"$listing"

python3 - .dockerignore "$listing" <<'PY'
import posixpath, re, sys

ignore_path = sys.argv[1]
tracked = [l.rstrip("\n") for l in open(sys.argv[2]) if l.strip()]
tracked_set = set(tracked)

def compile_pattern(p):
    p = p.strip()
    neg = p.startswith("!")
    if neg:
        p = p[1:].strip()
    p = posixpath.normpath(p.lstrip("/")).rstrip("/")
    out, i = "", 0
    while i < len(p):
        c = p[i]
        if p.startswith("**", i):
            out += ".*"; i += 2
            if i < len(p) and p[i] == "/":
                i += 1
                out = out[:-2] + "(?:.*/)?"
            continue
        out += {"*": "[^/]*", "?": "[^/]"}.get(c, re.escape(c))
        i += 1
    return neg, re.compile("^" + out + "$"), p

rules = []
for line in open(ignore_path):
    s = line.strip()
    if not s or s.startswith("#"):
        continue
    rules.append(compile_pattern(s))

def ignored(path):
    # A file is matched by a pattern that matches it or any parent directory.
    parts = path.split("/")
    prefixes = ["/".join(parts[:k]) for k in range(1, len(parts) + 1)]
    state = False
    for neg, rx, _ in rules:
        if any(rx.match(pp) for pp in prefixes):
            state = not neg
    return state

def glob_rx(g):
    out = ""
    for c in g:
        out += {"*": "[^/]*", "?": "[^/]"}.get(c, re.escape(c))
    return re.compile("^" + out + "$")

passed = failed = 0
def ok(msg):
    global passed; passed += 1; print("  ok    " + msg)
def no(msg, why):
    global failed; failed += 1; print("  FAIL  %s -- %s" % (msg, why))

dune_files = [f for f in tracked if posixpath.basename(f) == "dune" and not ignored(f)]
if not dune_files:
    no(".dockerignore", "no dune file reaches the context at all")
token = re.compile(r"(?:^|[\s(])(\.\./[^\s()]+)")
form = re.compile(r"\((deps|cat|glob_files)\b")
for df in sorted(dune_files):
    base = posixpath.dirname(df)
    text = open(df).read()
    # Every ../ path inside a deps / cat / glob_files form (forms nest, so a
    # path is taken when an enclosing open form is one of the three).
    refs, stack, i = [], [], 0
    while i < len(text):
        c = text[i]
        if c == ";":
            j = text.find("\n", i); i = len(text) if j < 0 else j; continue
        if c == "(":
            m = form.match(text, i)
            stack.append(m.group(1) if m else None)
        elif c == ")":
            if stack: stack.pop()
        elif text.startswith("../", i) and (i == 0 or text[i - 1] in " \t\n(") and any(stack):
            m = re.match(r"\.\./[^\s()]+", text[i:])
            refs.append((m.group(0), next(s for s in reversed(stack) if s)))
            i += len(m.group(0)); continue
        i += 1
    for ref, kind in sorted(set(refs)):
        target = posixpath.normpath(posixpath.join(base, ref))
        if any(ch in target for ch in "*?["):
            rx = glob_rx(target)
            files = [f for f in tracked if rx.match(f)]
            if not files:
                no("%s: %s" % (df, ref), "the glob matches no tracked file")
                continue
        else:
            files = [target]
            if target not in tracked_set:
                no("%s: %s" % (df, ref), "%s is not a tracked file" % target)
                continue
        bad = [f for f in files if ignored(f)]
        if bad:
            no("%s: (%s %s)" % (df, kind, ref),
               ".dockerignore denies %s -- the builder's `COPY . .` will not have it and dune stops with "
               "'No rule found'; add a `!` line for it" % ", ".join(bad))
        else:
            ok("%s: (%s %s) -> %d file(s) in the context" % (df, kind, ref, len(files)))

print("\n  %d passed, %d failed\n" % (passed, failed))
sys.exit(1 if failed or not passed else 0)
PY
