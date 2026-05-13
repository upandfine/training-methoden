# Quickstart targets. Assumes docker compose v2 and Python 3.11+.
#
# Python-Interpreter: per Default `python3`, aber überschreibbar.
# Beispiel: `make install PYTHON=python3.12` — wenn dein System-`python3`
# zu alt ist (<3.11), nimmst du eine explizite Version mit.
PYTHON ?= python3

.PHONY: help up down logs psql reset venv install migrate revision seed ingest verify test api check-python embed embed-dry embed-force eval-search smoketest ui snapshot api-snapshot

help:
	@echo "Targets:"
	@echo "  up        Start Postgres + Adminer"
	@echo "  down      Stop containers (keeps data volume)"
	@echo "  reset     Stop and WIPE the data volume (danger)"
	@echo "  logs      Tail container logs"
	@echo "  psql      Open a psql shell against the running DB"
	@echo "  venv      Create .venv (nutzt \$$PYTHON, default: python3)"
	@echo "  install   Install Python deps into .venv (editable + dev + api extras)"
	@echo "  migrate   Apply Alembic migrations"
	@echo "  revision m='msg'   Autogenerate a new migration"
	@echo "  seed      Load reference data (phases, settings, ...)"
	@echo "  ingest    Load method YAMLs from data/methods/"
	@echo "  verify    Syntax + YAML-Schema-Check + docker-compose config"
	@echo "  test      Run pytest (ohne DB-Zugriff)"
	@echo "  api       Start FastAPI dev-server (uvicorn --reload)"
	@echo "  browse    Build single-file HTML browser (keine DB nötig)"
	@echo "  embed     Embeddings für noch nicht eingebettete Methoden berechnen"
	@echo "  embed-force  Alle Embeddings neu berechnen"
	@echo "  embed-dry    Representational texts anzeigen (kein API-Call)"
	@echo "  eval-search  Such-Smoketest mit ~15 Beispiel-Queries"
	@echo "  smoketest    Komplette E2E-Pipeline (up → migrate → seed → ingest → embed)"
	@echo "  ui           Öffnet das Sucher-UI im Browser (uvicorn muss laufen)"
	@echo "  snapshot     Exportiert data/snapshot/ aus Postgres (für Cloud-Deploy)"
	@echo "  api-snapshot Startet die API im Snapshot-Modus (kein Postgres nötig)"
	@echo ""
	@echo "Beispiel bei altem system-python3:"
	@echo "  make install PYTHON=python3.12"

up:
	docker compose up -d

down:
	docker compose down

reset:
	docker compose down -v

logs:
	docker compose logs -f --tail=100

psql:
	docker compose exec db psql -U methods -d methods

check-python:
	@$(PYTHON) -c "import sys; v=sys.version_info; exit(0 if (v.major,v.minor) >= (3,11) else 1)" \
		|| (echo ""; \
		    echo "❌ $(PYTHON) ist zu alt (Projekt braucht 3.11+)."; \
		    echo "   Installiere eine neuere Version, z.B.:"; \
		    echo "     brew install python@3.12"; \
		    echo "   Und ruf make mit expliziter Version auf:"; \
		    echo "     make install PYTHON=python3.12"; \
		    echo ""; exit 1)

venv: check-python
	$(PYTHON) -m venv .venv

install: venv
	./.venv/bin/pip install --upgrade pip
	./.venv/bin/pip install -e ".[dev,api]"

migrate:
	./.venv/bin/alembic upgrade head

revision:
	@test -n "$(m)" || (echo "Usage: make revision m='your message'" && exit 1)
	./.venv/bin/alembic revision --autogenerate -m "$(m)"

seed:
	./.venv/bin/methods seed-refs

ingest:
	./.venv/bin/methods ingest data/methods

verify:
	./.venv/bin/python -m compileall -q src
	./.venv/bin/python -m compileall -q alembic
	./.venv/bin/python scripts/verify_yamls.py
	docker compose config -q
	@echo "ok"

test:
	./.venv/bin/pytest tests -q

api:
	./.venv/bin/uvicorn methods.api:app --reload

browse:
	./.venv/bin/python scripts/build_static_browser.py
	@echo "→ browse/katalog.html im Browser öffnen (kein Server nötig)"

embed:
	./.venv/bin/methods embed

embed-force:
	./.venv/bin/methods embed --force

embed-dry:
	./.venv/bin/methods embed --dry-run

eval-search:
	./.venv/bin/python scripts/eval_search.py

# Erzeugt einen statischen Snapshot aus der lokalen Postgres-DB. Output
# landet in data/snapshot/, wird in Git eingecheckt und vom Cloud-
# Container als Datenquelle gelesen (STORAGE=snapshot).
snapshot:
	./.venv/bin/python scripts/snapshot.py

# Startet die API im Snapshot-Modus — nützlich, um vor dem Deploy lokal
# zu prüfen, ob der Snapshot wie erwartet bedient wird. Braucht keinen
# Postgres, aber wenn semantische Suche getestet werden soll, muss
# OPENAI_API_KEY gesetzt sein.
api-snapshot:
	STORAGE=snapshot ./.venv/bin/uvicorn methods.api:app --reload --port 8765

# Öffnet das Single-Page-UI direkt im Default-Browser. Erwartet, dass uvicorn
# unter dem dort konfigurierten Port (8765) läuft.
ui:
	@open http://127.0.0.1:8765/app/ 2>/dev/null \
	  || xdg-open http://127.0.0.1:8765/app/ 2>/dev/null \
	  || echo "→ Bitte http://127.0.0.1:8765/app/ manuell im Browser öffnen."

# E2E-Smoketest: bringt eine frische Umgebung von 0 auf "Suche funktioniert".
# Erwartet OPENAI_API_KEY in der Umgebung (sonst überspringt embed).
smoketest:
	$(MAKE) up
	@echo "→ warte 5s auf Postgres-Bootstrap …"; sleep 5
	$(MAKE) migrate
	$(MAKE) seed
	$(MAKE) ingest
	@if [ -n "$$OPENAI_API_KEY" ]; then \
	  $(MAKE) embed; \
	else \
	  echo "⚠ OPENAI_API_KEY nicht gesetzt — Embed-Schritt übersprungen."; \
	fi
	./.venv/bin/methods stats
	@echo ""
	@echo "✓ Smoketest fertig. Starte API: make api"
