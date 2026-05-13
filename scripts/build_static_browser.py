"""Generiert einen single-file HTML-Browser aus allen Methoden-YAMLs.

Liest alle YAMLs in data/methods/, bettet die Daten als JSON in eine
HTML-Datei ein und rendert einen clientseitigen Browser (Filter, Suche,
Detail-Ansicht). Kein Server, keine DB — einfach die Datei im Browser
öffnen.

Ausgabe: browse/katalog.html
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
METHODS_DIR = ROOT / "data" / "methods"
SEED_DIR = ROOT / "data" / "seed"
OUT_DIR = ROOT / "browse"
OUT_FILE = OUT_DIR / "katalog.html"


def load_seed(name: str) -> dict[str, dict]:
    path = SEED_DIR / f"{name}.yaml"
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    return {row["code"]: row for row in data}


def build_data() -> dict:
    phases = load_seed("phases")
    settings = load_seed("settings")
    group_sizes = load_seed("group_sizes")
    audiences = load_seed("target_audiences")
    method_types = load_seed("method_types")

    methods: list[dict] = []
    for p in sorted(METHODS_DIR.glob("*.yaml")):
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            continue
        methods.append(raw)

    return {
        "methods": methods,
        "references": {
            "phases": phases,
            "settings": settings,
            "group_sizes": group_sizes,
            "audiences": audiences,
            "method_types": method_types,
        },
    }


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Trainingsmethoden-Katalog — Browser</title>
<style>
  :root {
    --bg: #fafaf7;
    --panel: #ffffff;
    --border: #e1dfd9;
    --text: #1a1a1a;
    --muted: #6b6b6b;
    --accent: #8b6f47;
    --accent-bg: #f4efe7;
    --stage2-ai: #b88a44;
    --stage2-human: #5e8c5e;
    --stage2-none: #b0b0b0;
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; height: 100%; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: var(--bg);
    color: var(--text);
    font-size: 14px;
    line-height: 1.5;
  }
  header {
    background: var(--panel);
    border-bottom: 1px solid var(--border);
    padding: 12px 20px;
    display: flex;
    gap: 16px;
    align-items: center;
    flex-wrap: wrap;
  }
  header h1 {
    font-size: 16px;
    margin: 0;
    font-weight: 600;
  }
  header .meta { color: var(--muted); font-size: 12px; }
  header input[type="search"] {
    padding: 6px 10px;
    border: 1px solid var(--border);
    border-radius: 4px;
    font-size: 14px;
    min-width: 240px;
  }
  header select {
    padding: 5px 8px;
    border: 1px solid var(--border);
    border-radius: 4px;
    font-size: 13px;
    background: white;
  }
  .layout {
    display: grid;
    grid-template-columns: 320px 1fr;
    height: calc(100vh - 58px);
  }
  aside {
    overflow-y: auto;
    border-right: 1px solid var(--border);
    background: var(--panel);
  }
  .method-item {
    padding: 10px 14px;
    border-bottom: 1px solid var(--border);
    cursor: pointer;
  }
  .method-item:hover { background: var(--accent-bg); }
  .method-item.active { background: var(--accent-bg); border-left: 3px solid var(--accent); padding-left: 11px; }
  .method-item h3 { margin: 0 0 3px 0; font-size: 13px; font-weight: 600; }
  .method-item .tags {
    font-size: 11px;
    color: var(--muted);
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }
  .badge {
    display: inline-block;
    padding: 1px 6px;
    border-radius: 3px;
    font-size: 10px;
    font-weight: 500;
    text-transform: uppercase;
  }
  .badge.stage2-ai { background: var(--stage2-ai); color: white; }
  .badge.stage2-human { background: var(--stage2-human); color: white; }
  .badge.stage2-none { background: var(--stage2-none); color: white; }
  .badge.setting { background: #e0e9f5; color: #2a4a7a; }
  main {
    overflow-y: auto;
    padding: 30px 40px;
    max-width: 900px;
  }
  main h2 { margin: 0 0 6px 0; font-size: 24px; }
  main .subtitle { color: var(--muted); margin-bottom: 20px; }
  section { margin: 24px 0; }
  section h4 {
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--muted);
    margin: 0 0 10px 0;
    border-bottom: 1px solid var(--border);
    padding-bottom: 4px;
  }
  .meta-grid {
    display: grid;
    grid-template-columns: 140px 1fr;
    gap: 6px 16px;
    font-size: 13px;
  }
  .meta-grid .k { color: var(--muted); }
  .pill {
    display: inline-block;
    padding: 2px 8px;
    background: var(--accent-bg);
    border-radius: 3px;
    margin-right: 4px;
    margin-bottom: 3px;
    font-size: 12px;
  }
  .step, .variation, .pitfall {
    padding: 12px 14px;
    background: white;
    border: 1px solid var(--border);
    border-radius: 4px;
    margin-bottom: 10px;
  }
  .step-head, .variation-head {
    font-weight: 600;
    margin-bottom: 8px;
    display: flex;
    justify-content: space-between;
    gap: 12px;
  }
  .step-actions { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-top: 8px; }
  .step-actions > div { padding: 8px 10px; background: var(--accent-bg); border-radius: 3px; }
  .step-actions .label { font-size: 11px; color: var(--muted); text-transform: uppercase; }
  .step .note, .variation .note, .pitfall .note {
    margin-top: 8px;
    padding: 6px 10px;
    background: #fff8e8;
    border-left: 3px solid #e6b84d;
    font-size: 12px;
  }
  .pitfall-head { display: flex; gap: 8px; margin-bottom: 6px; }
  .pitfall-sev { font-size: 10px; font-weight: 600; color: white; padding: 1px 6px; border-radius: 3px; }
  .pitfall-sev.common { background: #c05a4a; }
  .pitfall-sev.rare { background: #7a8a9a; }
  .sources li { margin-bottom: 6px; font-size: 13px; }
  .sources .kind { font-size: 10px; color: var(--muted); text-transform: uppercase; margin-right: 6px; }
  .empty {
    color: var(--muted);
    text-align: center;
    padding: 40px;
  }
  code { background: var(--accent-bg); padding: 1px 4px; border-radius: 2px; font-size: 90%; }
</style>
</head>
<body>
<header>
  <h1>Trainingsmethoden-Katalog</h1>
  <span class="meta" id="count"></span>
  <input type="search" id="search" placeholder="Suchen (Name, Synonym, Tag)...">
  <select id="filter-phase"><option value="">Alle Phasen</option></select>
  <select id="filter-setting">
    <option value="">Alle Settings</option>
    <option value="onsite">Präsenz</option>
    <option value="remote_sync">Remote (sync)</option>
    <option value="remote_async">Remote (async)</option>
    <option value="hybrid">Hybrid</option>
  </select>
  <select id="filter-stage2">
    <option value="">Alle Stufe-2</option>
    <option value="none">Nur Stufe 1</option>
    <option value="ai_draft">Stufe 2 (AI-Draft)</option>
    <option value="human_reviewed">Stufe 2 (reviewed)</option>
    <option value="expert_verified">Stufe 2 (expert)</option>
  </select>
</header>
<div class="layout">
  <aside id="list"></aside>
  <main id="detail"><div class="empty">← Methode auswählen</div></main>
</div>
<script id="data" type="application/json">__DATA__</script>
<script>
const DATA = JSON.parse(document.getElementById('data').textContent);
const { methods, references } = DATA;
const phases = references.phases || {};
const listEl = document.getElementById('list');
const detailEl = document.getElementById('detail');
const countEl = document.getElementById('count');
const searchEl = document.getElementById('search');
const filterPhase = document.getElementById('filter-phase');
const filterSetting = document.getElementById('filter-setting');
const filterStage2 = document.getElementById('filter-stage2');

// Phasen-Dropdown füllen
Object.values(phases).sort((a, b) => (a.sort_order || 0) - (b.sort_order || 0)).forEach(p => {
  const opt = document.createElement('option');
  opt.value = p.code;
  opt.textContent = p.label_de;
  filterPhase.appendChild(opt);
});

function escapeHtml(s) {
  return (s == null ? '' : String(s))
    .replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');
}

function labelFor(kind, code) {
  const ref = references[kind] || {};
  return (ref[code] || {}).label_de || code;
}

function stage2Badge(status) {
  const cls = status === 'ai_draft' ? 'stage2-ai' :
              (status === 'human_reviewed' || status === 'expert_verified') ? 'stage2-human' :
              'stage2-none';
  const text = status === 'ai_draft' ? 'Stufe 2 Draft' :
               status === 'human_reviewed' ? 'Stufe 2 ✓' :
               status === 'expert_verified' ? 'Stufe 2 ✓✓' :
               'Stufe 1';
  return `<span class="badge ${cls}">${text}</span>`;
}

function matchesFilters(m) {
  const q = searchEl.value.trim().toLowerCase();
  if (q) {
    const hay = [
      m.canonical_name,
      m.slug,
      m.short_description,
      (m.tags || []).join(' '),
      (m.synonyms || []).map(s => s.synonym).join(' '),
    ].join(' ').toLowerCase();
    if (!hay.includes(q)) return false;
  }
  if (filterPhase.value) {
    if (!(m.phases || []).some(p => p.code === filterPhase.value)) return false;
  }
  if (filterSetting.value) {
    const ok = (m.settings || []).some(s => s.code === filterSetting.value
      && ['excellent', 'good'].includes(s.suitability || 'good'));
    if (!ok) return false;
  }
  if (filterStage2.value) {
    const s2 = m.stage_2_status || 'none';
    if (s2 !== filterStage2.value) return false;
  }
  return true;
}

function render() {
  const filtered = methods.filter(matchesFilters);
  countEl.textContent = `${filtered.length} / ${methods.length} Methoden`;
  listEl.innerHTML = '';
  filtered.forEach(m => {
    const item = document.createElement('div');
    item.className = 'method-item';
    item.dataset.slug = m.slug;
    const primaryPhase = (m.phases || []).find(p => p.is_primary);
    const tags = [
      stage2Badge(m.stage_2_status || 'none'),
      primaryPhase ? `<span class="badge">${escapeHtml(labelFor('phases', primaryPhase.code))}</span>` : '',
      m.duration_typical_minutes ? `<span class="badge">${m.duration_typical_minutes} min</span>` : '',
    ].filter(Boolean).join('');
    item.innerHTML = `
      <h3>${escapeHtml(m.canonical_name)}</h3>
      <div class="tags">${tags}</div>
    `;
    item.addEventListener('click', () => {
      document.querySelectorAll('.method-item').forEach(x => x.classList.remove('active'));
      item.classList.add('active');
      showDetail(m);
    });
    listEl.appendChild(item);
  });
  if (filtered.length === 0) {
    listEl.innerHTML = '<div class="empty">Keine Treffer</div>';
  }
}

function showDetail(m) {
  const synonyms = (m.synonyms || []).map(s => escapeHtml(s.synonym)).join(', ');
  const phases = (m.phases || []).map(p =>
    `<span class="pill">${escapeHtml(labelFor('phases', p.code))}${p.is_primary ? ' ★' : ''}</span>`
  ).join('');
  const settings = (m.settings || []).map(s => {
    const suit = s.suitability || 'good';
    const icon = suit === 'excellent' ? '✓✓' : suit === 'good' ? '✓' : suit === 'limited' ? '~' : '✗';
    return `<span class="pill">${escapeHtml(labelFor('settings', s.code))} ${icon}${s.note ? ' · ' + escapeHtml(s.note) : ''}</span>`;
  }).join('');
  const audiences = (m.target_audiences || []).map(c =>
    `<span class="pill">${escapeHtml(labelFor('audiences', c))}</span>`).join('');
  const methodTypes = (m.method_types || []).map(c =>
    `<span class="pill">${escapeHtml(labelFor('method_types', c))}</span>`).join('');
  const groupSizes = (m.group_sizes || []).map(c =>
    `<span class="pill">${escapeHtml(labelFor('group_sizes', c))}</span>`).join('');

  const duration = [m.duration_min_minutes, m.duration_typical_minutes, m.duration_max_minutes]
    .map(d => d ?? '—').join(' / ') + ' min';

  const steps = (m.steps || []).map(s => `
    <div class="step">
      <div class="step-head">
        <span>${s.order}. ${escapeHtml(s.label)}</span>
        <span style="color: var(--muted); font-weight: normal;">${s.duration_minutes != null ? s.duration_minutes + ' min' : ''}</span>
      </div>
      <div class="step-actions">
        <div><div class="label">Trainer:in</div>${escapeHtml(s.trainer_action)}</div>
        <div><div class="label">Teilnehmende</div>${escapeHtml(s.participant_action)}</div>
      </div>
      ${s.note ? `<div class="note">💡 ${escapeHtml(s.note)}</div>` : ''}
    </div>
  `).join('');

  const variations = (m.variations || []).map(v => `
    <div class="variation">
      <div class="variation-head">
        <span>${escapeHtml(v.name)}</span>
        ${v.setting_codes ? v.setting_codes.map(c => `<span class="badge setting">${escapeHtml(c)}</span>`).join('') : ''}
      </div>
      <div>${escapeHtml(v.description)}</div>
      ${v.when_to_use ? `<div class="note"><strong>Wann:</strong> ${escapeHtml(v.when_to_use)}</div>` : ''}
    </div>
  `).join('');

  const pitfalls = (m.pitfalls || []).map(p => `
    <div class="pitfall">
      <div class="pitfall-head">
        <span class="pitfall-sev ${p.severity || 'common'}">${p.severity || 'common'}</span>
        ${p.setting_codes ? p.setting_codes.map(c => `<span class="badge setting">${escapeHtml(c)}</span>`).join('') : ''}
      </div>
      <div><strong>Issue:</strong> ${escapeHtml(p.issue)}</div>
      <div style="margin-top: 4px;"><strong>Gegenmaßnahme:</strong> ${escapeHtml(p.countermeasure)}</div>
    </div>
  `).join('');

  const sources = (m.sources || []).map(s => {
    const info = [
      s.author, s.year, s.url ? `<a href="${escapeHtml(s.url)}">Link</a>` : null,
      s.doi ? `DOI: ${escapeHtml(s.doi)}` : null,
    ].filter(Boolean).join(' · ');
    return `<li>
      <span class="kind">${escapeHtml(s.kind || '')}</span>
      <strong>${escapeHtml(s.title || '')}</strong>
      ${info ? ` — ${info}` : ''}
      ${s.relation && s.relation !== 'reference' ? ` <span class="pill">${escapeHtml(s.relation)}</span>` : ''}
      ${s.note ? `<div style="color: var(--muted); font-size: 12px; margin-top: 2px;">${escapeHtml(s.note)}</div>` : ''}
    </li>`;
  }).join('');

  detailEl.innerHTML = `
    <h2>${escapeHtml(m.canonical_name)} ${stage2Badge(m.stage_2_status || 'none')}</h2>
    <div class="subtitle">
      <code>${escapeHtml(m.slug)}</code>
      ${synonyms ? ' · auch: ' + synonyms : ''}
    </div>

    ${m.short_description ? `<section><p>${escapeHtml(m.short_description)}</p></section>` : ''}

    <section>
      <h4>Einordnung</h4>
      <div class="meta-grid">
        <div class="k">Phasen</div><div>${phases || '—'}</div>
        <div class="k">Settings</div><div>${settings || '—'}</div>
        <div class="k">Gruppengröße</div><div>${groupSizes || '—'}</div>
        <div class="k">Zielgruppen</div><div>${audiences || '—'}</div>
        <div class="k">Typen</div><div>${methodTypes || '—'}</div>
        <div class="k">Dauer (min/typ/max)</div><div>${duration}</div>
        ${m.originator ? `<div class="k">Urheber</div><div>${escapeHtml(m.originator)}${m.origin_year ? ' (' + m.origin_year + ')' : ''}</div>` : ''}
        ${m.origin_context ? `<div class="k">Kontext</div><div>${escapeHtml(m.origin_context)}</div>` : ''}
      </div>
    </section>

    ${m.long_description ? `<section><h4>Beschreibung</h4><p>${escapeHtml(m.long_description)}</p></section>` : ''}

    ${steps ? `<section><h4>Ablauf (${(m.steps || []).length} Schritte)</h4>${steps}</section>` : ''}

    ${variations ? `<section><h4>Varianten (${(m.variations || []).length})</h4>${variations}</section>` : ''}

    ${pitfalls ? `<section><h4>Stolperfallen (${(m.pitfalls || []).length})</h4>${pitfalls}</section>` : ''}

    ${sources ? `<section><h4>Quellen (${(m.sources || []).length})</h4><ul class="sources">${sources}</ul></section>` : ''}

    <section style="color: var(--muted); font-size: 12px;">
      Qualität: ${escapeHtml(m.quality_status || 'ai_draft')} ·
      Lizenz: ${escapeHtml(m.content_license || 'proprietary')}
      ${m.content_author ? ' · Autor: ' + escapeHtml(m.content_author) : ''}
    </section>
  `;
}

[searchEl, filterPhase, filterSetting, filterStage2].forEach(el =>
  el.addEventListener('input', render)
);
render();
</script>
</body>
</html>
"""


def main() -> int:
    OUT_DIR.mkdir(exist_ok=True)
    data = build_data()
    # JSON wird in einen <script type="application/json">-Block eingebettet.
    # Innerhalb eines <script>-Tags wird der Inhalt als "raw text" behandelt —
    # HTML-Entities werden NICHT dekodiert. Also kein html.escape(), sonst
    # liest JS {&quot;...&quot;} und JSON.parse scheitert.
    # Einziges echtes Risiko: ein Literal "</script>" in den Daten würde den
    # Tag vorzeitig schließen. Also nur "</" → "<\/" entschärfen.
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    html_out = HTML_TEMPLATE.replace("__DATA__", payload)
    OUT_FILE.write_text(html_out, encoding="utf-8")
    size_kb = OUT_FILE.stat().st_size / 1024
    print(f"✓ {OUT_FILE.relative_to(ROOT)} ({size_kb:.0f} KB, {len(data['methods'])} Methoden)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
