from __future__ import annotations

from html_downloader.auctions.invaluable_hubs import (
    auctions_list_page_url,
    expand_catalogs_from_auctions_list,
    expand_from_house_pages,
    extract_entity_entries_from_html,
    parse_auctions_total_pages,
)


def test_auctions_list_page_url() -> None:
    assert auctions_list_page_url(page=0) == "https://www.invaluable.com/auctions/"
    assert auctions_list_page_url(page=2) == "https://www.invaluable.com/auctions/?page=2"


def test_parse_auctions_total_pages() -> None:
    html = (
        '{"pagination":{"isFirst":true,"isLast":false,"itemsOnPage":50,'
        '"pageNumber":0,"pageSize":50,"totalPages":15},"totalElements":706}'
    )
    assert parse_auctions_total_pages(html) == 15


def test_extract_entity_entries_from_html() -> None:
    html = """
    <a href="/catalog/01BMDJ3KWY">sale</a>
    <a href="/auction-lot/foo-c-aaaaaaaaaa">lot</a>
    <a href="/auction-house/timeline-auctions-9mq71klbbn">house</a>
    """
    entries = extract_entity_entries_from_html(
        html,
        base_url="https://www.invaluable.com/auctions/",
        allowed_types=frozenset({"catalog", "lot"}),
    )
    keys = {e.entity_key for e in entries}
    assert keys == {("catalog", "01bmdj3kwy"), ("lot", "aaaaaaaaaa")}


def test_expand_catalogs_from_auctions_list() -> None:
    pages = {
        0: (
            '{"pagination":{"pageNumber":0,"totalPages":2}}'
            '<a href="/catalog/aaaaaaaaaa">a</a>'
        ),
        1: '<a href="/catalog/bbbbbbbbbb">b</a>',
    }

    def fetch(url: str) -> str:
        if "page=" not in url:
            return pages[0]
        return pages[int(url.rsplit("page=", 1)[-1])]

    entries = expand_catalogs_from_auctions_list(
        fetch_html=fetch,
        page_sleep=0,
        max_pages=5,
    )
    assert {e.entity_id for e in entries} == {"aaaaaaaaaa", "bbbbbbbbbb"}


def test_expand_from_house_pages(tmp_path) -> None:
    progress = tmp_path / "house_progress.json"
    house = "https://www.invaluable.com/auction-house/timeline-auctions-9mq71klbbn"

    def fetch(_url: str) -> str:
        return (
            '<a href="/catalog/cccccccccc">c</a>'
            '<a href="/auction-lot/bar-c-dddddddddd">d</a>'
        )

    entries = expand_from_house_pages(
        [house],
        fetch_html=fetch,
        progress_path=progress,
        concurrency=1,
        house_sleep=0,
    )
    assert {e.entity_key for e in entries} == {
        ("catalog", "cccccccccc"),
        ("lot", "dddddddddd"),
    }
