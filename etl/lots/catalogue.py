"""Facts read from a lot's catalogue text: size, medium, year, signature, and the artist line.

Auction catalogues write these the same way across houses: "oil on canvas", "30 x 40 in.
(76.2 x 101.6 cm.)", "signed and dated 1965 (lower right)", "Paule GOBILLARD (1869-1946)".
Sizes are height x width (x depth), the auction convention. Each function returns None when
the text does not say; a parser's structured value always wins over these.
"""

from __future__ import annotations

import re
from datetime import date

_FRACTIONS = {"⅛": 0.125, "¼": 0.25, "⅜": 0.375, "½": 0.5, "⅝": 0.625, "¾": 0.75, "⅞": 0.875}
_NUM = r"\d+(?:[.,]\d+)?(?:\s*[⅛¼⅜½⅝¾⅞]|\s+\d/\d{1,2})?"
_UNIT = r"(cm|mm|in(?:ch(?:es)?)?\b|\")"
_BY = r"(?:x|×|by)"
_SIZE = re.compile(rf"({_NUM})\s*{_BY}\s*({_NUM})(?:\s*{_BY}\s*({_NUM}))?\s*{_UNIT}", re.I)
_HEIGHT = re.compile(rf"\b(?:h|ht|haut|hauteur|height)\s*\.?\s*:?\s*({_NUM})\s*{_UNIT}?", re.I)
_WIDTH = re.compile(rf"\b(?:w|l|width|larg|largeur)\s*\.?\s*:?\s*({_NUM})\s*{_UNIT}?", re.I)
_LATER_UNIT = re.compile(_UNIT, re.I)
_TO_CM = {"cm": 1.0, "mm": 0.1, "in": 2.54}
_MIN_CM, _MAX_CM = 0.3, 2000.0

_MEDIUM_TERMS = (
    r"oil|acrylic|watercolou?r|gouache|tempera|pastel|charcoal|graphite|pencil|crayon|ink|pen|"
    r"bodycolou?r|sepia|inkjet|albumen|offset|"
    r"mixed media|collage|lithograph|etching|engraving|aquatint|drypoint|screenprint|silkscreen|"
    r"serigraph|woodcut|linocut|monotype|gicl[ée]e|photograph|gelatin silver|chromogenic|c-print|"
    r"pigment print|bronze|marble|terracotta|on canvas|on paper|on panel|on board|on linen|"
    r"huile|acrylique|aquarelle|fusain|encre|technique mixte|lithographie|eau-forte|gravure|"
    r"s[ée]rigraphie|bois grav[ée]|tirage|marbre|terre cuite|sur toile|sur papier|sur panneau|"
    r"sur carton|olio|tela|acquerello|tecnica mista"
)
_MEDIUM = re.compile(rf"\b(?:{_MEDIUM_TERMS})\b", re.I)
# A medium line starts with the medium ("Colour lithograph", "Oil on canvas"). Free-text listings
# only mention one ("Ben Farrell Oil Painting", "Autographed in silver ink").
_MEDIUM_START = re.compile(
    r"^(?:(?:colou?r(?:ed)?|original|vintage|unique|hand[- ]colou?red|patinated|gilt|polychrome|"
    r"opaque|copperplate|copper|steel|cast|india|indian|black|white|digital|warm-toned|matt|"
    r"relief-printed)\s+)*"
    rf"(?:{_MEDIUM_TERMS})\b",
    re.I,
)
_NOT_MEDIUM = re.compile(
    r"^(?:signed|signé|signée|inscribed|dated|daté|datée|titled|titré|stamped|numbered|numéroté|"
    r"photograph of|tirage (?:limité|à|de))",  # "tirage limité à 100": the edition size
    re.I,
)
_SEGMENTS = re.compile(r"\n|(?<=[.;])\s+")
_MAX_MEDIUM = 80

