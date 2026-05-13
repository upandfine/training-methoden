# Trainingsmethoden-Katalog

Postgres-Datenbank + Python-Ingest-Pipeline für einen strukturierten Katalog
didaktischer Methoden. Ausrichtung: Trainer:innen in Unternehmen, Schule, Jugend,
IHK, Erwachsenenbildung — für **Präsenz, Remote und Hybrid**.

## Stack

- **Postgres 16 + pgvector** (via Docker) — inkl. `pg_trgm`, `unaccent`, eigene
  FTS-Config `german_unaccent`. Host-Port **55432** (DDEV belegt 5432).
- **Adminer** — hängt sich an DDEVs Traefik-Router, GUI unter
  http://adminer.methods.ddev.site
- **Python 3.11+**, SQLAlchemy 2.x, Alembic, Pydantic 2, Typer, Rich, PyYAML
- **FastAPI + Uvicorn** (optional, unter `api` extra) — Read-only-API für Katalog/Suche

## Deployment als Microservice

Zwei Pfade, je nach Use-Case:

### Snapshot-Mode (empfohlen für Cloud-Deploy)

Lokal arbeitest du mit Postgres + Ingest weiter wie gewohnt. Beim
„Veröffentlichen" erzeugst du einen statischen Snapshot, committest ihn,
pushst. Der Cloud-Container liest beim Boot den Snapshot in den Speicher
— **kein Postgres in der Cloud nötig**.

```bash
# Lokal: Methoden ändern, Embeddings rechnen, dann Snapshot bauen
./.venv/bin/methods ingest data/methods --force
make embed
make snapshot                       # data/snapshot/{methods,refs,embeddings.npy,manifest}.json/npy

# Vor dem Deploy: lokal im Snapshot-Mode testen
make api-snapshot
curl http://localhost:8765/health
# → {"status":"ok","methods_total":163,"storage":"snapshot","snapshot":{...}}

# In Git committen
git add data/snapshot
git commit -m "Snapshot YYYY-MM-DD"
git push

# Cloud (Sliplane / Fly / Render / Coolify) baut das Image:
#   STORAGE=snapshot ist Default im Dockerfile.
#   CORS_ORIGINS=https://methoden-frontend.example.com setzen.
#   OPENAI_API_KEY=sk-... setzen (Pflicht für mode=semantic).
```

**Footprint:** Ein Container, kein DB-Service. ~50-80 MB RAM, < €5/Monat
auf Hetzner-basierten PaaS. Updates: neuer Snapshot + Git-Push → CI/CD
deployt automatisch.

### Postgres-Mode (für eigene Cloud-DB oder Hybrid-Setups)

Falls du parallel zum Frontend auch online Authoring willst oder eine
existente Managed-Postgres nutzen möchtest:

```bash
# Image bauen + alle Services starten (DB + App + Adminer)
STORAGE=postgres docker compose --profile app up -d --build

# Initial-Setup beim ersten Start
docker compose exec app alembic upgrade head
docker compose exec app methods seed-refs
docker compose exec app methods ingest data/methods
docker compose exec app methods embed       # nur wenn OPENAI_API_KEY in .env

# Smoketest
curl http://localhost:8765/health
# → {"status":"ok","methods_total":163,"storage":"postgres"}
```

Endpoints (in beiden Modi identisch):

```
http://localhost:8765/methods                       Liste mit Filtern
http://localhost:8765/methods/{slug}                Vollständige Methode
http://localhost:8765/search?q=...&mode=hybrid      Hybrid-Suche
http://localhost:8765/aggregations/facets           Facetten-Counter
http://localhost:8765/docs                          Swagger-UI
```

### Konfiguration über `.env`

