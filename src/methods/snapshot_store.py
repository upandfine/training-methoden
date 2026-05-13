"""In-Memory-Engine für den Snapshot-basierten Read-Only-Modus.

Lädt einen Snapshot (JSON + npy) und bedient dieselben Operationen wie der
Postgres-Pfad — list_methods, get_method, search, facets. So bleibt der
API-Endpoint-Code unverändert, der Storage tauscht nur intern.

Performance bei 163 Methoden:
  * list/filter:    < 1 ms (List-Comprehension über 163 Einträge)
  * facets:         < 2 ms (Counter-Aufbau in einem Pass)
  * search lexical: 1-5 ms (regex über vorgefertigtes Search-Korpus)
  * search semantic: 1-2 ms (numpy einmalige Matrix-Vektor-Mul)

Bewusste Design-Entscheidungen:
  * Methods-Liste wird beim Init in einer fest sortierten Reihenfolge
    gehalten, damit ``embeddings.npy``-Indizes 1:1 zur Liste passen.
  * Search-Korpus pro Methode (Name + Synonyme + Tags + Description)
    wird einmal normalisiert vorberechnet — kein Recomputing pro Query.
  * Cosine-Distance wird über vorab-normalisierte Vektoren als Dot-Product
    gerechnet — keine pro-Query Normalisierung der Korpus-Vektoren.
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np


# Stop-Words für die lexikalische Suche. Identisch zu dem, was im
# Postgres-Pfad in api.py steht — wenn dort geändert, hier auch.
# Zeit-Skala-Substantive sind drin, weil "30 Minuten" in Trainer-Queries
# fast immer eine Skala-Angabe ist (siehe Postgres-Pfad-Kommentar).
_STOP = frozenset({
    "der", "die", "das", "den", "dem", "des",
    "ein", "eine", "einer", "einen", "einem", "eines",
    "und", "oder", "aber",
    "ist", "sind", "bin", "war", "waren",
    "wir", "ihr", "sie", "ich", "man",
    "mit", "ohne", "bei", "für", "von", "zum", "zur",
    "in", "an", "auf", "über", "unter", "vor", "nach",
    "im", "am", "ins",
    "alle", "alles", "viele", "manche", "einige",
    "minute", "minuten", "min",
    "sekunde", "sekunden", "sek",
    "stunde", "stunden", "std",
    "tag", "tage", "tagen",
    "woche", "wochen",
    "monat", "monate", "monaten",
    "jahr", "jahre", "jahren",
})

LEXICAL_WEIGHT = 0.4
SEMANTIC_WEIGHT = 0.6


def _strip_accents(s: str) -> str:
    """Diacritics aus Unicode-Strings entfernen — sonst matcht 'krise' nicht
    'krisé' und FTS-style accent-insensitive-Suche fehlt."""
    return "".join(
        c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)
    )


def _normalize(s: str) -> str:
    return _strip_accents(s.lower())


def _tokenize_query(q: str) -> list[str]:
    """Tokenisiert eine Suchanfrage analog zum Postgres-Pfad."""
    tokens_raw = re.split(r"\W+", _normalize(q))
    return [t for t in tokens_raw if len(t) >= 3 and t not in _STOP and not t.isdigit()]


@dataclass
class SearchIndex:
    """Pro Methode vorbereitete Such-Felder."""

    slug: str
    name_norm: str            # canonical_name normalisiert
    haystack_norm: str        # name + synonyms + description + tags normalisiert
    synonyms_norm: set[str]   # Synonym-Texte normalisiert, für exact-substring


def _build_index(method: dict) -> SearchIndex:
    name = method.get("canonical_name", "")
    syn_texts = [s.get("synonym", "") for s in method.get("synonyms", [])]
    desc = method.get("short_description") or ""
    long_desc = method.get("long_description") or ""
    tags = method.get("tags") or []
    haystack = " ".join([name, *syn_texts, desc, long_desc, *tags])
    return SearchIndex(
        slug=method["slug"],
        name_norm=_normalize(name),
        haystack_norm=_normalize(haystack),
        synonyms_norm={_normalize(s) for s in syn_texts},
    )


class SnapshotStore:
    """Liest Snapshot-Dateien und beantwortet read-only-Abfragen."""

    def __init__(self, snapshot_dir: Path) -> None:
        self._dir = Path(snapshot_dir)
        if not (self._dir / "methods.json").exists():
            raise FileNotFoundError(
                f"Snapshot incomplete in {self._dir}: methods.json fehlt"
            )

        with (self._dir / "methods.json").open("r", encoding="utf-8") as f:
            self._methods: list[dict] = json.load(f)
        with (self._dir / "references.json").open("r", encoding="utf-8") as f:
            self._references: dict[str, list[dict]] = json.load(f)
        manifest_path = self._dir / "manifest.json"
        self._manifest = (
            json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest_path.exists()
            else {}
        )

        # slug → index
        self._by_slug: dict[str, int] = {m["slug"]: i for i, m in enumerate(self._methods)}

        # Such-Indexe vorberechnen
        self._indexes: list[SearchIndex] = [_build_index(m) for m in self._methods]

        # Embeddings, falls vorhanden
        emb_path = self._dir / "embeddings.npy"
        mask_path = self._dir / "has_embedding.npy"
        if emb_path.exists() and mask_path.exists():
            self._embeddings: np.ndarray | None = np.load(emb_path)
            self._has_embedding: np.ndarray | None = np.load(mask_path)
            # OpenAI-Embeddings sind L2-normalisiert; falls jemand einen
            # Snapshot mit nicht-normalisierten Vektoren reinträgt, ziehen
            # wir das hier nach. Cosine == Dot bei normalisierten Vektoren.
            norms = np.linalg.norm(self._embeddings, axis=1, keepdims=True)
            self._embeddings = self._embeddings / np.clip(norms, 1e-9, None)
        else:
            self._embeddings = None
            self._has_embedding = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def total_methods(self) -> int:
        return len(self._methods)

    @property
    def manifest(self) -> dict:
        return dict(self._manifest)

    def list_methods(
        self,
        *,
        phase: str | None = None,
        setting: list[str] | None = None,
        group_size: list[str] | None = None,
        audience: list[str] | None = None,
        method_type: list[str] | None = None,
        quality_status: str | None = None,
        stage_2_status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        """Gibt MethodListItem-shaped Dicts zurück."""
        filtered = self._apply_filters(
            phase=phase,
            setting=setting,
            group_size=group_size,
            audience=audience,
            method_type=method_type,
            quality_status=quality_status,
            stage_2_status=stage_2_status,
        )
        # sortiert nach canonical_name (Methods sind beim Export schon so sortiert)
        filtered.sort(key=lambda m: m["canonical_name"])
        sliced = filtered[offset : offset + limit]
        return [self._to_list_item(m) for m in sliced]

    def get_method(self, slug: str) -> dict | None:
        idx = self._by_slug.get(slug)
        if idx is None:
            return None
        return self._methods[idx]

    def search(
        self,
        q: str,
        *,
        mode: str = "hybrid",
        limit: int = 30,
        query_embedding: list[float] | None = None,
    ) -> list[dict]:
        """Hybrid-Suche analog zum Postgres-Pfad.

        ``query_embedding`` ist optional und wird vom Caller (api.py)
        gelegentlich übergeben — falls nicht da, wird nur lexical genutzt.
        Bei mode=semantic ohne Embedding gibt's eine leere Liste.
        """
        lexical_scores = self._lexical_scores(q) if mode in ("hybrid", "lexical") else {}
        semantic_scores: dict[str, float] = {}
        if mode in ("hybrid", "semantic") and query_embedding is not None:
            semantic_scores = self._semantic_scores(query_embedding, top_k=limit * 4)

        if mode == "lexical":
            combined = lexical_scores
        elif mode == "semantic":
            combined = semantic_scores
        else:
            norm_lex = _minmax(lexical_scores)
            norm_sem = _minmax(semantic_scores)
            all_slugs = set(norm_lex) | set(norm_sem)
            combined = {
                slug: LEXICAL_WEIGHT * norm_lex.get(slug, 0.0)
                + SEMANTIC_WEIGHT * norm_sem.get(slug, 0.0)
                for slug in all_slugs
            }

        if not combined:
            return []

        top_slugs = [
            slug
            for slug, _ in sorted(combined.items(), key=lambda kv: kv[1], reverse=True)[:limit]
        ]
        return [self._to_list_item(self._methods[self._by_slug[s]]) for s in top_slugs]

    def facets(
        self,
        *,
        phase: str | None = None,
        setting: list[str] | None = None,
        group_size: list[str] | None = None,
        audience: list[str] | None = None,
        method_type: list[str] | None = None,
        quality_status: str | None = None,
        stage_2_status: str | None = None,
        tag_limit: int = 20,
    ) -> dict:
        filtered = self._apply_filters(
            phase=phase,
            setting=setting,
            group_size=group_size,
            audience=audience,
            method_type=method_type,
            quality_status=quality_status,
            stage_2_status=stage_2_status,
        )

        # Label-Lookup-Tabellen — code → label_de pro Facette
        label_for = {
            "phases": {p["code"]: p["label_de"] for p in self._references["phases"]},
            "settings": {p["code"]: p["label_de"] for p in self._references["settings"]},
            "group_sizes": {
                p["code"]: p["label_de"] for p in self._references["group_sizes"]
            },
            "target_audiences": {
                p["code"]: p["label_de"] for p in self._references["target_audiences"]
            },
            "method_types": {
                p["code"]: p["label_de"] for p in self._references["method_types"]
            },
        }

        def _count(items: list[dict], facet_key: str, code_extract) -> list[dict]:
            counter: Counter[str] = Counter()
            for m in items:
                for code in code_extract(m):
                    counter[code] += 1
            labels = label_for[facet_key]
            return sorted(
                [
                    {"code": c, "label_de": labels.get(c, c), "count": n}
                    for c, n in counter.items()
                ],
                key=lambda x: x["code"],
            )

        phases_b = _count(filtered, "phases", lambda m: [p["code"] for p in m.get("phases", [])])
        settings_b = _count(
            filtered, "settings", lambda m: [s["code"] for s in m.get("settings", [])]
        )
        groups_b = _count(
            filtered, "group_sizes", lambda m: [g["code"] for g in m.get("group_sizes", [])]
        )
        audiences_b = _count(
            filtered,
            "target_audiences",
            lambda m: [a["code"] for a in m.get("target_audiences", [])],
        )
        types_b = _count(
            filtered, "method_types", lambda m: [t["code"] for t in m.get("method_types", [])]
        )

        # Direkt-Spalten ohne Label-Mapping (Code == Label)
        qs_counter: Counter[str] = Counter(m["quality_status"] for m in filtered)
        s2_counter: Counter[str] = Counter(m["stage_2_status"] for m in filtered)

        # Tags
        tags_counter: Counter[str] = Counter()
        for m in filtered:
            for t in m.get("tags") or []:
                tags_counter[t] += 1
        top_tags = [
            {"code": t, "label_de": t, "count": n}
            for t, n in tags_counter.most_common(tag_limit)
        ]

        return {
            "total": len(filtered),
            "phases": phases_b,
            "settings": settings_b,
            "group_sizes": groups_b,
            "target_audiences": audiences_b,
            "method_types": types_b,
            "quality_status": [
                {"code": k, "label_de": k, "count": v}
                for k, v in sorted(qs_counter.items())
            ],
            "stage_2_status": [
                {"code": k, "label_de": k, "count": v}
                for k, v in sorted(s2_counter.items())
            ],
            "top_tags": top_tags,
        }

    def reference(self, key: str) -> list[dict]:
        """Liefert eine Referenz-Liste (phases, settings, ...) als dicts."""
        return self._references.get(key, [])

    # ------------------------------------------------------------------
    # Intern: Filter & Helpers
    # ------------------------------------------------------------------

    def _apply_filters(
        self,
        *,
        phase: str | None,
        setting: list[str] | None,
        group_size: list[str] | None,
        audience: list[str] | None,
        method_type: list[str] | None,
        quality_status: str | None,
        stage_2_status: str | None,
    ) -> list[dict]:
        out = self._methods
        if quality_status:
            out = [m for m in out if m["quality_status"] == quality_status]
        if stage_2_status:
            out = [m for m in out if m["stage_2_status"] == stage_2_status]
        if phase:
            out = [m for m in out if any(p["code"] == phase for p in m.get("phases", []))]
        if setting:
            sset = set(setting)
            out = [
                m for m in out
                if any(s["code"] in sset for s in m.get("settings", []))
            ]
        if group_size:
            gset = set(group_size)
            out = [
                m for m in out
                if any(g["code"] in gset for g in m.get("group_sizes", []))
            ]
        if audience:
            aset = set(audience)
            out = [
                m for m in out
                if any(a["code"] in aset for a in m.get("target_audiences", []))
            ]
        if method_type:
            tset = set(method_type)
            out = [
                m for m in out
                if any(t["code"] in tset for t in m.get("method_types", []))
            ]
        return out

    def _to_list_item(self, m: dict) -> dict:
        """Reduziert ein MethodDetail-Dict auf MethodListItem-Shape."""
        primary_phase = next(
            (p["code"] for p in m.get("phases", []) if p.get("is_primary")),
            None,
        )
        return {
            "slug": m["slug"],
            "canonical_name": m["canonical_name"],
            "short_description": m.get("short_description"),
            "duration_typical_minutes": m.get("duration_typical_minutes"),
            "quality_status": m["quality_status"],
            "stage_2_status": m["stage_2_status"],
            "primary_phase_code": primary_phase,
            "setting_codes": [s["code"] for s in m.get("settings", [])],
            "method_type_codes": [t["code"] for t in m.get("method_types", [])],
        }

    # ------------------------------------------------------------------
    # Such-Scoring
    # ------------------------------------------------------------------

    def _lexical_scores(self, q: str) -> dict[str, float]:
        """Token-Prefix-Matching + Synonym-Bonus + Name-Boost.

        Approximation des Postgres-Setups: FTS-Rank + Trigram-Similarity
        + Synonym-Hit. Wir machen das hier mit regex-basierten Substring-
        Suchen plus einer einfachen Trigram-Jaccard-Approximation auf
        canonical_name. Bei 163 Methoden völlig ausreichend schnell.
        """
        tokens = _tokenize_query(q)
        q_norm = _normalize(q)
        scores: dict[str, float] = {}
        if not tokens and not q_norm:
            return scores

        # Regex-Pattern für Token-Prefix: \btoken
        token_patterns = [re.compile(rf"\b{re.escape(t)}", re.IGNORECASE) for t in tokens]

        for idx, m in enumerate(self._methods):
            ix = self._indexes[idx]
            # FTS-Approximation: wieviele der Query-Tokens kommen als
            # Wort-Anfang im Haystack vor?
            fts_hits = sum(1 for p in token_patterns if p.search(ix.haystack_norm))
            fts_rank = fts_hits / max(len(token_patterns), 1)

            # Name-Boost: kommt das Token im canonical_name vor?
            name_hits = sum(1 for p in token_patterns if p.search(ix.name_norm))
            name_boost = name_hits / max(len(token_patterns), 1)

            # Trigram-Similarity gegen canonical_name (Jaccard, simpel aber wirksam)
            trigram_sim = _trigram_jaccard(q_norm, ix.name_norm)

            # Synonym-Direkttreffer: kommt die Full-Query als Substring in
            # einem Synonym vor?
            synonym_hit = 1.0 if any(q_norm in syn for syn in ix.synonyms_norm) else 0.0

            score = fts_rank * 1.0 + trigram_sim * 0.6 + synonym_hit * 0.4 + name_boost * 0.3
            if score > 0:
                scores[ix.slug] = score
        return scores

    def _semantic_scores(self, query_emb: list[float], top_k: int) -> dict[str, float]:
        if self._embeddings is None or self._has_embedding is None:
            return {}
        qe = np.asarray(query_emb, dtype=np.float32)
        qe = qe / max(float(np.linalg.norm(qe)), 1e-9)
        # Dot-Product = Cosine bei normalisierten Vektoren.
        sims = self._embeddings @ qe
        # Methoden ohne Embedding ausblenden
        sims = np.where(self._has_embedding, sims, -np.inf)
        # Top-K via argpartition (schneller als argsort bei großen Arrays)
        if top_k >= len(sims):
            top_idx = np.argsort(-sims)
        else:
            unsorted = np.argpartition(-sims, top_k)[:top_k]
            top_idx = unsorted[np.argsort(-sims[unsorted])]
        return {
            self._methods[i]["slug"]: float(sims[i])
            for i in top_idx
            if sims[i] > -np.inf
        }


# ----------------------------------------------------------------------
# Hilfsfunktionen
# ----------------------------------------------------------------------


def _minmax(values: dict[str, float]) -> dict[str, float]:
    if not values:
        return {}
    vs = list(values.values())
    lo, hi = min(vs), max(vs)
    if hi - lo < 1e-9:
        return {k: 1.0 for k in values}
    return {k: (v - lo) / (hi - lo) for k, v in values.items()}


def _trigram_jaccard(a: str, b: str) -> float:
    """Trigram-Jaccard-Similarity — Annäherung an pg_trgm's similarity()."""
    if not a or not b:
        return 0.0
    grams_a = {a[i : i + 3] for i in range(max(len(a) - 2, 1))}
    grams_b = {b[i : i + 3] for i in range(max(len(b) - 2, 1))}
    if not grams_a or not grams_b:
        return 0.0
    union = grams_a | grams_b
    return len(grams_a & grams_b) / len(union) if union else 0.0


