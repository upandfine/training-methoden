"""SQLAlchemy-Modelle für den Trainingsmethoden-Katalog.

Design-Prinzipien:
  * Referenzdaten (Phasen, Settings, Gruppengrößen, Zielgruppen, Methoden-Typen)
    als eigene Tabellen mit ``code``-Schlüssel — nicht als Enum, damit Metadaten
    (Sortierreihenfolge, Beschreibung, Deutsch/Englisch) dranhängen können.
  * N:M-Relationen über Join-Tabellen, mit Platz für Kontext-Felder
    (z.B. ``suitability_note`` an ``method_settings``).
  * Content-Versionierung light: ``quality_status`` + ``review_log`` reichen
    initial; echtes Audit-Trailing kommt später, wenn eine UI existiert.
  * pgvector-Spalte für spätere semantische Suche ist vorgesehen, aber nullable.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSONB}


# ---------------------------------------------------------------------------
# Referenz-Tabellen
# ---------------------------------------------------------------------------


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class Phase(Base, TimestampMixin):
    """Trainingsphasen: Ankommen, Warm-up, Wissensvermittlung, Abschluss, ..."""

    __tablename__ = "phases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    label_de: Mapped[str] = mapped_column(String(128), nullable=False)
    label_en: Mapped[str | None] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class Setting(Base, TimestampMixin):
    """Einsatz-Settings: Präsenz, Remote-synchron, Remote-asynchron, Hybrid."""

    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    label_de: Mapped[str] = mapped_column(String(128), nullable=False)
    label_en: Mapped[str | None] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(Text)


class GroupSize(Base, TimestampMixin):
    """Gruppengrößen-Kategorien: 1:1, Klein, Mittel, Groß, Plenum."""

    __tablename__ = "group_sizes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    label_de: Mapped[str] = mapped_column(String(64), nullable=False)
    min_participants: Mapped[int | None] = mapped_column(Integer)
    max_participants: Mapped[int | None] = mapped_column(Integer)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class TargetAudience(Base, TimestampMixin):
    """Zielgruppen: Unternehmen, Schule, Jugend, IHK, Erwachsenenbildung."""

    __tablename__ = "target_audiences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    label_de: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)


class MethodType(Base, TimestampMixin):
    """Methoden-Typen: didaktisch, aktivierend, kreativ, reflexiv, ..."""

    __tablename__ = "method_types"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    label_de: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)


# ---------------------------------------------------------------------------
# Haupt-Entität
# ---------------------------------------------------------------------------


QUALITY_STATUSES = ("ai_draft", "human_reviewed", "expert_verified", "deprecated")
CONTENT_LICENSES = ("proprietary", "cc-by-4.0", "cc-by-sa-4.0", "public-domain")
STAGE_2_STATUSES = ("none", "ai_draft", "human_reviewed", "expert_verified")


class Method(Base, TimestampMixin):
    """Eine Trainingsmethode (Stufe 1 = Stammdaten, Stufe 2 = Ablauf via ``steps``)."""

    __tablename__ = "methods"
    __table_args__ = (
        CheckConstraint(
            f"quality_status IN {QUALITY_STATUSES}",
            name="ck_methods_quality_status",
        ),
        CheckConstraint(
            f"content_license IN {CONTENT_LICENSES}",
            name="ck_methods_content_license",
        ),
        CheckConstraint(
            f"stage_2_status IN {STAGE_2_STATUSES}",
            name="ck_methods_stage_2_status",
        ),
        CheckConstraint(
            "duration_min_minutes IS NULL OR duration_min_minutes >= 0",
            name="ck_methods_duration_min_nonneg",
        ),
        CheckConstraint(
            "duration_max_minutes IS NULL OR duration_max_minutes >= duration_min_minutes",
            name="ck_methods_duration_order",
        ),
        Index(
            "ix_methods_name_trgm",
            "canonical_name",
            postgresql_using="gin",
            postgresql_ops={"canonical_name": "gin_trgm_ops"},
        ),
        Index(
            "ix_methods_fts_de",
            text(
                "to_tsvector('german_unaccent', "
                "coalesce(canonical_name,'') || ' ' || "
                "coalesce(short_description,'') || ' ' || "
                "coalesce(long_description,''))"
            ),
            postgresql_using="gin",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("uuid_generate_v4()")
    )
    slug: Mapped[str] = mapped_column(String(160), unique=True, nullable=False)
    canonical_name: Mapped[str] = mapped_column(String(200), nullable=False)

    short_description: Mapped[str | None] = mapped_column(Text)  # 1–3 Sätze
    long_description: Mapped[str | None] = mapped_column(Text)  # Stufe 2, optional

    # Ursprung / Zuschreibung
    originator: Mapped[str | None] = mapped_column(String(200))
    origin_year: Mapped[int | None] = mapped_column(Integer)
    origin_context: Mapped[str | None] = mapped_column(Text)

    # Dauer (Minuten)
    duration_min_minutes: Mapped[int | None] = mapped_column(Integer)
    duration_typical_minutes: Mapped[int | None] = mapped_column(Integer)
    duration_max_minutes: Mapped[int | None] = mapped_column(Integer)

    # Material + freie Tags
    materials: Mapped[list[str] | None] = mapped_column(ARRAY(String))
    tags: Mapped[list[str] | None] = mapped_column(ARRAY(String))

    # Qualität / Herkunft der DATENBANK-EINTRAGS (nicht der Methode selbst)
    quality_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="ai_draft"
    )
    content_license: Mapped[str] = mapped_column(
        String(32), nullable=False, default="proprietary"
    )
    content_author: Mapped[str | None] = mapped_column(String(200))

    # Stufe-2-Metadaten — separat von quality_status, weil Stufe 1 und 2
    # unterschiedliche Reife-Grade haben können (Stufe 1 human-reviewed,
    # Stufe 2 ai-draft).
    stage_2_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="none"
    )
    stage_2_author: Mapped[str | None] = mapped_column(String(200))

    # Spätere semantische Suche; OpenAI text-embedding-3-small = 1536 dim
    embedding: Mapped[Any | None] = mapped_column(Vector(1536), nullable=True)

    # Relationen
    synonyms: Mapped[list["MethodSynonym"]] = relationship(
        back_populates="method", cascade="all, delete-orphan", lazy="selectin"
    )
    steps: Mapped[list["MethodStep"]] = relationship(
        back_populates="method",
        cascade="all, delete-orphan",
        order_by="MethodStep.order_index",
        lazy="selectin",
    )
    sources: Mapped[list["MethodSource"]] = relationship(
        back_populates="method", cascade="all, delete-orphan", lazy="selectin"
    )
    variations: Mapped[list["MethodVariation"]] = relationship(
        back_populates="method", cascade="all, delete-orphan", lazy="selectin"
    )
    pitfalls: Mapped[list["MethodPitfall"]] = relationship(
        back_populates="method", cascade="all, delete-orphan", lazy="selectin"
    )
    phase_links: Mapped[list["MethodPhase"]] = relationship(
        back_populates="method", cascade="all, delete-orphan"
    )
    setting_links: Mapped[list["MethodSetting"]] = relationship(
        back_populates="method", cascade="all, delete-orphan"
    )
    group_size_links: Mapped[list["MethodGroupSize"]] = relationship(
        back_populates="method", cascade="all, delete-orphan"
    )
    audience_links: Mapped[list["MethodTargetAudience"]] = relationship(
        back_populates="method", cascade="all, delete-orphan"
    )
    type_links: Mapped[list["MethodTypeLink"]] = relationship(
        back_populates="method", cascade="all, delete-orphan"
    )
    review_log: Mapped[list["ReviewLogEntry"]] = relationship(
        back_populates="method", cascade="all, delete-orphan"
    )


class MethodSynonym(Base, TimestampMixin):
    __tablename__ = "method_synonyms"
    __table_args__ = (
        UniqueConstraint("method_id", "synonym", name="uq_method_synonym"),
        Index(
            "ix_method_synonyms_trgm",
            "synonym",
            postgresql_using="gin",
            postgresql_ops={"synonym": "gin_trgm_ops"},
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    method_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("methods.id", ondelete="CASCADE"), nullable=False
    )
    synonym: Mapped[str] = mapped_column(String(200), nullable=False)
    language: Mapped[str] = mapped_column(String(8), nullable=False, default="de")

    method: Mapped[Method] = relationship(back_populates="synonyms")


class MethodStep(Base, TimestampMixin):
    """Stufe 2: Ablauf in nummerierten Schritten.

    ``trainer_action`` und ``participant_action`` sind beide Pflicht —
    ein Schritt beschreibt immer beide Seiten. ``note`` sind Facilitator-Hinweise.
    """

    __tablename__ = "method_steps"
    __table_args__ = (
        UniqueConstraint("method_id", "order_index", name="uq_method_step_order"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    method_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("methods.id", ondelete="CASCADE"), nullable=False
    )
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    duration_minutes: Mapped[int | None] = mapped_column(Integer)
    trainer_action: Mapped[str] = mapped_column(Text, nullable=False)
    participant_action: Mapped[str] = mapped_column(Text, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)

    method: Mapped[Method] = relationship(back_populates="steps")


class MethodVariation(Base, TimestampMixin):
    """Varianten einer Methode (Remote-Adaption, Kurzversion, Jugend-Variante, ...).

    ``setting_codes`` markiert, für welche Settings (onsite/remote_sync/...) die
    Variante besonders gedacht ist — kein FK, nur ein Tag-Array.
    """

    __tablename__ = "method_variations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    method_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("methods.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    when_to_use: Mapped[str | None] = mapped_column(Text)
    setting_codes: Mapped[list[str] | None] = mapped_column(ARRAY(String))

    method: Mapped[Method] = relationship(back_populates="variations")


class MethodPitfall(Base, TimestampMixin):
    """Stolperfallen + Gegenmaßnahmen bei der Anwendung einer Methode.

    ``severity`` ist 'common' oder 'rare'. ``setting_codes`` kann markieren,
    dass die Stolperfalle nur z.B. remote auftritt.
    """

    __tablename__ = "method_pitfalls"
    __table_args__ = (
        CheckConstraint(
            "severity IN ('common', 'rare')",
            name="ck_method_pitfalls_severity",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    method_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("methods.id", ondelete="CASCADE"), nullable=False
    )
    issue: Mapped[str] = mapped_column(Text, nullable=False)
    countermeasure: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="common")
    setting_codes: Mapped[list[str] | None] = mapped_column(ARRAY(String))

    method: Mapped[Method] = relationship(back_populates="pitfalls")


class Source(Base, TimestampMixin):
    """Buch / URL / Artikel, den eine Methode referenziert."""

    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # book, article, url, other
    title: Mapped[str] = mapped_column(String(400), nullable=False)
    author: Mapped[str | None] = mapped_column(String(300))
    year: Mapped[int | None] = mapped_column(Integer)
    isbn: Mapped[str | None] = mapped_column(String(32))
    url: Mapped[str | None] = mapped_column(String(1000))
    doi: Mapped[str | None] = mapped_column(String(128))
    notes: Mapped[str | None] = mapped_column(Text)

    method_links: Mapped[list["MethodSource"]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )


class MethodSource(Base, TimestampMixin):
    __tablename__ = "method_sources"
    __table_args__ = (
        UniqueConstraint("method_id", "source_id", name="uq_method_source"),
    )

    method_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("methods.id", ondelete="CASCADE"),
        primary_key=True,
    )
    source_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sources.id", ondelete="CASCADE"), primary_key=True
    )
    relation: Mapped[str] = mapped_column(String(32), nullable=False, default="reference")
    # Werte: "originator" (Quelle prägte die Methode), "reference" (Inspirationslektüre),
    # "deep_dive" (Weiterführend)
    page_reference: Mapped[str | None] = mapped_column(String(64))

    method: Mapped[Method] = relationship(back_populates="sources")
    source: Mapped[Source] = relationship(back_populates="method_links")


# ---------------------------------------------------------------------------
# N:M-Join-Tabellen zu den Referenzdaten
# ---------------------------------------------------------------------------


class MethodPhase(Base):
    __tablename__ = "method_phases"
    method_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("methods.id", ondelete="CASCADE"),
        primary_key=True,
    )
    phase_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("phases.id", ondelete="CASCADE"), primary_key=True
    )
    is_primary: Mapped[bool] = mapped_column(nullable=False, default=False)

    method: Mapped[Method] = relationship(back_populates="phase_links")
    phase: Mapped[Phase] = relationship()


class MethodSetting(Base):
    __tablename__ = "method_settings"
    method_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("methods.id", ondelete="CASCADE"),
        primary_key=True,
    )
    setting_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("settings.id", ondelete="CASCADE"), primary_key=True
    )
    suitability: Mapped[str] = mapped_column(String(16), nullable=False, default="good")
    # Werte: excellent, good, limited, unsuitable
    suitability_note: Mapped[str | None] = mapped_column(Text)

    method: Mapped[Method] = relationship(back_populates="setting_links")
    setting: Mapped[Setting] = relationship()


class MethodGroupSize(Base):
    __tablename__ = "method_group_sizes"
    method_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("methods.id", ondelete="CASCADE"),
        primary_key=True,
    )
    group_size_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("group_sizes.id", ondelete="CASCADE"), primary_key=True
    )

    method: Mapped[Method] = relationship(back_populates="group_size_links")
    group_size: Mapped[GroupSize] = relationship()


class MethodTargetAudience(Base):
    __tablename__ = "method_target_audiences"
    method_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("methods.id", ondelete="CASCADE"),
        primary_key=True,
    )
    audience_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("target_audiences.id", ondelete="CASCADE"), primary_key=True
    )

    method: Mapped[Method] = relationship(back_populates="audience_links")
    audience: Mapped[TargetAudience] = relationship()


class MethodTypeLink(Base):
    __tablename__ = "method_type_links"
    method_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("methods.id", ondelete="CASCADE"),
        primary_key=True,
    )
    method_type_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("method_types.id", ondelete="CASCADE"), primary_key=True
    )

    method: Mapped[Method] = relationship(back_populates="type_links")
    method_type: Mapped[MethodType] = relationship()


# ---------------------------------------------------------------------------
# Review-Trail
# ---------------------------------------------------------------------------


class ReviewLogEntry(Base, TimestampMixin):
    __tablename__ = "review_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    method_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("methods.id", ondelete="CASCADE"), nullable=False
    )
    reviewer: Mapped[str] = mapped_column(String(100), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    # Werte: reviewed, edited, approved, flagged
    from_status: Mapped[str | None] = mapped_column(String(32))
    to_status: Mapped[str | None] = mapped_column(String(32))
    note: Mapped[str | None] = mapped_column(Text)

    method: Mapped[Method] = relationship(back_populates="review_log")
