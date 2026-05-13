# Frontend-Agent-Briefing: Trainingsmethoden-Katalog API

Du baust ein Frontend, das den Trainingsmethoden-Katalog konsumiert. Dieses
Dokument enthält alles, was du brauchst — Endpoints, Datenmodelle, Such-
Modi, UI-Patterns, und Stolperfallen. Stand: Mai 2026.

## TL;DR — Was die API liefert

Eine read-only HTTP-API über 163+ didaktische Methoden für Trainer:innen
(Unternehmen, Schule, Jugend, IHK, Erwachsenenbildung; on-site / remote /
hybrid). Jede Methode hat Stufe-1-Stammdaten (Name, Synonyme, Phase,
Setting, Dauer, Tags) und optional Stufe-2-Tiefe (Ablauf-Steps, Varianten,
Stolperfallen, Quellen).

Drei Such-Modi: **lexikalisch** (FTS+Trigram, schnell), **semantisch** (Vektor-
Embeddings, findet inhaltliche Verwandtschaft), **hybrid** (kombiniert).

## Konfiguration

- **Base-URL:** `http://localhost:8765` lokal, in Prod über die deployment-URL
- **Auth:** aktuell keine (siehe Abschnitt „Auth" unten)
- **CORS:** server-seitig via `CORS_ORIGINS`-Env. Wenn der Browser blockt: dem
  Backend-Team sagen, dass `https://deine-frontend-domain` in die Origin-Liste muss
- **Format:** alle Responses sind JSON. Alle GETs sind idempotent und cacheable
- **OpenAPI-Spec:** `GET /openapi.json` — kannst du in Tools wie OpenAPI-TS
  reinwerfen, um TS-Types automatisch zu generieren statt sie hier abzutippen

## Endpoints

### `GET /health`

Healthcheck. Liefert `{status, methods_total}`. Nutze als Erstes, wenn die
App lädt, um zu wissen ob die API erreichbar ist.

```bash
curl http://localhost:8765/health
# → {"status":"ok","methods_total":163}
```

### `GET /methods` — gefilterte Liste

Standardliste mit Filtern. Mehrfach-Werte (z.B. `setting=onsite&setting=remote_sync`)
werden als ODER kombiniert, verschiedene Parameter als UND.

| Param | Typ | Beispiel |
|---|---|---|
| `phase` | string | `phase=warmup` |
| `setting` | string (multi) | `setting=onsite&setting=remote_sync` |
| `group_size` | string (multi) | `group_size=small&group_size=medium` |
| `audience` | string (multi) | `audience=corporate&audience=ihk` |
| `method_type` | string (multi) | `method_type=activating` |
| `quality_status` | string | `quality_status=human_reviewed` |
| `stage_2_status` | string | `stage_2_status=human_reviewed` |
| `limit` | int 1-500 | `limit=50` |
| `offset` | int | `offset=50` |

```bash
curl 'http://localhost:8765/methods?phase=warmup&setting=remote_sync&limit=20'
```

Response: Array von `MethodListItem` (siehe „Typen" unten). Pro Methode
kommen die wichtigsten Felder zurück, KEINE steps/variations/pitfalls —
das spart Bandbreite bei Listings. Für Vollansicht: `/methods/{slug}`.

### `GET /methods/{slug}` — vollständige Methode

Einzelne Methode mit allem: Stufe-1 + Stufe-2 + Quellen.

```bash
curl http://localhost:8765/methods/tetralemma
```

Response: `MethodDetail`. 404 wenn der Slug nicht existiert.

### `GET /search?q=...` — Such-Endpoint

Drei Modi via `mode`-Parameter:

| Mode | Wann | Latenz | Embedding-Cost |
|---|---|---|---|
| `lexical` | Tipp-as-you-type, exakte Begriffe, Filter-Counter | 2-10 ms | $0 |
| `semantic` | Trainer formuliert in natürlicher Sprache | 200-800 ms (cached: 2 ms) | ~$0.00002 |
| `hybrid` (default) | Standardpfad, robusteste Ergebnisse | 200-800 ms (cached: 2 ms) | ~$0.00002 |

```bash
# Natürlich-sprachlich, beste UX
curl 'http://localhost:8765/search?q=Methode+für+Konflikt+zwischen+zwei+Personen&mode=hybrid&limit=5'

# Bei Such-Autocomplete in der Search-Box: lexical reicht und ist schneller
curl 'http://localhost:8765/search?q=brainsto&mode=lexical&limit=10'
```

Response: Array von `MethodListItem` (sortiert nach Score).

**Wichtig:** Der Hybrid-/Semantic-Modus fällt auf lexical zurück, wenn die
Backend-Umgebung keinen `OPENAI_API_KEY` hat. `mode=semantic` ohne Key
liefert dann 503 — fang das ab und falle in der UI auf `mode=lexical` zurück.

### `GET /aggregations/facets` — Filter-Counter

Liefert für jede Facette (Phase, Setting, Group-Size, Audience, Type, Tags,
Quality, Stage-2) die Methoden-Anzahl unter Berücksichtigung der aktiven
Filter. Idealer Endpoint, um in der Filter-Sidebar live „(87)" hinter
Checkboxen anzuzeigen.

```bash
curl 'http://localhost:8765/aggregations/facets?setting=remote_sync&audience=corporate'
```

Response:
```json
{
  "total": 87,
  "phases": [{"code": "warmup", "label_de": "Warm-up", "count": 12}, ...],
  "settings": [...],
  "group_sizes": [...],
  "target_audiences": [...],
  "method_types": [...],
  "quality_status": [{"code": "human_reviewed", "label_de": "human_reviewed", "count": 87}],
  "stage_2_status": [...],
  "top_tags": [{"code": "kurz", "label_de": "kurz", "count": 23}, ...]
}
```

**UX-Tipp:** Beim Toggle eines Filters in der Sidebar rufst du `/aggregations/facets`
mit den neuen Filtern UND `/methods` für die Result-Liste auf — parallel,
in einem `Promise.all`.

### `GET /reference/*` — Dropdown-Befüllung

Statische Stammdaten für UI-Selects. Cache forever (oder mit langem `Cache-Control`).

- `GET /reference/phases` → Liste der Trainingsphasen (warmup, reflection, ...)
- `GET /reference/settings` → onsite, remote_sync, remote_async, hybrid
- `GET /reference/group-sizes` → 1:1, klein, mittel, groß, plenum
- `GET /reference/target-audiences` → corporate, school, youth, ihk, ...
- `GET /reference/method-types` → didactic, activating, cooperative, ...

Jeder liefert `[{code, label_de, ...}]`.

## TypeScript-Typen

Du kannst die per `npx openapi-typescript http://localhost:8765/openapi.json` generieren.
Falls du sie manuell willst, hier die wichtigsten:

```typescript
// Filter-Codes — sind alles freie Strings, die mit Reference-Endpoints
// validiert werden. Nicht hardcoden, lieber zur Laufzeit aus /reference/* laden.

type Suitability = "excellent" | "good" | "limited" | "unsuitable";
type QualityStatus = "ai_draft" | "human_reviewed" | "expert_verified" | "deprecated";
type Stage2Status = "none" | "ai_draft" | "human_reviewed" | "expert_verified";
type Severity = "common" | "rare";
type SearchMode = "lexical" | "semantic" | "hybrid";

interface RefItem {
  code: string;
  label_de: string;
  label_en?: string | null;
  description?: string | null;
  sort_order?: number;
}

interface GroupSizeItem extends RefItem {
  min_participants: number | null;
  max_participants: number | null;
}

// Kompakter Eintrag — was in Listen + Search-Results rauskommt
interface MethodListItem {
  slug: string;
  canonical_name: string;
  short_description: string | null;
  duration_typical_minutes: number | null;
  quality_status: QualityStatus;
  stage_2_status: Stage2Status;
  primary_phase_code: string | null;
  setting_codes: string[];
  method_type_codes: string[];
}

// Vollansicht — kommt von /methods/{slug}
interface MethodDetail {
  slug: string;
  canonical_name: string;
  short_description: string | null;
  long_description: string | null;
  originator: string | null;
  origin_year: number | null;
  origin_context: string | null;
  duration_min_minutes: number | null;
  duration_typical_minutes: number | null;
  duration_max_minutes: number | null;
  materials: string[];
  tags: string[];
  quality_status: QualityStatus;
  stage_2_status: Stage2Status;
  stage_2_author: string | null;
  content_author: string | null;
  content_license: string;
  synonyms: Array<{ synonym: string; language: string }>;
  phases: Array<{ code: string; label_de: string; is_primary: boolean }>;
  settings: Array<{
    code: string;
    label_de: string;
    suitability: Suitability;
    suitability_note: string | null;
  }>;
  group_sizes: Array<{ code: string; label_de: string }>;
  target_audiences: Array<{ code: string; label_de: string }>;
  method_types: Array<{ code: string; label_de: string }>;
  steps: Array<{
    order_index: number;
    label: string;
    duration_minutes: number | null;
    trainer_action: string;
    participant_action: string;
    note: string | null;
  }>;
  variations: Array<{
    name: string;
    description: string;
    when_to_use: string | null;
    setting_codes: string[] | null;
  }>;
  pitfalls: Array<{
    issue: string;
    countermeasure: string;
    severity: Severity;
    setting_codes: string[] | null;
  }>;
  sources: Array<{
    kind: string;
    title: string;
    author: string | null;
    year: number | null;
    url: string | null;
    doi: string | null;
    isbn: string | null;
    relation: string;
    page_reference: string | null;
  }>;
}

interface FacetBucket {
  code: string;
  label_de: string;
  count: number;
}

interface FacetsOut {
  total: number;
  phases: FacetBucket[];
  settings: FacetBucket[];
  group_sizes: FacetBucket[];
  target_audiences: FacetBucket[];
  method_types: FacetBucket[];
  quality_status: FacetBucket[];
  stage_2_status: FacetBucket[];
  top_tags: FacetBucket[];
}
```

## Minimal-Client

```typescript
// src/api/methods.ts
const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8765";

async function api<T>(path: string, params?: Record<string, string | string[] | number | undefined>): Promise<T> {
  const url = new URL(path, API_BASE);
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v === undefined || v === null) continue;
      if (Array.isArray(v)) v.forEach((item) => url.searchParams.append(k, String(item)));
      else url.searchParams.append(k, String(v));
    }
  }
  const r = await fetch(url.toString(), { method: "GET" });
  if (!r.ok) throw new Error(`API ${r.status}: ${await r.text()}`);
  return r.json() as Promise<T>;
}

export const methodsApi = {
  health: () => api<{ status: string; methods_total: number }>("/health"),

  list: (filters: {
    phase?: string;
    setting?: string[];
    group_size?: string[];
    audience?: string[];
    method_type?: string[];
    quality_status?: string;
    stage_2_status?: string;
    limit?: number;
    offset?: number;
  }) => api<MethodListItem[]>("/methods", filters),

  get: (slug: string) => api<MethodDetail>(`/methods/${encodeURIComponent(slug)}`),

  search: (q: string, opts?: { mode?: SearchMode; limit?: number }) =>
    api<MethodListItem[]>("/search", { q, mode: opts?.mode ?? "hybrid", limit: opts?.limit ?? 30 }),

  facets: (filters: Parameters<typeof methodsApi.list>[0]) =>
    api<FacetsOut>("/aggregations/facets", filters),

  reference: {
    phases: () => api<RefItem[]>("/reference/phases"),
    settings: () => api<RefItem[]>("/reference/settings"),
    groupSizes: () => api<GroupSizeItem[]>("/reference/group-sizes"),
    audiences: () => api<RefItem[]>("/reference/target-audiences"),
    methodTypes: () => api<RefItem[]>("/reference/method-types"),
  },
};
```

## UI-Patterns

### Pattern 1: Filter-Sidebar mit Live-Counter

```tsx
function Sidebar() {
  const [filters, setFilters] = useState<FilterState>(defaultFilters);
  const { data: facets } = useSWR(["facets", filters], () => methodsApi.facets(filters));

  return (
    <aside>
      {facets?.phases.map((p) => (
        <Checkbox
          key={p.code}
          checked={filters.phase === p.code}
          onChange={() => setFilters((f) => ({ ...f, phase: p.code }))}
          label={`${p.label_de} (${p.count})`}
          disabled={p.count === 0}
        />
      ))}
    </aside>
  );
}
```

### Pattern 2: Such-Box mit Debounce + Mode-Toggle

```tsx
function SearchBox() {
  const [q, setQ] = useState("");
  const [mode, setMode] = useState<SearchMode>("hybrid");
  const debouncedQ = useDebounce(q, 250);

  const { data: results } = useSWR(
    debouncedQ.length >= 2 ? ["search", debouncedQ, mode] : null,
    () => methodsApi.search(debouncedQ, { mode, limit: 30 })
  );

  // ...
}
```

**Wichtige Verhaltens-Regel:** wenn `q.length < 2` ist, lieber `/methods` mit
den aktuellen Filtern aufrufen statt `/search` — `/search` validiert
mindestens 2 Zeichen.

### Pattern 3: Detail-Drawer

```tsx
function MethodDrawer({ slug, onClose }: { slug: string | null; onClose: () => void }) {
  const { data: method } = useSWR(
    slug ? ["method", slug] : null,
    () => methodsApi.get(slug!)
  );

  if (!slug) return null;
  if (!method) return <Spinner />;

  return <DetailView method={method} onClose={onClose} />;
}
```

## KI-Agent-Integration

Wenn du im Frontend einen Agent baust, der für Nutzer:innen Methoden vorschlägt
("Ich habe eine Gruppe von 15 Auszubildenden remote, 90 min, Konflikt im Team"),
hat sich folgendes Muster bewährt:

### Strukturierte Filter zuerst, dann Suche

1. **Agent extrahiert Constraints aus dem User-Prompt:**
   - Setting → `remote_sync`
   - Audience → `ihk`
   - Group size → `medium`
   - Duration → max 90 min (client-seitig filtern)
   - Topic → "Konflikt"

2. **Strukturierte Vorfilterung über `/methods`:**
   ```typescript
   const candidates = await methodsApi.list({
     setting: ["remote_sync"],
     audience: ["ihk"],
     group_size: ["medium"],
     stage_2_status: "human_reviewed",   // nur tiefe Methoden
     limit: 200,
   });
   const filtered = candidates.filter(m =>
     !m.duration_typical_minutes || m.duration_typical_minutes <= 90
   );
   ```

3. **Semantisches Re-Ranking auf der vorgefilterten Liste:**
   Wenn `filtered.length > 20`, schick die Topic-Phrase an `/search?mode=hybrid`
   mit niedrigem Limit (5-10) und schneide gegen die `filtered`-Slug-Liste.
   ```typescript
   const ranked = await methodsApi.search("Konflikt", { mode: "hybrid", limit: 30 });
   const intersected = ranked.filter(r => filtered.find(f => f.slug === r.slug));
   ```

4. **Detail-Lookup für die finalen 3-5 Vorschläge:**
   ```typescript
   const details = await Promise.all(
     intersected.slice(0, 5).map(m => methodsApi.get(m.slug))
   );
   ```

5. **Prompt-Kontext bauen:** gib dem Agent für jede Method `canonical_name`,
   `short_description`, die ersten 3 `steps` und 2-3 `pitfalls`. Das reicht für
   eine fundierte Empfehlung ohne den Context aufzublasen.

### Tool-Definitionen für Function-Calling

Wenn dein Agent OpenAI / Claude mit Tools nutzt, sind diese Tools sinnvoll:

```typescript
const tools = [
  {
    name: "search_methods",
    description: "Search the training methods catalog. Use natural language for topic-based queries; use filters for structured constraints.",
    input_schema: {
      type: "object",
      properties: {
        query: { type: "string", description: "Free-text query in German, e.g. 'Konflikt zwischen Teammitgliedern'" },
        phase: { type: "string", enum: ["arrival", "warmup", "getting_acquainted", "expectations", "input", "activation", "deepening", "group_work", "practice", "reflection", "feedback", "transfer", "evaluation", "closing"] },
        setting: { type: "array", items: { type: "string", enum: ["onsite", "remote_sync", "remote_async", "hybrid"] } },
        group_size: { type: "array", items: { type: "string", enum: ["one_on_one", "small", "medium", "large", "plenary"] } },
        audience: { type: "array", items: { type: "string", enum: ["corporate", "school", "youth", "ihk", "adult_education", "higher_education"] } },
        max_duration_min: { type: "integer", description: "Filter by typical duration ≤ this value" },
        limit: { type: "integer", default: 5 },
      },
    },
  },
  {
    name: "get_method_detail",
    description: "Fetch the full content of one method including step-by-step process, variations, and pitfalls. Use this AFTER search_methods to get details for a specific recommendation.",
    input_schema: {
      type: "object",
      properties: {
        slug: { type: "string", description: "The slug from search_methods results" },
      },
      required: ["slug"],
    },
  },
];
```

## Stolperfallen

- **Suchmodus auf `semantic` ohne Backend-OpenAI-Key:** 503. Fang das mit
  `try/catch` und fall auf `mode=lexical` zurück. Im Production-Build kann
  das Backend ein Logging zeigen, ob Embeddings aktiv sind (siehe Backend-Log
  „Semantic search ENABLED").
- **Stage-2-Filter erwartet specifischen Wert, nicht boolean:** `stage_2_status=human_reviewed`,
  nicht `stage_2_status=true`.
- **Slugs sind URL-safe, aber Sonderzeichen in Methoden-Namen sind unicodig:**
  Wenn du Methoden-Titel im URL anzeigst, ist `decodeURIComponent`/`encodeURIComponent` Pflicht.
- **`primary_phase_code` in MethodListItem ist nullable:** nicht jede Methode hat
  eine primäre Phase markiert. Wenn du nach „nur eine Phase pro Card" anzeigst,
  fallback auf erste Phase aus den Phase-Links (kommt im Detail-Endpoint).
- **`group_size`/`audience`-Filter sind im `/search`-Endpoint NICHT serverseitig** —
  Search hat sein eigenes Ranking und akzeptiert nur `q`, `mode`, `limit`.
  Wenn du beim Suchen filtern willst: client-seitig nach-filtern auf den Results.
- **CORS-Preflights für GET-Requests:** Wenn du custom Headers schickst (z.B.
  `X-Request-ID` für Tracing), löst das OPTIONS-Preflight aus. Backend
  erlaubt aktuell nur `GET`-Methoden — keine PUT/POST/DELETE.
- **`/health` zählt alle Methoden, egal Quality-Status:** Wenn du eine
  „Anzahl reviewed"-Anzeige willst, nutz `/aggregations/facets` und schau auf
  `quality_status[].count` mit `code=human_reviewed`.

## Storage-Backend (für dich transparent)

Das Backend hat zwei Storage-Modi: `postgres` (lokales Authoring) und
`snapshot` (Cloud-Read-Only). **Für dich als Frontend-Agent macht das keinen
Unterschied** — die Endpoints, Response-Schemas und Filter-Parameter sind in
beiden Modi exakt identisch.

Einziger sichtbarer Unterschied: `GET /health` enthält ein `storage`-Feld
(`"postgres"` oder `"snapshot"`) und im Snapshot-Mode zusätzlich ein
`snapshot`-Objekt mit Generierungs-Zeitpunkt und Counts. Nützlich, um in der
UI „Daten aktualisiert am DD.MM.YYYY" anzuzeigen.

## Auth (Status)

Aktuell **keine Authentifizierung**. Was das für dich heißt:

- **Lokal / interne Nutzung:** Kein Problem. Backend läuft hinter VPN oder ist nur
  intern erreichbar.
- **Öffentliches Frontend:** Backend-Team muss vor Go-Live eine API-Token-Schicht
  einbauen. Du würdest dann pro Call einen Header `X-API-Key: ...` mitsenden.
  Frag explizit nach, bevor du gegen eine Public-URL gehst.

## Performance & Caching

- Reference-Endpoints (`/reference/*`) sind statisch — clientseitig cachen mit
  langem TTL oder beim App-Start einmal in einen globalen Store legen
- `/methods/{slug}` ist stabil pro Slug — cacheable, mit `SWR`/`React Query`
  Default-Verhalten passt
- `/search` ist NICHT cacheable per HTTP-Header (kein `Cache-Control`), aber
  identische Queries mit gleichem `mode` produzieren identische Results — du
  kannst clientseitig per `useSWR` cachen
- `/aggregations/facets` ist günstig (~5-30ms), kann auf jeden Filter-Toggle
  neu aufgerufen werden ohne Performance-Sorge

## Debugging-Tipps

- **Swagger-UI:** `http://localhost:8765/docs` — komplette interaktive API-Doku,
  lässt sich direkt im Browser ausprobieren
- **OpenAPI-Spec:** `http://localhost:8765/openapi.json` — für TS-Generierung
  oder Postman-Import
- **Backend-Logs:** das Backend printet beim Startup eine Zeile
  `[methods.api] ✓ Semantic search ENABLED` oder `... DISABLED` — damit weißt
  du, ob `mode=semantic` funktionieren wird
- **Existiert ein Slug?** `curl http://localhost:8765/methods/{slug}` → 404 wenn nicht.
  Statt zu raten, gegen `/methods?limit=500` cachen und die Slug-Liste lokal halten

## Datenmodell — Konzepte

Auch wenn du nur die JSON-Strukturen siehst, hilft das mentale Modell für UI-
Entscheidungen:

- **Methode** = eine konkrete Trainings-Methode. Identifiziert per `slug`.
- **Phase** = wann im Seminar setze ich sie ein (Warm-up, Reflexion, Abschluss …).
  Eine Methode kann mehreren Phasen zugeordnet sein; eine davon ist `is_primary: true`.
- **Setting** = onsite, remote_sync, remote_async, hybrid. Pro Methode + Setting
  gibt es eine `suitability` (excellent/good/limited/unsuitable) plus optionalen
  Hinweis. „limited" heißt nicht „geht nicht", sondern „mit Einschränkungen".
- **Group Size** = 1:1, Kleingruppe, mittlere Gruppe, große Gruppe, Plenum.
- **Audience** = Zielgruppe (Corporate, School, Youth, IHK, Adult Ed, Higher Ed).
- **Method Type** = grobe Kategorisierung (activating, reflective, cooperative,
  didactic, creative, diagnostic, game, visualization, feedback).
- **Stufe 1 vs. Stufe 2:** Stufe 1 = Stammdaten + Beschreibung. Stufe 2 =
  konkreter Ablauf (Steps mit `trainer_action`/`participant_action`/`duration`),
  Variationen, typische Stolperfallen. Stage-2 ist optional — `stage_2_status:
  none` heißt „nur Stammdaten, kein Ablauf hinterlegt".
- **Quality Status:** ai_draft → human_reviewed → expert_verified. Filter
  auf `human_reviewed` für Produktions-UX.

## Versioning

Die API hat aktuell keine explizite Versionierung (kein `/v1/`-Prefix).
Vor Breaking-Changes wird das Backend einen Migration-Pfad ankündigen.
Schau auf `GET /openapi.json` → `info.version` für die aktuelle Schema-Version.

---

**Letzter Tipp:** der schnellste Weg, dich mit der API vertraut zu machen,
ist `http://localhost:8765/docs` im Browser zu öffnen und ein paar Requests
mit „Try it out" zu schicken. Das Swagger-UI ist klickbar und zeigt dir
Response-Shapes live.