_Y = r"(1[4-9]\d\d|20[0-2]\d)"
_MADE = re.compile(
    r"\b(?:painted|executed|drawn|conceived|cast|carved|printed|photographed|created|realised|realized|"
    r"peinte?|réalisée?|exécutée?|dipinto|eseguito)\s+(?:in\s+|en\s+|nel\s+)?(?:circa\s+|c\.\s*|vers\s+)?"
    + _Y,
    re.I,
)
_DATED = re.compile(r"\b(?:dated|daté|datée|datés|datées|datato)\b[^\d\n]{0,40}?" + _Y, re.I)
_TITLE_YEAR = re.compile(r",\s*" + _Y + r"\s*$")

_UNSIGNED = re.compile(r"\b(?:unsigned|not signed|non signée?s?|non firmato)\b", re.I)
_SIGNED = re.compile(
    r"\b(?:signed|signée?s?|signé|firmato|firmata|signiert|monogrammed|monogrammée?)\b", re.I
)

_LAST_PARENS = re.compile(r"\(([^()]*)\)\s*$")
_LIFE = re.compile(
    r"(1[2-9]\d\d|20[0-2]\d)(?:/\d{1,2})?\s*[-–]\s*(?:after\s+|circa\s+|c\.\s*|vers\s+)?(1[2-9]\d\d|20[0-2]\d)"
)
_BORN = re.compile(r"(?:\bb\.|\bborn|\bn[ée]e?\s+en|\bgeb\.?)\s*(?:in\s+)?(1[2-9]\d\d|20[0-2]\d)", re.I)
_NATIONALITY = re.compile(r"^[A-Z][A-Za-zÀ-ſ\-/ ]{2,30}$")
# How nationalities end (Italian, Chinese, Polish, French, Swiss, Greek, Française, Allemand);
# birthplaces in the same slot ("Lyon, 1814-1892", "Antwerp 1581") do not. Endings in -i are
# mostly towns (Napoli, Shanghai), so those nationalities are listed; -ich is Munich, Zurich.
_DEMONYM = re.compile(r"(?:an|ane|ain|aine|ien|ienne|ese|ais|aise|ois|oise|ish|ch|ss|isse|ek|and|ande)$")
_DEMONYMS_I = frozenset(
    {"thai", "iraqi", "israeli", "pakistani", "bangladeshi", "kuwaiti", "saudi", "somali", "omani"}
    | {"yemeni", "emirati", "nepali", "qatari"}
)
_NOT_DEMONYMS = frozenset({"milan", "tehran", "teheran", "isfahan", "abidjan", "yerevan"})


def size_cm(text: str | None) -> tuple[float | None, float | None, float | None]:
    """(height, width, depth) in cm. A metric size wins over an imperial one in the same text."""
    if not text:
        return None, None, None
    sizes = [m for m in _SIZE.finditer(text) if _unit(m.group(4))]
    metric = [m for m in sizes if _unit(m.group(4)) != "in"]
    for match in metric or sizes:
        factor = _TO_CM[_unit(match.group(4))]
        values = [_cm(match.group(i), factor) for i in (1, 2, 3)]
        if values[0] is not None and values[1] is not None:
            return values[0], values[1], values[2]
    return _labelled(text, _HEIGHT), _labelled(text, _WIDTH), None


def medium(text: str | None) -> str | None:
    """The first short catalogue line (or sentence) that starts with a medium, cut before any
    size. Within a line, the medium starts at the first comma-separated part that opens with one
    ("Couple de mariés, huile sur toile") and runs while the parts still name one, so
    "Oil on canvas, signed lower right" is "Oil on canvas"."""
    if not text:
        return None
    for segment in _SEGMENTS.split(text):
        parts = [part.strip(" .;:(") for part in segment.split(",")]
        start = next((i for i, part in enumerate(parts) if _is_medium_start(part)), None)
        if start is None:
            continue
        kept = [parts[start]]
        for part in parts[start + 1 :]:
            if not part or _NOT_MEDIUM.match(part) or not _MEDIUM.search(part):
                break
            kept.append(part)
        line = re.split(r"\s*\d", ", ".join(kept), maxsplit=1)[0].strip(" .;,:(")
        if 3 <= len(line) <= _MAX_MEDIUM and _MEDIUM.search(line):
            return line
    return None


