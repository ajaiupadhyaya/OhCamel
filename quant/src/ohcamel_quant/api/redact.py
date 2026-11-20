"""No commit SHA leaves the public app (the owner's ruling, 2026-11-20).

The Quant app is what the public host serves: deploy/Caddyfile proxies the public block to ohcamel-quant and
nothing else, and the password-gated live host proxies to the OCaml engine and the burst door, never to this app.
So the rule is enforced here, once, on the way out: ``RedactSha`` is an ASGI middleware around every route the app
mounts, and every JSON response passes ``redact_payload`` before it is sent (and before it is compressed). A route
added later cannot leak a SHA, because it never sees this step.

What is removed, wherever it sits in the payload:

* every value under a key named ``code_sha`` or ``git_sha`` (the key goes too);
* every string that is a full 40-hex SHA-1, under any key (the machine graph's ``code``, a provenance entry);
* every string that is, or every hex run inside a sentence that equals or prefixes (7 characters or more), a SHA
  known for this payload: the ones just found, and this image's ``OHCAMEL_GIT_SHA``. A whole-value match removes
  the key (a list keeps its length, with ``null``); a run inside text becomes ``·``.

A candidate must hold at least one letter a-f: a run of digits is a number (a CIK, a date, a count), never taken
for a SHA, whatever it prefixes.

The redaction knows a SHA only if the payload carries it, so the convention is: a route that serves text derived
from a manifest (notes, provenance) passes the manifest's ``code_sha`` under that key -- the artifacts router's
``_head`` does, on every artifact route -- and the key never leaves; it only teaches the redaction what to scrub. A
SHA the app reads from somewhere else (the committed perf recordings' ``sha``) is dropped at its route.
quant/tests/test_public_sha.py walks every public GET route and asks git whether any hex run is a commit.

NOT REDACTED: ``text/event-stream``. The one stream, ``/api/jobs/events``, sends a fixed set of fields per event
(``type``, ``id``, ``state``, ``progress``, ``message``) from job_events; it must never carry a SHA, and the job
queue writes none there (pinned by quant/tests/test_public_sha.py). Any stream added later must hold to the same
rule, because this middleware does not buffer streams.

What is not touched: 64-hex data and params hashes (they name data, not code), ULIDs, a CIK or a date written as
digits, and anything else that merely looks like hex without matching a known SHA. The stored manifests are never
rewritten (tools/regenerate.py reads ``code_sha`` from the files), and the gated host is unchanged: the burst
door, a separate app, still returns the deployment's SHA to the owner's tool.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable
from typing import Any

SHA_KEYS = frozenset({"code_sha", "git_sha"})
_FULL = re.compile(r"^[0-9a-fA-F]{40}$")
_HEXVAL = re.compile(r"^[0-9a-fA-F]{7,40}$")
_TOKEN = re.compile(r"(?<![0-9a-fA-F])[0-9a-fA-F]{7,40}(?![0-9a-fA-F])")
_FULL_BYTES = re.compile(rb"(?<![0-9a-fA-F])[0-9a-fA-F]{40}(?![0-9a-fA-F])")
_DROP = object()


def _env_sha() -> str | None:
    s = (os.environ.get("OHCAMEL_GIT_SHA") or "").strip().lower()
    return s if _HEXVAL.match(s) else None


def _collect(obj: Any, out: set[str]) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in SHA_KEYS and isinstance(v, str) and _HEXVAL.match(v):
                out.add(v.lower())
            _collect(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _collect(v, out)
    elif isinstance(obj, str) and _FULL.match(obj) and _LETTER.search(obj):
        out.add(obj.lower())


_LETTER = re.compile(r"[a-fA-F]")


def _is_known(token: str, known: set[str]) -> bool:
    t = token.lower()
    return bool(_LETTER.search(t)) and any(k.startswith(t) for k in known)


def _clean(obj: Any, known: set[str]) -> Any:
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in SHA_KEYS:
                continue
            c = _clean(v, known)
            if c is not _DROP:
                out[k] = c
        return out
    if isinstance(obj, list):
        return [None if (c := _clean(v, known)) is _DROP else c for v in obj]
    if isinstance(obj, str):
        if (_FULL.match(obj) and _LETTER.search(obj)) or (_HEXVAL.match(obj) and _is_known(obj, known)):
            return _DROP
        if known and _TOKEN.search(obj):
            return _TOKEN.sub(lambda m: "·" if _is_known(m.group(0), known) else m.group(0), obj)
    return obj


def redact_payload(obj: Any, *, extra: Iterable[str] = ()) -> Any:
    """``obj`` with every commit SHA removed (module docstring). ``extra`` adds SHAs to the known set."""
    known = {e.lower() for e in extra if isinstance(e, str) and _HEXVAL.match(e)}
    env = _env_sha()
    if env:
        known.add(env)
    _collect(obj, known)
    out = _clean(obj, known)
    return None if out is _DROP else out


def _may_carry(body: bytes) -> bool:
    """Cheap test first: a body with no SHA key, no 40-hex run and not this image's SHA cannot carry one the
    payload walk would find, so it is sent as it is."""
    if b'"code_sha"' in body or b'"git_sha"' in body or _FULL_BYTES.search(body):
        return True
    env = _env_sha()
    return bool(env) and env[:7].encode() in body.lower()


class RedactSha:
    """ASGI middleware: every ``application/json`` response of the public app passes ``redact_payload``."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        start: dict[str, Any] | None = None
        chunks: list[bytes] = []

        async def wrapped(message: dict[str, Any]) -> None:
            nonlocal start
            if message["type"] == "http.response.start":
                headers = {k.lower(): v for k, v in message.get("headers", [])}
                if headers.get(b"content-type", b"").startswith(b"application/json") \
                        and b"content-encoding" not in headers:
                    start = message
                    return
                await send(message)
                return
            if message["type"] == "http.response.body" and start is not None:
                chunks.append(message.get("body", b""))
                if message.get("more_body"):
                    return
                body = b"".join(chunks)
                if _may_carry(body):
                    try:
                        body = json.dumps(redact_payload(json.loads(body)), ensure_ascii=False,
                                          separators=(",", ":")).encode()
                    except ValueError:
                        pass
                headers = [(k, v) for k, v in start.get("headers", []) if k.lower() != b"content-length"]
                headers.append((b"content-length", str(len(body)).encode()))
                await send({**start, "headers": headers})
                await send({"type": "http.response.body", "body": body})
                return
            await send(message)

        await self.app(scope, receive, wrapped)
