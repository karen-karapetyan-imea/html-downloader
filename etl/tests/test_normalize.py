from datetime import date

import pytest

from artists.normalize import (
    Links,
    classify_social_links,
    clean_text,
    country_name,
    normalize_gender,
    plain_text,
    sane_year,
    to_url,
    username_to_url,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("", None),
        ("   ", None),
        ("None", None),
        ("unknown date", None),
        ("  Jane\u200b  Doe\xa0 ", "Jane Doe"),
        ("a\x00b", "ab"),
        (42, "42"),
    ],
)
def test_clean_text(value, expected):
    assert clean_text(value) == expected


def test_clean_text_multiline_keeps_paragraphs():
    assert clean_text(" one  two \n\n\n\n three ", multiline=True) == "one two\n\nthree"


def test_plain_text_strips_html_and_keeps_breaks():
    assert plain_text("<p>Painter &amp; sculptor.</p><p>Lives in <b>Paris</b>.</p>") == (
        "Painter & sculptor.\nLives in Paris."
    )
    assert plain_text("No tags here") == "No tags here"
    assert plain_text(None) is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1961, 1961),
        ("1961", 1961),
        ("c. 1650", 1650),
        ("b. 1975, Rome", 1975),
        ("unknown date", None),
        (999, None),
        (date.today().year + 1, None),
        (True, None),
        (None, None),
    ],
)
def test_sane_year(value, expected):
    assert sane_year(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [("https://schema.org/Female", "female"), ("Male", "male"), ("non-binary", "non-binary"), ("", None)],
)
def test_normalize_gender(value, expected):
    assert normalize_gender(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("FR", "France"),
        ("gb", "United Kingdom"),
        ("DEU", "Germany"),
        ("UK", "United Kingdom"),
        ("Germany", "Germany"),
        ("ZZ", "ZZ"),
        (None, None),
    ],
)
def test_country_name(value, expected):
    assert country_name(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://example.com/a", "https://example.com/a"),
        ("www.example.com", "https://www.example.com"),
        ("//cdn.example.com/x.jpg", "https://cdn.example.com/x.jpg"),
        ("mailto:jane@example.com", None),
        ("not a url", None),
        ("janedoe", None),
        (None, None),
    ],
)
def test_to_url(value, expected):
    assert to_url(value) == expected


@pytest.mark.parametrize(
    ("platform", "value", "expected"),
    [
        ("instagram", "@jane.paints", "https://www.instagram.com/jane.paints"),
        ("instagram", "instagram.com/jane", "https://instagram.com/jane"),
        ("twitter", "jsmith", "https://twitter.com/jsmith"),
        ("tiktok", "@jane", "https://www.tiktok.com/@jane"),
        ("facebook", "https://www.facebook.com/jane", "https://www.facebook.com/jane"),
        ("instagram", "bad handle!", None),
        ("instagram", "", None),
    ],
)
def test_username_to_url(platform, value, expected):
    assert username_to_url(platform, value) == expected


def test_classify_social_links():
    links = classify_social_links(
        [
            "https://www.saatchiart.com/jane",
            "https://pinterest.com/jane",
            "https://m.facebook.com/jane",
            "instagram.com/jane",
            "https://x.com/jane",
            "https://www.tiktok.com/@jane",
            "https://jane-art.com",
            "https://second-site.com",
            "https://www.instagram.com/other",
            None,
        ]
    )
    assert links == Links(
        website="https://jane-art.com",
        facebook="https://m.facebook.com/jane",
        instagram="https://instagram.com/jane",
        tiktok="https://www.tiktok.com/@jane",
        twitter="https://x.com/jane",
    )


def test_links_fill_from_keeps_own_values():
    own = Links(website="https://a.com", instagram=None)
    other = Links(website="https://b.com", instagram="https://instagram.com/x")
    assert own.fill_from(other) == Links(website="https://a.com", instagram="https://instagram.com/x")
