from __future__ import annotations

from unittest.mock import MagicMock, patch

from html_downloader.discover.sitemap import (
    DEFAULT_ARTMAJEUR_INDEX,
    DEFAULT_SAATCHI_INDEX,
    SitemapEntry,
    diff_sitemap_entries,
    fetch_artmajeur_sitemap_entries,
    fetch_artsy_sitemap_entries,
    fetch_saatchi_sitemap_entries,
    filter_saatchi_child_sitemaps,
    is_sitemap_index,
    known_keys_from_sources,
    known_saatchi_keys_from_paths,
    parse_child_sitemap_locs,
    parse_url_entries,
)


INDEX_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://www.artsper.com/sitemap.artist_0.xml</loc></sitemap>
  <sitemap><loc>https://www.artsper.com/sitemap.artwork_category_6_0.xml</loc></sitemap>
</sitemapindex>"""

URLSET_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://www.artsper.com/us/contemporary-artists/france/128876/nathalie-cubero</loc>
    <lastmod>2026-06-08T04:00:50+02:00</lastmod>
  </url>
  <url>
    <loc>https://www.artsper.com/us/contemporary-artworks/painting/2361374/un-petit-bout</loc>
    <lastmod>2026-06-09T04:00:50+02:00</lastmod>
  </url>
</urlset>"""

ARTSY_ARTISTS_INDEX = b"""<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://www.artsy.net/sitemap-artists-0.xml</loc></sitemap>
</sitemapindex>"""

ARTSY_ARTWORKS_INDEX = b"""<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://www.artsy.net/sitemap-artworks-nested.xml</loc></sitemap>
</sitemapindex>"""

ARTSY_NESTED_INDEX = b"""<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://www.artsy.net/sitemap-artworks-0.xml</loc></sitemap>
</sitemapindex>"""

ARTSY_ARTISTS_URLSET = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://www.artsy.net/artist/pablo-picasso</loc>
    <lastmod>2026-06-01T00:00:00Z</lastmod>
  </url>
  <url>
    <loc>https://www.artsy.net/artist/pablo-picasso/auction-results</loc>
    <lastmod>2026-06-01T00:00:00Z</lastmod>
  </url>
</urlset>"""

ARTSY_ARTWORKS_URLSET = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://www.artsy.net/artwork/william-michael-harnett-the-old-violin</loc>
    <lastmod>2026-06-02T00:00:00Z</lastmod>
  </url>
  <url>
    <loc>https://www.artsy.net/collect</loc>
    <lastmod>2026-06-02T00:00:00Z</lastmod>
  </url>
</urlset>"""

SAATCHI_INDEX_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://www.saatchiart.com/sitemap-artworks-1.xml</loc></sitemap>
  <sitemap><loc>https://www.saatchiart.com/sitemap-profiles-1.xml</loc></sitemap>
  <sitemap><loc>https://www.saatchiart.com/sitemap-plps-1.xml</loc></sitemap>
</sitemapindex>"""

SAATCHI_ARTWORKS_URLSET = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://www.saatchiart.com/art/Painting-Test/735695/9336593/view</loc>
    <lastmod>2026-06-01T00:00:00+00:00</lastmod>
  </url>
</urlset>"""

SAATCHI_PROFILES_URLSET = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://www.saatchiart.com/radeksmach</loc>
    <lastmod>2026-06-02T00:00:00+00:00</lastmod>
  </url>
</urlset>"""

ARTMAJEUR_INDEX_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://www.artmajeur.com/sitemap-members-0.xml</loc></sitemap>
  <sitemap><loc>https://www.artmajeur.com/sitemap-artworks-0.xml</loc></sitemap>
</sitemapindex>"""

ARTMAJEUR_MEMBERS_URLSET = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://www.artmajeur.com/jane-doe</loc>
    <lastmod>2026-06-01T00:00:00Z</lastmod>
  </url>
</urlset>"""

ARTMAJEUR_MALFORMED_URLSET = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://www.artmajeur.com/bad-user/artworks/99999/title&broken</loc>
    <lastmod>2026-06-01T00:00:00Z</lastmod>
  </url>
</urlset>"""

ARTMAJEUR_ARTWORKS_URLSET = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://www.artmajeur.com/jane-doe/artworks/12345/title</loc>
    <lastmod>2026-06-02T00:00:00Z</lastmod>
  </url>