# ----------------------------------------------------------------------
# Query-Embedding (OpenAI live, identisch zum api.py-Cache)
# ----------------------------------------------------------------------


@lru_cache(maxsize=512)
def _embed_query_cached(q: str, model: str) -> tuple[float, ...]:
    from openai import OpenAI  # type: ignore[import-not-found]

    client = OpenAI()
    resp = client.embeddings.create(model=model, input=[q])
    return tuple(resp.data[0].embedding)


def embed_query(q: str) -> list[float] | None:
    """Erzeugt das Query-Embedding live über OpenAI. None bei fehlendem Setup."""
    if not os.environ.get("OPENAI_API_KEY"):
        return None
    model = os.environ.get("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
    try:
        return list(_embed_query_cached(q, model))
    except ImportError as exc:
        # SDK fehlt — Caller fällt auf lexical zurück.
        print(f"[snapshot_store.embed_query] ImportError: {exc}", flush=True)
        return None
    except Exception as exc:  # noqa: BLE001
        # API-Fehler dürfen den Such-Pfad nicht töten — aber wir loggen sie,
        # sonst ist "503 — Semantic nicht verfügbar" undiagnostizierbar.
        print(
            f"[snapshot_store.embed_query] API-Fehler "
            f"({exc.__class__.__name__}): {exc}",
            flush=True,
        )
        return None
