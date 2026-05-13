"""FastAPI-Skelett für den Trainingsmethoden-Katalog.

Read-only-API in dieser ersten Iteration — ingest läuft weiterhin per CLI.
Design-Entscheidungen:

  * Pydantic-Response-Modelle sind getrennt von den YAML-Input-Schemas
    (``schemas.py``). Grund: Output hat andere Pflichtfelder als Input
    (z.B. ``id`` statt ``slug`` als PK, ausgepackte Referenz-Labels
    statt Codes).
  * Listen-Endpoint ist bewusst kompakt (``MethodListItem``), damit
    Listings in der späteren UI schnell laden. Volles Objekt mit
    steps/variations/pitfalls gibt es nur per slug.
  * Filter werden ODER-frei kombiniert (alle gesetzten Filter müssen
    matchen). Bei Settings/Audience/Type/GroupSize reicht es, wenn
    eine der übergebenen Codes matcht (IN).
  * Full-Text-Suche nutzt das ``ix_methods_fts_de`` (german_unaccent)
    plus Trigram-Fallback für kurze/fuzzy Queries.

Zum Lokalstart:
    uvicorn methods.api:app --reload
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session, selectinload

# Storage-Modus: "postgres" (Default, lokales Entwickeln) oder "snapshot"
# (Read-Only-Cloud-Deployment ohne DB). Wird beim Startup einmal evaluiert.
STORAGE_MODE = os.environ.get("STORAGE", "postgres").strip().lower()
SNAPSHOT_DIR = Path(os.environ.get("SNAPSHOT_DIR", "data/snapshot"))

# Wird beim Startup populiert, wenn STORAGE_MODE == "snapshot".
_snapshot_store_singleton = None  # type: ignore[var-annotated]


def _get_snapshot_store():
    """Lazy-Loader für den SnapshotStore — Singleton."""
    global _snapshot_store_singleton
    if _snapshot_store_singleton is None:
        from .snapshot_store import SnapshotStore

        _snapshot_store_singleton = SnapshotStore(SNAPSHOT_DIR)
    return _snapshot_store_singleton


def _is_snapshot() -> bool:
    return STORAGE_MODE == "snapshot"


from .db import SessionLocal
from .models import (
    GroupSize,
    Method,
    MethodGroupSize,
    MethodPhase,
    MethodSetting,
    MethodSource,
    MethodSynonym,
    MethodTargetAudience,
    MethodType,
    MethodTypeLink,
    Phase,
    Setting,
    TargetAudience,
)

app = FastAPI(
    title="Trainingsmethoden-Katalog API",
    version="0.2.0",
    description=(
        "Read-only-API für den Methoden-Katalog. "
        "Ingest läuft per CLI (`methods ingest data/methods`)."
    ),
)

# CORS — Origins kommen aus der Env-Variable CORS_ORIGINS (komma-separiert).
# Default "*" für Dev. In Prod ENV setzen auf konkrete Frontend-URLs, sonst
# kann jede beliebige Webseite die API ansprechen.
#   CORS_ORIGINS=https://methoden.example.com,https://staging.methoden.example.com
_cors_raw = os.environ.get("CORS_ORIGINS", "*").strip()
_cors_origins = (
    ["*"] if _cors_raw == "*" else [o.strip() for o in _cors_raw.split(",") if o.strip()]
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)

# Frontend-Static-Mount: das Single-Page-UI in browse/app/ wird unter /app
# direkt vom uvicorn ausgeliefert. Same-Origin → keine CORS-Probleme.
# ``check_dir=False``, weil die Datei ggf. erst zur Laufzeit angelegt wurde.
import os as _os

_browse_app = _os.path.join(
    _os.path.dirname(_os.path.dirname(_os.path.dirname(__file__))),
    "browse", "app",
)
if _os.path.isdir(_browse_app):
    app.mount("/app", StaticFiles(directory=_browse_app, html=True), name="app")


# Beim Start klar auf STDOUT protokollieren, ob die semantische Suche
# scharf ist. print() statt logger, weil uvicorn den methods.api-Logger
# nicht automatisch aktiviert (default ist WARNING) — print() landet
# zuverlässig im uvicorn-Boot-Log.
@app.on_event("startup")
def _log_embedding_readiness() -> None:
    # Storage-Modus anzeigen + bei Snapshot direkt laden (Fail-Fast bei
    # fehlendem Snapshot-Verzeichnis, statt erst beim ersten Request).
    print(f"[methods.api] storage mode = {STORAGE_MODE}", flush=True)
    if _is_snapshot():
        try:
            store = _get_snapshot_store()
            print(
                f"[methods.api] ✓ Snapshot geladen: "
                f"{store.total_methods} Methoden, "
                f"{store.manifest.get('embedding_count', 0)} mit Embedding "
                f"(Schema v{store.manifest.get('schema_version', '?')}, "
                f"generated {store.manifest.get('generated_at', '?')})",
                flush=True,
            )
        except Exception as exc:  # noqa: BLE001
            print(
                f"[methods.api] ✘ Snapshot konnte nicht geladen werden: {exc}",
                flush=True,
            )
            raise

    has_key = bool(os.environ.get("OPENAI_API_KEY"))
    try:
        import openai  # noqa: F401

        has_sdk = True
    except ImportError:
        has_sdk = False
    if has_key and has_sdk:
        print(
            "[methods.api] ✓ Semantic search ENABLED — "
            "OPENAI_API_KEY set, openai SDK installed",
            flush=True,
        )
    else:
        missing = []
        if not has_key:
            missing.append("OPENAI_API_KEY")
        if not has_sdk:
            missing.append("openai SDK")
        print(
            f"[methods.api] ⚠ Semantic search DISABLED — missing: "
            f"{', '.join(missing)}. /search?mode=hybrid läuft rein lexikalisch, "
            f"/search?mode=semantic wirft 503.",
            flush=True,
        )


# ---------------------------------------------------------------------------
# Dependency: DB-Session per Request
# ---------------------------------------------------------------------------


def get_db() -> Iterator[Session]:
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


# ---------------------------------------------------------------------------
# Response-Models
# ---------------------------------------------------------------------------


class SynonymOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    synonym: str
    language: str


class PhaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    code: str
    label_de: str
    is_primary: bool = False


class SettingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    code: str
    label_de: str
    suitability: str
    suitability_note: str | None = None


class RefLabel(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    code: str
    label_de: str


class StepOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    order_index: int
    label: str
    duration_minutes: int | None = None
    trainer_action: str
    participant_action: str
    note: str | None = None


class VariationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    name: str
    description: str
    when_to_use: str | None = None
    setting_codes: list[str] | None = None


class PitfallOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    issue: str
    countermeasure: str
    severity: str
    setting_codes: list[str] | None = None


class SourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    kind: str
    title: str
    author: str | None = None
    year: int | None = None
    url: str | None = None
    doi: str | None = None
    isbn: str | None = None
    relation: str
    page_reference: str | None = None


class MethodListItem(BaseModel):
    """Kompakter Eintrag für Listen-Views."""

    slug: str
    canonical_name: str
    short_description: str | None = None
    duration_typical_minutes: int | None = None
    quality_status: str
    stage_2_status: str
    primary_phase_code: str | None = None
    setting_codes: list[str] = []
    method_type_codes: list[str] = []


class MethodDetail(BaseModel):
    slug: str
    canonical_name: str
    short_description: str | None = None
    long_description: str | None = None
    originator: str | None = None
    origin_year: int | None = None
    origin_context: str | None = None
    duration_min_minutes: int | None = None
    duration_typical_minutes: int | None = None
    duration_max_minutes: int | None = None
    materials: list[str] = []
    tags: list[str] = []
    quality_status: str
    stage_2_status: str
    stage_2_author: str | None = None
    content_author: str | None = None
    content_license: str
    synonyms: list[SynonymOut] = []
    phases: list[PhaseOut] = []
    settings: list[SettingOut] = []
    group_sizes: list[RefLabel] = []
    target_audiences: list[RefLabel] = []
    method_types: list[RefLabel] = []
    steps: list[StepOut] = []
    variations: list[VariationOut] = []
    pitfalls: list[PitfallOut] = []
    sources: list[SourceOut] = []


# ---------------------------------------------------------------------------
# Serializer-Helfer
# ---------------------------------------------------------------------------


def _to_list_item(m: Method) -> MethodListItem:
    primary_phase = next(
        (pl.phase.code for pl in m.phase_links if pl.is_primary),
        None,
    )
    return MethodListItem(
        slug=m.slug,
        canonical_name=m.canonical_name,
        short_description=m.short_description,
        duration_typical_minutes=m.duration_typical_minutes,
        quality_status=m.quality_status,
        stage_2_status=m.stage_2_status,
        primary_phase_code=primary_phase,
        setting_codes=[sl.setting.code for sl in m.setting_links],
        method_type_codes=[tl.method_type.code for tl in m.type_links],
    )


def _to_detail(m: Method) -> MethodDetail:
    return MethodDetail(
        slug=m.slug,
        canonical_name=m.canonical_name,
        short_description=m.short_description,
        long_description=m.long_description,
        originator=m.originator,
        origin_year=m.origin_year,
        origin_context=m.origin_context,
        duration_min_minutes=m.duration_min_minutes,
        duration_typical_minutes=m.duration_typical_minutes,
        duration_max_minutes=m.duration_max_minutes,
        materials=list(m.materials or []),
        tags=list(m.tags or []),
        quality_status=m.quality_status,
        stage_2_status=m.stage_2_status,
        stage_2_author=m.stage_2_author,
        content_author=m.content_author,
        content_license=m.content_license,
        synonyms=[SynonymOut.model_validate(s) for s in m.synonyms],
        phases=[
            PhaseOut(
                code=pl.phase.code,
                label_de=pl.phase.label_de,
                is_primary=pl.is_primary,
            )
            for pl in m.phase_links
        ],
        settings=[
            SettingOut(
                code=sl.setting.code,
                label_de=sl.setting.label_de,
                suitability=sl.suitability,
                suitability_note=sl.suitability_note,
            )
            for sl in m.setting_links
        ],
        group_sizes=[
            RefLabel(code=gl.group_size.code, label_de=gl.group_size.label_de)
            for gl in m.group_size_links
        ],
        target_audiences=[
            RefLabel(code=al.audience.code, label_de=al.audience.label_de)
            for al in m.audience_links
        ],
        method_types=[
            RefLabel(code=tl.method_type.code, label_de=tl.method_type.label_de)
            for tl in m.type_links
        ],
        steps=[
            StepOut(
                order_index=s.order_index,
                label=s.label,
                duration_minutes=s.duration_minutes,
                trainer_action=s.trainer_action,
                participant_action=s.participant_action,
                note=s.note,
            )
            for s in m.steps
        ],
        variations=[
            VariationOut(
                name=v.name,
                description=v.description,
                when_to_use=v.when_to_use,
                setting_codes=list(v.setting_codes or []) or None,
            )
            for v in m.variations
        ],
        pitfalls=[
            PitfallOut(
                issue=p.issue,
                countermeasure=p.countermeasure,
                severity=p.severity,
                setting_codes=list(p.setting_codes or []) or None,
            )
            for p in m.pitfalls
        ],
        sources=[
            SourceOut(
                kind=ms.source.kind,
                title=ms.source.title,
                author=ms.source.author,
                year=ms.source.year,
                url=ms.source.url,
                doi=ms.source.doi,
                isbn=ms.source.isbn,
                relation=ms.relation,
                page_reference=ms.page_reference,
            )
            for ms in m.sources
        ],
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health", tags=["meta"])
def health(db: Session = Depends(get_db)) -> dict[str, object]:
    """Healthcheck — prüft Storage-Reachability und zählt Methoden."""
    if _is_snapshot():
        store = _get_snapshot_store()
        return {
            "status": "ok",
            "methods_total": store.total_methods,
            "storage": "snapshot",
            "snapshot": store.manifest,
        }
    try:
        total = db.scalar(select(func.count()).select_from(Method))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"DB unreachable: {exc}") from exc
    return {"status": "ok", "methods_total": total, "storage": "postgres"}


@app.get("/methods", response_model=list[MethodListItem], tags=["methods"])
def list_methods(
    db: Session = Depends(get_db),
    phase: str | None = Query(None, description="Phase-Code (z.B. ``warmup``)"),
    setting: list[str] | None = Query(None, description="Setting-Code(s)"),
    group_size: list[str] | None = Query(None),
    audience: list[str] | None = Query(None, description="Zielgruppen-Code(s)"),
    method_type: list[str] | None = Query(None),
    quality_status: str | None = Query(None),
    stage_2_status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> list[MethodListItem]:
    """Listet Methoden mit kombinierbaren Filtern.

    Mehrfach-Query-Params (``setting``, ``audience``, ...) werden als
    OR innerhalb eines Parameters, AND zwischen Parametern interpretiert.
    """
    if _is_snapshot():
        rows = _get_snapshot_store().list_methods(
            phase=phase,
            setting=setting,
            group_size=group_size,
            audience=audience,
            method_type=method_type,
            quality_status=quality_status,
            stage_2_status=stage_2_status,
            limit=limit,
            offset=offset,
        )
        return [MethodListItem.model_validate(r) for r in rows]

    q = (
        select(Method)
        .options(
            selectinload(Method.phase_links).selectinload(MethodPhase.phase),
            selectinload(Method.setting_links).selectinload(MethodSetting.setting),
            selectinload(Method.type_links).selectinload(MethodTypeLink.method_type),
        )
        .order_by(Method.canonical_name)
    )

    if quality_status:
        q = q.where(Method.quality_status == quality_status)
    if stage_2_status:
        q = q.where(Method.stage_2_status == stage_2_status)

    if phase:
        q = q.where(
            Method.phase_links.any(
                MethodPhase.phase.has(Phase.code == phase)
            )
        )
    if setting:
        q = q.where(
            Method.setting_links.any(
                MethodSetting.setting.has(Setting.code.in_(setting))
            )
        )
    if group_size:
        q = q.where(
            Method.group_size_links.any(
                MethodGroupSize.group_size.has(GroupSize.code.in_(group_size))
            )
        )
    if audience:
        q = q.where(
            Method.audience_links.any(
                MethodTargetAudience.audience.has(
                    TargetAudience.code.in_(audience)
                )
            )
        )
    if method_type:
        q = q.where(
            Method.type_links.any(
                MethodTypeLink.method_type.has(MethodType.code.in_(method_type))
            )
        )

    q = q.limit(limit).offset(offset)
    rows = db.execute(q).scalars().all()
    return [_to_list_item(m) for m in rows]


@app.get("/methods/{slug}", response_model=MethodDetail, tags=["methods"])
def get_method(slug: str, db: Session = Depends(get_db)) -> MethodDetail:
    """Vollständige Methode inkl. steps/variations/pitfalls/sources."""
    if _is_snapshot():
        data = _get_snapshot_store().get_method(slug)
        if data is None:
            raise HTTPException(status_code=404, detail=f"Unbekannter Slug: {slug}")
        return MethodDetail.model_validate(data)

    m = db.execute(
        select(Method)
        .where(Method.slug == slug)
        .options(
            selectinload(Method.synonyms),
            selectinload(Method.phase_links).selectinload(MethodPhase.phase),
            selectinload(Method.setting_links).selectinload(MethodSetting.setting),
            selectinload(Method.group_size_links).selectinload(
                MethodGroupSize.group_size
            ),
            selectinload(Method.audience_links).selectinload(
                MethodTargetAudience.audience
            ),
            selectinload(Method.type_links).selectinload(MethodTypeLink.method_type),
            selectinload(Method.steps),
            selectinload(Method.variations),
            selectinload(Method.pitfalls),
            selectinload(Method.sources).selectinload(MethodSource.source),
        )
    ).scalar_one_or_none()

    if m is None:
        raise HTTPException(status_code=404, detail=f"Unbekannter Slug: {slug}")
    return _to_detail(m)


# ---------------------------------------------------------------------------
# Such-Endpoint mit drei Modi
# ---------------------------------------------------------------------------
#
# - lexical: nur FTS + Trigram + Synonym-ILIKE. Schnell, schlägt fehl bei
#   semantisch verwandten Begriffen ohne Wort-Überlappung
#   ("Energizer" findet keine "Aufwärm-Übung").
# - semantic: nur Vektor-Cosine-Similarity. Findet thematisch Verwandtes,
#   verfehlt aber exakte Slug-/Synonym-Treffer und scheitert bei Methoden
#   ohne Embedding (NULL).
# - hybrid (default): beides, gewichtet kombiniert. Reziproke Rang-Fusion
#   wäre theoretisch sauberer, ist aber bei < 10k Zeilen Overkill —
#   stattdessen min-max-normalisieren wir die Roh-Scores und addieren mit
#   konfigurierbaren Gewichten.
#
# Konstante Gewichte: lexical:0.4 + semantic:0.6. Semantik wertvoller bei
# unspezifischen Anfragen ("Methode für Konflikte"), Lexik bei expliziten
# Namensanfragen — die kommen aber sowieso oben raus, weil der Name 1:1
# das FTS-Vec füttert.

LEXICAL_WEIGHT = 0.4
SEMANTIC_WEIGHT = 0.6
EMBEDDING_MODEL = os.environ.get("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")


@lru_cache(maxsize=512)
def _embed_query_cached(q: str) -> tuple[float, ...]:
    """Hot path: Embedding für Query, cached. Wirft bei Fehler (kein None-Cache!).

    Tuple, damit immutable und hashable bei Bedarf.
    """
    from openai import OpenAI  # type: ignore[import-not-found]

    client = OpenAI()
    resp = client.embeddings.create(model=EMBEDDING_MODEL, input=[q])
    return tuple(resp.data[0].embedding)


def _embed_query(q: str) -> tuple[float, ...] | None:
    """Liefert das Query-Embedding oder None bei fehlendem Setup / API-Fehler.

    Wir cachen NUR Erfolge (in ``_embed_query_cached``). Wenn der API-Key
    fehlt oder die SDK nicht installiert ist, gibt diese Funktion None
    zurück, OHNE das im Cache zu hinterlegen — sodass ein nachträglich
    gesetzter Key beim nächsten Aufruf greift.
    """
    if not os.environ.get("OPENAI_API_KEY"):
        return None
    try:
        return _embed_query_cached(q)
    except ImportError:
        # openai-SDK fehlt — Caller fällt auf lexical zurück.
        return None
    except Exception:  # noqa: BLE001
        # API-Fehler dürfen den Such-Pfad nicht töten.
        return None


def _normalize(values: dict[str, float]) -> dict[str, float]:
    """Min-Max-Normalisierung auf [0, 1]; leere/konstante Inputs → leer."""
    if not values:
        return {}
    vs = list(values.values())
    lo, hi = min(vs), max(vs)
    if hi - lo < 1e-9:
        # alle gleich → keine Diskriminierung möglich, alles auf 1.0
        return {k: 1.0 for k in values}
    return {k: (v - lo) / (hi - lo) for k, v in values.items()}


@app.get("/search", response_model=list[MethodListItem], tags=["search"])
def search(
    q: str = Query(..., min_length=2, max_length=120, description="Suchbegriff"),
    limit: int = Query(30, ge=1, le=200),
    mode: str = Query(
        "hybrid",
        pattern="^(hybrid|lexical|semantic)$",
        description="Suchmodus: lexical (FTS+Trigram), semantic (Vektor), hybrid (Default)",
    ),
    db: Session = Depends(get_db),
) -> list[MethodListItem]:
    """Hybride Suche: lexikalisch (FTS + Trigram + Synonym) + semantisch (Vektor).

    - ``mode=lexical``: nur Wort-/Trigram-Matches.
    - ``mode=semantic``: nur Vektor-Distanz (cosine). Braucht ``OPENAI_API_KEY``.
    - ``mode=hybrid`` (default): beides, gewichtete Score-Fusion.

    Fehlt der API-Key oder die OpenAI-SDK, fällt jeder semantische Anteil
    transparent auf 0 zurück — der Endpoint liefert weiterhin lexikalische
    Treffer.
    """
    # ---------- Snapshot-Pfad ----------
    if _is_snapshot():
        from .snapshot_store import embed_query as _snap_embed

        store = _get_snapshot_store()
        query_emb = None
        if mode in ("hybrid", "semantic"):
            query_emb = _snap_embed(q)
            if query_emb is None and mode == "semantic":
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "Semantische Suche nicht verfügbar: OPENAI_API_KEY fehlt "
                        "oder die openai-SDK ist nicht installiert. "
                        "Wechsle auf mode=lexical oder setze den Key."
                    ),
                )
        rows = store.search(q, mode=mode, limit=limit, query_embedding=query_emb)
        return [MethodListItem.model_validate(r) for r in rows]

    # ---------- Lexikalischer Score ----------
    # Wir bauen die FTS-Query selbst: Inhaltswörter (>=3 Zeichen, keine
    # Zahlen, keine Funktions-Wörter) per OR verknüpft mit ``:*``-Prefix.
    # Eigene Filter-Liste, weil german_unaccent zwar ein paar Stoppwörter
    # aussortiert, aber "minute"/"sekunde"/"team" als Inhaltswort behandelt
    # — und die sind in Trainings-Queries oft nur Skala-Hinweise, keine
    # eigentlichen Suchbegriffe.
    import re

    # Funktionswörter, die als Solo-Token zu viel False-Positive bringen.
    # Inhaltsleere Wörter ("klären") drin lassen — die haben semantischen Wert.
    # Zusätzlich: Zeit-Skala-Substantive ausfiltern. "30 Minuten" in einer
    # Trainer-Query ist fast immer eine Skala-Angabe, kein Suchbegriff —
    # sonst rankt jede Methode, die "X min" in der Beschreibung hat, mit.
    _STOP = {
        "der", "die", "das", "den", "dem", "des",
        "ein", "eine", "einer", "einen", "einem", "eines",
        "und", "oder", "aber",
        "ist", "sind", "bin", "war", "waren",
        "wir", "ihr", "sie", "ich", "man",
        "mit", "ohne", "bei", "für", "von", "zum", "zur",
        "in", "an", "auf", "über", "unter", "vor", "nach",
        "im", "am", "ins",
        "alle", "alles", "viele", "manche", "einige",
        # Zeit-Skalen
        "minute", "minuten", "min",
        "sekunde", "sekunden", "sek",
        "stunde", "stunden", "std",
        "tag", "tage", "tagen",
        "woche", "wochen",
        "monat", "monate", "monaten",
        "jahr", "jahre", "jahren",
    }

    tokens_raw = re.split(r"\W+", q.lower())
    tokens = [
        t for t in tokens_raw
        if len(t) >= 3 and t not in _STOP and not t.isdigit()
    ]
    if tokens:
        # Format: "energizer:* | morgen:*" → matched any prefix
        fts_string = " | ".join(f"{t}:*" for t in tokens)
        fts_query = func.to_tsquery("german_unaccent", fts_string)
    else:
        # Fallback für sehr kurze/funktionswort-lastige Queries
        fts_query = func.plainto_tsquery("german_unaccent", q)

    text_vec = func.to_tsvector(
        "german_unaccent",
        func.coalesce(Method.canonical_name, "")
        + " "
        + func.coalesce(Method.short_description, "")
        + " "
        + func.coalesce(Method.long_description, ""),
    )
    fts_rank = func.ts_rank(text_vec, fts_query)
    trigram_sim = func.similarity(Method.canonical_name, q)
    # Synonym-Treffer als binärer Bonus modellieren (0/1) — über EXISTS-Subquery.
    synonym_hit = (
        select(func.count())
        .select_from(MethodSynonym)
        .where(MethodSynonym.method_id == Method.id)
        .where(MethodSynonym.synonym.ilike(f"%{q}%"))
        .correlate(Method)
        .scalar_subquery()
    )

    lexical_score_expr = (
        fts_rank * 1.0 + trigram_sim * 0.6 + func.least(synonym_hit, 1) * 0.4
    ).label("lex_score")

    lexical_filter = or_(
        text_vec.op("@@")(fts_query),
        Method.canonical_name.op("%")(q),
        Method.canonical_name.ilike(f"%{q}%"),
        synonym_hit > 0,
    )

    # ---------- Semantischer Score (optional) ----------
    semantic_scores: dict[str, float] = {}
    if mode in ("hybrid", "semantic"):
        emb = _embed_query(q)
        if emb is not None:
            # cosine distance ∈ [0, 2]; similarity = 1 - distance ∈ [-1, 1].
            # Wir nehmen die ersten ``2*limit`` Kandidaten und normalisieren später.
            distance = Method.embedding.cosine_distance(list(emb))
            sem_stmt = (
                select(Method.slug, distance.label("dist"))
                .where(Method.embedding.is_not(None))
                .order_by(distance)
                .limit(limit * 4)
            )
            for slug, dist in db.execute(sem_stmt).all():
                semantic_scores[slug] = 1.0 - float(dist)
        elif mode == "semantic":
            # Reine Semantik gewünscht, aber kein Embedding-Service verfügbar
            # → klare Fehlermeldung statt stillem Fallback.
            raise HTTPException(
                status_code=503,
                detail=(
                    "Semantische Suche nicht verfügbar: OPENAI_API_KEY fehlt "
                    "oder die openai-SDK ist nicht installiert. "
                    "Wechsle auf mode=lexical oder setze den Key."
                ),
            )

    # ---------- Lexikalische Kandidaten (immer abfragen, außer reine Semantik) ----------
    lexical_scores: dict[str, float] = {}
    if mode in ("hybrid", "lexical"):
        lex_stmt = (
            select(Method.slug, lexical_score_expr)
            .where(lexical_filter)
            .order_by(lexical_score_expr.desc())
            .limit(limit * 4)
        )
        for slug, score in db.execute(lex_stmt).all():
            lexical_scores[slug] = float(score or 0.0)

    # ---------- Score-Fusion ----------
    if mode == "lexical":
        combined = lexical_scores
    elif mode == "semantic":
        combined = semantic_scores
    else:
        norm_lex = _normalize(lexical_scores)
        norm_sem = _normalize(semantic_scores)
        all_slugs = set(norm_lex) | set(norm_sem)
        combined = {
            slug: LEXICAL_WEIGHT * norm_lex.get(slug, 0.0)
            + SEMANTIC_WEIGHT * norm_sem.get(slug, 0.0)
            for slug in all_slugs
        }

    if not combined:
        return []

    top_slugs = [
        slug
        for slug, _ in sorted(combined.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    ]

    # ---------- Volle Methoden-Objekte für die Top-Slugs in einem Rutsch holen ----------
    rows = (
        db.execute(
            select(Method)
            .options(
                selectinload(Method.phase_links).selectinload(MethodPhase.phase),
                selectinload(Method.setting_links).selectinload(MethodSetting.setting),
                selectinload(Method.type_links).selectinload(MethodTypeLink.method_type),
            )
            .where(Method.slug.in_(top_slugs))
        )
        .scalars()
        .all()
    )
    by_slug = {m.slug: m for m in rows}
    # Reihenfolge nach Score erhalten — top_slugs ist schon richtig sortiert.
    return [_to_list_item(by_slug[s]) for s in top_slugs if s in by_slug]


# ---------------------------------------------------------------------------
# Referenztabellen — nützlich für UI-Dropdowns
# ---------------------------------------------------------------------------


class PhaseItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    code: str
    label_de: str
    label_en: str | None = None
    description: str | None = None
    sort_order: int


class SettingItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    code: str
    label_de: str
    label_en: str | None = None
    description: str | None = None


class GroupSizeItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    code: str
    label_de: str
    min_participants: int | None = None
    max_participants: int | None = None
    sort_order: int


class AudienceItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    code: str
    label_de: str
    description: str | None = None


class MethodTypeItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    code: str
    label_de: str
    description: str | None = None


@app.get("/reference/phases", response_model=list[PhaseItem], tags=["reference"])
def ref_phases(db: Session = Depends(get_db)) -> list[PhaseItem]:
    if _is_snapshot():
        return [PhaseItem.model_validate(r) for r in _get_snapshot_store().reference("phases")]
    rows = db.execute(select(Phase).order_by(Phase.sort_order, Phase.code)).scalars().all()
    return [PhaseItem.model_validate(r) for r in rows]


@app.get("/reference/settings", response_model=list[SettingItem], tags=["reference"])
def ref_settings(db: Session = Depends(get_db)) -> list[SettingItem]:
    if _is_snapshot():
        return [SettingItem.model_validate(r) for r in _get_snapshot_store().reference("settings")]
    rows = db.execute(select(Setting).order_by(Setting.code)).scalars().all()
    return [SettingItem.model_validate(r) for r in rows]


@app.get(
    "/reference/group-sizes", response_model=list[GroupSizeItem], tags=["reference"]
)
def ref_group_sizes(db: Session = Depends(get_db)) -> list[GroupSizeItem]:
    if _is_snapshot():
        return [
            GroupSizeItem.model_validate(r)
            for r in _get_snapshot_store().reference("group_sizes")
        ]
    rows = (
        db.execute(select(GroupSize).order_by(GroupSize.sort_order, GroupSize.code))
        .scalars()
        .all()
    )
    return [GroupSizeItem.model_validate(r) for r in rows]


@app.get(
    "/reference/target-audiences",
    response_model=list[AudienceItem],
    tags=["reference"],
)
def ref_audiences(db: Session = Depends(get_db)) -> list[AudienceItem]:
    if _is_snapshot():
        return [
            AudienceItem.model_validate(r)
            for r in _get_snapshot_store().reference("target_audiences")
        ]
    rows = (
        db.execute(select(TargetAudience).order_by(TargetAudience.code)).scalars().all()
    )
    return [AudienceItem.model_validate(r) for r in rows]


@app.get(
    "/reference/method-types",
    response_model=list[MethodTypeItem],
    tags=["reference"],
)
def ref_method_types(db: Session = Depends(get_db)) -> list[MethodTypeItem]:
    if _is_snapshot():
        return [
            MethodTypeItem.model_validate(r)
            for r in _get_snapshot_store().reference("method_types")
        ]
    rows = db.execute(select(MethodType).order_by(MethodType.code)).scalars().all()
    return [MethodTypeItem.model_validate(r) for r in rows]


# ---------------------------------------------------------------------------
# Aggregations / Facets
# ---------------------------------------------------------------------------
#
# Use-Cases:
#   * UI-Filter-Sidebar: hinter jedem Checkbox die aktuelle Treffer-Zahl
#     anzeigen ("Remote (synchron) (87)"), und Optionen ausgrauen, die zu 0
#     Treffern führen würden.
#   * KI-Agent: vorab-Histogramm verstehen ("welche Phasen hat der Korpus?",
#     "welche Tags sind häufig?") um Prompts gezielter zu bauen.
#
# Implementierungs-Anmerkung:
#   Wir machen TRUE faceted search — die Zählung pro Facette berücksichtigt
#   alle anderen aktiven Filter, ABER nicht die Filter-Werte derselben
#   Facette. Beispiel: bei phase=warmup sollen wir hinter "Setting → Onsite"
#   die Zahl der warmup-Methoden zeigen, die onsite tauglich sind. Hinter
#   "Phase → reflection" zeigen wir aber NICHT 0, sondern die Zahl der
#   reflection-Methoden ohne den phase=warmup-Filter — sonst kann der User
#   nie umschalten.


class FacetBucket(BaseModel):
    code: str
    label_de: str
    count: int


class FacetsOut(BaseModel):
    """Counter pro Filter-Facette für UI-Sidebar oder Agent-Briefing."""

    total: int
    phases: list[FacetBucket]
    settings: list[FacetBucket]
    group_sizes: list[FacetBucket]
    target_audiences: list[FacetBucket]
    method_types: list[FacetBucket]
    quality_status: list[FacetBucket]
    stage_2_status: list[FacetBucket]
    top_tags: list[FacetBucket]


def _build_method_filter(
    *,
    phase: str | None,
    setting: list[str] | None,
    group_size: list[str] | None,
    audience: list[str] | None,
    method_type: list[str] | None,
    quality_status: str | None,
    stage_2_status: str | None,
):
    """Sammelt die WHERE-Bedingungen, die im /methods-Endpoint identisch
    angewandt werden — extrahiert, weil /aggregations dieselben Filter braucht.

    Skip-Argumente (z.B. ``skip='phase'``) erlauben es, beim Berechnen der
    Phase-Facette den eigenen Phase-Filter wegzulassen. Macht der Caller.
    """
    conds = []
    if quality_status:
        conds.append(Method.quality_status == quality_status)
    if stage_2_status:
        conds.append(Method.stage_2_status == stage_2_status)
    if phase:
        conds.append(Method.phase_links.any(MethodPhase.phase.has(Phase.code == phase)))
    if setting:
        conds.append(
            Method.setting_links.any(MethodSetting.setting.has(Setting.code.in_(setting)))
        )
    if group_size:
        conds.append(
            Method.group_size_links.any(
                MethodGroupSize.group_size.has(GroupSize.code.in_(group_size))
            )
        )
    if audience:
        conds.append(
            Method.audience_links.any(
                MethodTargetAudience.audience.has(TargetAudience.code.in_(audience))
            )
        )
    if method_type:
        conds.append(
            Method.type_links.any(
                MethodTypeLink.method_type.has(MethodType.code.in_(method_type))
            )
        )
    return conds


def _facet_counts_via_link(
    db: Session,
    *,
    link_model,
    link_target_attr: str,
    target_model,
    base_filter,
):
    """Zählt Methoden pro Code einer N:M-Verknüpfung.

    Generischer Helfer für phases/settings/groups/audiences/types — alle
    folgen demselben Muster (Method via Join-Table mit target_model).
    """
    target_code = target_model.code
    target_label = target_model.label_de
    fk_attr = getattr(link_model, link_target_attr)
    stmt = (
        select(target_code, target_label, func.count(func.distinct(Method.id)).label("n"))
        .join(link_model, link_model.method_id == Method.id)
        .join(target_model, fk_attr == target_model.id)
        .where(*base_filter)
        .group_by(target_code, target_label)
        .order_by(target_code)
    )
    return [
        FacetBucket(code=code, label_de=label, count=int(n))
        for code, label, n in db.execute(stmt).all()
    ]


@app.get("/aggregations/facets", response_model=FacetsOut, tags=["aggregations"])
def facets(
    db: Session = Depends(get_db),
    phase: str | None = Query(None, description="Phase-Code"),
    setting: list[str] | None = Query(None),
    group_size: list[str] | None = Query(None),
    audience: list[str] | None = Query(None),
    method_type: list[str] | None = Query(None),
    quality_status: str | None = Query(None),
    stage_2_status: str | None = Query(None),
    tag_limit: int = Query(20, ge=1, le=200),
) -> FacetsOut:
    """Counter pro Facette unter Berücksichtigung der aktiven Filter.

    Der Endpoint ist explizit nicht-faceted-perfekt: er rechnet bei jeder
    Facette mit ALLEN aktiven Filtern. Für eine "echte" UI-Faceted-Search
    (Phase-Counts ignorieren den phase=-Filter, damit der User umschalten
    kann), filtern wir client-seitig die Buckets aus, die zur aktiv
    gewählten Facette gehören. Bei N=163 ist die Server-Last egal — wir
    optimieren auf API-Einfachheit, nicht auf SQL-Cleverness.
    """
    if _is_snapshot():
        data = _get_snapshot_store().facets(
            phase=phase,
            setting=setting,
            group_size=group_size,
            audience=audience,
            method_type=method_type,
            quality_status=quality_status,
            stage_2_status=stage_2_status,
            tag_limit=tag_limit,
        )
        return FacetsOut.model_validate(data)

    base_filter = _build_method_filter(
        phase=phase,
        setting=setting,
        group_size=group_size,
        audience=audience,
        method_type=method_type,
        quality_status=quality_status,
        stage_2_status=stage_2_status,
    )

    # Total-Count mit dem vollen Filter-Stack
    total = db.scalar(
        select(func.count(func.distinct(Method.id))).select_from(Method).where(*base_filter)
    ) or 0

    # Histogramm pro N:M-Link
    phases_b = _facet_counts_via_link(
        db, link_model=MethodPhase, link_target_attr="phase_id",
        target_model=Phase, base_filter=base_filter,
    )
    settings_b = _facet_counts_via_link(
        db, link_model=MethodSetting, link_target_attr="setting_id",
        target_model=Setting, base_filter=base_filter,
    )
    groups_b = _facet_counts_via_link(
        db, link_model=MethodGroupSize, link_target_attr="group_size_id",
        target_model=GroupSize, base_filter=base_filter,
    )
    audiences_b = _facet_counts_via_link(
        db, link_model=MethodTargetAudience, link_target_attr="audience_id",
        target_model=TargetAudience, base_filter=base_filter,
    )
    types_b = _facet_counts_via_link(
        db, link_model=MethodTypeLink, link_target_attr="method_type_id",
        target_model=MethodType, base_filter=base_filter,
    )

    # Direkt-Spalten — keine Join-Tabelle
    quality_b = [
        FacetBucket(code=str(s), label_de=str(s), count=int(n))
        for s, n in db.execute(
            select(Method.quality_status, func.count())
            .where(*base_filter)
            .group_by(Method.quality_status)
            .order_by(Method.quality_status)
        ).all()
    ]
    stage_b = [
        FacetBucket(code=str(s), label_de=str(s), count=int(n))
        for s, n in db.execute(
            select(Method.stage_2_status, func.count())
            .where(*base_filter)
            .group_by(Method.stage_2_status)
            .order_by(Method.stage_2_status)
        ).all()
    ]

    # Top-Tags — UNNEST des string[]-Arrays. Postgres-spezifisch, aber wir
    # binden ja sowieso an Postgres.
    tag_stmt = (
        select(
            func.unnest(Method.tags).label("tag"),
            func.count(),
        )
        .where(*base_filter)
        .where(Method.tags.is_not(None))
        .group_by(text("tag"))
        .order_by(func.count().desc(), text("tag"))
        .limit(tag_limit)
    )
    tags_b = [
        FacetBucket(code=tag, label_de=tag, count=int(n))
        for tag, n in db.execute(tag_stmt).all()
    ]

    return FacetsOut(
        total=total,
        phases=phases_b,
        settings=settings_b,
        group_sizes=groups_b,
        target_audiences=audiences_b,
        method_types=types_b,
        quality_status=quality_b,
        stage_2_status=stage_b,
        top_tags=tags_b,
    )
