#!/usr/bin/env bash
#
# Bootstrap-Sequenz für einen frischen Container/Deployment.
#
# Reihenfolge ist kritisch:
#   1. alembic upgrade head   → Schema anlegen / aktualisieren
#   2. methods seed-refs       → Referenz-Daten (Phasen, Settings, ...) laden
#   3. methods ingest          → 163 Methoden-YAMLs in die DB ziehen
#   4. methods embed           → Embeddings via OpenAI (nur wenn API-Key da)
#
# Idempotent: alle Schritte überspringen vorhandene Einträge bzw. nutzen
# Upsert-Semantik. Kann bei jedem Container-Start aufgerufen werden, ohne
# Daten zu zerstören.
#
# Verwendung auf Sliplane / anderem PaaS:
#   * Als "release command" oder "init job" konfigurieren, der VOR dem
#     ersten Web-Start läuft.
#   * Oder einmalig per `docker exec` / SSH-Konsole nach dem ersten Deploy
#     aufrufen.

set -euo pipefail

echo "→ alembic upgrade head"
alembic upgrade head

echo "→ methods seed-refs"
methods seed-refs

echo "→ methods ingest data/methods"
methods ingest data/methods

if [ -n "${OPENAI_API_KEY:-}" ]; then
  echo "→ methods embed (OPENAI_API_KEY gesetzt)"
  methods embed || echo "⚠ embed fehlgeschlagen — Lexical-Suche bleibt funktionsfähig"
else
  echo "⚠ OPENAI_API_KEY nicht gesetzt — Embeddings werden übersprungen (Lexical-Suche reicht)"
fi

echo "✓ Bootstrap abgeschlossen."
