from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from html_downloader.auctions.algolia_partition import (
    AlgoliaIndexSearcher,
    FacetValues,
    LeafUrls,
    NumericRange,
    Partition,
    PartitionCollector,
    PartitionLog,
    PartitionQueryError,
    collect_hits,
    parse_page,
    partition_log_path,
    plan_chunks,
)
from html_downloader.auctions.search_state import SaleSearchState
from tests.auctions.algolia_fake import FakeIndex

BASE = "kind:lot"


def _records(n: int, **extra: Any) -> list[dict[str, Any]]:
    return [{"id": i, "kind": "lot", "nr": i, **extra} for i in range(n)]


def _collector(
    fake: FakeIndex, dims: list[Any], *, max_hits: int = 5
) -> PartitionCollector:
    searcher = AlgoliaIndexSearcher(fake.query, "idx", attributes=("id",), max_hits=max_hits)
    return PartitionCollector(searcher, dims, base=BASE)


def _ids(hits: list[dict[str, Any]]) -> list[int]:
    return sorted(h["id"] for h in hits)


def test_plan_chunks_is_half_open_and_isolates_heavy_values() -> None:
    assert plan_chunks({}, 0, 100, 4) == [(0, 100)]
    counts = {10: 2, 20: 2, 30: 1, 50: 9, 70: 3}
    assert plan_chunks(counts, 0, 100, 4) == [(0, 30), (30, 50), (50, 51), (51, 100)]
    assert plan_chunks({0: 9}, 0, 100, 4) == [(0, 1), (1, 100)]


def test_parse_page_reads_both_exhaustive_shapes() -> None:
    assert parse_page({"nbHits": 3, "hits": [{}], "exhaustive": {"nbHits": True}}).exact
    assert parse_page({"nbHits": 3, "hits": [], "exhaustiveNbHits": True}).exact
    page = parse_page({"hits": [{}, 1]})
    assert (page.nb_hits, page.exact, len(page.hits)) == (1, False, 1)


@pytest.mark.parametrize("facets", [True, False])
def test_full_page_splits_until_every_record_is_collected(facets: bool) -> None:
    fake = FakeIndex(_records(23), facets=facets)
    hits, summary = collect_hits(_collector(fake, [NumericRange("nr", 0, 64, budget=4)]))
    assert _ids(hits) == list(range(23))
    assert summary.complete and summary.partitions > 1
    assert fake.hit_queries[0]["filters"] == BASE


def test_numeric_split_keeps_records_without_the_attribute() -> None:
    records = _records(6) + [{"id": 100 + i, "kind": "lot"} for i in range(3)]
    records.append({"id": 200, "kind": "lot", "nr": -5})
    fake = FakeIndex(records)
    hits, summary = collect_hits(_collector(fake, [NumericRange("nr", 0, 64, budget=4)]))
    assert _ids(hits) == [*range(6), 100, 101, 102, 200]
    assert summary.complete


def test_fractional_values_are_not_lost_between_chunks() -> None:
    records = [{"id": i, "kind": "lot", "nr": i + 0.5} for i in range(12)]
    hits, _ = collect_hits(_collector(FakeIndex(records), [NumericRange("nr", 0, 64, budget=3)]))
    assert _ids(hits) == list(range(12))


def test_exact_count_mismatch_is_retried_then_incomplete() -> None:
    fake = FakeIndex(_records(6), exact=True, page_cap=4)
    hits, summary = collect_hits(_collector(fake, [], max_hits=10))
    assert len(hits) == 4
    assert len(fake.hit_queries) == 2  # one retry
    assert not summary.complete
    assert summary.incomplete[0]["reason"] == "count_mismatch"


def test_exact_count_above_limit_with_short_page_still_splits() -> None:
    fake = FakeIndex(_records(12), exact=True, page_cap=4)
    hits, summary = collect_hits(
        _collector(fake, [NumericRange("nr", 0, 16, budget=3)], max_hits=10)
    )
    assert _ids(hits) == list(range(12))
    assert summary.complete
    assert summary.expected_hits == 12 and summary.expected_exact


def test_estimated_count_with_short_page_is_complete() -> None:
    fake = FakeIndex(_records(3), exact=False, nb_hits=lambda n: n * 1000)
    hits, summary = collect_hits(_collector(fake, [NumericRange("nr", 0, 16)]))
    assert len(hits) == 3 and summary.complete and summary.partitions == 1


def test_unsplittable_partition_keeps_hits_and_is_incomplete() -> None:
    fake = FakeIndex([{"id": i, "kind": "lot", "nr": 7} for i in range(8)])
    hits, summary = collect_hits(_collector(fake, [NumericRange("nr", 0, 16, budget=4)]))
    assert len(hits) == 5
    assert not summary.complete
    assert [s["reason"] for s in summary.incomplete] == ["unsplittable"]


