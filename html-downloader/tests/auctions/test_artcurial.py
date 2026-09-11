from __future__ import annotations

from pathlib import Path
from typing import Any

from html_downloader.auctions.artcurial import (
    entries_from_sale_items,
    fetch_artcurial_entries,
    load_sales_progress,
    save_sales_progress,
    select_pending_sales,
    should_skip_sale,
)
from html_downloader.auctions.paths import auction_sales_progress_file


def _sale(
    ref: str,
    *,
    specialty: str = "MODERN",
    parent: str | None = None,
    begin: str = "2026-09-08T14:00:00Z",
) -> dict[str, Any]:
    parent_ref = parent if parent is not None else specialty
    return {
        "ref": ref,
        "name": f"Sale {ref}",
        "validity": {"beginDate": begin},
        "specialties": [
            {
                "principal": True,
                "specialty": {
                    "ref": specialty,
                    "name": specialty,
                    "parent": {"ref": parent_ref},
                },
            }
        ],
    }


def _item(
    sale_ref: str,
    index: int,
    *,
    sub: str = "a",
    publish: bool = True,
    adjudication: str | None = "2026-09-08T14:04:00Z",
) -> dict[str, Any]:
    return {
        "saleRef": sale_ref,
        "index": index,
        "subIndex": sub,
        "publishLot": publish,
        "adjudicationDate": adjudication,
        "status": "SOLD",
    }


def test_should_skip_motorcars_sales() -> None:
    assert should_skip_sale(_sale("1", specialty="CARS"))
    assert should_skip_sale(_sale("2", specialty="AUTOMOBILIA", parent="CARS"))
    assert should_skip_sale(_sale("3", specialty="VOIT"))
    assert not should_skip_sale(_sale("4", specialty="MODERN"))
    assert not should_skip_sale(_sale("5", specialty="FURNITURE_ART_OBJECTS"))


def test_entries_from_sale_items_builds_lot_urls() -> None:
    sale = _sale("6641")
    items = [
        _item("6641", 1),
        _item("6641", 2, sub="b"),
        _item("6641", 3, publish=False),
        {"index": None},
    ]
    entries = entries_from_sale_items(sale, items)
    by_key = {e.entity_key: e for e in entries}
    assert ("lot", "6641:1-a") in by_key
    assert ("lot", "6641:2-b") in by_key
    assert ("lot", "6641:3-a") not in by_key
    assert by_key[("lot", "6641:1-a")].url == (
        "https://www.artcurial.com/en/sales/6641/lots/1-a"
    )
    assert by_key[("lot", "6641:1-a")].lastmod == "2026-09-08"
    assert by_key[("lot", "6641:2-b")].url == (
        "https://www.artcurial.com/en/sales/6641/lots/2-b"
    )


def test_select_pending_sales_skips_done_and_limits() -> None:
    sales = [
        _sale("100"),
        _sale("200", specialty="CARS"),
        _sale("300"),
        _sale("400"),
    ]
    progress = {"100": {"status": "done", "lots_found": 10}}
    selected = select_pending_sales(sales, progress, max_sales=1)
    assert [s["ref"] for s in selected] == ["300"]
    selected2 = select_pending_sales(sales, progress, max_sales=5)
    assert [s["ref"] for s in selected2] == ["300", "400"]


def test_sales_progress_roundtrip(tmp_path: Path) -> None:
    path = auction_sales_progress_file(tmp_path, "artcurial")
    save_sales_progress(
        path,
        {"6641": {"status": "done", "lots_found": 90}},
    )
    loaded = load_sales_progress(path)
    assert loaded["6641"]["status"] == "done"
    assert loaded["6641"]["lots_found"] == 90


def test_fetch_expands_sales_with_max_sales_and_resume(tmp_path: Path) -> None:
    progress_path = auction_sales_progress_file(tmp_path, "artcurial")
    sales_payload = {
        "content": [
            _sale("100"),
            _sale("200", specialty="CARS"),
            _sale("300"),
        ],
        "totalPages": 1,
        "totalElements": 3,
    }
    items_by_ref = {
        "100": {
            "content": [_item("100", 1), _item("100", 2)],
            "totalPages": 1,
        },
        "300": {
            "content": [_item("300", 5, sub="b")],
            "totalPages": 1,
        },
    }

    def fetch_json(url: str, params: dict[str, Any] | None) -> Any:
        if url.endswith("/sales/results"):
            return sales_payload
        if "/sales/100/items" in url:
            return items_by_ref["100"]
        if "/sales/300/items" in url:
            return items_by_ref["300"]
        if "/sales/200/items" in url:
            raise AssertionError("motorcars sale should not be fetched")
        raise AssertionError(f"unexpected url {url}")

    entries = fetch_artcurial_entries(
        fetch_json=fetch_json,
        max_sales=1,
        concurrency=1,
        sales_progress_path=progress_path,
        max_retries=1,
    )
    assert {e.entity_id for e in entries} == {"100:1-a", "100:2-a"}
    progress = load_sales_progress(progress_path)
    assert progress["100"]["status"] == "done"
    assert progress["200"]["status"] == "done"
    assert progress["200"].get("skipped") is True
    assert "300" not in progress or progress["300"].get("status") != "done"

    entries2 = fetch_artcurial_entries(
        fetch_json=fetch_json,
        max_sales=1,
        concurrency=1,
        sales_progress_path=progress_path,
        max_retries=1,
    )
    assert {e.entity_id for e in entries2} == {"300:5-b"}
    progress2 = load_sales_progress(progress_path)
    assert progress2["300"]["status"] == "done"


def test_fetch_paginates_items(tmp_path: Path) -> None:
    sales_payload = {
        "content": [_sale("50")],
        "totalPages": 1,
    }
    page0 = {
        "content": [_item("50", 1)],
        "totalPages": 2,
    }
    page1 = {
        "content": [_item("50", 2)],
        "totalPages": 2,
    }
    calls: list[tuple[str, dict[str, Any] | None]] = []

    def fetch_json(url: str, params: dict[str, Any] | None) -> Any:
        calls.append((url, params))
        if url.endswith("/sales/results"):
            return sales_payload
        page = int((params or {}).get("page", 0))
        return page0 if page == 0 else page1

    entries = fetch_artcurial_entries(
        fetch_json=fetch_json,
        max_sales=1,
        concurrency=1,
        sales_progress_path=tmp_path / "progress.json",
        max_retries=1,
    )
    assert {e.entity_id for e in entries} == {"50:1-a", "50:2-a"}
    item_pages = [
        p.get("page")
        for u, p in calls
        if "/items" in u and isinstance(p, dict)
    ]
    assert item_pages == [0, 1]
