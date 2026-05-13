"""Idempotenter Ingest für Referenzdaten und Methoden-YAMLs.

Leitideen:
  * Re-runs überschreiben kein Menschen-Reviewtes. Wenn eine Methode
    in der DB bereits ``quality_status = human_reviewed`` oder höher hat,
    ändern wir sie per Default nicht — außer ``--force`` wird gesetzt.
  * Referenzdaten (phases, settings, ...) sind idempotent per ``code``.
  * Methoden werden per ``slug`` identifiziert.
  * Kinder-Collections (Synonyme, Phasen-Links, Sources, Steps,
    Variations, Pitfalls) werden auf den YAML-Stand synchronisiert —
    nicht dazugemergt, sondern vollständig gesetzt. Das vermeidet
    Karteileichen (z.B. wenn ein Schritt in der YAML entfernt wurde).
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import yaml
from rich.console import Console
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    GroupSize,
    Method,
    MethodGroupSize,
    MethodPhase,
    MethodPitfall,
    MethodSetting,
    MethodSource,
    MethodStep,
    MethodSynonym,
    MethodTargetAudience,
    MethodType,
    MethodTypeLink,
    MethodVariation,
    Phase,
    Setting,
    Source,
    TargetAudience,
)
from .schemas import MethodIn, ReferenceItem, SourceRef

console = Console()

REFERENCE_FILES: dict[str, tuple[type, Path]] = {
    "phases": (Phase, Path("data/seed/phases.yaml")),
    "settings": (Setting, Path("data/seed/settings.yaml")),
    "group_sizes": (GroupSize, Path("data/seed/group_sizes.yaml")),
    "target_audiences": (TargetAudience, Path("data/seed/target_audiences.yaml")),
    "method_types": (MethodType, Path("data/seed/method_types.yaml")),
}

# Welche Felder pro Referenztabelle gültig sind (gegen Schreibfehler in YAMLs).
_REF_FIELDS: dict[type, tuple[str, ...]] = {
    Phase: ("code", "label_de", "label_en", "description", "sort_order"),
    Setting: ("code", "label_de", "label_en", "description"),
    GroupSize: ("code", "label_de", "min_participants", "max_participants", "sort_order"),
    TargetAudience: ("code", "label_de", "description"),
    MethodType: ("code", "label_de", "description"),
}


def _load_yaml(path: Path) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(f"Seed-Datei fehlt: {path}")
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if data is None:
        return []
    if not isinstance(data, list):
        raise ValueError(f"{path} muss eine Liste von Einträgen enthalten")
    return data


def upsert_reference(session: Session, model_cls: type, rows: Iterable[dict]) -> tuple[int, int]:
    """Idempotenter Upsert anhand der Spalte ``code``. Return (neu, aktualisiert)."""
    allowed = set(_REF_FIELDS[model_cls])
    created = updated = 0
    for raw in rows:
        # Validiere & kürze auf erlaubte Felder
        item = ReferenceItem.model_validate(raw)
        payload = {k: v for k, v in item.model_dump().items() if k in allowed}

        existing = session.execute(
            select(model_cls).where(model_cls.code == item.code)  # type: ignore[attr-defined]
        ).scalar_one_or_none()

        if existing is None:
            session.add(model_cls(**payload))
            created += 1
        else:
            dirty = False
            for key, value in payload.items():
                if getattr(existing, key) != value:
                    setattr(existing, key, value)
                    dirty = True
            if dirty:
                updated += 1
    return created, updated


def seed_references(session: Session, base_dir: Path = Path(".")) -> None:
    for name, (model_cls, rel_path) in REFERENCE_FILES.items():
        path = base_dir / rel_path
        rows = _load_yaml(path)
        new, upd = upsert_reference(session, model_cls, rows)
        console.print(
            f"[green]{name:<18}[/green] neu={new} aktualisiert={upd} gesamt={len(rows)}"
        )


# --------------------- Methoden-Ingest -----------------------------------


def _lookup_phase(session: Session, code: str) -> Phase:
    obj = session.execute(select(Phase).where(Phase.code == code)).scalar_one_or_none()
    if obj is None:
        raise ValueError(f"Unbekannte Phase: {code!r} (seed-refs vergessen?)")
    return obj


def _lookup_setting(session: Session, code: str) -> Setting:
    obj = session.execute(select(Setting).where(Setting.code == code)).scalar_one_or_none()
    if obj is None:
        raise ValueError(f"Unbekanntes Setting: {code!r}")
    return obj


def _lookup_group_size(session: Session, code: str) -> GroupSize:
    obj = session.execute(
        select(GroupSize).where(GroupSize.code == code)
    ).scalar_one_or_none()
    if obj is None:
        raise ValueError(f"Unbekannte Gruppengröße: {code!r}")
    return obj


def _lookup_audience(session: Session, code: str) -> TargetAudience:
    obj = session.execute(
        select(TargetAudience).where(TargetAudience.code == code)
    ).scalar_one_or_none()
    if obj is None:
        raise ValueError(f"Unbekannte Zielgruppe: {code!r}")
    return obj


def _lookup_method_type(session: Session, code: str) -> MethodType:
    obj = session.execute(
        select(MethodType).where(MethodType.code == code)
    ).scalar_one_or_none()
    if obj is None:
        raise ValueError(f"Unbekannter Methoden-Typ: {code!r}")
    return obj


def _source_note(sref: SourceRef) -> str | None:
    """YAML hat ``note`` (neu) und ``notes`` (Alt). Beides erlauben, erstes
    gewinnt. Ergebnis landet in der DB-Spalte ``notes``.
    """
    return sref.note or sref.notes


def _get_or_create_source(session: Session, sref: SourceRef) -> Source:
    """Matcht primär über (title, author, year). DOI/URL als Fallback.

    DOI ist der zuverlässigste Identifikator wenn vorhanden — deshalb
    zuerst darauf matchen. Sonst title+author+year.
    """
    if sref.doi:
        existing = session.execute(
            select(Source).where(Source.doi == sref.doi)
        ).scalar_one_or_none()
        if existing:
            return existing

    q = select(Source).where(Source.title == sref.title)
    if sref.author is not None:
        q = q.where(Source.author == sref.author)
    if sref.year is not None:
        q = q.where(Source.year == sref.year)
    existing = session.execute(q).scalar_one_or_none()
    if existing:
        # DOI/URL nachziehen, falls beim ersten Anlegen noch unbekannt
        if sref.doi and not existing.doi:
            existing.doi = sref.doi
        if sref.url and not existing.url:
            existing.url = sref.url
        return existing

    src = Source(
        kind=sref.kind,
        title=sref.title,
        author=sref.author,
        year=sref.year,
        isbn=sref.isbn,
        url=sref.url,
        doi=sref.doi,
        notes=_source_note(sref),
    )
    session.add(src)
    session.flush()
    return src


def upsert_method(session: Session, data: MethodIn, *, force: bool = False) -> str:
    """Upsert einer Methode anhand ``slug``. Return: 'created' | 'updated' | 'skipped'."""
    existing = session.execute(
        select(Method).where(Method.slug == data.slug)
    ).scalar_one_or_none()

    if existing and not force and existing.quality_status in (
        "human_reviewed",
        "expert_verified",
    ):
        console.print(
            f"[yellow]skip[/yellow] {data.slug} — quality_status={existing.quality_status} "
            f"(nutze --force zum Überschreiben)"
        )
        return "skipped"

    method = existing or Method(slug=data.slug)
    method.canonical_name = data.canonical_name
    method.short_description = data.short_description
    method.long_description = data.long_description
    method.originator = data.originator
    method.origin_year = data.origin_year
    method.origin_context = data.origin_context
    method.duration_min_minutes = data.duration_min_minutes
    method.duration_typical_minutes = data.duration_typical_minutes
    method.duration_max_minutes = data.duration_max_minutes
    method.materials = list(data.materials) or None
    method.tags = list(data.tags) or None
    method.quality_status = data.quality_status
    method.content_license = data.content_license
    method.content_author = data.content_author
    method.stage_2_status = data.stage_2_status
    method.stage_2_author = data.stage_2_author

    if existing is None:
        session.add(method)
        session.flush()  # für method.id

    # Children: vollständig synchronisieren.
    # Wichtig: erst leeren + flushen (löst die DELETEs der alten Rows aus,
    # cascade=delete-orphan), DANN die neuen Items zuweisen. Sonst versucht
    # SQLAlchemy beim Autoflush, neue INSERTs vor den alten DELETEs zu
    # senden — das verletzt UNIQUE-Constraints wie ``uq_method_synonym``,
    # wenn neuer und alter Wert identisch sind.
    if existing is not None:
        method.synonyms.clear()
        method.phase_links.clear()
        method.setting_links.clear()
        method.group_size_links.clear()
        method.audience_links.clear()
        method.type_links.clear()
        method.steps.clear()
        method.variations.clear()
        method.pitfalls.clear()
        method.sources.clear()
        session.flush()  # DELETEs sofort committen, BEVOR neue INSERTs kommen.

    method.synonyms = [
        MethodSynonym(synonym=s.synonym, language=s.language) for s in data.synonyms
    ]
    method.phase_links = [
        MethodPhase(phase=_lookup_phase(session, p.code), is_primary=p.is_primary)
        for p in data.phases
    ]
    method.setting_links = [
        MethodSetting(
            setting=_lookup_setting(session, s.code),
            suitability=s.suitability,
            suitability_note=s.note,
        )
        for s in data.settings
    ]
    method.group_size_links = [
        MethodGroupSize(group_size=_lookup_group_size(session, c)) for c in data.group_sizes
    ]
    method.audience_links = [
        MethodTargetAudience(audience=_lookup_audience(session, c)) for c in data.target_audiences
    ]
    method.type_links = [
        MethodTypeLink(method_type=_lookup_method_type(session, c)) for c in data.method_types
    ]
    method.steps = [
        MethodStep(
            order_index=s.order_index,
            label=s.label,
            duration_minutes=s.duration_minutes,
            trainer_action=s.trainer_action,
            participant_action=s.participant_action,
            note=s.note,
        )
        for s in data.steps
    ]
    method.variations = [
        MethodVariation(
            name=v.name,
            description=v.description,
            when_to_use=v.when_to_use,
            setting_codes=list(v.setting_codes) or None,
        )
        for v in data.variations
    ]
    method.pitfalls = [
        MethodPitfall(
            issue=p.issue,
            countermeasure=p.countermeasure,
            severity=p.severity,
            setting_codes=list(p.setting_codes) or None,
        )
        for p in data.pitfalls
    ]
    method.sources = [
        MethodSource(
            source=_get_or_create_source(session, sref),
            relation=sref.relation,
            page_reference=sref.page_reference,
        )
        for sref in data.sources
    ]

    return "updated" if existing else "created"


def ingest_methods_dir(session: Session, methods_dir: Path, *, force: bool = False) -> dict[str, int]:
    counts = {"created": 0, "updated": 0, "skipped": 0, "errors": 0}
    yaml_files = sorted(methods_dir.rglob("*.yaml")) + sorted(methods_dir.rglob("*.yml"))
    for path in yaml_files:
        with path.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        # Pro Methode ein SAVEPOINT — ein Fehler darf nicht alle nachfolgenden
        # Methoden vergiften (PendingRollbackError-Cascade). begin_nested()
        # rollt nur den inneren Block zurück, der äußere Transaktions-Rahmen
        # bleibt aktiv.
        try:
            with session.begin_nested():
                method_in = MethodIn.model_validate(raw)
                result = upsert_method(session, method_in, force=force)
            counts[result] += 1
            console.print(f"  [cyan]{result:>8}[/cyan]  {path.name}")
        except Exception as exc:  # noqa: BLE001
            counts["errors"] += 1
            # Erste Zeile der Exception reicht — die SQLAlchemy-Anhängsel
            # mit „Background on this error" und Stack sind nur Lärm.
            msg = str(exc).split("\n", 1)[0]
            console.print(f"  [red]   error[/red]  {path.name}: {msg}")
    return counts
