"""HNSW-Index auf methods.embedding (cosine).

Revision ID: 0003
Revises: 0002
Create Date: 2026-04-27

Hintergrund: Die Spalte ``methods.embedding`` (Vector(1536)) existiert seit
0001, ist aber nullable und unindiziert. Bei nur ~150 Zeilen würde ein
Sequential Scan ausreichen — wir legen den Index trotzdem an, damit
``ORDER BY embedding <=> query_embedding`` ab Tag 1 indexgestützt läuft
und das Verhalten unter Wachstum stabil bleibt (HNSW skaliert besser als
IVFFlat ohne Re-Training, wenn die Datenmenge steigt).

Operatorklasse:
  * ``vector_cosine_ops`` für ``<=>`` (cosine distance).
  * Cosine, weil OpenAI text-embedding-3-small vorab L2-normalisierte
    Vektoren ausgibt — cosine und dot product sind dann effektiv
    äquivalent, aber cosine ist der semantisch klarere Ausdruck.

Parameter (m, ef_construction): Default-Werte von pgvector reichen für
< 10k Zeilen. Tuning erst, wenn Latenz spürbar wird.
"""

from __future__ import annotations

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # HNSW-Index auf den Vektor — pgvector >= 0.5.0 vorausgesetzt.
    # Der Index ignoriert NULL-Werte automatisch, daher ist es OK, ihn
    # anzulegen, bevor alle Einbettungen befüllt sind.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_methods_embedding_hnsw "
        "ON methods USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_methods_embedding_hnsw")
