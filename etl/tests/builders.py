"""Minimal synthetic HTML pages in the shape each platform parser expects, and crawl folders of them."""

import json
from pathlib import Path


def make_crawl(root: Path, crawl_date: str, pages: dict[str, str], log: list[dict] | None = None) -> Path:
    data = root / crawl_date
    for name, html in pages.items():
        path = data / "html" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html, encoding="utf-8")
    if log:
        (data / "results.jsonl").write_text("\n".join(json.dumps(r) for r in log) + "\nnot json\n")
    return data


def write_crawl_manifest(
    data: Path, *, status: str = "completed", finished_at: str | None = "2026-09-23T12:00:00Z"
) -> None:
    """The downloader's manifest.json next to html/ (only the fields `sync` reads matter)."""
    manifest = {
        "marketplace": data.parent.name,
        "crawl_date": data.name,
        "started_at": "2026-09-23T08:00:00Z",
        "finished_at": finished_at,
        "status": status,
    }
    data.mkdir(parents=True, exist_ok=True)
    (data / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def saatchi_pages(city: str = "Berlin") -> dict[str, str]:
    return {
        "a.html": saatchi_artist(1, city=city),
        "b.html": saatchi_artist(2, city=city),
        "x/c.html": saatchi_artwork(),
        "x/d.html": saatchi_broken(),
        "x/e.html": saatchi_artist(1, city=city),
    }


def _next_data(payload: dict) -> str:
    return f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(payload)}</script>'


def saatchi_card(artwork_id: int = 5, user_id: int = 123, *, list_price_cents: int = 100000) -> dict:
    """An artwork card as embedded in a Saatchi artist profile."""
    return {
        "artworkID": artwork_id,
        "title": "Blue",
        "pdpUrl": f"/art/Painting-Blue/{user_id}/{artwork_id + 1000}/view",
        "category": "Painting",
        "subject": "Abstract",
        "widthInCentimeters": 50,
        "heightInCentimeters": 70,
        "depthInCentimeters": 2,
        "originalStatus": "avail",
        "listPrice": list_price_cents,
        "artworkImage": "//images.saatchiart.com/blue-card.jpg",
        "styles": ["Abstract"],
        "mediums": ["Oil", "Acrylic"],
    }


def saatchi_artist(
    user_id: int = 123,
    *,
    city: str = "Berlin",
    about: str | None = None,
    artworks: list[dict] | None = None,
) -> str:
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
        "artworks": artworks or [],
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


def saatchi_artwork(*, server_ms: int | None = None) -> str:
    artwork = {
        "artworkId": 5,
        "userId": 123,
        "title": "Blue",
        "slug": "Painting-Blue",
        "category": "Painting",
        "subject": "Abstract",
        "description": "<p>Blue study.</p>",
        "yearProduced": 2021,
        "styles": ["Abstract"],
        "mediums": ["Oil"],
        "artworkImage": {"imageUrl": "https://images.saatchiart.com/blue.jpg"},
        "products": [
            {
                "isOriginal": True,
                "sku": "P1",
                "isAvailableForSale": True,
                "original": {"listPrice": 150000, "shippingCountry": "DE"},
            }
        ],
    }
    pdp = {
        "artworkOriginalUrl": "https://www.saatchiart.com/art/Painting-Blue/123/1005/view",
        "originalArtworkStatus": "avail",
        "dimensions": {"widthInCentimeters": 50, "heightInCentimeters": 70, "depthInCentimeters": 2},
    }
    shared = {"serverTimestampInMilliseconds": server_ms} if server_ms else {}
    data = {
        "props": {
            "pageProps": {
                "initialProps": {"metaData": {}},
                "initialState": {
                    "page": {
                        "data": {
                            "artwork": artwork,
                            "pdpArtwork": pdp,
                            "artist": {"userId": 123, "firstName": "Jane", "lastName": "Doe"},
                        }
                    },
                    "shared": shared,
                },
            }
        }
    }
    return f"<html><body>{_next_data(data)}</body></html>"


def saatchi_broken() -> str:
    return '<html><body><script id="__NEXT_DATA__" type="application/json">{not json</script></body></html>'


def _artsy_page(query_id: str, data: dict, canonical: str, head: str = "") -> str:
    responses = [[json.dumps({"queryID": query_id, "variables": {}}), {"json": {"data": data}}]]
    payload = json.dumps(json.dumps(responses))
    return (
        f'<html><head>{head}<link rel="canonical" href="{canonical}"></head>'
        f"<body><script>var __RELAY_HYDRATION_DATA__ = {payload};</script></body></html>"
    )


def artsy_artwork() -> str:
    artwork = {
        "slug": "andy-warhol-flowers",
        "internalID": "aw1",
        "title": "Flowers",
        "date": "1970",
        "href": "/artwork/andy-warhol-flowers",
        "artistNames": "Andy Warhol",
        "category": "Print",
        "medium": "Screenprint on paper",
        "dimensions": {"in": "36 x 36 in", "cm": "91.4 x 91.4 cm"},
        "widthCm": 91.4,
        "heightCm": 91.4,
        "listPrice": {"major": 25000, "currencyCode": "USD"},
        "availability": "for sale",
        "isSold": False,
        "shippingOrigin": "New York, NY, US",
        "descriptionHTML": "<p>Bright <b>flowers</b>.</p>",
        "hasCertificateOfAuthenticity": True,
        "signatureInfo": {"details": "Hand-signed by artist"},
        "image": {"resized": {"src": "https://d32dm0rphc51dk.cloudfront.net/flowers.jpg"}},
        "artists": [{"internalID": "4d8b92b34eb68a1b2c0003f4", "slug": "andy-warhol", "name": "Andy Warhol"}],
    }
    return _artsy_page(
        "artworkRoutes_ArtworkQuery",
        {"artworkResult": artwork},
        "https://www.artsy.net/artwork/andy-warhol-flowers",
    )


