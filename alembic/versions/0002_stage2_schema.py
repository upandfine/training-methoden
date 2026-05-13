"""Stage-2 schema: steps/variations/pitfalls + method-level stage_2 status.

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-23

Ändert das Schema so, dass die YAML-Struktur der Stufe-2-Felder
(steps, variations, pitfalls) 1:1 gespeichert werden kann:

  * ``method_steps``: ``title``→``label``, ``description``→``trainer_action``,
    neues Pflichtfeld ``participant_action``, ``facilitator_note``→``note``.
  * ``method_variations``: ``title``→``name``, neue Felder ``when_to_use``
    und ``setting_codes``.
  * ``method_pitfalls``: komplett neu (issue + countermeasure + severity +
    setting_codes).
  * ``methods``: ``stage_2_status`` + ``stage_2_author`` — Stufe-2-Reife
    getrennt vom Top-Level ``quality_status``.
  * ``sources``: ``doi`` hinzufügen (YAML-Schema sieht DOI vor).

Bewusst so geschrieben, dass bestehende Daten (falls vorhanden) per
RENAME erhalten bleiben. Neues Pflichtfeld ``participant_action`` wird
temporär nullable angelegt, mit Platzhalter befüllt und dann auf NOT NULL
gesetzt — so läuft die Migration auch auf einer DB, die zufällig schon
Zeilen aus 0001 enthält, ohne zu crashen.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # methods: stage_2_status + stage_2_author
    # ------------------------------------------------------------------
    op.add_column(
        "methods",
        sa.Column(
            "stage_2_status",
            sa.String(32),
            nullable=False,
            server_default="none",
        ),
    )
    op.add_column("methods", sa.Column("stage_2_author", sa.String(200), nullable=True))
    op.create_check_constraint(
        "ck_methods_stage_2_status",
        "methods",
        "stage_2_status IN ('none','ai_draft','human_reviewed','expert_verified')",
    )

    # ------------------------------------------------------------------
    # sources: doi
    # ------------------------------------------------------------------
    op.add_column("sources", sa.Column("doi", sa.String(128), nullable=True))

    # ------------------------------------------------------------------
    # method_steps: rename + expand
    # ------------------------------------------------------------------
    op.alter_column("method_steps", "title", new_column_name="label")
    op.alter_column("method_steps", "description", new_column_name="trainer_action")
    op.alter_column("method_steps", "facilitator_note", new_column_name="note")
    # participant_action: temporär nullable, dann Backfill, dann NOT NULL.
    op.add_column(
        "method_steps",
        sa.Column("participant_action", sa.Text(), nullable=True),
    )
    op.execute(
        "UPDATE method_steps "
        "SET participant_action = '' "
        "WHERE participant_action IS NULL"
    )
    op.alter_column("method_steps", "participant_action", nullable=False)

    # ------------------------------------------------------------------
    # method_variations: rename + expand
    # ------------------------------------------------------------------
    op.alter_column("method_variations", "title", new_column_name="name")
    op.add_column("method_variations", sa.Column("when_to_use", sa.Text(), nullable=True))
    op.add_column(
        "method_variations",
        sa.Column("setting_codes", postgresql.ARRAY(sa.String()), nullable=True),
    )

    # ------------------------------------------------------------------
    # method_pitfalls: neu
    # ------------------------------------------------------------------
    op.create_table(
        "method_pitfalls",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "method_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("methods.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("issue", sa.Text(), nullable=False),
        sa.Column("countermeasure", sa.Text(), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False, server_default="common"),
        sa.Column("setting_codes", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "severity IN ('common', 'rare')",
            name="ck_method_pitfalls_severity",
        ),
    )
    op.create_index(
        "ix_method_pitfalls_method_id", "method_pitfalls", ["method_id"]
    )


def downgrade() -> None:
    # method_pitfalls
    op.drop_index("ix_method_pitfalls_method_id", table_name="method_pitfalls")
    op.drop_table("method_pitfalls")

    # method_variations
    op.drop_column("method_variations", "setting_codes")
    op.drop_column("method_variations", "when_to_use")
    op.alter_column("method_variations", "name", new_column_name="title")

    # method_steps
    op.alter_column("method_steps", "participant_action", nullable=True)
    op.drop_column("method_steps", "participant_action")
    op.alter_column("method_steps", "note", new_column_name="facilitator_note")
    op.alter_column("method_steps", "trainer_action", new_column_name="description")
    op.alter_column("method_steps", "label", new_column_name="title")

    # sources
    op.drop_column("sources", "doi")

    # methods
    op.drop_constraint("ck_methods_stage_2_status", "methods", type_="check")
    op.drop_column("methods", "stage_2_author")
    op.drop_column("methods", "stage_2_status")
