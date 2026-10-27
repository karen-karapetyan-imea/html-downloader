"""Work and artist detail read from auction catalogue text."""

import pytest

from lots import catalogue


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("30 x 40 in. (76.2 x 101.6 cm.)", (76.2, 101.6, None)),
        ("61 by 100.3 cm.; 24 by 39 1/2 in.", (61.0, 100.3, None)),
        ("76 × 101.6 × 12 cm", (76.0, 101.6, 12.0)),
        ("24 x 39½ in.", (61.0, 100.3, None)),
        ("H. : 76 cm ; L. : 91 cm", (76.0, 91.0, None)),
        ("no size here", (None, None, None)),
    ],
)
def test_size_cm(text, expected):
    assert catalogue.size_cm(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("signed lower right\noil on canvas\n61 by 100 cm.", "oil on canvas"),
        ("Huile sur toile, 73 x 92 cm", "Huile sur toile"),
        ("Tirage limité à 100 exemplaires", None),
        ("A pair of boots", None),
        ("Oil on canvas, signed lower right\n53 x 45 cm", "Oil on canvas"),
        ("Couple de mariés et enfant, huile sur toile, 40 x 30 cm", "huile sur toile"),
        ("oil, gouache and pencil on paper", "oil, gouache and pencil on paper"),
        ("Colour lithograph on wove paper", "Colour lithograph on wove paper"),
        ('Ben Farrell Oil Painting "Self Portrait"', None),  # a listing title that mentions a medium
        ("Autographed in silver ink by the artist", None),
        ("Photograph of the artist in his studio", None),
        ("pen and India ink on paper", "pen and India ink on paper"),
        ("Copperplate engraving", "Copperplate engraving"),
        ("Warm-toned matt gelatin silver print", "Warm-toned matt gelatin silver print"),
        ("A WHITE MARBLE BUST OF A ROMAN SENATOR", None),  # an object, not a medium line
        ("With numerous woodcut illustrations", None),
    ],
)
def test_medium(text, expected):
    assert catalogue.medium(text) == expected


def test_year():
    assert catalogue.year("signed and dated 1874 (lower left)") == 1874
    assert catalogue.year("painted in 1965") == 1965
    assert catalogue.year(None, "Composition, 1986") == 1986
    assert catalogue.year("dated 1850", born=1848) is None  # before the artist could paint


def test_is_signed():
    assert catalogue.is_signed("Signée en bas à droite") is True
    assert catalogue.is_signed("unsigned") is False
    assert catalogue.is_signed("oil on canvas") is None


@pytest.mark.parametrize(
    ("artist", "expected"),
    [
        ("Paule GOBILLARD (1869-1946)", (1869, 1946, None)),
        ("Jean DUPONT (Française, 1901-1980)", (1901, 1980, "Française")),
        ("Sam SMITH (American, b. 1962)", (1962, None, "American")),
        ("Louis JANMOT (Lyon, 1814 - 1892)", (1814, 1892, None)),  # a birthplace, not a nationality
        ("Franz MARC (Munich, 1880-1916)", (1880, 1916, None)),
        ("No dates", (None, None, None)),
    ],
)
def test_life(artist, expected):
    assert catalogue.life(artist) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("French", "French"),
        ("South African", "South African"),
        ("British-Nigerian", "British-Nigerian"),
        ("Hungarian,", "Hungarian"),
        ("Swiss", "Swiss"),
        ("Iraqi", "Iraqi"),
        ("Paris", None),
        ("Milan", None),
        ("Napoli", None),
        ("Zurich", None),
        ("oil on canvas", None),
        ("When I go to sleep I dream of you inside of me", None),
    ],
)
def test_nationality(text, expected):
    assert catalogue.nationality(text) == expected
