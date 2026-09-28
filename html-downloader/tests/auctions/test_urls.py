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


def test_barnebys_live_lot_slug_normalize_and_entity() -> None:
    from html_downloader.auctions.urls import (
        barnebys_entity_from_url,
        barnebys_realized_twin_url,
        is_barnebys_auction_url,
    )

    url = (
        "https://barnebys.com/auctions/lot/"
        "f-clark-oil-painting-of-bridge-gGZS28C-629739503?utm_source=x"
    )
    assert (
        normalize_auction_url(url)
        == "https://www.barnebys.com/auctions/lot/"
        "f-clark-oil-painting-of-bridge-gGZS28C-629739503"
    )
    assert barnebys_entity_from_url(url) == ("lot", "629739503")
    assert is_barnebys_auction_url(url)
    twin = barnebys_realized_twin_url(url)
    assert twin == (
        "https://www.barnebys.com/realized-prices/lot/"
        "f-clark-oil-painting-of-bridge-gGZS28C-629739503"
    )
    assert barnebys_entity_from_url(twin) == ("result_lot", "629739503")


def test_barnebys_live_lot_id_slug_form() -> None:
    from html_downloader.auctions.urls import barnebys_entity_from_url

    url = "https://www.barnebys.com/auctions/lot/443468158/Lockers/"
    assert (
        normalize_auction_url(url)
        == "https://www.barnebys.com/auctions/lot/443468158/lockers"
    )
    assert barnebys_entity_from_url(url) == ("lot", "443468158")


def test_barnebys_rejects_non_lot_paths() -> None:
    from html_downloader.auctions.urls import is_barnebys_auction_url

    rejected = [
        "https://www.barnebys.com/auctions/all",
        "https://www.barnebys.com/redirect",
        "https://www.barnebys.com/re/foo",
        "https://www.barnebys.com/realized-prices",
        "https://www.barnebys.com/blog/search/foo",
        "https://www.example.com/auctions/lot/1/x",
    ]
    for url in rejected:
        assert not is_barnebys_auction_url(url), url


def test_saleroom_lot_normalize_and_entity() -> None:
    from html_downloader.auctions.urls import (
        is_saleroom_auction_url,
        is_saleroom_lot_url,
        saleroom_entity_from_url,
    )

    url = (
        "https://the-saleroom.com/fr-fr/auction-catalogues/Kew/"
        "catalogue-id-KEW-AU10015/"
        "lot-003BC9C2-31B8-4BBF-BB91-B4BC01078797?utm_source=x"
    )
    assert (
        normalize_auction_url(url)
        == "https://www.the-saleroom.com/en-gb/auction-catalogues/kew/"
        "catalogue-id-kew-au10015/lot-003bc9c2-31b8-4bbf-bb91-b4bc01078797"
    )
    assert saleroom_entity_from_url(url) == (
        "lot",
        "003bc9c2-31b8-4bbf-bb91-b4bc01078797",
    )
    assert is_saleroom_auction_url(url)
    assert is_saleroom_lot_url(url)

    catalogue = (
        "https://www.the-saleroom.com/en-us/auction-catalogues/kew/"
        "catalogue-id-kew-au10015/"
    )
    assert (
        normalize_auction_url(catalogue)
        == "https://www.the-saleroom.com/en-gb/auction-catalogues/kew/"
        "catalogue-id-kew-au10015"
    )
    assert saleroom_entity_from_url(catalogue) == ("catalogue", "kew-au10015")
    assert not is_saleroom_lot_url(catalogue)

    assert not is_saleroom_auction_url(
        "https://www.the-saleroom.com/en-gb/for-sale/fine-art"
    )
    assert not is_auction_url(
        "https://www.the-saleroom.com/en-gb/auction-catalogues/kew/"
        "catalogue-id-kew-au10015/lot-003bc9c2-31b8-4bbf-bb91-b4bc01078797"
    )


def test_drouot_lot_and_sale_normalize_and_entity() -> None:
    from html_downloader.auctions.urls import (
        build_drouot_lot_url,
        drouot_entity_from_url,
        is_drouot_auction_url,
        is_drouot_lot_url,
    )

    url = (
        "https://www.drouot.com/fr/l/34624465-Tiffany-Heart-Tag"
        "/__data.json?utm_source=x"
    )
    assert (
        normalize_auction_url(url)
        == "https://drouot.com/en/l/34624465-tiffany-heart-tag"
    )
    assert drouot_entity_from_url(url) == ("lot", "34624465")
    assert is_drouot_auction_url(url)
    assert is_drouot_lot_url(url)

    sale = "https://drouot.com/de/v/184832-Japanese-Crafts/"
    assert (
        normalize_auction_url(sale)
        == "https://drouot.com/en/v/184832-japanese-crafts"
    )
    assert drouot_entity_from_url(sale) == ("sale", "184832")
    assert not is_drouot_lot_url(sale)

    assert build_drouot_lot_url(100, "Silver Coin") == (
        "https://drouot.com/en/l/100-silver-coin"
    )
    rejected = [
        "https://drouot.com/en/s?query=painting",
        "https://drouot.com/en/c/626/paintings",
        "https://drouot.com/en/account/profile",
        "https://www.example.com/en/l/1-x",
    ]
    for bad in rejected:
        assert not is_drouot_auction_url(bad), bad


def test_christies_lot_and_sale_normalize_and_entity() -> None:
    from html_downloader.auctions.urls import (
        build_christies_lot_url,
        build_christies_sale_url,
        christies_entity_from_url,
        is_christies_auction_url,
        is_christies_lot_url,
    )

    lot = "https://christies.com/zh-cn/lot/lot-6557734/?ldp_breadcrumb=back"
    assert normalize_auction_url(lot) == "https://www.christies.com/en/lot/lot-6557734"
    assert christies_entity_from_url(lot) == ("lot", "6557734")
    assert is_christies_lot_url(lot)
    assert christies_entity_from_url("https://www.christies.com/lot/lot-42") == (
        "lot",
        "42",
    )

    sale = "https://www.christies.com/zh/auction/auction-22252-NYR"
    assert (
        normalize_auction_url(sale)
        == "https://www.christies.com/en/auction/auction-22252-nyr"
    )
    assert christies_entity_from_url(sale) == ("sale", "22252-nyr")
    assert is_christies_auction_url(sale)
    assert not is_christies_lot_url(sale)

    assert build_christies_lot_url(6599499) == (
        "https://www.christies.com/en/lot/lot-6599499"
    )
    assert build_christies_sale_url(24211, "PAR") == (
        "https://www.christies.com/en/auction/auction-24211-par"
    )
    rejected = [
        "https://www.christies.com/en/sso?ObjectID=24496.1&LotNumber=1",
        "https://www.christies.com/en/stories/some-story",
        "https://www.christies.com/en/auction/de-la-collection-l-on-parc-31009/",
        "https://www.christies.com/en/search?entry=picasso",
        "https://www.example.com/en/lot/lot-1",
    ]
    for bad in rejected:
        assert not is_christies_auction_url(bad), bad
