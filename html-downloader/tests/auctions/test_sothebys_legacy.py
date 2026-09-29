from __future__ import annotations

import json
from pathlib import Path

import pytest

from html_downloader.auctions import sothebys_legacy as sl
from html_downloader.auctions.sothebys_legacy import (
    LISTING_KEY,
    LegacyFetchError,
    LegacyHtmlClient,
    LegacySale,
    contiguous_lot_numbers,
    crawl_legacy_sale,
    expand_legacy_archive,
    list_legacy_sales,
    lot_cache_path,
    parse_department_filters,
    parse_results_page,
    parse_sale_page,
    results_page_url,
)

ROOT = "https://www.sothebys.com"


def results_html(sales: list[str], *, pages: int, modern: tuple[str, ...] = ()) -> str:
    links = [f'<a href="{ROOT}/en/auctions/{key}.html">x</a>' for key in sales]
    links += [f'<a href="{ROOT}/en/buy/auction/{key}">y</a>' for key in modern]
    return (
        f"<ul>{''.join(links)}</ul>"
        f'<li><span data-page-number>1</span> of <span data-page-count>{pages}</span></li>'
    )


def sale_html(sale: LegacySale, count: int | None, lots: list[str]) -> str:
    counter = f'<div class="AuctionsModule-lotsCount"> {count:,} lots</div>' if count else ""
    links = "".join(
        f'<a href="/en/auctions/ecatalogue/{sale.year}/{sale.slug}/lot.{lot}.html">{lot}</a>'
        for lot in lots
    )
    other = '<a href="/en/auctions/ecatalogue/2001/other-sale/lot.999.html">other</a>'
    return counter + links + other


def paged_sale(sale: LegacySale, lots: list[str]) -> dict[str, str]:
    """URL → HTML for every lot page of a sale (12 lots per page)."""
    pages: dict[str, str] = {}
    chunks = [lots[i : i + 12] for i in range(0, len(lots), 12)] or [[]]
    for page, chunk in enumerate(chunks, start=1):
        pages[sale.page_url(page)] = sale_html(sale, len(lots), chunk)
    return pages


class FakeSite:
    def __init__(self, pages: dict[str, str | None], fail: set[str] | None = None) -> None:
        self.pages = pages
        self.fail = fail or set()
        self.calls: list[str] = []

    def get(self, url: str) -> str | None:
        self.calls.append(url)
        if url in self.fail:
            raise LegacyFetchError(f"boom {url}")
        if url not in self.pages:
            return None
        return self.pages[url]


def test_parse_results_page_keeps_legacy_only_and_dedupes() -> None:
    body = results_html(
        ["2008/Indian-Art-N08417", "2008/indian-art-n08417", "2005/marine-l05135"],
        pages=526,
        modern=("2026/fine-jewelry",),
    )
    sales, pages = parse_results_page(body)
    assert [s.key for s in sales] == ["2008/indian-art-n08417", "2005/marine-l05135"]
    assert pages == 526
    assert parse_results_page("<html></html>") == ([], None)


def test_parse_department_filters_maps_labels_to_ids() -> None:
    body = (
        '<input type="checkbox" name="f2" id="a1" value="a1"><label for="a1">Prints</label>'
        '<input type="checkbox" name="f2" id="b2" value="b2">'
        '<label for="b2">Chinese Paintings &#x2013; Modern</label>'
        '<input type="checkbox" name="f1" id="c3" value="c3"><label for="c3">London</label>'
    )
    assert parse_department_filters(body) == {"Prints": "a1", "Chinese Paintings – Modern": "b2"}


def test_parse_sale_page_scopes_lots_to_the_sale() -> None:
    sale = LegacySale("2005", "marine-l05135")
    count, lots = parse_sale_page(sale_html(sale, 1234, ["1", "2", "60A", "2"]), sale)
    assert count == 1234
    assert lots == ["1", "2", "60a"]


