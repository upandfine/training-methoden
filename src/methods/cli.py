"""Typer-CLI für Seed- und Ingest-Kommandos."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from .db import session_scope
from .ingest import ingest_methods_dir, seed_references
from .models import Method

app = typer.Typer(help="Trainingsmethoden-Katalog — Datenbank-Tooling")
console = Console()


@app.command("seed-refs")
def seed_refs_cmd(
    base_dir: Path = typer.Option(Path("."), help="Projekt-Root (enthält data/seed/)"),
) -> None:
    """Lade/aktualisiere die Referenztabellen aus data/seed/*.yaml."""
    with session_scope() as s:
        seed_references(s, base_dir=base_dir)


@app.command("ingest")
def ingest_cmd(
    methods_dir: Path = typer.Argument(Path("data/methods")),
    force: bool = typer.Option(False, "--force", help="Auch menschen-reviewte Einträge überschreiben"),
) -> None:
    """Lade Methoden aus YAML-Dateien in die DB (idempotent)."""
    if not methods_dir.exists():
        console.print(f"[yellow]Kein Verzeichnis: {methods_dir} — nichts zu tun.[/yellow]")
        raise typer.Exit(0)

    with session_scope() as s:
        counts = ingest_methods_dir(s, methods_dir, force=force)

    table = Table(title="Ingest-Ergebnis")
    for col in ("created", "updated", "skipped", "errors"):
        table.add_column(col, justify="right")
    table.add_row(*[str(counts[c]) for c in ("created", "updated", "skipped", "errors")])
    console.print(table)

    if counts["errors"]:
        raise typer.Exit(1)


@app.command("list")
def list_cmd(
    status: str | None = typer.Option(None, help="Filter: ai_draft | human_reviewed | expert_verified"),
    limit: int = typer.Option(50, min=1, max=500),
) -> None:
    """Liste Methoden mit wichtigsten Feldern."""
    from sqlalchemy import select

    with session_scope() as s:
        q = select(Method).order_by(Method.canonical_name).limit(limit)
        if status:
            q = q.where(Method.quality_status == status)
        rows = s.execute(q).scalars().all()

    table = Table(title=f"Methoden ({len(rows)})")
    table.add_column("Name")
    table.add_column("Slug", style="dim")
    table.add_column("Status")
    table.add_column("Urheber")
    for m in rows:
        table.add_row(m.canonical_name, m.slug, m.quality_status, m.originator or "—")
    console.print(table)


@app.command("stats")
def stats_cmd() -> None:
    """Kurze Kennzahlen zum Katalog."""
    from sqlalchemy import func, select

    with session_scope() as s:
        total = s.scalar(select(func.count()).select_from(Method))
        by_status = s.execute(
            select(Method.quality_status, func.count())
            .group_by(Method.quality_status)
            .order_by(Method.quality_status)
        ).all()
        with_embedding = s.scalar(
            select(func.count())
            .select_from(Method)
            .where(Method.embedding.is_not(None))
        )

    table = Table(title="Katalog-Statistik")
    table.add_column("Metrik")
    table.add_column("Wert", justify="right")
    table.add_row("Methoden gesamt", str(total))
    for status, n in by_status:
        table.add_row(f"  davon {status}", str(n))
    table.add_row("Mit Embedding", f"{with_embedding} / {total}")
    console.print(table)


@app.command("embed")
def embed_cmd(
    force: bool = typer.Option(False, "--force", help="Auch vorhandene Embeddings neu berechnen"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Nur Texte anzeigen, kein API-Call"),
    model: str = typer.Option("text-embedding-3-small", help="OpenAI-Embedding-Modell"),
    dimensions: int | None = typer.Option(
        None,
        "--dimensions",
        help=(
            "Vektor auf N Dimensionen kürzen (Matryoshka-Cut, nur für "
            "text-embedding-3-small/-large). Setze auf 1536 für 3-large, "
            "damit es in die methods.embedding-Spalte passt."
        ),
    ),
    limit: int | None = typer.Option(None, help="Max. N Methoden einbetten (Test)"),
) -> None:
    """Berechnet Embeddings für alle Methoden via OpenAI API.

    Dünner Wrapper um ``scripts/embed_methods.py`` — gleiche Argumente,
    integriert in das Typer-CLI, damit ``make embed`` analog zu
    ``make ingest`` aussieht. Braucht ``OPENAI_API_KEY`` in der Umgebung.
    """
    import sys
    from pathlib import Path

    project_root = Path(__file__).resolve().parents[2]
    script = project_root / "scripts" / "embed_methods.py"
    sys.argv = [str(script)]
    if force:
        sys.argv.append("--force")
    if dry_run:
        sys.argv.append("--dry-run")
    sys.argv.extend(["--model", model])
    if dimensions is not None:
        sys.argv.extend(["--dimensions", str(dimensions)])
    if limit is not None:
        sys.argv.extend(["--limit", str(limit)])

    sys.path.insert(0, str(project_root / "scripts"))
    import embed_methods

    raise typer.Exit(embed_methods.main())


if __name__ == "__main__":
    app()