| Variable | Default | Zweck |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://methods:methods@db:5432/methods` | Compose-intern |
| `OPENAI_API_KEY` | — | Für semantische Suche; ohne läuft Hybrid rein lexikalisch |
| `OPENAI_EMBEDDING_MODEL` | `text-embedding-3-small` | Modell für Query-Embeddings |
| `CORS_ORIGINS` | `*` | In Prod auf `https://frontend.example.com,https://...` setzen |
| `WEB_CONCURRENCY` | `4` | Gunicorn-Worker; bei 1-2 vCPU okay, bei mehr Last hochsetzen |
| `API_PORT` | `8765` | Host-Port des `app`-Service |

### API-Pattern für Frontend / Agent

**Filter-Sidebar mit live-Counter:**

```bash
# Alle Facetten ohne Filter — initiale UI-Anzeige
curl http://localhost:8765/aggregations/facets
# → {total: 163, phases: [{code: "warmup", label_de: "Warm-up", count: 28}, ...], ...}

# Mit aktiven Filtern — UI weiß, welche Optionen noch "lohnen"
curl 'http://localhost:8765/aggregations/facets?setting=remote_sync&audience=corporate'
# → angepasste counts pro Facette
```

**KI-Agent: strukturierte Suche (kein Embedding nötig, sub-10ms):**

```bash
curl 'http://localhost:8765/methods?phase=warmup&setting=remote_sync&audience=corporate&limit=10'
```

**KI-Agent: natürlich-sprachliche Suche (Embedding-Cache, ~200-800ms first-call):**

```bash
curl 'http://localhost:8765/search?q=Methode+f%C3%BCr+Konflikt+zwischen+zwei+Personen&mode=hybrid&limit=5'
```

**Detail-Lookup (für UI-Drawer oder Agent-Kontext):**

```bash
curl http://localhost:8765/methods/tetralemma
```

Der Endpoint liefert das Method-Detail-Objekt inkl. steps/variations/pitfalls/sources
in einem Aufruf — keine N+1-Queries auf Frontend-Seite.

### Lokale Entwicklung ohne Container

Wenn du die App lokal mit `make api` laufen lassen willst (Hot-Reload, Debugger),
bleibt das wie bisher unverändert: `docker compose up -d` startet **nur** DB + Adminer
(Profil `app` ist optional). Der Container ist ein Add-on, keine Pflicht.

## Quickstart

```bash
# 1. Config
cp .env.example .env

# 2. DB hochfahren
make up

# 3. Python-Umgebung + Abhängigkeiten
make install

# 4. Schema anlegen
make migrate

# 5. Referenzdaten (Phasen, Settings, Gruppengrößen, ...) laden
make seed

# 6. Beispiel-Methoden importieren
make ingest

# 7. Anschauen
make psql                                      # Postgres-Shell
./.venv/bin/methods list
./.venv/bin/methods stats
# Browser: http://adminer.methods.ddev.site    # Adminer via DDEV-Traefik

# 8. API starten (Swagger-UI unter /docs)
make api                                       # http://localhost:8000/docs
```

### E2E-Smoketest

Wenn du eine frische Maschine in einem Rutsch hochziehen willst:

```bash
# OpenAI-Key in .env oder Shell setzen, sonst wird der Embed-Schritt übersprungen
export OPENAI_API_KEY=sk-...
make install
pip install -e ".[embed]"   # einmalig, falls noch nicht passiert
make smoketest              # up → migrate → seed → ingest → embed → stats
make api                    # Server hoch
# in einer zweiten Shell:
./.venv/bin/python scripts/eval_search.py    # 15 Test-Queries gegen lexical / semantic / hybrid
```

`make smoketest` läuft auch ohne Key — der Embed-Schritt wird dann
übersprungen, die Suche fällt automatisch auf rein lexikalisch zurück.

### Voraussetzungen

- **Docker Desktop** läuft
- **DDEV** ist installiert und mindestens ein DDEV-Projekt wurde schon einmal
  gestartet (damit `ddev-router` läuft und das Netzwerk `ddev_default` existiert).
  Check: `docker network ls | grep ddev_default`. Wenn nichts kommt: ein
  beliebiges DDEV-Projekt starten (`ddev start` in einem DDEV-Projektordner)
  oder `ddev poweron`.