@pytest.mark.parametrize(
    ("count", "first", "last", "expected"),
    [
        (30, [str(n) for n in range(1, 13)], ["25", "26", "27", "28", "29", "30"], 30),
        (25, [str(n) for n in range(101, 113)], ["125"], 25),
        (29, [str(n) for n in range(1, 13)], ["25", "26", "27", "28", "29", "30"], None),
        (30, ["1", "2", "3a"], ["30"], None),
        (30, ["1", "3"], ["30"], None),
        (0, ["1"], ["1"], None),
    ],
)
def test_contiguous_lot_numbers(
    count: int, first: list[str], last: list[str], expected: int | None
) -> None:
    result = contiguous_lot_numbers(count, first, last)
    if expected is None:
        assert result is None
    else:
        assert result is not None and len(result) == expected
        assert result[0] == first[0] and result[-1] == last[-1]


def test_crawl_contiguous_sale_uses_first_and_last_page_only() -> None:
    sale = LegacySale("2010", "big-sale-l10001")
    lots = [str(n) for n in range(1, 41)]
    site = FakeSite(paged_sale(sale, lots))
    result = crawl_legacy_sale(sale, get=site.get)
    assert result.status == "done"
    assert list(result.lots) == lots
    assert site.calls == [sale.page_url(1), sale.page_url(4)]


def test_crawl_gapped_sale_pages_through_everything() -> None:
    sale = LegacySale("2005", "marine-l05135")
    lots = [str(n) for n in range(1, 30)] + ["29a"]
    site = FakeSite(paged_sale(sale, lots))
    result = crawl_legacy_sale(sale, get=site.get)
    assert list(result.lots) == lots
    assert result.lot_count == 30
    assert site.calls == [sale.page_url(1), sale.page_url(3), sale.page_url(2)]


def test_crawl_single_page_and_missing_sale() -> None:
    sale = LegacySale("2012", "small-n01")
    site = FakeSite(paged_sale(sale, ["1", "2", "5"]))
    assert crawl_legacy_sale(sale, get=site.get).lots == ("1", "2", "5")
    assert site.calls == [sale.page_url(1)]

    gone = crawl_legacy_sale(LegacySale("2013", "wine-n09011"), get=site.get)
    assert gone.status == "missing" and gone.lots == ()


def test_crawl_raises_when_a_middle_page_disappears() -> None:
    sale = LegacySale("2005", "marine-l05135")
    pages = paged_sale(sale, [str(n) for n in range(1, 30)] + ["29a"])
    del pages[sale.page_url(2)]
    with pytest.raises(LegacyFetchError):
        crawl_legacy_sale(sale, get=FakeSite(pages).get)


def test_list_legacy_sales_walks_all_pages_with_department_filters() -> None:
    form = (
        '<input name="f2" value="id-prints"><label for="id-prints">Prints</label>'
        '<input name="f2" value="id-photo"><label for="id-photo">Photographs</label>'
    )
    ids = ["id-prints", "id-photo"]
    site = FakeSite(
        {
            sl.RESULTS_URL: form,
            results_page_url(1, ids): results_html(["2018/a-1"], pages=3, modern=("2026/m",)),
            results_page_url(2, ids): results_html(["2018/a-1", "2017/b-2"], pages=3),
            results_page_url(3, ids): results_html(["2006/c-3"], pages=3),
        }
    )
    sales, complete = list_legacy_sales(
        site.get, departments=["Prints", "Photographs", "Nope"], workers=2
    )
    assert complete
    assert [s.key for s in sales] == ["2018/a-1", "2017/b-2", "2006/c-3"]
    assert results_page_url(2, ids) == f"{sl.RESULTS_URL}?f2=id-prints&f2=id-photo&p=2"

    with pytest.raises(ValueError):
        list_legacy_sales(site.get, departments=["Nope"])


