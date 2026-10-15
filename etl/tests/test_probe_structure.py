import importlib.util
import json
from pathlib import Path
from types import ModuleType

from tests import builders

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "probe_structure.py"


def _load_probe() -> ModuleType:
    spec = importlib.util.spec_from_file_location("probe_structure", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _artist_page() -> str:
    person = {"@context": "https://schema.org", "@type": "Person", "name": "Ana Lima", "birthDate": "1980"}
    state = {"props": {"pageProps": {"artist": {"id": 42, "country": "Brazil"}}}}
    return (
        f'<html><head><script type="application/ld+json">{json.dumps(person)}</script>'
        '<meta property="og:title" content="Ana Lima"></head><body>'
        '<h1 class="artist-name">Ana Lima</h1>'
        f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(state)}</script>'
        "<script>var a={b:1};</script><style>h1{}</style></body></html>"
    )


def test_probe_reports_structured_data_per_page_type(tmp_path, capsys):
    data = builders.make_crawl(
        tmp_path,
        "2026-10-14",
        {"a1.html": _artist_page(), "x.html": "<html><body><p>about</p></body></html>"},
        log=[
            {"filename": "a1.html", "url": "https://www.singulart.com/en/artist/ana-lima-42"},
            {"filename": "x.html", "url": "https://www.singulart.com/en/about"},
        ],
    )
    out = tmp_path / "probe"

    assert _load_probe().main(["--platform", "singulart", "--data", str(data), "--out", str(out)]) == 0

    report = (out / "singulart" / "report.txt").read_text()
    assert report == capsys.readouterr().out.removesuffix("\n")
    assert "# groups: artist=1, other=1" in report
    assert "    1  Person = Person" in report
    assert "    1  birthDate = 1980" in report
    assert "--- embedded json script#__NEXT_DATA__" in report
    assert "    1  props.pageProps.artist.country = Brazil" in report
    assert "    1  og:title = Ana Lima" in report
    assert "    1  artist-name = Ana Lima" in report

    example = (out / "singulart" / "examples" / "artist" / "a1.html").read_text()
    assert "__NEXT_DATA__" in example and "application/ld+json" in example
    assert "var a=" not in example and "<style>" not in example