## API

FastAPI-Read-only-Endpoints (mit `make install` automatisch mit installiert):

| Pfad                              | Zweck                                                  |
|-----------------------------------|--------------------------------------------------------|
| `GET /health`                     | DB-Reachability + Methoden-Total                       |
| `GET /methods`                    | Liste mit Filtern (phase, setting, audience, ...)      |
| `GET /methods/{slug}`             | Vollständige Methode inkl. steps/variations/pitfalls   |
| `GET /search?q=...&mode=hybrid`   | Hybrid-Suche (lexical + semantic). `mode=lexical\|semantic\|hybrid` |
| `GET /aggregations/facets`        | Counter pro Filter-Facette (für UI-Sidebar + Agent-Briefing) |
| `GET /reference/phases`           | Dropdown-Daten: Phasen                                 |
| `GET /reference/settings`         | Dropdown-Daten: Settings                               |
| `GET /reference/group-sizes`      | Dropdown-Daten: Gruppengrößen                          |
| `GET /reference/target-audiences` | Dropdown-Daten: Zielgruppen                            |
| `GET /reference/method-types`     | Dropdown-Daten: Methoden-Typen                         |

Swagger-Dokumentation: http://localhost:8000/docs

## Projektstruktur

```
.
├── docker-compose.yml       # Postgres + Adminer
├── docker/initdb/           # Einmalige SQL-Initialisierung (Extensions, FTS-Config)
├── alembic/                 # Migrations
├── alembic.ini
├── data/
│   ├── seed/                # Referenzdaten-YAMLs (Phasen, Settings, ...)
│   └── methods/             # Methoden-YAMLs, eine Datei = eine Methode
├── src/methods/
│   ├── models.py            # SQLAlchemy-Modelle
│   ├── schemas.py           # Pydantic-Validierung für YAML
│   ├── ingest.py            # Idempotenter Ingest
│   ├── api.py               # FastAPI-Read-only-API
│   ├── cli.py               # `methods` Kommando
│   └── db.py                # Engine + Session-Scope
├── scripts/
│   └── verify_yamls.py      # Standalone YAML-Validator (kein DB-Zugriff)
├── tests/
│   ├── test_yaml_schema.py  # Alle 153 YAMLs gegen Pydantic-Schema
│   └── test_imports.py      # Modul-Smoke-Tests
├── Makefile
└── pyproject.toml
```

## Datenmodell

**Haupttabelle:** `methods` — kanonischer Name, Kurz- und Langbeschreibung,
Urheber, Dauer-Range, Material, Tags, Qualitätsstatus, Inhaltslizenz, Embedding
(pgvector, 1536 dim, für spätere semantische Suche).

**Referenztabellen** (per `code` adressierbar, keine Enums):
`phases`, `settings`, `group_sizes`, `target_audiences`, `method_types`

**N:M-Zuordnungen** mit Kontextfeldern, wo sinnvoll:
- `method_phases` (mit `is_primary`)
- `method_settings` (mit `suitability`: excellent/good/limited/unsuitable + Freitextnotiz)
- `method_group_sizes`, `method_target_audiences`, `method_type_links`

**Stufe-2-Tabellen** (on-demand befüllt):
- `method_steps` — nummerierter Ablauf mit `trainer_action` + `participant_action`,
  `duration_minutes`, `note` (Facilitator-Hinweis)
- `method_variations` — Varianten mit `name`, `description`, `when_to_use`,
  `setting_codes` (z.B. "nur für Remote")
- `method_pitfalls` — typische Stolperfallen + Gegenmaßnahmen mit
  `severity ∈ {common, rare}`
- `sources` + `method_sources` — Quellen mit `kind` (book/article/url/paper/...),
  `relation` (`originator` / `reference` / `deep_dive` / `practice_guide` / `tool`),
  optional `doi`, `isbn`, `page_reference`

