"""tools/measure_spreads.py (completion task 2.3, final review): the output is checked before any request, requests
are paced and a 429 or 5xx is retried with backoff, and the run writes a provenance sidecar. Offline: no request is
sent; the opener is a stand-in."""

from __future__ import annotations

import importlib.util
import io
import json
import urllib.error
import urllib.request

import pytest

from ohcamel_quant.config import REPO_ROOT


def _tool():
    spec = importlib.util.spec_from_file_location("measure_spreads", REPO_ROOT / "tools" / "measure_spreads.py")
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    return tool


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_an_unwritable_output_fails_before_any_request(tmp_path):
    tool = _tool()
    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(0o500)
    try:
        with pytest.raises(SystemExit, match="cannot be written"):
            tool.check_out(str(ro / "costs_measured.csv"))
        with pytest.raises(SystemExit, match="does not exist"):
            tool.check_out(str(tmp_path / "missing" / "x.csv"))
    finally:
        ro.chmod(0o700)
    tool.check_out(str(tmp_path / "ok.csv"))        # writable: no error, and nothing created
    assert not (tmp_path / "ok.csv").exists()


def test_a_429_and_a_503_are_retried_with_backoff_then_the_body_is_read():
    tool = _tool()
    sleeps: list[float] = []
    calls = []

    def opener(req, timeout):
        calls.append(req)
        if len(calls) == 1:
            raise urllib.error.HTTPError(req.full_url, 429, "slow down", {"Retry-After": "2"}, None)
        if len(calls) == 2:
            raise urllib.error.HTTPError(req.full_url, 503, "busy", {}, None)
        return _Resp(json.dumps({"quotes": []}).encode())

    t = iter(range(100))
    client = tool.Paced(0.0, sleep=sleeps.append, now=lambda: float(next(t)))
    assert client.get(urllib.request.Request("https://example.invalid"), opener=opener) == {"quotes": []}
    assert len(calls) == 3 and sleeps == [2.0, 2.0]   # Retry-After, then the doubled backoff


def test_a_400_is_not_retried():
    tool = _tool()

    def opener(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 400, "bad", {}, None)

    with pytest.raises(urllib.error.HTTPError):
        tool.Paced(0.0, sleep=lambda s: None).get(urllib.request.Request("https://example.invalid"), opener=opener)


def test_requests_are_paced_and_name_their_feed():
    tool = _tool()
    sleeps: list[float] = []
    clock = iter([0.0, 0.1, 0.35])
    client = tool.Paced(0.35, sleep=sleeps.append, now=lambda: next(clock))
    opener = lambda req, timeout: _Resp(b"{}")   # noqa: E731
    client.get(urllib.request.Request("https://example.invalid"), opener=opener)
    client.get(urllib.request.Request("https://example.invalid"), opener=opener)
    assert sleeps == [pytest.approx(0.25)]
    # 2026-11-27: the feed is a parameter (iex by default, sip for the consolidated quotes); any other is refused
    class _Seen:
        urls: list[str] = []

        def get(self, req):
            self.urls.append(req.full_url)
            return {"quotes": [{"bp": 10.0, "ap": 10.02}]}

    from datetime import UTC, datetime, timedelta

    t0 = datetime(2026, 11, 2, 15, 0, tzinfo=UTC)
    seen = _Seen()
    assert tool.quotes("USO", t0, t0 + timedelta(minutes=1), "k", "s", seen) == [(10.0, 10.02)]
    assert tool.quotes("USO", t0, t0 + timedelta(minutes=1), "k", "s", seen, feed="sip") == [(10.0, 10.02)]
    assert "feed=iex" in seen.urls[0] and "feed=sip" in seen.urls[1]
    with pytest.raises(ValueError, match="not one of"):
        tool.quotes("USO", t0, t0 + timedelta(minutes=1), "k", "s", seen, feed="otc")