def year(text: str | None, title: str | None = None, born: int | None = None) -> int | None:
    """Year the work was made: "painted in 1965", "dated 1879", or a title ending ", 1986"."""
    found = None
    for pattern in (_MADE, _DATED):
        match = pattern.search(text or "")
        if match:
            found = int(match.group(1))
            break
    if found is None and title:
        match = _TITLE_YEAR.search(title)
        found = int(match.group(1)) if match else None
    if found is None or found > date.today().year or (born is not None and found < born + 5):
        return None
    return found


def is_signed(text: str | None) -> bool | None:
    if not text:
        return None
    if _UNSIGNED.search(text):
        return False
    return True if _SIGNED.search(text) else None


def life(artist: str | None) -> tuple[int | None, int | None, str | None]:
    """(birth, death, nationality) from the closing parentheses of an artist line:
    "(1887-1985)", "(American, 1860-1954)", "(b. 1932)", "(né en 1949)", "(Antwerp 1581 - 1641)"."""
    match = _LAST_PARENS.search(artist or "")
    if match is None:
        return None, None, None
    inside = match.group(1)
    born = died = None
    span = _LIFE.search(inside)
    if span:
        born, died = int(span.group(1)), int(span.group(2))
        if not 15 <= died - born <= 110:
            born = died = None
    else:
        start = _BORN.search(inside)
        born = int(start.group(1)) if start else None
    if born is None:
        return None, None, None
    first = inside.split(",", 1)[0]
    return born, died, nationality(first) if "," in inside else None


def nationality(text: str | None) -> str | None:
    """The text when it reads like a nationality ("French", "South African", "British-Nigerian"):
    short, capitalised, Latin letters, ending like a demonym. Callers check it is not the
    artist's name."""
    text = (text or "").strip(" ,.")
    if not _NATIONALITY.match(text) or _BORN.match(text):
        return None
    last = re.split(r"[\s\-/]+", text)[-1].lower()
    if last in _DEMONYMS_I:
        return text
    if not _DEMONYM.search(last) or last.endswith("ich") or last in _NOT_DEMONYMS:
        return None
    return text


def _is_medium_start(part: str) -> bool:
    return bool(part) and not _NOT_MEDIUM.match(part) and _MEDIUM_START.match(part) is not None


def _unit(raw: str | None) -> str | None:
    if not raw:
        return None
    raw = raw.lower()
    return "in" if raw.startswith("in") or raw == '"' else raw


def _number(raw: str) -> float | None:
    raw = raw.strip()
    fraction = 0.0
    if raw[-1] in _FRACTIONS:
        fraction, raw = _FRACTIONS[raw[-1]], raw[:-1].strip()
    elif "/" in raw:
        whole, part = raw.rsplit(None, 1)
        top, bottom = part.split("/")
        fraction, raw = int(top) / int(bottom) if int(bottom) else 0.0, whole
    if not raw:
        return fraction or None
    if re.fullmatch(r"\d+,\d{3}", raw):  # 1,200: thousands
        raw = raw.replace(",", "")
    try:
        return float(raw.replace(",", ".")) + fraction
    except ValueError:
        return None


def _cm(raw: str | None, factor: float) -> float | None:
    if raw is None:
        return None
    number = _number(raw)
    if number is None:
        return None
    value = round(number * factor, 1)
    return value if _MIN_CM <= value <= _MAX_CM else None


def _labelled(text: str, pattern: re.Pattern[str]) -> float | None:
    match = pattern.search(text)
    if match is None:
        return None
    unit = _unit(match.group(2))
    if unit is None:
        later = _LATER_UNIT.search(text, match.end(), match.end() + 40)
        unit = _unit(later.group(1)) if later else None
    return _cm(match.group(1), _TO_CM[unit]) if unit else None