def test_list_legacy_sales_reports_incomplete_walk() -> None:
    site = FakeSite(
        {
            results_page_url(1): results_html(["2018/a-1"], pages=2),
            results_page_url(2): results_html(["2017/b-2"], pages=2),
        },
        fail={results_page_url(2)},
    )
    sales, complete = list_legacy_sales(site.get)
    assert [s.key for s in sales] == ["2018/a-1"]
    assert not complete


def _archive_site() -> tuple[FakeSite, LegacySale, LegacySale, LegacySale]:
    good = LegacySale("2010", "good-l10001")
    flaky = LegacySale("2009", "flaky-n08500")
    gone = LegacySale("2008", "gone-n08400")
    pages: dict[str, str | None] = {
        results_page_url(1): results_html([good.key, flaky.key], pages=2),
        results_page_url(2): results_html([gone.key], pages=2),
    }
    pages.update(paged_sale(good, ["1", "2"]))
    pages.update(paged_sale(flaky, ["7"]))
    return FakeSite(pages, fail={flaky.page_url(1)}), good, flaky, gone


def test_expand_legacy_archive_checkpoints_and_resumes(tmp_path: Path) -> None:
    state_path = tmp_path / "sothebys_legacy_state.json"
    site, good, flaky, gone = _archive_site()

    entries = expand_legacy_archive(state_path=state_path, get=site.get, workers=1)
    assert {e.entity_key for e in entries} == {
        ("sale", "legacy/2010/good-l10001"),
        ("lot", "legacy/2010/good-l10001/1"),
        ("lot", "legacy/2010/good-l10001/2"),
    }
    assert entries[1].url == f"{ROOT}/en/auctions/ecatalogue/2010/good-l10001/lot.1.html"
    sales = json.loads(state_path.read_text(encoding="utf-8"))["sales"]
    assert sales[LISTING_KEY]["status"] == "done"
    assert sales[good.key] == {"status": "done", "hits": 2, "written": 3}
    assert sales[flaky.key]["status"] == "failed"
    assert sales[gone.key]["status"] == "missing"

    # Second run: no listing walk, only the failed sale is retried.
    site.fail.clear()
    site.calls.clear()
    again = expand_legacy_archive(state_path=state_path, get=site.get, workers=1)
    assert site.calls == [flaky.page_url(1)]
    assert [e.entity_key for e in again] == [
        ("sale", "legacy/2009/flaky-n08500"),
        ("lot", "legacy/2009/flaky-n08500/7"),
    ]
    rows = lot_cache_path(state_path).read_text(encoding="utf-8").splitlines()
    assert len(rows) == 5

    site.calls.clear()
    assert expand_legacy_archive(state_path=state_path, get=site.get) == []
    assert site.calls == []


def test_expand_legacy_archive_force_and_max_sales(tmp_path: Path) -> None:
    state_path = tmp_path / "sothebys_legacy_state.json"
    site, good, _flaky, _gone = _archive_site()
    site.fail.clear()
    first = expand_legacy_archive(state_path=state_path, get=site.get, max_sales=1)
    assert {e.entity_id for e in first} >= {f"legacy/{good.key}"}

    forced = expand_legacy_archive(state_path=state_path, get=site.get, force=True)
    assert len(lot_cache_path(state_path).read_text(encoding="utf-8").splitlines()) == len(forced)
    assert len(forced) == 5


def test_html_client_retries_and_treats_404_as_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sl.time, "sleep", lambda _s: None)
    big = "x" * sl.MIN_HTML_BYTES
    responses = {
        "a": [(503, ""), (200, "tiny"), (200, big)],
        "b": [(404, "not found")],
        "c": [(500, "")] * 3,
    }

    def fetch(url: str) -> tuple[int, str]:
        if url == "t":
            raise OSError("reset")
        return responses[url].pop(0)

    client = LegacyHtmlClient(delay=0, retries=3, fetch=fetch)
    assert client.get("a") == big
    assert client.get("b") is None
    with pytest.raises(LegacyFetchError):
        client.get("c")
    with pytest.raises(LegacyFetchError):
        client.get("t")