def artsy_artist(notable: list[dict] | None = None) -> str:
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
        "notableArtworks": notable or [],
    }
    return _artsy_page(
        "artistRoutes_ArtistAppQuery",
        {"artist": artist},
        "https://www.artsy.net/artist/andy-warhol",
        '<meta property="og:image" content="https://d32dm0rphc51dk.cloudfront.net/warhol.jpg">',
    )


def artfinder_card(artwork_id: int, *, usd: float = 300) -> dict:
    """An artwork card as listed on an Artfinder artist shop grid."""
    return {
        "id": artwork_id,
        "artist_id": 42,
        "artist_slug": "john-smith",
        "artist_name": "John Smith",
        "name": f"Hill {artwork_id}",
        "slug": f"hill-{artwork_id}",
        "category_slug": "painting",
        "style_slug": "impressionism",
        "is_in_stock": True,
        "prices": {"USD": usd, "GBP": usd * 0.75},
        "dimensions": {"units": "cm", "width": 30, "height": 40, "depth": 2},
        "images": [{"path": f"https://d2m7ibezl7l5lt.cloudfront.net/img/hill-{artwork_id}.jpg"}],
    }


def artfinder_artwork() -> str:
    product = {
        "id": 9001,
        "artist_id": 42,
        "name": "Sunset",
        "slug": "sunset",
        "full_url": "https://www.artfinder.com/product/sunset/",
        "category_slug": "oil-painting",
        "category_full_name": "Oil painting",
        "style_name": "Impressionistic",
        "subject_name": "Landscape",
        "description": "<p>Warm evening.</p>",
        "year_made": 2022,
        "substrate": "Canvas",
        "materials": "Oil paint",
        "signature_type_name": "Signed on the front",
        "dimensions_text_cm": "50 x 40 x 2cm (unframed)",
        "dimensions_text_in": "19.7 x 15.7 x 0.8in",
        "original_currency": "gbp",
        "original_currency_amount": "450.00",
        "pricing": {"USD": {"current_amount": 600}},
        "is_in_stock": True,
        "images": [{"url": "https://d2m7ibezl7l5lt.cloudfront.net/img/sunset.jpg"}],
    }
    data = {
        "page": "/product/[slug]",
        "props": {"pageProps": {"product": product, "artist": {"id": 42, "name": "John Smith"}}},
    }
    return f"<html><head></head><body>{_next_data(data)}</body></html>"


def artfinder_artist(items: list[dict] | None = None) -> str:
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
                "initItems": items or [],
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


def artmajeur_artwork() -> str:
    product = {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": "Blue Sea",
        "productId": "901",
        "offers": {"price": 1200},
        "creator": {"@type": "Person", "name": "Marie Curie"},
        "image": [{"contentUrl": "https://cdn.artmajeur.com/blue-sea.jpg"}],
    }
    data_layer = {
        "route": "profile_artwork",
        "artwork_id": 901,
        "account_artmajeurId": "777",
        "profile_artwork_category": "painting",
        "profile_artwork_style": "Abstract",
        "profile_artwork_theme": "Seascape",
    }
    return (
        "<html><head>"
        '<link rel="canonical" href="https://www.artmajeur.com/marie-curie/en/artworks/901/blue-sea">'
        f'<script type="application/ld+json">{json.dumps(product)}</script>'
        f"<script>window.dataLayer.push({json.dumps(data_layer)});</script>"
        '</head><body><div id="artwork_pane">'
        "<h1><span>Blue Sea</span> <span>(2020)</span></h1>"
        '<ul class="list-unstyled">'
        "<li><strong>Original Artwork (One Of A Kind)</strong> Painting, Oil on Canvas</li>"
        "<li><strong>Dimensions</strong> Height 50 cm, Width 40 cm, Depth 2 cm</li>"
        "</ul>"
        '<div class="row text-center small">'
        "<div>Signed artwork</div><div>Certificate of authenticity</div></div>"
        '<div id="productTypesContent"><div id="original">'
        '<span data-analytics-price="1,200">$1,200</span> Shipping from <strong>France</strong>'
        "</div></div>"
        '<div id="collapsed_artwork_about_container"><div id="full_description_text">Calm waves.</div></div>'
        "</div></body></html>"
    )


def artsper_card(artwork_id: int, *, price: str = "$2,557") -> str:
    """An artwork card from the artwork grid of an Artsper artist page."""
    return (
        f'<article class="card-artwork" data-id="{artwork_id}" '
        f'data-url="/us/contemporary-artworks/painting/{artwork_id}/red">'
        f'<img class="card-artwork__image" data-src="https://media.artsper.com/{artwork_id}.jpg">'
        '<p class="card-artwork__title" title="Red">Red</p>'
        '<p class="measure--cm">Painting . 85 x 85 x 4 cm</p>'
        '<p class="measure--inch">Painting . 33.5 x 33.5 x 1.6 in</p>'
        f'<p class="card-artwork__price">{price}</p>'
        "</article>"
    )


def artsper_artist(cards: list[str] | None = None) -> str:
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
        f"{''.join(cards or [])}"
        "</main></body></html>"
    )


def artsper_listing() -> str:
    return '<html><body><div id="catalog-artists"></div></body></html>'
