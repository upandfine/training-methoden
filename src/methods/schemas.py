"""Pydantic-Schemas für die YAML-Ingest-Dateien.

Jede Methoden-YAML wird hierüber validiert, bevor sie in die DB geht.
So fallen typos, fehlende Pflichtfelder und inkonsistente enum-Werte
sofort auf — und nicht erst beim Einfügen in Postgres.

Spiegelt den Stage-2-Schema-Stand (steps/variations/pitfalls + stage_2_*-Felder).
``extra="ignore"`` auf dem Top-Level, weil YAMLs Kommentar-Felder und
Historie-Felder enthalten können, die wir nicht in die DB aufnehmen wollen.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

QualityStatus = Literal["ai_draft", "human_reviewed", "expert_verified", "deprecated"]
Stage2Status = Literal["none", "ai_draft", "human_reviewed", "expert_verified"]
ContentLicense = Literal["proprietary", "cc-by-4.0", "cc-by-sa-4.0", "public-domain"]
Suitability = Literal["excellent", "good", "limited", "unsuitable"]
SourceRelation = Literal[
    "originator",
    "reference",
    "deep_dive",
    "practice_guide",
    "tool",
]
SourceKind = Literal[
    "book",
    "article",
    "url",
    "standard",
    "tool",
    "paper",
    "other",
]
SynonymLang = Literal["de", "en", "fr", "es", "other"]
Severity = Literal["common", "rare"]


class SynonymIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    synonym: str
    language: SynonymLang = "de"


class SettingAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    suitability: Suitability = "good"
    note: str | None = None


class PhaseAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    is_primary: bool = False


class SourceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: SourceKind
    title: str
    author: str | None = None
    year: int | None = None
    isbn: str | None = None
    url: str | None = None
    doi: str | None = None
    relation: SourceRelation = "reference"
    page_reference: str | None = None
    note: str | None = None  # in YAMLs 'note'
    notes: str | None = None  # älteres Feld, zur Kompatibilität


class StepIn(BaseModel):
    """Ein Schritt im Ablauf (Stufe 2).

    YAML-Felder: ``order`` (statt ``order_index``), ``label``, ``trainer_action``,
    ``participant_action``, ``duration_minutes``, ``note``.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    order_index: int = Field(ge=1, alias="order")
    label: str
    duration_minutes: int | None = None
    trainer_action: str
    participant_action: str
    note: str | None = None


class VariationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    description: str
    when_to_use: str | None = None
    setting_codes: list[str] = Field(default_factory=list)


class PitfallIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    issue: str
    countermeasure: str
    severity: Severity = "common"
    setting_codes: list[str] = Field(default_factory=list)


class MethodIn(BaseModel):
    """Eine komplette Methode, wie sie in einer YAML-Datei steht.

    ``extra="ignore"``: Wir erlauben zusätzliche Felder in YAMLs für spätere
    Erweiterungen (z.B. Stufe 3) ohne Schema-Aufräumen erzwingen zu müssen.
    """

    model_config = ConfigDict(extra="ignore")

    # Identität
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,158}$")
    canonical_name: str
    synonyms: list[SynonymIn] = Field(default_factory=list)

    # Inhalt — Stufe 1
    short_description: str | None = None
    originator: str | None = None
    origin_year: int | None = None
    origin_context: str | None = None

    # Klassifikation
    phases: list[PhaseAssignment] = Field(default_factory=list)
    settings: list[SettingAssignment] = Field(default_factory=list)
    group_sizes: list[str] = Field(default_factory=list)
    target_audiences: list[str] = Field(default_factory=list)
    method_types: list[str] = Field(default_factory=list)

    # Dauer
    duration_min_minutes: int | None = None
    duration_typical_minutes: int | None = None
    duration_max_minutes: int | None = None

    # Frei
    materials: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)

    # Inhalt — Stufe 2
    long_description: str | None = None
    steps: list[StepIn] = Field(default_factory=list)
    variations: list[VariationIn] = Field(default_factory=list)
    pitfalls: list[PitfallIn] = Field(default_factory=list)

    # Stufe-2-Meta
    stage_2_status: Stage2Status = "none"
    stage_2_author: str | None = None

    # Quellen
    sources: list[SourceRef] = Field(default_factory=list)

    # Metadaten
    quality_status: QualityStatus = "ai_draft"
    content_license: ContentLicense = "proprietary"
    content_author: str | None = None

    @field_validator("phases")
    @classmethod
    def at_most_one_primary_phase(cls, v: list[PhaseAssignment]) -> list[PhaseAssignment]:
        primaries = [p for p in v if p.is_primary]
        if len(primaries) > 1:
            raise ValueError("Nur eine Phase darf is_primary=true sein.")
        return v

    @field_validator("duration_max_minutes")
    @classmethod
    def duration_order(cls, v: int | None, info) -> int | None:
        data = info.data
        mn = data.get("duration_min_minutes")
        if v is not None and mn is not None and v < mn:
            raise ValueError("duration_max_minutes < duration_min_minutes")
        return v

    @field_validator("steps")
    @classmethod
    def step_order_contiguous(cls, v: list[StepIn]) -> list[StepIn]:
        if not v:
            return v
        orders = [s.order_index for s in v]
        expected = list(range(1, len(v) + 1))
        if sorted(orders) != expected:
            raise ValueError(
                f"steps.order muss lückenlos 1..{len(v)} sein, bekam {sorted(orders)}"
            )
        return v


class ReferenceItem(BaseModel):
    """Seed-Eintrag für Referenztabellen (phases, settings, ...).

    ``coerce_numbers_to_str``: Falls jemand in einer Seed-YAML einen Label-
    Wert vergisst zu quoten und YAML ihn als Zahl interpretiert (Klassiker:
    ``1:1`` → 61 Sexagesimal, ``2024`` → int), konvertieren wir still zum
    String statt zu crashen. Die Datei sollte trotzdem gefixt werden, aber
    der Seed läuft durch.
    """

    model_config = ConfigDict(extra="forbid", coerce_numbers_to_str=True)
    code: str
    label_de: str
    label_en: str | None = None
    description: str | None = None
    sort_order: int = 0
    min_participants: int | None = None
    max_participants: int | None = None
