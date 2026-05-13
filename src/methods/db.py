"""DB-Engine und Session-Factory."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

# override=True macht .env zur Source-of-Truth — sonst gewinnen vor-exportierte
# Shell-Variablen (auch leere) gegen .env, was zu schwer diagnostizierbaren
# "OPENAI_API_KEY fehlt"-Fehlern führt, obwohl der Key in .env steht.
load_dotenv(override=True)

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+psycopg://methods:methods@localhost:5432/methods",
)

engine = create_engine(DATABASE_URL, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transaktions-Scope für Ingest-Skripte: commit bei Erfolg, rollback sonst."""
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
