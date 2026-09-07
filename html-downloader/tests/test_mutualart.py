from __future__ import annotations

from pathlib import Path

from html_downloader.discover.mutualart import (
    DEFAULT_MUTUALART_SITEMAP,
    _looks_like_bot_challenge,
    extract_entity_urls,
    extract_letter_index_urls,
    fetch_mutualart_sitemap_entries,
)
from html_downloader.discover.sitemap import known_keys_from_sources

SITEMAP_ROOT_HTML = """
<html><body>
<a href="/ArtistsIndex/a/">A</a>
<a href="/ArtistsIndex/b/">B</a>
<a href="/SiteMapOrganizations/A/">A</a>
<a href="/SiteMapOrganizations/B/">B</a>
<a href="/SiteMapEvents/A/">A</a>
<a href="/buy-art">Skip</a>
<a href="/Artist/Banksy/993F064E6ED15A35">Trending</a>
</body></html>
"""

ARTISTS_LETTER_HTML = """
<html><body>
<a href="/Artist/Banksy/993F064E6ED15A35">Banksy</a>
<a href="/Artist/Andy-Warhol/85A84FA828A34B78/Graphs">Nested skip</a>
<a href="/Artist/Banksy/993F064E6ED15A35">Duplicate</a>
</body></html>
"""

ORGS_LETTER_HTML = """
<html><body>
<a href="/Organization/a-fantastic-find/02081EA1D4EA7618">Org</a>
</body></html>
"""

EVENTS_LETTER_HTML = """
<html><body>
<a href="/Exhibition/-A-Body-Of-Art/1EE8BE929E8657E1">Exhibition</a>
<a href="/Auction/--Art-Sale/7D66190318370CA0">Auction</a>
</body></html>
"""

CAPTCHA_HTML = b"""<!DOCTYPE html><html><body>
<input type="hidden" name="code" value="582" />
<div class="g-recaptcha" data-sitekey="x"></div>
Help us out by completing the CAPTCHA below
</body></html>"""


def test_extract_letter_index_urls() -> None:
    urls = extract_letter_index_urls(SITEMAP_ROOT_HTML)
    assert "https://www.mutualart.com/ArtistsIndex/a/" in urls
    assert "https://www.mutualart.com/ArtistsIndex/b/" in urls
    assert "https://www.mutualart.com/SiteMapOrganizations/A/" in urls
    assert "https://www.mutualart.com/SiteMapEvents/A/" in urls
    assert all("/Artist/" not in url for url in urls)


def test_extract_entity_urls_from_letter_pages() -> None:
    artists = extract_entity_urls(
        ARTISTS_LETTER_HTML,
        "https://www.mutualart.com/ArtistsIndex/a/",
    )
    assert artists == ["https://www.mutualart.com/Artist/Banksy/993F064E6ED15A35"]

    orgs = extract_entity_urls(
        ORGS_LETTER_HTML,
        "https://www.mutualart.com/SiteMapOrganizations/A/",
    )
    assert orgs == [
        "https://www.mutualart.com/Organization/a-fantastic-find/02081EA1D4EA7618"
    ]

    events = extract_entity_urls(
        EVENTS_LETTER_HTML,
        "https://www.mutualart.com/SiteMapEvents/A/",
    )
    assert set(events) == {
        "https://www.mutualart.com/Exhibition/-A-Body-Of-Art/1EE8BE929E8657E1",
        "https://www.mutualart.com/Auction/--Art-Sale/7D66190318370CA0",
    }


def test_looks_like_bot_challenge_detects_mutualart_captcha() -> None:
    assert _looks_like_bot_challenge(CAPTCHA_HTML) is True
    assert _looks_like_bot_challenge(b"<html><body><a href='/Artist/x/ABC'>ok</a></body></html>") is False


def test_fetch_mutualart_sitemap_entries_with_injected_fetch() -> None:
    fixtures = {
        DEFAULT_MUTUALART_SITEMAP: SITEMAP_ROOT_HTML,
        "https://www.mutualart.com/ArtistsIndex/a/": ARTISTS_LETTER_HTML,
        "https://www.mutualart.com/ArtistsIndex/b/": "<html></html>",
        "https://www.mutualart.com/SiteMapOrganizations/A/": ORGS_LETTER_HTML,
        "https://www.mutualart.com/SiteMapOrganizations/B/": "<html></html>",
        "https://www.mutualart.com/SiteMapEvents/A/": EVENTS_LETTER_HTML,
    }

    def fake_fetch(url: str) -> str:
        return fixtures[url]

    entries = fetch_mutualart_sitemap_entries(fetch_html=fake_fetch, concurrency=2)
    by_key = {(e.entity_type, e.entity_id): e for e in entries}
    assert ("artist", "993F064E6ED15A35") in by_key
    assert ("organization", "02081EA1D4EA7618") in by_key
    assert ("exhibition", "1EE8BE929E8657E1") in by_key
    assert ("auction", "7D66190318370CA0") in by_key
    assert len(by_key) == 4


def test_fetch_mutualart_continues_when_artist_letter_fails() -> None:
    fixtures = {
        DEFAULT_MUTUALART_SITEMAP: (
            '<html><body>'
            '<a href="/ArtistsIndex/a/">A</a>'
            '<a href="/SiteMapOrganizations/A/">A</a>'
            "</body></html>"
        ),
        "https://www.mutualart.com/SiteMapOrganizations/A/": ORGS_LETTER_HTML,
    }

    def fake_fetch(url: str) -> str:
        if "ArtistsIndex" in url:
            raise RuntimeError("bot challenge instead of html")
        return fixtures[url]

    entries = fetch_mutualart_sitemap_entries(fetch_html=fake_fetch, concurrency=2)
    by_key = {(e.entity_type, e.entity_id): e for e in entries}
    assert ("organization", "02081EA1D4EA7618") in by_key
    assert ("artist", "993F064E6ED15A35") not in by_key


def test_known_keys_from_sources_mutualart(tmp_path: Path) -> None:
    path = tmp_path / "urls.txt"
    path.write_text(
        "https://www.mutualart.com/Artist/Banksy/993F064E6ED15A35\n"
        "https://www.mutualart.com/Organization/a-fantastic-find/02081EA1D4EA7618\n",
        encoding="utf-8",
    )
    keys = known_keys_from_sources(known_paths=[path], source="mutualart")
    assert keys == {
        ("artist", "993F064E6ED15A35"),
        ("organization", "02081EA1D4EA7618"),
    }
