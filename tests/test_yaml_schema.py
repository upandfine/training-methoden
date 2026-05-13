"""Schema-Smoke-Tests: alle Methoden-YAMLs müssen durch ``MethodIn`` laufen.

Kein DB-Zugriff — reine Parse/Validate-Tests. Läuft damit auch ohne
laufenden Postgres.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from methods.schemas import MethodIn

METHODS_DIR = Path(__file__).resolve().parent.parent / "data" / "methods"


def _yaml_files() -> list[Path]:
    return sorted(METHODS_DIR.glob("*.yaml"))


def test_methods_dir_has_content() -> None:
    files = _yaml_files()
    assert len(files) >= 150, f"Zu wenige Methoden-YAMLs: {len(files)}"


@pytest.mark.parametrize("yaml_path", _yaml_files(), ids=lambda p: p.name)
def test_yaml_parses_and_validates(yaml_path: Path) -> None:
    raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    assert isinstance(raw, dict), f"Top-Level muss Mapping sein: {yaml_path.name}"

    # pydantic validiert — jedes Feld gegen die Literals,
    # Step-Order-Contiguität, Phase-Primary-Unique, Duration-Order.
    MethodIn.model_validate(raw)


def test_stage2_methods_have_minimum_depth() -> None:
    """Alle stage_2_status != none müssen steps+variations+pitfalls haben."""
    missing: list[str] = []
    for p in _yaml_files():
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
        if raw.get("stage_2_status", "none") == "none":
            continue
        m = MethodIn.model_validate(raw)
        if not m.steps:
            missing.append(f"{p.name}: keine steps")
        if not m.variations:
            missing.append(f"{p.name}: keine variations")
        # pitfalls sind empfohlen, nicht strikt Pflicht
    assert not missing, "Stage-2-Methoden ohne erforderliche Tiefe:\n" + "\n".join(missing)


def test_step_duration_sum_within_typical() -> None:
    """Σ step.duration_minutes darf duration_max_minutes * 1.2 nicht überschreiten."""
    problems: list[str] = []
    for p in _yaml_files():
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
        m = MethodIn.model_validate(raw)
        if not m.steps or m.duration_max_minutes is None:
            continue
        total = sum(s.duration_minutes or 0 for s in m.steps)
        if total > m.duration_max_minutes * 1.2:
            problems.append(
                f"{p.name}: Σ steps = {total} > duration_max × 1.2 = "
                f"{m.duration_max_minutes * 1.2:.0f}"
            )
    assert not problems, "Duration-Budget überschritten:\n" + "\n".join(problems)