Separat von `quality_status` steuert `stage_2_status ∈ {none, ai_draft,
human_reviewed, expert_verified}` die Reife der **Stufe-2-Inhalte**. So kann
eine Methode Stufe-1-reviewed sein, während die Stufe-2-Vertiefung noch als
AI-Draft vorliegt.

**Audit:** `review_log` — wer hat wann was an einer Methode geändert.

### Indexe

- GIN-Trigram-Index auf `methods.canonical_name` und `method_synonyms.synonym`
  (schnelle ILIKE-/Fuzzy-Suche auf Eigennamen, Ö/Ä/Ü-tolerant per `unaccent`)
- GIN-FTS-Index mit `german_unaccent` auf Name + Beschreibungen
- HNSW-Index auf `methods.embedding` (cosine distance, `vector_cosine_ops`) —
  bedient `ORDER BY embedding <=> query_embedding` indexgestützt, ignoriert
  NULL-Werte.

## Ingest-Workflow

### Stufe 1 — Stammdaten

Eine Methode = eine YAML-Datei in `data/methods/`. Pflichtfelder: `slug`,
`canonical_name`. Empfohlen: `short_description`, `phases`, `settings`,
`group_sizes`, `target_audiences`, `method_types`, Dauer.

```bash
make ingest
```

Der Ingest ist idempotent anhand `slug`. **Human-reviewte Einträge werden
standardmäßig nicht überschrieben** — das verhindert, dass eine neue AI-Draft-YAML
deine Korrekturen kaputtmacht.

```bash
./.venv/bin/methods ingest data/methods --force   # überschreibt auch reviewte
```

### Stufe 2 — Vertiefung

Gleiches Schema, zusätzlich `long_description`, `steps`, `variations`, `pitfalls`,
erweiterte `sources`, sowie die Stufe-2-Meta-Felder `stage_2_status` und
`stage_2_author`. Einfach die bestehende YAML-Datei erweitern und `make ingest`
erneut laufen lassen (nur wenn der Top-Level `quality_status` noch `ai_draft`
ist — sonst `--force`).

Struktur eines `step`:

```yaml
steps:
  - order: 1
    label: Kurzer Titel
    duration_minutes: 5              # optional, kann 0 sein (Prep/async)
    trainer_action: Was Trainer:in tut.
    participant_action: Was TN tun.
    note: Facilitator-Hinweis (optional).
```

Struktur einer `variation`:

```yaml
variations:
  - name: Remote-Variante
    description: Was ist anders?
    when_to_use: Wann greift die Variante?
    setting_codes: [remote_sync]     # optional
```

Struktur eines `pitfall`:

```yaml
pitfalls:
  - issue: Was geht typischerweise schief?
    countermeasure: Wie verhindern / auffangen?
    severity: common                 # oder 'rare'
    setting_codes: [hybrid]          # optional, nur für bestimmte Settings
```

### Verifikation

```bash
python3 scripts/verify_yamls.py   # Standalone, ohne Pydantic/DB
make test                         # pytest inkl. Schema-Parametrize
```

## Semantische Suche

```bash
pip install -e ".[embed]"           # OpenAI SDK
export OPENAI_API_KEY=sk-...

make embed                          # nur fehlende Embeddings füllen
make embed-force                    # alles neu (z.B. nach Modellwechsel)
make embed-dry                      # nur die Texte ansehen, kein API-Call

curl 'http://localhost:8000/search?q=Konflikt+klären&mode=hybrid&limit=10'
```

Die `/search`-API kennt drei Modi:
- `lexical` — FTS + Trigram + Synonym-Match (kein API-Key nötig).
- `semantic` — reine Vektor-Cosine-Suche; benötigt Embeddings + `OPENAI_API_KEY`.
- `hybrid` (default) — gewichtete Score-Fusion, robust gegen beide
  Schwächen. Fehlt der Key, fällt der semantische Anteil leise auf 0
  zurück und der Endpoint bleibt funktionsfähig (lexical-only).