def test_facet_values_split_with_remainder_and_shortfall_detection() -> None:
    records = [
        *({"id": i, "kind": "lot", "loc": "A"} for i in range(4)),
        *({"id": 10 + i, "kind": "lot", "loc": "B"} for i in range(4)),
        *({"id": 20 + i, "kind": "lot"} for i in range(3)),
    ]
    fake = FakeIndex(records)
    hits, summary = collect_hits(_collector(fake, [FacetValues("loc")]))
    assert _ids(hits) == [*range(4), *range(10, 14), *range(20, 23)]
    assert summary.complete
    assert any("NOT loc:" in p["filters"] for p in fake.hit_queries)


def test_facet_values_refuse_to_split_a_single_value() -> None:
    fake = FakeIndex([{"id": i, "kind": "lot", "loc": "A"} for i in range(7)])
    _, summary = collect_hits(_collector(fake, [FacetValues("loc")]))
    assert summary.incomplete_count == 1


def test_partition_keys_are_deterministic_and_round_trip() -> None:
    root = Partition().child("nr", "nr >= 0 AND nr < 8", 0, (0, 8))
    child = root.child("nr", "nr >= 0 AND nr < 4", 0, (0, 4)).child("loc", 'loc:"A"', 2)
    assert child.key == 'nr >= 0 AND nr < 4 AND loc:"A"'
    assert child.filters(BASE) == f"{BASE} AND {child.key}"
    assert Partition.from_json(json.loads(json.dumps(child.to_json()))) == child

    fake = FakeIndex(_records(17))

    def leaf_keys() -> list[str]:
        seen: list[str] = []
        _collector(fake, [NumericRange("nr", 0, 64, budget=4)]).collect(
            lambda p, h: seen.append(p.key) or LeafUrls(unique=len(h))
        )
        return seen

    assert leaf_keys() == leaf_keys()


def test_interrupted_window_resumes_at_unfinished_partitions(tmp_path: Path) -> None:
    log_path = tmp_path / "state_partitions.jsonl"
    dims = [NumericRange("nr", 0, 64, budget=4)]
    first_leaves: list[str] = []

    fake = FakeIndex(_records(23))
    fake.fail_after = 8
    run = PartitionLog(log_path).start("w1")
    with pytest.raises(PartitionQueryError):
        _collector(fake, dims).collect(
            lambda p, h: first_leaves.append(p.key) or LeafUrls(len(h), len(h)), run=run
        )
    assert first_leaves

    resumed_fake = FakeIndex(_records(23))
    log = PartitionLog(log_path)
    resumed = log.start("w1")
    assert resumed.epoch == run.epoch and resumed.resumed
    second_leaves: list[str] = []
    summary = _collector(resumed_fake, dims).collect(
        lambda p, h: second_leaves.append(p.key) or LeafUrls(len(h), len(h)), run=resumed
    )
    assert not set(first_leaves) & set(second_leaves)
    assert summary.retrieved_hits == 23 and summary.complete
    assert summary.resumed_partitions == len(first_leaves)
    # The root split plan is replayed, not re-planned: no root query / facet call.
    assert all(p["filters"] != BASE for p in resumed_fake.calls)

    resumed.finish("done", summary)
    assert log.start("w1").epoch != run.epoch


def test_partition_log_tolerates_torn_line_and_compacts(tmp_path: Path) -> None:
    path = tmp_path / "log.jsonl"
    log = PartitionLog(path)
    old = log.start("w")
    old.finish("done", collect_hits(_collector(FakeIndex(_records(2)), []))[1])
    new = log.start("w")
    with open(path, "a", encoding="utf-8") as handle:
        handle.write('{"window": "w", "epo')
    reloaded = PartitionLog(path)
    assert reloaded.start("w").epoch == new.epoch
    reloaded.compact()
    epochs = {json.loads(line)["epoch"] for line in path.read_text().splitlines()}
    assert epochs == {new.epoch}


def test_partition_log_path_sits_next_to_state() -> None:
    assert partition_log_path(Path("/s/sothebys_site_search_state.json")) == Path(
        "/s/sothebys_site_search_state_partitions.jsonl"
    )


def test_sale_search_state_extra_fields_are_backward_compatible(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"sales": {"a": {"status": "done", "hits": 3, "written": 3}}}))
    state = SaleSearchState(path)
    assert state.status("a") == "done"
    state.mark("b", "incomplete", hits=5, written=4, expected=6, partitions=2)
    state.flush()
    reloaded = SaleSearchState(path)
    assert reloaded.get("a") == {"status": "done", "hits": 3, "written": 3}
    assert reloaded.get("b") == {
        "status": "incomplete",
        "hits": 5,
        "written": 4,
        "expected": 6,
        "partitions": 2,
    }
    assert reloaded.get("missing") is None
