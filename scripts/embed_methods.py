"""Berechnet Embeddings für alle Methoden und schreibt sie in ``methods.embedding``.

Aufruf:
    OPENAI_API_KEY=sk-... python scripts/embed_methods.py
    python scripts/embed_methods.py --force         # alles neu einbetten
    python scripts/embed_methods.py --dry-run       # nur Texte ausgeben
    python scripts/embed_methods.py --model text-embedding-3-large

Design-Entscheidungen
---------------------

* **Modell**: OpenAI ``text-embedding-3-small`` (1536 dim) — günstig
  (~$0.02 / 1M Tokens), stark genug für deutsche Trainings-Methodentexte,
  Spalte ``methods.embedding`` ist auf 1536 dim festgelegt.

* **Was wird eingebettet?** Ein synthetisierter "representational text"
  pro Methode, der die Felder bündelt, nach denen Nutzer:innen suchen:
  Name, Synonyme, Kurzbeschreibung, Tags, Methoden-Typen, Phasen,
  Settings, plus die Step-Labels (geben semantische Hinweise wie
  "Reflexion" oder "Brainstorm", die im Fließtext fehlen können).
  Lange ``trainer_action``-Texte werden **nicht** eingebettet — sie
  würden Such-Begriffe wie "Energizer" oder "Konflikt" mit
  Standardvokabular verdünnen.

* **Idempotenz**: Per Default werden nur Methoden eingebettet, deren
  ``embedding`` NULL ist. ``--force`` erneuert alle. Kein
  Hash-Tracking — bei Content-Änderungen Skript einfach mit ``--force``
  laufen lassen oder ein Cron-Job nightly.

* **Batching**: 32 Texte pro Request (OpenAI erlaubt bis zu 2048 Inputs;
  32 hält Latenz und Memory niedrig und ist robust gegen Tokens-Limits
  von 8k pro Input).

* **Retry**: 3 Versuche mit exponentiellem Backoff bei
  ``RateLimitError`` / ``APIConnectionError``. Andere Fehler werden
  geloggt und das Batch übersprungen.

* **Output-Validierung**: Wir prüfen die Vektor-Länge — wenn das Modell
  eine andere Dimension liefert (z.B. wenn jemand das ``--model`` ändert),
  bricht das Skript früh ab, statt fehlerhafte Vektoren zu schreiben.

CLI ist absichtlich Argparse-basiert (kein Typer), damit das Skript
auch ohne installiertes ``methods``-Package laufen kann.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass

# Ensure ``src`` is importable, regardless of how the script is invoked.
_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_HERE)
_SRC = os.path.join(_PROJECT_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import selectinload  # noqa: E402

from methods.db import session_scope  # noqa: E402
from methods.models import (  # noqa: E402
    Method,
    MethodGroupSize,
    MethodPhase,
    MethodSetting,
    MethodTargetAudience,
    MethodTypeLink,
)


MODEL_DEFAULT = "text-embedding-3-small"
MODEL_NATIVE_DIM = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
}
# Modelle, die den ``dimensions``-Parameter zum Truncate unterstützen
# (Matryoshka-Embeddings — kürzere Vektoren bleiben mathematisch sinnvoll).
MODEL_SUPPORTS_DIMENSIONS = {"text-embedding-3-small", "text-embedding-3-large"}
BATCH_SIZE = 32
RETRY_MAX = 3
RETRY_BACKOFF_BASE = 2.0  # 2s, 4s, 8s
EXPECTED_DIM = 1536  # methods.embedding ist auf 1536 fixiert


@dataclass
class MethodPayload:
    method_id: str
    slug: str
    text: str


def build_representational_text(m: Method) -> str:
    """Baut den Such-relevanten Text aus einer Methode.

    Kompakt, semantisch dicht — nicht der ganze YAML-Inhalt.
    Reihenfolge ist bewusst: Name zuerst (höchstes Gewicht durch
    Position), dann Synonyme + Kategorien, am Ende narrative Felder.
    """
    parts: list[str] = []

    parts.append(m.canonical_name)

    if m.synonyms:
        syns = ", ".join(s.synonym for s in m.synonyms)
        parts.append(f"Auch bekannt als: {syns}")

    # Methoden-Typen + Phasen + Settings: hilft semantisch
    # ("Reflexion", "Energizer", "Remote-tauglich")
    type_labels = [link.method_type.label_de for link in m.type_links if link.method_type]
    if type_labels:
        parts.append("Typ: " + ", ".join(type_labels))

    phase_labels = [link.phase.label_de for link in m.phase_links if link.phase]
    if phase_labels:
        parts.append("Phase: " + ", ".join(phase_labels))

    setting_labels = [
        link.setting.label_de
        for link in m.setting_links
        if link.setting and link.suitability in ("excellent", "good")
    ]
    if setting_labels:
        parts.append("Setting: " + ", ".join(setting_labels))

    audience_labels = [
        link.audience.label_de for link in m.audience_links if link.audience
    ]
    if audience_labels:
        parts.append("Zielgruppe: " + ", ".join(audience_labels))

    group_labels = [
        link.group_size.label_de for link in m.group_size_links if link.group_size
    ]
    if group_labels:
        parts.append("Gruppengröße: " + ", ".join(group_labels))

    if m.tags:
        parts.append("Tags: " + ", ".join(m.tags))

    if m.short_description:
        parts.append(m.short_description.strip())

    # Step-Labels — kurze Hinweise auf Ablaufstruktur
    if m.steps:
        step_labels = [s.label for s in sorted(m.steps, key=lambda s: s.order_index)]
        parts.append("Ablauf: " + " → ".join(step_labels))

    if m.originator:
        parts.append(f"Urheber: {m.originator}")

    return "\n".join(parts)


def fetch_methods(session, only_missing: bool = True) -> list[Method]:
    stmt = select(Method).options(
        selectinload(Method.synonyms),
        selectinload(Method.steps),
        selectinload(Method.phase_links).selectinload(MethodPhase.phase),
        selectinload(Method.setting_links).selectinload(MethodSetting.setting),
        selectinload(Method.audience_links).selectinload(MethodTargetAudience.audience),
        selectinload(Method.group_size_links).selectinload(MethodGroupSize.group_size),
        selectinload(Method.type_links).selectinload(MethodTypeLink.method_type),
    )
    if only_missing:
        stmt = stmt.where(Method.embedding.is_(None))
    stmt = stmt.order_by(Method.canonical_name)
    return list(session.execute(stmt).scalars().all())


def embed_batch(
    client,
    texts: list[str],
    model: str,
    *,
    expected_dim: int,
    truncate_dim: int | None = None,
) -> list[list[float]]:
    """Ein Batch durchs OpenAI-API jagen, mit Retry + Dimension-Check.

    ``truncate_dim`` wird als ``dimensions``-Parameter an die API
    weitergereicht, wenn das Modell das unterstützt (Matryoshka-Cut).
    Ist es None, kommt der native Vektor zurück.
    """
    create_kwargs: dict = {"model": model, "input": texts}
    if truncate_dim is not None:
        create_kwargs["dimensions"] = truncate_dim

    last_err: Exception | None = None
    for attempt in range(1, RETRY_MAX + 1):
        try:
            resp = client.embeddings.create(**create_kwargs)
            vectors = [d.embedding for d in resp.data]
            if not vectors:
                raise RuntimeError("Leere Antwort von OpenAI")
            if len(vectors[0]) != expected_dim:
                raise RuntimeError(
                    f"Embedding-Dimension {len(vectors[0])} passt nicht zur "
                    f"erwarteten {expected_dim} (Modell {model}). "
                    f"Spalte methods.embedding ist auf {expected_dim} dim festgelegt."
                )
            return vectors
        except Exception as e:  # noqa: BLE001
            last_err = e
            cls = e.__class__.__name__
            # RateLimitError / APIConnectionError werden retry-würdig behandelt;
            # alles andere fliegt direkt weiter.
            retryable = cls in {"RateLimitError", "APIConnectionError", "APITimeoutError"}
            if not retryable or attempt == RETRY_MAX:
                raise
            wait = RETRY_BACKOFF_BASE**attempt
            print(
                f"  ⚠ {cls} (Versuch {attempt}/{RETRY_MAX}), warte {wait:.1f}s …",
                file=sys.stderr,
            )
            time.sleep(wait)
    # Sollte nicht erreichbar sein
    raise RuntimeError(f"Embed fehlgeschlagen: {last_err}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Alle Embeddings neu berechnen (auch wenn schon vorhanden)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Nur die representational texts anzeigen — kein API-Call, kein DB-Write",
    )
    parser.add_argument(
        "--model",
        default=MODEL_DEFAULT,
        help=f"OpenAI-Embedding-Modell (default: {MODEL_DEFAULT})",
    )
    parser.add_argument(
        "--dimensions",
        type=int,
        default=None,
        help=(
            "Vektor-Dimension reduzieren (Matryoshka-Cut). Nur für "
            "text-embedding-3-small/large unterstützt. Default: native "
            "Modell-Dimension. Setze auf 1536, um text-embedding-3-large "
            "in die methods.embedding-Spalte einzupassen."
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximal N Methoden einbetten (zum Testen)",
    )
    args = parser.parse_args()

    native_dim = MODEL_NATIVE_DIM.get(args.model, EXPECTED_DIM)
    truncate_dim = args.dimensions

    # Wenn truncate_dim gesetzt ist, muss das Modell den dimensions-Parameter
    # unterstützen — sonst frühzeitig abbrechen statt API-Fehler kassieren.
    if truncate_dim is not None and args.model not in MODEL_SUPPORTS_DIMENSIONS:
        print(
            f"❌ Modell {args.model} unterstützt --dimensions nicht. "
            f"Bekannte Modelle mit Truncate: "
            f"{', '.join(sorted(MODEL_SUPPORTS_DIMENSIONS))}",
            file=sys.stderr,
        )
        return 2

    # Effektive Vektor-Dimension nach evtl. Truncate.
    effective_dim = truncate_dim if truncate_dim is not None else native_dim

    if effective_dim != EXPECTED_DIM and not args.dry_run:
        # Truncate-Hint anbieten, wenn das Modell ihn unterstützt.
        hint = ""
        if args.model in MODEL_SUPPORTS_DIMENSIONS and effective_dim > EXPECTED_DIM:
            hint = (
                f"\n   Tipp: füge `--dimensions {EXPECTED_DIM}` hinzu, "
                f"um den Vektor passend zu trimmen."
            )
        print(
            f"❌ Modell {args.model} liefert {effective_dim}-dim Vektoren, "
            f"aber methods.embedding ist auf {EXPECTED_DIM} festgelegt.{hint}",
            file=sys.stderr,
        )
        return 2

    if not args.dry_run and not os.environ.get("OPENAI_API_KEY"):
        print(
            "❌ OPENAI_API_KEY ist nicht gesetzt. Setze ihn z.B. via\n"
            "   export OPENAI_API_KEY=sk-...\n"
            "oder leg ihn in .env ab.",
            file=sys.stderr,
        )
        return 2

    client = None
    if not args.dry_run:
        try:
            from openai import OpenAI
        except ImportError:
            print(
                "❌ Paket 'openai' fehlt. Installiere via\n"
                "   pip install -e '.[embed]'",
                file=sys.stderr,
            )
            return 2
        client = OpenAI()

    with session_scope() as session:
        methods = fetch_methods(session, only_missing=not args.force)
        if args.limit:
            methods = methods[: args.limit]

        if not methods:
            print(
                "Nichts zu tun — alle Methoden haben bereits ein Embedding. "
                "Mit --force kannst du sie neu berechnen."
            )
            return 0

        print(f"Bette {len(methods)} Methoden ein "
              f"(Modell={args.model}, batch={BATCH_SIZE}, force={args.force}).")

        payloads = [
            MethodPayload(
                method_id=str(m.id),
                slug=m.slug,
                text=build_representational_text(m),
            )
            for m in methods
        ]

        if args.dry_run:
            for p in payloads[:5]:
                print(f"\n────── {p.slug} ──────")
                print(p.text)
            if len(payloads) > 5:
                print(f"\n… und {len(payloads) - 5} weitere (--dry-run zeigt nur 5).")
            return 0

        # Batches durchschicken und sofort committen — so verlieren wir bei
        # Abbruch nur das aktuelle Batch.
        method_by_id = {str(m.id): m for m in methods}
        done = 0
        for i in range(0, len(payloads), BATCH_SIZE):
            batch = payloads[i : i + BATCH_SIZE]
            print(
                f"  • Batch {i // BATCH_SIZE + 1}/"
                f"{(len(payloads) + BATCH_SIZE - 1) // BATCH_SIZE} "
                f"({len(batch)} Methoden) …",
                end=" ",
                flush=True,
            )
            try:
                vectors = embed_batch(
                    client,
                    [p.text for p in batch],
                    args.model,
                    expected_dim=effective_dim,
                    truncate_dim=truncate_dim,
                )
            except Exception as e:  # noqa: BLE001
                print(f"FEHLER: {e}", file=sys.stderr)
                continue

            for p, vec in zip(batch, vectors, strict=True):
                m = method_by_id[p.method_id]
                m.embedding = vec
            session.flush()  # Per-Batch-Flush; Commit am Ende des with-Blocks.
            done += len(batch)
            print(f"ok ({done} kumuliert)")

        print(f"\n✓ {done}/{len(methods)} Embeddings geschrieben.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
