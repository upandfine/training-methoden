"""Pure-Python-Tests für die Such- und Embedding-Helfer.

Lebensraum dieser Tests: keine DB, kein OpenAI-Key. Wir testen nur die
Logik, die ohne externe Abhängigkeiten reproduzierbar ist:
  * ``_normalize``: Min-Max-Normalisierung mit Sonderfällen.
  * ``build_representational_text``: deterministische Text-Komposition
    für Embeddings — wir bauen dazu Fake-Method-Objekte mit den nötigen
    Attributen (kein SQLAlchemy-Mock).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest


def _normalize_or_skip():
    """Lade ``_normalize``, oder skippe falls API-Extras fehlen."""
    try:
        from methods.api import _normalize
    except ImportError as exc:
        pytest.skip(f"API-Deps fehlen: {exc}")
    return _normalize


# ---------------------------------------------------------------------------
# _normalize (aus methods.api)
# ---------------------------------------------------------------------------


def test_normalize_typical_range():
    _normalize = _normalize_or_skip()
    out = _normalize({"a": 0.0, "b": 5.0, "c": 10.0})
    assert out["a"] == pytest.approx(0.0)
    assert out["b"] == pytest.approx(0.5)
    assert out["c"] == pytest.approx(1.0)


def test_normalize_constant_values_become_one():
    _normalize = _normalize_or_skip()
    out = _normalize({"a": 0.7, "b": 0.7})
    # Wenn alle Werte gleich sind, sind alle gleichgut → 1.0.
    # (Alternative wäre 0.0; 1.0 erhält die Treffer in der Score-Fusion.)
    assert out == {"a": 1.0, "b": 1.0}


def test_normalize_empty():
    _normalize = _normalize_or_skip()
    assert _normalize({}) == {}


# ---------------------------------------------------------------------------
# build_representational_text (aus scripts/embed_methods.py)
# ---------------------------------------------------------------------------


def _ns(**kwargs):
    return SimpleNamespace(**kwargs)


@dataclass
class _FakeMethod:
    """Minimal-Stub für build_representational_text — keine ORM-Bindung."""

    canonical_name: str
    short_description: str | None = None
    originator: str | None = None
    tags: list[str] | None = None
    synonyms: list = field(default_factory=list)
    steps: list = field(default_factory=list)
    type_links: list = field(default_factory=list)
    phase_links: list = field(default_factory=list)
    setting_links: list = field(default_factory=list)
    audience_links: list = field(default_factory=list)
    group_size_links: list = field(default_factory=list)


def _import_builder():
    """Lade build_representational_text aus dem Skript ohne CLI-Side-Effects.

    Das Skript ruft beim Modulladen ``sys.path.insert`` auf — das ist hier OK,
    es macht das Package-Import nur sicherer.
    """
    import importlib.util
    import pathlib

    here = pathlib.Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location(
        "embed_methods", here / "scripts" / "embed_methods.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    # Vor exec_module registrieren: @dataclass im Skript sucht das Modul in sys.modules
    # (Python 3.12 wirft sonst AttributeError auf NoneType).
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod.build_representational_text


def test_representational_text_minimal():
    build = _import_builder()
    m = _FakeMethod(canonical_name="Brainwriting 6-3-5")
    out = build(m)
    assert "Brainwriting 6-3-5" in out
    # Kein optionales Feld → keine leeren Zeilen / Platzhalter
    assert "Auch bekannt als" not in out
    assert "Tags:" not in out


def test_representational_text_full():
    build = _import_builder()
    m = _FakeMethod(
        canonical_name="World Café",
        short_description="Wandernde Tisch-Gespräche zum gemeinsamen Verstehen.",
        originator="Juanita Brown, David Isaacs",
        tags=["dialog", "gross-gruppe"],
        synonyms=[_ns(synonym="Wissenscafé"), _ns(synonym="Café-Methode")],
        type_links=[
            _ns(method_type=_ns(label_de="Dialogisch")),
            _ns(method_type=_ns(label_de="Kollaborativ")),
        ],
        phase_links=[_ns(phase=_ns(label_de="Vertiefung"))],
        setting_links=[
            _ns(setting=_ns(label_de="Präsenz"), suitability="excellent"),
            _ns(setting=_ns(label_de="Remote-synchron"), suitability="limited"),
        ],
        audience_links=[_ns(audience=_ns(label_de="Unternehmen"))],
        group_size_links=[_ns(group_size=_ns(label_de="Groß"))],
        steps=[
            _ns(order_index=1, label="Tische gestalten"),
            _ns(order_index=2, label="Erste Runde"),
            _ns(order_index=3, label="Wandern & Vertiefen"),
        ],
    )
    out = build(m)

    # Wichtige Felder müssen alle drin sein
    assert "World Café" in out
    assert "Wissenscafé" in out
    assert "Café-Methode" in out
    assert "Dialogisch" in out and "Kollaborativ" in out
    assert "Vertiefung" in out
    # Limited-Setting wird ausgefiltert (nur excellent + good zählen)
    assert "Präsenz" in out
    assert "Remote-synchron" not in out
    assert "dialog" in out
    assert "Tische gestalten → Erste Runde → Wandern & Vertiefen" in out
    assert "Juanita Brown" in out


def test_representational_text_step_order():
    """Steps müssen unabhängig von Eingabe-Reihenfolge nach order_index sortiert sein."""
    build = _import_builder()
    m = _FakeMethod(
        canonical_name="Test",
        steps=[
            _ns(order_index=3, label="Drittens"),
            _ns(order_index=1, label="Erstens"),
            _ns(order_index=2, label="Zweitens"),
        ],
    )
    out = build(m)
    assert "Erstens → Zweitens → Drittens" in out
