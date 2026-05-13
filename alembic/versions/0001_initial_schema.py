"""Initial schema

Revision ID: 0001
Revises:
Create Date: 2026-04-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Extensions sind schon via docker/initdb angelegt; hier defensiv nochmal.
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
    op.execute('CREATE EXTENSION IF NOT EXISTS "pg_trgm"')
    op.execute('CREATE EXTENSION IF NOT EXISTS "unaccent"')
    op.execute('CREATE EXTENSION IF NOT EXISTS "vector"')

    # -------- Referenztabellen --------
    op.create_table(
        "phases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(64), nullable=False, unique=True),
        sa.Column("label_de", sa.String(128), nullable=False),
        sa.Column("label_en", sa.String(128)),
        sa.Column("description", sa.Text()),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(64), nullable=False, unique=True),
        sa.Column("label_de", sa.String(128), nullable=False),
        sa.Column("label_en", sa.String(128)),
        sa.Column("description", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "group_sizes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(32), nullable=False, unique=True),
        sa.Column("label_de", sa.String(64), nullable=False),
        sa.Column("min_participants", sa.Integer()),
        sa.Column("max_participants", sa.Integer()),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "target_audiences",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(64), nullable=False, unique=True),
        sa.Column("label_de", sa.String(128), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "method_types",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(64), nullable=False, unique=True),
        sa.Column("label_de", sa.String(128), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # -------- Sources --------
    op.create_table(
        "sources",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("title", sa.String(400), nullable=False),
        sa.Column("author", sa.String(300)),
        sa.Column("year", sa.Integer()),
        sa.Column("isbn", sa.String(32)),
        sa.Column("url", sa.String(1000)),
        sa.Column("notes", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # -------- Methods (Haupttabelle) --------
    op.create_table(
        "methods",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column("slug", sa.String(160), nullable=False, unique=True),
        sa.Column("canonical_name", sa.String(200), nullable=False),
        sa.Column("short_description", sa.Text()),
        sa.Column("long_description", sa.Text()),
        sa.Column("originator", sa.String(200)),
        sa.Column("origin_year", sa.Integer()),
        sa.Column("origin_context", sa.Text()),
        sa.Column("duration_min_minutes", sa.Integer()),
        sa.Column("duration_typical_minutes", sa.Integer()),
        sa.Column("duration_max_minutes", sa.Integer()),
        sa.Column("materials", postgresql.ARRAY(sa.String())),
        sa.Column("tags", postgresql.ARRAY(sa.String())),
        sa.Column("quality_status", sa.String(32), nullable=False, server_default="ai_draft"),
        sa.Column("content_license", sa.String(32), nullable=False, server_default="proprietary"),
        sa.Column("content_author", sa.String(200)),
        sa.Column("embedding", Vector(1536), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "quality_status IN ('ai_draft','human_reviewed','expert_verified','deprecated')",
            name="ck_methods_quality_status",
        ),
        sa.CheckConstraint(
            "content_license IN ('proprietary','cc-by-4.0','cc-by-sa-4.0','public-domain')",
            name="ck_methods_content_license",
        ),
        sa.CheckConstraint(
            "duration_min_minutes IS NULL OR duration_min_minutes >= 0",
            name="ck_methods_duration_min_nonneg",
        ),
        sa.CheckConstraint(
            "duration_max_minutes IS NULL OR duration_max_minutes >= duration_min_minutes",
            name="ck_methods_duration_order",
        ),
    )

    op.execute(
        "CREATE INDEX ix_methods_name_trgm ON methods "
        "USING gin (canonical_name gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX ix_methods_fts_de ON methods USING gin ("
        "to_tsvector('german_unaccent', "
        "coalesce(canonical_name,'') || ' ' || "
        "coalesce(short_description,'') || ' ' || "
        "coalesce(long_description,'')))"
    )

    # -------- Child-Tabellen --------
    op.create_table(
        "method_synonyms",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "method_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("methods.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("synonym", sa.String(200), nullable=False),
        sa.Column("language", sa.String(8), nullable=False, server_default="de"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("method_id", "synonym", name="uq_method_synonym"),
    )
    op.execute(
        "CREATE INDEX ix_method_synonyms_trgm ON method_synonyms "
        "USING gin (synonym gin_trgm_ops)"
    )

    op.create_table(
        "method_steps",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "method_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("methods.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("duration_minutes", sa.Integer()),
        sa.Column("facilitator_note", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("method_id", "order_index", name="uq_method_step_order"),
    )

    op.create_table(
        "method_variations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "method_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("methods.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "method_sources",
        sa.Column(
            "method_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("methods.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "source_id",
            sa.Integer(),
            sa.ForeignKey("sources.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("relation", sa.String(32), nullable=False, server_default="reference"),
        sa.Column("page_reference", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # -------- N:M zu Referenzen --------
    op.create_table(
        "method_phases",
        sa.Column(
            "method_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("methods.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "phase_id",
            sa.Integer(),
            sa.ForeignKey("phases.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )

    op.create_table(
        "method_settings",
        sa.Column(
            "method_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("methods.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "setting_id",
            sa.Integer(),
            sa.ForeignKey("settings.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("suitability", sa.String(16), nullable=False, server_default="good"),
        sa.Column("suitability_note", sa.Text()),
    )

    op.create_table(
        "method_group_sizes",
        sa.Column(
            "method_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("methods.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "group_size_id",
            sa.Integer(),
            sa.ForeignKey("group_sizes.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )

    op.create_table(
        "method_target_audiences",
        sa.Column(
            "method_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("methods.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "audience_id",
            sa.Integer(),
            sa.ForeignKey("target_audiences.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )

    op.create_table(
        "method_type_links",
        sa.Column(
            "method_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("methods.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "method_type_id",
            sa.Integer(),
            sa.ForeignKey("method_types.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )

    op.create_table(
        "review_log",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "method_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("methods.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("reviewer", sa.String(100), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("from_status", sa.String(32)),
        sa.Column("to_status", sa.String(32)),
        sa.Column("note", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    for t in [
        "review_log",
        "method_type_links",
        "method_target_audiences",
        "method_group_sizes",
        "method_settings",
        "method_phases",
        "method_sources",
        "method_variations",
        "method_steps",
        "method_synonyms",
        "methods",
        "sources",
        "method_types",
        "target_audiences",
        "group_sizes",
        "settings",
        "phases",
    ]:
        op.drop_table(t)
