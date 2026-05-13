"""Import-Smoke-Tests — stellen sicher, dass alle Module syntaktisch OK sind
und die wichtigsten öffentlichen Namen importiert werden können.
"""

from __future__ import annotations


def test_models_importable() -> None:
    from methods import models

    assert hasattr(models, "Method")
    assert hasattr(models, "MethodStep")
    assert hasattr(models, "MethodPitfall")
    assert hasattr(models, "MethodVariation")
    assert hasattr(models, "STAGE_2_STATUSES")


def test_schemas_importable() -> None:
    from methods import schemas

    assert hasattr(schemas, "MethodIn")
    assert hasattr(schemas, "PitfallIn")
    assert hasattr(schemas, "VariationIn")
    assert hasattr(schemas, "StepIn")


def test_ingest_importable() -> None:
    from methods import ingest

    assert callable(ingest.ingest_methods_dir)
    assert callable(ingest.seed_references)


def test_api_importable() -> None:
    # FastAPI-Import fällt aus, wenn fastapi nicht installiert ist.
    # Deshalb optional: Test wird geskippt, wenn extras ``api`` fehlen.
    try:
        from methods import api  # noqa: F401
    except ImportError as exc:
        import pytest

        pytest.skip(f"API-Deps nicht installiert: {exc}")