Das Embedding pro Methode bündelt Name + Synonyme + Tags + Methoden-Typ +
Phase + Setting + Zielgruppe + Step-Labels in einen kompakten Text.
Lange `trainer_action`-Felder fließen **nicht** ein — sonst dominiert
Standardvokabular die semantische Distanz.

`scripts/eval_search.py` schickt 15 Beispiel-Queries gegen alle drei
Modi und druckt die Top-5 nebeneinander — manueller Sanity-Check.

## Qualitäts-Workflow

Jede Methode hat `quality_status ∈ {ai_draft, human_reviewed, expert_verified, deprecated}`.

Typischer Ablauf:

1. AI generiert YAML → `quality_status: ai_draft`
2. Review durch dich → Edit + Statuswechsel auf `human_reviewed`
3. Optional: Review durch Fachperson → `expert_verified`

Alle Statuswechsel sollten im `review_log` landen (UI-Tooling folgt in einer
späteren Phase; initial per Hand in Adminer oder per SQL).

## Rechtliches

- Inhalte sind standardmäßig `content_license: proprietary` — für eine SaaS,
  die Methoden hinter Login ausliefert, ist das der sichere Default.
- `content_author` dokumentiert, wer die Beschreibung verfasst hat
  (z.B. "AI-generated, reviewed by Samuel Sommer 2026-04").
- `sources[].relation: originator` markiert die Quelle, die die Methode **erfunden**
  hat — das ist fachliche Zuschreibung, nicht Urheberrecht an unserem Text.

## Roadmap (Vorschlag)

1. **Batch 1 (150 Methoden):** Stufe-1-YAMLs aus dem Kernbestand der deutschsprachigen
   Seminardidaktik — alle Phasen abgedeckt, Setting-Eignung erstmal grob.
2. **Review-Pass:** Stichproben (~10 %) durchgehen, Stati pflegen, Korrekturen in YAML.
3. **Batch 2 (150 weitere):** Nischen + Remote-Spezifika (Liberating Structures,
   kollaborative Online-Formate).
4. **Batch 3 (150+):** Varianten, Jugend-/Schulspezifika, IHK-Trainerwelt.
5. **Stufe-2-Vertiefung:** priorisiert für die ~100 wichtigsten Methoden.
6. **API-Layer:** FastAPI vor Postgres — Suche, Filter, einzelne Methoden.
7. **Optional: OpenSearch** für komplexe Facettensuche, wenn Postgres-FTS an Grenzen stößt.
8. **Embeddings + semantische Suche:** ✅ erledigt (April 2026) — Migration 0003,
   `methods embed`, `/search?mode=hybrid`. Tuning-Hebel: Gewichte in
   `src/methods/api.py` (`LEXICAL_WEIGHT`, `SEMANTIC_WEIGHT`).

## Nützliche SQL-Snippets

```sql
-- Alle Methoden für Phase "Warm-up", die sich für Remote eignen
SELECT m.canonical_name, m.duration_typical_minutes
FROM methods m
JOIN method_phases mp ON mp.method_id = m.id
JOIN phases p ON p.id = mp.phase_id AND p.code = 'warmup'
JOIN method_settings ms ON ms.method_id = m.id
JOIN settings s ON s.id = ms.setting_id AND s.code = 'remote_sync'
WHERE ms.suitability IN ('excellent', 'good')
ORDER BY m.canonical_name;

-- Fuzzy-Suche auf Methoden-Name + Synonyme
SELECT DISTINCT m.canonical_name
FROM methods m
LEFT JOIN method_synonyms s ON s.method_id = m.id
WHERE m.canonical_name % 'blitzlich'          -- pg_trgm
   OR s.synonym % 'blitzlich';

-- Volltextsuche (deutsch, unaccent-tolerant)
SELECT canonical_name, short_description
FROM methods
WHERE to_tsvector('german_unaccent',
        coalesce(canonical_name,'') || ' ' ||
        coalesce(short_description,'') || ' ' ||
        coalesce(long_description,''))
      @@ plainto_tsquery('german_unaccent', 'perspektivwechsel');
```
