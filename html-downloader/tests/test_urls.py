from html_downloader.discover.urls import (
    artsper_entity_from_url,
    artsy_entity_from_url,
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