</urlset>"""


def test_parse_child_sitemap_locs() -> None:
    locs = parse_child_sitemap_locs(INDEX_XML)
    assert len(locs) == 2
    assert "artist_0.xml" in locs[0]


def test_parse_url_entries() -> None:
    entries = parse_url_entries(URLSET_XML)
    assert len(entries) == 2
    assert entries[0][1] == "2026-06-08T04:00:50+02:00"


def test_is_sitemap_index() -> None:
    assert is_sitemap_index(INDEX_XML) is True
    assert is_sitemap_index(URLSET_XML) is False


def test_diff_sitemap_new_and_updated() -> None:
    entries = [
        SitemapEntry(
            url="https://www.artsper.com/us/contemporary-artists/france/128876/nathalie-cubero",
            lastmod="2026-06-08T04:00:50+02:00",
            entity_type="artist",
            entity_id="128876",
        ),
        SitemapEntry(
            url="https://www.artsper.com/us/contemporary-artworks/painting/2361374/un-petit-bout",
            lastmod="2026-06-09T04:00:50+02:00",
            entity_type="artwork",
            entity_id="2361374",
        ),
    ]
    known = {("artist", "128876")}
    state = {"artist:128876": "2026-06-01T00:00:00+02:00"}
    result = diff_sitemap_entries(entries, known_entity_keys=known, lastmod_state=state)
    assert result.stats.new_entities == 1
    assert result.stats.updated_entities == 1
    assert len(result.to_crawl) == 2


def test_fetch_artsy_sitemap_entries_nested_and_filters() -> None:
    fixtures = {
        "https://www.artsy.net/sitemap-artists.xml": ARTSY_ARTISTS_INDEX,
        "https://www.artsy.net/sitemap-artworks.xml": ARTSY_ARTWORKS_INDEX,
        "https://www.artsy.net/sitemap-artists-0.xml": ARTSY_ARTISTS_URLSET,
        "https://www.artsy.net/sitemap-artworks-nested.xml": ARTSY_NESTED_INDEX,
        "https://www.artsy.net/sitemap-artworks-0.xml": ARTSY_ARTWORKS_URLSET,
    }

    def fake_fetch(url: str) -> bytes:
        return fixtures[url]

    entries = fetch_artsy_sitemap_entries(
        [
            "https://www.artsy.net/sitemap-artists.xml",
            "https://www.artsy.net/sitemap-artworks.xml",
        ],
        fetch_bytes=fake_fetch,
    )
    by_key = {(e.entity_type, e.entity_id): e for e in entries}
    assert ("artist", "pablo-picasso") in by_key
    assert ("artwork", "william-michael-harnett-the-old-violin") in by_key
    assert len(by_key) == 2
    assert by_key[("artist", "pablo-picasso")].lastmod == "2026-06-01T00:00:00Z"


def test_filter_saatchi_child_sitemaps() -> None:
    urls = [
        "https://www.saatchiart.com/sitemap-artworks-1.xml",
        "https://www.saatchiart.com/sitemap-profiles-1.xml",
        "https://www.saatchiart.com/sitemap-plps-1.xml",
    ]
    filtered = filter_saatchi_child_sitemaps(urls)
    assert filtered == [
        "https://www.saatchiart.com/sitemap-artworks-1.xml",
        "https://www.saatchiart.com/sitemap-profiles-1.xml",
    ]


def test_fetch_saatchi_sitemap_entries() -> None:
    fixtures = {
        DEFAULT_SAATCHI_INDEX: SAATCHI_INDEX_XML,
        "https://www.saatchiart.com/sitemap-artworks-1.xml": SAATCHI_ARTWORKS_URLSET,
        "https://www.saatchiart.com/sitemap-profiles-1.xml": SAATCHI_PROFILES_URLSET,
    }

    def fake_fetch(_client: object, url: str, **kwargs: object) -> bytes:
        return fixtures[url]

    with patch("html_downloader.discover.sitemap.fetch_sitemap_bytes", side_effect=fake_fetch):
        entries = fetch_saatchi_sitemap_entries(concurrency=2, client=MagicMock())

    by_key = {(e.entity_type, e.entity_id): e for e in entries}
    assert ("artwork", "9336593") in by_key
    assert ("artist", "radeksmach") in by_key
    assert len(by_key) == 2


def test_fetch_artmajeur_skips_malformed_child_sitemap() -> None:
    fixtures = {
        DEFAULT_ARTMAJEUR_INDEX: ARTMAJEUR_INDEX_XML,
        "https://www.artmajeur.com/sitemap-members-0.xml": ARTMAJEUR_MEMBERS_URLSET,
        "https://www.artmajeur.com/sitemap-artworks-0.xml": ARTMAJEUR_MALFORMED_URLSET,
    }

    def fake_fetch(url: str) -> bytes:
        return fixtures[url]

    entries = fetch_artmajeur_sitemap_entries(fetch_bytes=fake_fetch, concurrency=2)
    by_key = {(e.entity_type, e.entity_id): e for e in entries}
    assert ("artist", "jane-doe") in by_key
    assert len(by_key) == 1

    fixtures["https://www.artmajeur.com/sitemap-artworks-0.xml"] = ARTMAJEUR_ARTWORKS_URLSET
    entries = fetch_artmajeur_sitemap_entries(fetch_bytes=fake_fetch, concurrency=2)
    by_key = {(e.entity_type, e.entity_id): e for e in entries}
    assert ("artist", "jane-doe") in by_key
    assert ("artwork", "12345") in by_key
    assert len(by_key) == 2


def test_known_saatchi_keys_from_paths(tmp_path) -> None:
    path = tmp_path / "urls.txt"
    path.write_text(
        "https://www.saatchiart.com/art/Painting-Test/735695/9336593/view\n",
        encoding="utf-8",
    )
    keys = known_saatchi_keys_from_paths([path])
    assert keys == {("artwork", "9336593")}


def test_known_keys_from_sources_saatchi(tmp_path) -> None:
    path = tmp_path / "results.jsonl"
    path.write_text(
        '{"url": "https://www.saatchiart.com/account/profile/735695"}\n',
        encoding="utf-8",
    )
    keys = known_keys_from_sources(known_paths=[path], source="saatchi")
    assert keys == {("artist", "735695")}
