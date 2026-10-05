"""Minimal synthetic HTML pages in the shape each platform parser expects."""

import json


def _next_data(payload: dict) -> str:
    return f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(payload)}</script>'


def saatchi_artist(user_id: int = 123, *, city: str = "Berlin", about: str | None = None) -> str:
    account = {
        "userId": user_id,
        "userName": "jane-doe",
        "firstName": "Jane",
        "lastName": "Doe",
        "avatar": "//images.saatchiart.com/avatar.jpg",
        "city": city,
        "state": "Berlin",
        "zipcode": "10115",
        "country": "Germany",
        "countryCode": "DE",
        "aboutArtist": {"about": about or "<p>Painter.</p><p>Second &amp; last.</p>"},
        "socialLinks": {
            "artistHomepage": "janedoe.com",
            "facebook": "https://facebook.com/janedoe",
            "instagram": "@janedoe",
            "tiktok": "",
            "twitter": "https://x.com/janedoe",
        },
        "artworks": [],
    }
    data = {
        "props": {
            "pageProps": {
                "initialProps": {"metaData": {"title": "Jane Doe | Saatchi Art"}},
                "initialState": {
                    "page": {"data": {"accountData": account}},
                    "shared": {"serverTimestampInMilliseconds": 1758585600000},
                },
            }
        }
    }
    return f'<html><head><link rel="canonical" href="https://www.saatchiart.com/jane-doe"></head><body>{_next_data(data)}</body></html>'


def saatchi_artwork() -> str:
    data = {
        "props": {
            "pageProps": {
                "initialProps": {"metaData": {}},
                "initialState": {
                    "page": {
                        "data": {
                            "artwork": {"artworkId": 5, "userId": 123, "title": "Blue"},
                            "artist": {"userId": 123},
                        }
                    },
                    "shared": {},
                },
            }
        }
    }
    return f"<html><body>{_next_data(data)}</body></html>"


def saatchi_broken() -> str:
    return '<html><body><script id="__NEXT_DATA__" type="application/json">{not json</script></body></html>'


def artsy_artist() -> str:
    artist = {
        "internalID": "4d8b92b34eb68a1b2c0003f4",
        "slug": "andy-warhol",
        "name": "Andy Warhol",
        "href": "/artist/andy-warhol",
        "gender": "male",
        "nationality": "American",
        "birthday": "1928",
        "deathday": "1987",
        "hometown": "Pittsburgh, Pennsylvania",
        "biographyBlurbPlain": {"text": "Pop art pioneer."},
        "biographyBlurb": {"text": "<p>Pop art pioneer.</p>"},
    }
    responses = [
        [
            json.dumps({"queryID": "artistRoutes_ArtistAppQuery", "variables": {}}),
            {"json": {"data": {"artist": artist}}},
        ]
    ]
    payload = json.dumps(json.dumps(responses))
    return (
        '<html><head><meta property="og:image" content="https://d32dm0rphc51dk.cloudfront.net/warhol.jpg">'
        '<link rel="canonical" href="https://www.artsy.net/artist/andy-warhol"></head>'
        f"<body><script>var __RELAY_HYDRATION_DATA__ = {payload};</script></body></html>"
    )


def artfinder_artist() -> str:
    artist = {
        "id": 42,
        "username": "johnsmith",
        "slug": "john-smith",
        "name": "John Smith",
        "url": "https://www.artfinder.com/artist/john-smith}/",
        "country": "United Kingdom",
        "country_code": "GB",
        "avatar_url": "https://d2m7ibezl7l5lt.cloudfront.net/img/avatar.jpg",
        "facebook_url": "https://www.facebook.com/johnsmithart",
        "instagram_username": "johnsmith.art",
        "twitter_username": "@jsmith",
    }
    data = {
        "page": "/artist/[slug]",
        "props": {
            "pageProps": {
                "artist": artist,
                "initArtistPageInfo": {
                    "about": {
                        "biography": {"text": "<p>Landscape painter.</p>"},
                        "contact_info": {
                            "website": "https://www.johnsmithart.co.uk",
                            "social_media": [
                                {"platform": "tiktok", "url": "https://www.tiktok.com/@johnsmith"}
                            ],
                        },
                    }
                },
                "initItems": [],
            }
        },
    }
    return f'<html><head><link rel="canonical" href="https://www.artfinder.com/artist/john-smith/"></head><body>{_next_data(data)}</body></html>'


def artmajeur_artist() -> str:
    profile = {
        "@context": "https://schema.org",
        "@type": "ProfilePage",
        "mainEntity": {
            "@type": "Person",
            "name": "Marie Curie",
            "gender": "https://schema.org/Female",
            "address": {
                "addressLocality": "Lyon",
                "addressRegion": "Auvergne-Rhone-Alpes",
                "postalCode": "69001",
                "addressCountry": "FR",
            },
            "sameAs": [
                "https://www.artmajeur.com/marie-curie",
                "https://www.instagram.com/marie.paints",
                "https://www.mariecurie-art.fr",
                "https://www.facebook.com/mariepaints",
            ],
        },
    }
    return (
        "<html><head><title>Marie Curie | Artmajeur</title>"
        '<link rel="canonical" href="https://www.artmajeur.com/marie-curie">'
        f'<script type="application/ld+json">{json.dumps(profile)}</script>'
        '<script>window.dataLayer.push({"route": "profile_index", "account_artmajeurId": "777"});</script>'
        '</head><body><div id="presentation">'
        '<img alt="Marie Curie Portrait" src="https://cdn.artmajeur.com/portrait.jpg">'
        "<div><h3>Biography</h3><p>First paragraph.</p><p>Second paragraph.</p></div>"
        "<ul><li><strong>Nationality:</strong> French</li><li><strong>Date of birth :</strong> 1980</li></ul>"
        "</div></body></html>"
    )


def artsper_artist() -> str:
    person = {
        "@context": "https://schema.org",
        "@type": "Person",
        "name": "Luca Rossi",
        "url": "https://www.artsper.com/us/contemporary-artists/italy/555/luca-rossi",
        "nationality": "Italy",
        "birthDate": "1975",
        "image": "https://media.artsper.com/luca.jpg",
        "description": "Short bio.",
        "sameAs": ["https://instagram.com/lucarossi", "https://lucarossi.it"],
    }
    return (
        f'<html><head><script type="application/ld+json">{json.dumps(person)}</script></head><body>'
        '<div id="page-context" data-artist-id="555"></div><main>'
        '<div class="about__block__item"><div class="about__block__item__title">Nationality</div>'
        '<div class="about__block__item__description">'
        '<a href="/us/contemporary-artists/italy">Italian</a></div></div>'
        '<div class="section-biography__biography"><div><p>Long bio.</p><p>More.</p></div></div>'
        "</main></body></html>"
    )


def artsper_listing() -> str:
    return '<html><body><div id="catalog-artists"></div></body></html>'
