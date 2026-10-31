"""What a page says before its JavaScript runs (api/seo.py, app.py's fallback).

The shell is a small stand-in for quant/web/index.html with the same markers, so
these tests need no built bundle; the one test that reads the real index.html
pins the markers there.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ohcamel_quant.api import app as app_mod
from ohcamel_quant.api import seo

REPO = Path(__file__).resolve().parents[2]
WEB = REPO / "quant" / "web"

SHELL = """<!doctype html><html><head>
<!--seo:head--><title>x</title><!--/seo:head-->
<script>theme()</script>
</head><body><div id="root"><!--seo:body--><!--/seo:body--></div>
<script type="module" src="/assets/main.js"></script></body></html>"""


@pytest.fixture
def client(tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text(SHELL)
    (tmp_path / "robots.txt").write_text("User-agent: *\n")
    (tmp_path / "assets").mkdir()
    monkeypatch.setattr(app_mod, "WEB_DIST", tmp_path)
    return TestClient(app_mod.create_app())


def _text(html: str) -> str:
    body = html.split('<div id="root">', 1)[1]
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body))


def test_every_public_route_is_in_the_table():
    src = (WEB / "src" / "lib" / "routes.ts").read_text()
    registered = set(re.findall(r'\{ path: "([^"]+)"', src))
    assert registered == {r.path for r in seo.ROUTES}


def test_the_real_shell_carries_the_markers():
    html = (WEB / "index.html").read_text()
    for marker in ("<!--seo:head-->", "<!--/seo:head-->", "<!--seo:body-->", "<!--/seo:body-->"):
        assert html.count(marker) == 1
    assert '<div id="root"><!--seo:body-->' in html


def test_descriptions_fit_a_preview_and_state_no_figure():
    for r in seo.ROUTES:
        assert 50 <= len(r.description) <= 170, r.path
        assert not re.search(r"\d", r.description.replace("13F", "")), f"{r.path}: a figure belongs to the page"


def test_front_page_gives_a_crawler_text_and_a_preview(client):
    r = client.get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    html = r.text
    assert "<title>The machine · OhCamel Quant</title>" in html
    for tag in ('name="description"', 'property="og:title"', 'property="og:description"',
                'property="og:image"', 'property="og:type" content="website"',
                'property="og:url"', 'name="twitter:card" content="summary_large_image"',
                'rel="canonical"'):
        assert tag in html, tag
    assert len(_text(html)) > 400
    assert "Incremental" in _text(html)


def test_each_page_has_its_own_title_description_and_canonical(client):
    risk = client.get("/risk").text
    macro = client.get("/macro").text
    assert "<title>Risk · OhCamel Quant</title>" in risk
    assert "<title>Rates &amp; Macro · OhCamel Quant</title>" in macro
    assert re.search(r'rel="canonical" href="[^"]+/risk"', risk)
    assert seo.ROUTES_BY_PATH["/risk"].description in risk
    assert seo.ROUTES_BY_PATH["/risk"].description not in macro


def test_a_page_links_to_the_rest_of_the_site(client):
    html = client.get("/risk").text
    for r in seo.ROUTES:
        assert f'href="{r.link}"' in html, r.path


def test_parameterised_pages_name_their_ticker(client):
    html = client.get("/ticker/spy").text
    assert client.get("/ticker/spy").status_code == 200
    assert "<title>SPY · Ticker · OhCamel Quant</title>" in html
    assert re.search(r'rel="canonical" href="[^"]+/ticker/SPY"', html)
    assert client.get("/company/BRK.B").status_code == 200
    assert client.get("/company").status_code == 200
    assert client.get("/ticker").status_code == 200


def test_a_ticker_cannot_write_markup(client):
    r = client.get("/ticker/%3Cscript%3Ealert(1)%3C%2Fscript%3E")
    assert r.status_code == 404
    assert "alert(1)" not in r.text


def test_an_unknown_path_is_a_real_404_that_still_boots_the_app(client):
    r = client.get("/no/such/page")
    assert r.status_code == 404
    assert '<div id="root">' in r.text
    assert "/assets/main.js" in r.text
    assert 'name="robots" content="noindex"' in r.text
    assert 'rel="canonical"' not in r.text


def test_a_trailing_slash_and_a_query_keep_the_page(client):
    assert client.get("/risk/").status_code == 200
    html = client.get("/risk?alpha=0.99").text
    assert re.search(r'rel="canonical" href="[^"]+/risk"', html)


def test_a_static_file_is_still_served_as_itself(client):
    r = client.get("/robots.txt")
    assert r.status_code == 200
    assert r.text == "User-agent: *\n"


def test_the_real_robots_file_points_at_the_sitemap_and_off_the_api():
    text = (WEB / "public" / "robots.txt").read_text()
    assert "Disallow: /api/" in text
    assert re.search(r"^Sitemap: https://\S+/sitemap\.xml$", text, re.M)


def test_the_sitemap_lists_every_page_and_no_parameter(client):
    r = client.get("/sitemap.xml")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/xml")
    locs = re.findall(r"<loc>([^<]+)</loc>", r.text)
    assert len(locs) == len(seo.ROUTES)
    assert not any(":" in loc.split("//", 1)[1] for loc in locs)


def test_the_api_still_answers_json_404s(client):
    r = client.get("/api/nope")
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/json")


def test_the_preview_image_is_the_size_a_card_expects():
    png = (WEB / "public" / "og.png").read_bytes()
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert int.from_bytes(png[16:20], "big") == seo.PREVIEW_WIDTH
    assert int.from_bytes(png[20:24], "big") == seo.PREVIEW_HEIGHT
