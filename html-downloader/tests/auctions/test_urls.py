from __future__ import annotations

from html_downloader.auctions.urls import (
    is_auction_url,
    invaluable_entity_from_url,
    normalize_auction_url,
    prefer_entity_url,
)


def test_normalize_absolute_catalog_url() -> None:
    assert (
        normalize_auction_url("https://www.invaluable.com/catalog/01BMDJ3KWY")
        == "https://www.invaluable.com/catalog/01bmdj3kwy"
    )


def test_normalize_relative_url() -> None:
    assert (
        normalize_auction_url("/catalog/0ak1fxhm3a", base_url="https://www.invaluable.com/")
        == "https://www.invaluable.com/catalog/0ak1fxhm3a"
    )


def test_normalize_strips_fragment_and_tracking() -> None:
    url = "https://www.invaluable.com/catalog/01bmdj3kwy?utm_source=x&fbclid=1#section"
    assert normalize_auction_url(url) == "https://www.invaluable.com/catalog/01bmdj3kwy"


def test_normalize_rejects_non_http() -> None:
    assert normalize_auction_url("javascript:alert(1)") is None
    assert normalize_auction_url("mailto:a@b.com") is None


def test_normalize_non_www_host() -> None:
    assert (
        normalize_auction_url("https://invaluable.com/catalog/01bmdj3kwy")
        == "https://www.invaluable.com/catalog/01bmdj3kwy"
    )


def test_is_auction_url_accepts_catalog_and_lot() -> None:
    assert is_auction_url("https://www.invaluable.com/catalog/01bmdj3kwy")
    assert is_auction_url("https://WWW.INVALUABLE.COM/catalog/01BMDJ3KWY/")
    assert is_auction_url(
        "https://www.invaluable.com/auction-lot/"
        "heuer-abercrombie-fitch-co-stainless-steel-seafar-134-c-f38e67d2a8"
    )


def test_is_auction_url_accepts_hubs() -> None:
    assert is_auction_url(
        "https://www.invaluable.com/auction-house/timeline-auctions-9mq71klbbn"
    )
    assert is_auction_url("https://www.invaluable.com/auction-houses/9MQ71KLBBN")
    assert is_auction_url("https://www.invaluable.com/artist/dali-salvador-9chkguv69j")
    assert is_auction_url("https://www.invaluable.com/fine-art/pc-SG2BIX3JPJ/")
    assert is_auction_url("https://www.invaluable.com/paintings/cc-AF14022FJQ")
    assert is_auction_url("https://www.invaluable.com/abstract-prints/sc-R00VWG56FO/")


def test_is_auction_url_rejects_non_auctions() -> None:
    rejected = [
        "https://www.invaluable.com/auction-lot/heuer-abercrombie-fitch-co-134",
        "https://www.invaluable.com/artists/",
        "https://www.invaluable.com/auctions/",
        "https://www.invaluable.com/catalog/advancedSearch.cfm",
        "https://www.invaluable.com/sitemap",
        "https://www.invaluable.com/artist/dali-salvador-9chkguv69j/sold-at-auction-prices",
        "https://www.example.com/catalog/01bmdj3kwy",
    ]
    for url in rejected:
        assert not is_auction_url(url), url


def test_invaluable_entity_from_url_catalog_and_lot() -> None:
    assert invaluable_entity_from_url(
        "https://www.invaluable.com/catalog/01BMDJ3KWY"
    ) == ("catalog", "01bmdj3kwy")
    assert invaluable_entity_from_url(
        "https://www.invaluable.com/auction-lot/"
        "Heuer-Abercrombie-Fitch-Co-Stainless-Steel-Seafar-134-c-F38E67D2A8"
    ) == ("lot", "f38e67d2a8")
    assert invaluable_entity_from_url(
        "https://www.invaluable.com/lot/F38E67D2A8"
    ) == ("lot", "f38e67d2a8")


def test_invaluable_entity_from_url_hubs() -> None:
    assert invaluable_entity_from_url(
        "https://www.invaluable.com/auction-house/timeline-auctions-9MQ71KLBBN"
    ) == ("house", "9mq71klbbn")
    assert invaluable_entity_from_url(
        "https://www.invaluable.com/artist/dali-salvador-9CHKGuV69J/"
    ) == ("artist", "9chkguv69j")
    assert invaluable_entity_from_url(
        "https://www.invaluable.com/fine-art/pc-SG2BIX3JPJ/"
    ) == ("category", "pc-sg2bix3jpj")


def test_prefer_entity_url_canonical_forms() -> None:
    short = "https://www.invaluable.com/lot/f38e67d2a8"
    long = (
        "https://www.invaluable.com/auction-lot/"
        "heuer-abercrombie-fitch-co-stainless-steel-seafar-134-c-f38e67d2a8"
    )
    assert prefer_entity_url(short, long) == long
    house_alt = "https://www.invaluable.com/auction-houses/9mq71klbbn"
    house = "https://www.invaluable.com/auction-house/timeline-auctions-9mq71klbbn"
    assert prefer_entity_url(house_alt, house) == house


def test_duplicate_normalization() -> None:
    a = normalize_auction_url("https://www.invaluable.com/catalog/AbCdEfGhIj/")
    b = normalize_auction_url("https://www.invaluable.com/catalog/abcdefghij?utm_medium=email")
    assert a == b == "https://www.invaluable.com/catalog/abcdefghij"

    lot_a = normalize_auction_url(
        "https://www.invaluable.com/auction-lot/foo-bar-134-c-F38E67D2A8?utm_source=x"
    )
    lot_b = normalize_auction_url(
        "https://www.invaluable.com/auction-lot/foo-bar-134-c-f38e67d2a8#frag"
    )
    assert lot_a == lot_b == "https://www.invaluable.com/auction-lot/foo-bar-134-c-f38e67d2a8"


def test_liveauctioneers_price_result_normalize_and_entity() -> None:
    from html_downloader.auctions.urls import (
        is_liveauctioneers_auction_url,
        liveauctioneers_entity_from_url,
    )

    assert (
        normalize_auction_url(
            "https://liveauctioneers.com/price-result/Oil-Painting-123/?utm_source=x"
        )
        == "https://www.liveauctioneers.com/price-result/oil-painting-123"
    )
    assert liveauctioneers_entity_from_url(
        "https://www.liveauctioneers.com/price-result/Oil-Painting-123/"
    ) == ("price_result", "oil-painting-123")
    assert is_liveauctioneers_auction_url(
        "https://www.liveauctioneers.com/price-result/foo-bar"
    )
    assert not is_liveauctioneers_auction_url(
        "https://www.liveauctioneers.com/item/123_foo"
    )
    assert not is_auction_url("https://www.liveauctioneers.com/price-result/foo")


def test_artcurial_lot_normalize_and_entity() -> None:
    from html_downloader.auctions.urls import (
        artcurial_entity_from_url,
        is_artcurial_auction_url,
    )

    assert (
        normalize_auction_url(
            "https://artcurial.com/fr/sales/6641/lots/1-A/?utm_source=x"
        )
        == "https://www.artcurial.com/en/sales/6641/lots/1-a"
    )
    assert artcurial_entity_from_url(
        "https://www.artcurial.com/en/sales/6641/lots/2-b/"
    ) == ("lot", "6641:2-b")
    assert is_artcurial_auction_url(
        "https://www.artcurial.com/en/sales/6641/lots/1-a"
    )
    assert not is_artcurial_auction_url(
        "https://www.artcurial.com/en/sales/6641"
    )
    assert not is_auction_url("https://www.artcurial.com/en/sales/6641/lots/1-a")
