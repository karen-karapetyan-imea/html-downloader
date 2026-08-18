from __future__ import annotations

import hashlib
from pathlib import Path

from html_downloader.discover.registry import entity_key_from_url, html_filename_for_url, normalize_url
from html_downloader.discover.sitemap import write_url_list


def test_normalize_url_strips_query_and_trailing_slash() -> None:
    assert (
        normalize_url("https://WWW.Artsper.com/us/foo/?x=1#frag")
        == "https://www.artsper.com/us/foo"
    )


def test_entity_key_from_url() -> None:
    url = "https://www.artsper.com/us/contemporary-artworks/painting/2361374/title"
    assert entity_key_from_url(url) == ("artwork", "2361374")


def test_html_filename_for_url() -> None:
    url = "https://www.saatchiart.com/art/Painting-Test/735695/9336593/view"
    expected = hashlib.sha1(url.encode()).hexdigest() + ".html"
    assert html_filename_for_url(url) == expected


def test_write_url_list(tmp_path: Path) -> None:
    out = tmp_path / "new.txt"
    count = write_url_list(out, ["https://example.com/a", "https://example.com/b"])
    assert count == 2
    assert out.read_text(encoding="utf-8").count("\n") == 2
