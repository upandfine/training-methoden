"""Exportiert die Postgres-Daten in einen statischen Snapshot.

Erzeugt drei Dateien in ``data/snapshot/``:

* ``methods.json``     — Liste aller Methoden im MethodDetail-Schema
                          (ein API-Aufruf in JSON-Form).
* ``references.json``  — { phases, settings, group_sizes,
                          target_audiences, method_types } für Dropdowns.
* ``embeddings.npy``   — np.float32-Matrix shape (N, 1536). Indizes
                          stimmen mit ``methods.json``-Reihenfolge überein.
                          Methoden ohne Embedding bekommen eine Null-Zeile;
                          beim Such-Ranking werden die per Maske gefiltert.
* ``manifest.json``    — Generierungs-Zeit, Counts, Schema-Version.

Aufruf:
    OPENAI_API_KEY=... python scripts/snapshot.py            # mit Embeddings
    python scripts/snapshot.py --skip-embeddings             # nur Stage-1+2

Schreibt atomar: erst in temp-Dateien, am Ende rename. So ist ein
unterbrochener Lauf nie ein halb-gültiger Snapshot.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

# src/ in den Import-Pfad
_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import selectinload  # noqa: E402

from methods.db import session_scope  # noqa: E402
from methods.models import (  # noqa: E402
    GroupSize,
    Method,
    MethodGroupSize,
    MethodPhase,
    MethodSetting,
    MethodSource,
    MethodTargetAudience,
    MethodType,
    MethodTypeLink,
    Phase,
    Setting,
    TargetAudience,
)

SCHEMA_VERSION = 1
EMBED_DIM = 1536


def _method_to_dict(m: Method) -> dict:
    """Wandelt ein Method-ORM-Objekt in den Snapshot-Eintrag.

    Der Eintrag ist identisch zum API-Response von /methods/{slug}
    (MethodDetail) — so kann der In-Memory-Store den Eintrag direkt
    zurückgeben, ohne Mapping.
    """
    return {
        "slug": m.slug,
        "canonical_name": m.canonical_name,
        "short_description": m.short_description,
        "long_description": m.long_description,
        "originator": m.originator,
        "origin_year": m.origin_year,
        "origin_context": m.origin_context,
        "duration_min_minutes": m.duration_min_minutes,
        "duration_typical_minutes": m.duration_typical_minutes,
        "duration_max_minutes": m.duration_max_minutes,
        "materials": list(m.materials or []),
        "tags": list(m.tags or []),
        "quality_status": m.quality_status,
        "stage_2_status": m.stage_2_status,
        "stage_2_author": m.stage_2_author,
        "content_author": m.content_author,
        "content_license": m.content_license,
        "synonyms": [
            {"synonym": s.synonym, "language": s.language} for s in m.synonyms
        ],
        "phases": [
            {
                "code": pl.phase.code,
                "label_de": pl.phase.label_de,
                "is_primary": pl.is_primary,
            }
            for pl in m.phase_links
        ],
        "settings": [
            {
                "code": sl.setting.code,
                "label_de": sl.setting.label_de,
                "suitability": sl.suitability,
                "suitability_note": sl.suitability_note,
            }
            for sl in m.setting_links
        ],
        "group_sizes": [
            {"code": gl.group_size.code, "label_de": gl.group_size.label_de}
            for gl in m.group_size_links
        ],
        "target_audiences": [
            {"code": al.audience.code, "label_de": al.audience.label_de}
            for al in m.audience_links
        ],
        "method_types": [
            {"code": tl.method_type.code, "label_de": tl.method_type.label_de}
            for tl in m.type_links
        ],
        "steps": [
            {
                "order_index": s.order_index,
                "label": s.label,
                "duration_minutes": s.duration_minutes,
                "trainer_action": s.trainer_action,
                "participant_action": s.participant_action,
                "note": s.note,
            }
            for s in sorted(m.steps, key=lambda s: s.order_index)
        ],
        "variations": [
            {
                "name": v.name,
                "description": v.description,
                "when_to_use": v.when_to_use,
                "setting_codes": list(v.setting_codes or []) or None,
            }
            for v in m.variations
        ],
        "pitfalls": [
            {
                "issue": p.issue,
                "countermeasure": p.countermeasure,
                "severity": p.severity,
                "setting_codes": list(p.setting_codes or []) or None,
            }
            for p in m.pitfalls
        ],
        "sources": [
            {
                "kind": ms.source.kind,
                "title": ms.source.title,
                "author": ms.source.author,
                "year": ms.source.year,
                "url": ms.source.url,
                "doi": ms.source.doi,
                "isbn": ms.source.isbn,
                "relation": ms.relation,
                "page_reference": ms.page_reference,
            }
            for ms in m.sources
        ],
    }


def _ref_to_dict(obj, fields: tuple[str, ...]) -> dict:
    return {f: getattr(obj, f, None) for f in fields}


def export(
    target_dir: Path,
    skip_embeddings: bool = False,
) -> dict:
    """Schreibt den Snapshot, gibt das Manifest zurück."""
    target_dir.mkdir(parents=True, exist_ok=True)

    with session_scope() as s:
        # Methoden mit allen Relationen in einem Rutsch eager-laden.
        # selectinload statt joinedload, damit's keine kartesische Explosion
        # bei den Many-to-Many-Beziehungen gibt.
        methods = list(
            s.execute(
                select(Method)
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
                .order_by(Method.canonical_name)
            ).scalars()
        )

        methods_payload = [_method_to_dict(m) for m in methods]

        # Embeddings als parallele Matrix. Bei NULL: Null-Zeile, In-Memory-Store
        # maskiert die später beim Cosine-Ranking aus.
        embeddings = np.zeros((len(methods), EMBED_DIM), dtype=np.float32)
        has_embedding = np.zeros(len(methods), dtype=bool)
        if not skip_embeddings:
            for i, m in enumerate(methods):
                if m.embedding is not None:
                    embeddings[i] = np.asarray(m.embedding, dtype=np.float32)
                    has_embedding[i] = True

        # Referenz-Daten
        ref_fields = {
            "phases": (Phase, ("code", "label_de", "label_en", "description", "sort_order")),
            "settings": (Setting, ("code", "label_de", "label_en", "description")),
            "group_sizes": (
                GroupSize,
                ("code", "label_de", "min_participants", "max_participants", "sort_order"),
            ),
            "target_audiences": (TargetAudience, ("code", "label_de", "description")),
            "method_types": (MethodType, ("code", "label_de", "description")),
        }
        references = {}
        for key, (model_cls, fields) in ref_fields.items():
            rows = s.execute(select(model_cls).order_by(model_cls.code)).scalars().all()
            references[key] = [_ref_to_dict(r, fields) for r in rows]

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "method_count": len(methods_payload),
        "embedding_count": int(has_embedding.sum()),
        "embedding_dim": EMBED_DIM,
        "skipped_embeddings": skip_embeddings,
    }

    # Atomar schreiben: erst .tmp, dann rename.
    def _write_json(name: str, data) -> None:
        tmp = target_dir / f"{name}.tmp"
        final = target_dir / name
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=False)
        tmp.replace(final)

    _write_json("methods.json", methods_payload)
    _write_json("references.json", references)
    _write_json("manifest.json", manifest)

    np.save(target_dir / "embeddings.npy.tmp", embeddings, allow_pickle=False)
    (target_dir / "embeddings.npy.tmp.npy").replace(target_dir / "embeddings.npy")

    # Maske als separate Datei — wichtig für In-Memory-Store, um Methoden
    # ohne echtes Embedding bei mode=semantic auszublenden.
    np.save(target_dir / "has_embedding.npy.tmp", has_embedding, allow_pickle=False)
    (target_dir / "has_embedding.npy.tmp.npy").replace(target_dir / "has_embedding.npy")

    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target",
        default="data/snapshot",
        help="Zielverzeichnis (default: data/snapshot)",
    )
    parser.add_argument(
        "--skip-embeddings",
        action="store_true",
        help="Embeddings nicht exportieren (kleinerer Snapshot, semantic mode dann lexical-fallback)",
    )
    args = parser.parse_args()

    target_dir = Path(args.target).resolve()
    print(f"→ Exportiere Snapshot nach {target_dir}")
    manifest = export(target_dir, skip_embeddings=args.skip_embeddings)
    print(
        f"✓ {manifest['method_count']} Methoden, "
        f"{manifest['embedding_count']} mit Embedding "
        f"({manifest['embedding_dim']} dim). "
        f"Schema v{manifest['schema_version']}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
