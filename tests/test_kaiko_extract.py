"""Tests for app.kaiko HTML/JSON-LD extraction — the parse layer of the non-RSS
Kaiko Research source (sitemap → per-article JSON-LD + <main> paragraph scrape).

These pin the parse contract that feeds `feed_items`: double-decoding Yoast entities,
<main>-scoped body extraction (no sidebar/related-article bleed), breadcrumb category
gating, and the title-suffix / thin-content / noise-category rejections in build_item.
Network is stubbed via monkeypatch on the module's `_get`; everything else is pure.
"""
from __future__ import annotations

from app import kaiko


# ── _unescape (Yoast double-encodes) ─────────────────────────────────────────

def test_unescape_double_encoded_entity():
    assert kaiko._unescape("Bitcoin&amp;#8217;s rally") == "Bitcoin’s rally"


def test_unescape_single_pass_entity():
    assert kaiko._unescape("Tom &amp; Jerry") == "Tom & Jerry"


def test_unescape_none_and_strip():
    assert kaiko._unescape(None) == ""
    assert kaiko._unescape("  spaced  ") == "spaced"


# ── _sitemap_locs / discover_urls (network stubbed) ──────────────────────────

def test_sitemap_locs_parses_loc(monkeypatch):
    xml = ("<urlset><url><loc>https://www.kaiko.com/news/a</loc></url>"
           "<url><loc> https://www.kaiko.com/news/b </loc></url></urlset>")
    monkeypatch.setattr(kaiko, "_get", lambda *a, **k: xml)
    assert kaiko._sitemap_locs("x") == [
        "https://www.kaiko.com/news/a", "https://www.kaiko.com/news/b"]


def test_sitemap_locs_empty_when_no_xml(monkeypatch):
    monkeypatch.setattr(kaiko, "_get", lambda *a, **k: None)
    assert kaiko._sitemap_locs("x") == []


def test_discover_urls_filters_index_categories_deny_and_dedupes(monkeypatch):
    xml = (
        "<urlset>"
        "<url><loc>https://www.kaiko.com/news/real-article</loc></url>"
        "<url><loc>https://www.kaiko.com/news</loc></url>"                 # index path
        "<url><loc>https://www.kaiko.com/resources/categories/defi</loc></url>"  # categories
        "<url><loc>https://www.kaiko.com/reports/uniswap-v3-methodology</loc></url>"  # deny
        "<url><loc>https://www.kaiko.com/news/real-article</loc></url>"    # duplicate
        "</urlset>"
    )
    monkeypatch.setattr(kaiko, "_get", lambda *a, **k: xml)
    assert kaiko.discover_urls() == ["https://www.kaiko.com/news/real-article"]


# ── _ldjson ──────────────────────────────────────────────────────────────────

_LD_HTML = (
    '<script type="application/ld+json">{"@graph":['
    '{"@type":"NewsArticle","name":"Bitcoin Volatility","description":"A look",'
    '"articleBody":"Body text here","datePublished":"2026-06-20"},'
    '{"@type":"BreadcrumbList","itemListElement":['
    '{"name":"Resources"},{"name":"Market Data"},{"name":"Bitcoin Volatility"}]}'
    ']}</script>'
)


def test_ldjson_extracts_article_and_breadcrumbs():
    ld = kaiko._ldjson(_LD_HTML)
    assert ld["name"] == "Bitcoin Volatility"
    assert ld["articleBody"] == "Body text here"
    assert ld["datePublished"] == "2026-06-20"
    assert ld["categories"] == ["Resources", "Market Data", "Bitcoin Volatility"]


def test_ldjson_invalid_json_returns_empty():
    assert kaiko._ldjson('<script type="application/ld+json">{not json}</script>') == {}


def test_ldjson_no_article_type_returns_empty():
    html = '<script type="application/ld+json">{"@graph":[{"@type":"Organization","name":"Kaiko"}]}</script>'
    assert kaiko._ldjson(html) == {}


# ── _extract_body ────────────────────────────────────────────────────────────

def test_extract_body_joins_long_paragraphs_drops_short():
    html = "<main><p>" + "x" * 80 + "</p><p>short</p><p>" + "y" * 80 + "</p></main>"
    body = kaiko._extract_body(html)
    assert "x" * 80 in body
    assert "y" * 80 in body
    assert "short" not in body


def test_extract_body_scopes_to_main_excluding_sidebar():
    html = "<aside><p>" + "S" * 80 + "</p></aside><main><p>" + "M" * 80 + "</p></main>"
    body = kaiko._extract_body(html)
    assert body == "M" * 80
    assert "S" not in body


def test_extract_body_falls_back_without_main():
    html = "<p>" + "z" * 80 + "</p>"
    assert "z" * 80 in kaiko._extract_body(html)


def test_extract_body_skips_social_prompts():
    html = "<main><p>" + "Share this article on social media right now please now" + "</p></main>"
    assert kaiko._extract_body(html) == ""


def test_extract_body_respects_max_chars():
    html = "<main><p>" + "w" * 500 + "</p></main>"
    assert len(kaiko._extract_body(html, max_chars=100)) == 100


# ── _og ──────────────────────────────────────────────────────────────────────

def test_og_extracts_title_and_description():
    html = ('<meta property="og:title" content="The Title">'
            '<meta property="og:description" content="The Desc">')
    og = kaiko._og(html)
    assert og == {"title": "The Title", "description": "The Desc"}


# ── build_item ───────────────────────────────────────────────────────────────

def _article_html(name="Bitcoin Liquidity Deepens - Kaiko", crumbs=None,
                  article_body="Bitcoin market depth rose sharply in June as liquidity providers returned to the order books.",
                  description="Spreads tighten across major venues as market makers return."):
    crumbs = crumbs if crumbs is not None else ["Resources", "Market Data", "Bitcoin Liquidity Deepens"]
    crumb_json = ",".join('{"name":"%s"}' % c for c in crumbs)
    return (
        '<script type="application/ld+json">{"@graph":['
        '{"@type":"NewsArticle","name":"%s","description":"%s",'
        '"articleBody":"%s","datePublished":"2026-06-20"},'
        '{"@type":"BreadcrumbList","itemListElement":[%s]}'
        ']}</script>' % (name, description, article_body, crumb_json)
    )


def test_build_item_happy_path():
    item = kaiko.build_item("https://www.kaiko.com/resources/btc-liquidity", _article_html())
    assert item is not None
    assert item["title"] == "Bitcoin Liquidity Deepens"   # " - Kaiko" suffix stripped
    assert item["creator"] == "Kaiko"
    assert item["pub_date"] == "2026-06-20"
    assert item["categories"] == ["Market Data"]          # section + title crumbs dropped
    assert "market depth rose" in item["content"]


def test_build_item_rejects_noise_category():
    html = _article_html(crumbs=["Resources", "Use Cases", "Some Guide"])
    assert kaiko.build_item("https://www.kaiko.com/x", html) is None


def test_build_item_rejects_missing_title():
    html = _article_html(name="", description="", article_body="")
    assert kaiko.build_item("https://www.kaiko.com/x", html) is None


def test_build_item_rejects_thin_content():
    html = _article_html(name="BTC", description="", article_body="", crumbs=["Resources"])
    assert kaiko.build_item("https://www.kaiko.com/x", html) is None
