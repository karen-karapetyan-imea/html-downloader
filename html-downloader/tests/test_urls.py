from html_downloader.discover.urls import (
    artfinder_entity_from_url,
    artsper_entity_from_url,
    artsy_entity_from_url,
    fineartamerica_entity_from_url,
    firstdibs_entity_from_url,
    phaidon_entity_from_url,
    saatchi_artist_from_url,
    saatchi_artwork_from_url,
    saatchi_entity_from_url,
    singulart_entity_from_url,
)


def test_artsper_artwork_url() -> None:
    url = "https://www.artsper.com/us/contemporary-artworks/painting/2361374/title"
    assert artsper_entity_from_url(url) == ("artwork", "2361374")


def test_artsper_artist_url() -> None:
    url = "https://www.artsper.com/us/contemporary-artists/france/128876/nathalie-cubero"
    assert artsper_entity_from_url(url) == ("artist", "128876")


def test_saatchi_artwork_url() -> None:
    url = (
        "https://www.saatchiart.com/art/Painting-Gold-abstract-painting-GB416-FEATURED/"
        "735695/9336593/view"
    )
    assert saatchi_artwork_from_url(url) == ("735695", "9336593")


def test_saatchi_artist_url() -> None:
    url = "https://www.saatchiart.com/account/profile/735695"
    assert saatchi_artist_from_url(url) == "735695"


def test_saatchi_entity_artwork_url() -> None:
    url = "https://www.saatchiart.com/art/Painting-Test/735695/9336593/view"
    assert saatchi_entity_from_url(url) == ("artwork", "9336593")


def test_saatchi_entity_artist_profile_url() -> None:
    url = "https://www.saatchiart.com/account/profile/735695"
    assert saatchi_entity_from_url(url) == ("artist", "735695")


def test_saatchi_entity_artist_username_url() -> None:
    url = "https://www.saatchiart.com/radeksmach"
    assert saatchi_entity_from_url(url) == ("artist", "radeksmach")


def test_artsy_artwork_url() -> None:
    url = "https://www.artsy.net/artwork/william-michael-harnett-the-old-violin"
    assert artsy_entity_from_url(url) == ("artwork", "william-michael-harnett-the-old-violin")


def test_artsy_artist_url() -> None:
    url = "https://www.artsy.net/artist/pablo-picasso"
    assert artsy_entity_from_url(url) == ("artist", "pablo-picasso")


def test_artsy_rejects_nested_paths() -> None:
    assert artsy_entity_from_url("https://www.artsy.net/artist/pablo-picasso/auction-results") is None
    assert artsy_entity_from_url("https://www.artsy.net/artwork/foo/images") is None


def test_singulart_artist_url() -> None:
    url = "https://www.singulart.com/en/artist/alexandre-taillandier-2"
    assert singulart_entity_from_url(url) == ("artist", "2")


def test_singulart_artwork_url() -> None:
    url = "https://www.singulart.com/en/artworks/philippa-paterson-charred-black-crow-28"
    assert singulart_entity_from_url(url) == ("artwork", "28")


def test_singulart_rejects_pagination_and_collections() -> None:
    assert (
        singulart_entity_from_url("https://www.singulart.com/en/artist/graeme-williams-7?page=2")
        is None
    )
    assert (
        singulart_entity_from_url("https://www.singulart.com/en/collection/the-shape-of-maps-27593")
        is None
    )


def test_firstdibs_item_url() -> None:
    url = "https://www.1stdibs.com/art/paintings/landscape/nick-white/id-a_12382842/"
    assert firstdibs_entity_from_url(url) == ("item", "12382842")


def test_firstdibs_dealer_url() -> None:
    url = "https://www.1stdibs.com/dealers/1-drop-gallery/"
    assert firstdibs_entity_from_url(url) == ("dealer", "1-drop-gallery")


def test_firstdibs_dealer_shop_url_maps_to_dealer() -> None:
    url = "https://www.1stdibs.com/dealers/1-drop-gallery/shop/art/paintings/"
    assert firstdibs_entity_from_url(url) == ("dealer", "1-drop-gallery")


def test_firstdibs_rejects_sitemap_and_search() -> None:
    assert (
        firstdibs_entity_from_url("https://www.1stdibs.com/sitemap/art/items/3/6001/")
        is None
    )
    assert firstdibs_entity_from_url("https://www.1stdibs.com/search/?q=foo") is None


def test_firstdibs_creator_url() -> None:
    url = "https://www.1stdibs.com/creators/pablo-picasso/art/paintings/"
    assert firstdibs_entity_from_url(url) == ("creator", "pablo-picasso")


def test_artfinder_artwork_url() -> None:
    url = "https://www.artfinder.com/product/dama-33/"
    assert artfinder_entity_from_url(url) == ("artwork", "dama-33")


def test_artfinder_artist_url() -> None:
    url = "https://www.artfinder.com/artist/sallyjfisher/"
    assert artfinder_entity_from_url(url) == ("artist", "sallyjfisher")


def test_artfinder_rejects_query_locale_and_shop() -> None:
    assert artfinder_entity_from_url("https://www.artfinder.com/product/dama-33/?utm=1") is None
    assert artfinder_entity_from_url("https://www.artfinder.com/en-US/product/dama-33/") is None
    assert (
        artfinder_entity_from_url(
            "https://www.artfinder.com/art/product_category-prints/subject-abstract-conceptual/"
        )
        is None
    )


def test_fineartamerica_artist_url() -> None:
    url = "https://fineartamerica.com/profiles/aaronblaise"
    assert fineartamerica_entity_from_url(url) == ("artist", "aaronblaise")


def test_fineartamerica_artwork_url() -> None:
    url = "https://fineartamerica.com/featured/poolside-glamour-slim-aarons.html"
    assert fineartamerica_entity_from_url(url) == ("artwork", "poolside-glamour-slim-aarons")


def test_fineartamerica_rejects_shop_query_and_merch() -> None:
    assert fineartamerica_entity_from_url("https://fineartamerica.com/profiles/aaronblaise/shop") is None
    assert (
        fineartamerica_entity_from_url(
            "https://fineartamerica.com/featured/poolside-glamour-slim-aarons.html?utm=1"
        )
        is None
    )
    assert fineartamerica_entity_from_url("https://fineartamerica.com/shop/canvas+prints") is None


def test_phaidon_product_url() -> None:
    url = "https://www.phaidon.com/products/cook-in-a-book"
    assert phaidon_entity_from_url(url) == ("product", "cook-in-a-book")


def test_phaidon_rejects_locale_query_and_collections() -> None:
    assert phaidon_entity_from_url("https://www.phaidon.com/en-us/products/cook-in-a-book") is None
    assert phaidon_entity_from_url("https://www.phaidon.com/products/cook-in-a-book?utm=1") is None
    assert phaidon_entity_from_url("https://www.phaidon.com/collections/art") is None
