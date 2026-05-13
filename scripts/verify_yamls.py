"""Standalone YAML-Validator.

Prüft alle Methoden-YAMLs gegen das Stage-2-Schema OHNE Pydantic,
damit wir im Sandbox ohne pip-Zugriff validieren können.

Spiegelt die Regeln aus src/methods/schemas.py wider:
- Pflichtfelder (slug, canonical_name)
- slug-Pattern
- Enum-Werte (quality_status, stage_2_status, severity, kind, relation,
  suitability, language)
- step.order lückenlos 1..N
- Höchstens eine Phase mit is_primary=true
- duration_max >= duration_min

Außerdem einige Stage-2-spezifische Heuristiken:
- Σ step.duration_minutes (sofern gesetzt) <= duration_max_minutes * 1.2
- steps/variations/pitfalls nichtleer wenn stage_2_status != 'none'
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

QUALITY_STATUSES = {"ai_draft", "human_reviewed", "expert_verified", "deprecated"}
STAGE2_STATUSES = {"none", "ai_draft", "human_reviewed", "expert_verified"}
CONTENT_LICENSES = {"proprietary", "cc-by-4.0", "cc-by-sa-4.0", "public-domain"}
SUITABILITIES = {"excellent", "good", "limited", "unsuitable"}
SOURCE_RELATIONS = {"originator", "reference", "deep_dive", "practice_guide", "tool"}
SOURCE_KINDS = {"book", "article", "url", "standard", "tool", "paper", "other"}
SYNONYM_LANGS = {"de", "en", "fr", "es", "other"}
SEVERITIES = {"common", "rare"}

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,158}$")

# Gruppengrößen-Ranges (konservativ, Werte aus data/seed/group_sizes.yaml).
# Für die Heuristik-Prüfung: welches group_sizes-Label deckt welche TN-Zahl ab?
GROUP_SIZE_RANGES: dict[str, tuple[int, int]] = {
    "one_on_one": (2, 2),
    "small": (3, 8),
    "medium": (9, 20),
    "large": (21, 50),
    "plenary": (51, 9999),
}

# Textuelle Zahlwörter, die in "zwei Teams à X" auftauchen
_NUM_WORDS = {"zwei": 2, "drei": 3, "vier": 4, "fünf": 5, "fuenf": 5}


def _estimate_min_active_tn(text: str) -> int | None:
    """Heuristisch: Minimum an aktiven TN, das der Text voraussetzt.

    Sucht nach eindeutigen Mustern wie "zwei Teams à 4", "mindestens 8 TN",
    "3 Redner:innen pro Team". Gibt ``None`` zurück, wenn nichts Eindeutiges
    gefunden wird — bewusst konservativ, um falsche Positive
    ("3 Fragen", "5 min") zu vermeiden.
    """
    max_estimate = 0

    # "zwei Teams à 4" / "2 Teams à 4" / "3 Gruppen à 5"
    for m in re.finditer(
        r"\b(zwei|drei|vier|fünf|fuenf|\d+)\s+(?:Teams?|Gruppen)\s+(?:à|a)\s+(\d+)",
        text,
        re.IGNORECASE,
    ):
        raw_n = m.group(1).lower()
        n_teams = _NUM_WORDS.get(raw_n)
        if n_teams is None:
            try:
                n_teams = int(raw_n)
            except ValueError:
                continue
        per_team = int(m.group(2))
        max_estimate = max(max_estimate, n_teams * per_team)

    # "mindestens 8 TN" / "min. 8 Personen" / "ab 8 Teilnehmenden"
    for m in re.finditer(
        r"\b(?:min\.|mindestens|ab)\s+(\d+)\s+(?:aktive\s+)?(?:TN|Personen|Teilnehmende[rn]?)\b",
        text,
        re.IGNORECASE,
    ):
        max_estimate = max(max_estimate, int(m.group(1)))

    # "X Redner:innen pro Team" / "X Sprecher:innen je Team" × 2 Teams
    for m in re.finditer(
        r"(\d+)\s+(?:Redner:?innen|Rednern|Sprecher:?innen|Teammitgliedern?)\s+(?:pro|je)\s+Team",
        text,
        re.IGNORECASE,
    ):
        max_estimate = max(max_estimate, int(m.group(1)) * 2)

    return max_estimate if max_estimate > 0 else None


def _check_group_size_consistency(raw: dict) -> list[str]:
    """Heuristische Warnung: group_sizes deckt nicht ab, was der Ablauf
    implizit voraussetzt. Nur Warnung, kein harter Fehler — die Heuristik
    kann falsche Positive produzieren.
    """
    warnings: list[str] = []
    gs = raw.get("group_sizes") or []
    steps = raw.get("steps") or []
    if not gs or not steps:
        return warnings

    covered_max = max(
        (GROUP_SIZE_RANGES.get(c, (0, 0))[1] for c in gs), default=0
    )
    if covered_max == 0:
        return warnings  # unbekannte Codes, nicht prüfbar

    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        for fname in ("trainer_action", "participant_action", "note"):
            text = step.get(fname) or ""
            est = _estimate_min_active_tn(text)
            if est is not None and est > covered_max:
                warnings.append(
                    f"steps[{i}].{fname} setzt ≥{est} TN voraus, "
                    f"aber group_sizes={gs} deckt nur bis {covered_max} ab"
                )
    return warnings


def validate(path: Path) -> list[str]:
    errors: list[str] = []

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        return [f"YAML-Parse-Error: {e}"]

    if not isinstance(raw, dict):
        return ["Top-Level muss ein Mapping sein."]

    # Pflichtfelder
    for field in ("slug", "canonical_name"):
        if not raw.get(field):
            errors.append(f"Pflichtfeld fehlt: {field}")

    slug = raw.get("slug")
    if slug and not SLUG_RE.match(slug):
        errors.append(f"slug entspricht nicht dem Muster: {slug!r}")

    # Enum-Felder (Top-Level)
    qs = raw.get("quality_status", "ai_draft")
    if qs not in QUALITY_STATUSES:
        errors.append(f"quality_status ungültig: {qs!r}")

    s2 = raw.get("stage_2_status", "none")
    if s2 not in STAGE2_STATUSES:
        errors.append(f"stage_2_status ungültig: {s2!r}")

    lic = raw.get("content_license", "proprietary")
    if lic not in CONTENT_LICENSES:
        errors.append(f"content_license ungültig: {lic!r}")

    # Dauern
    mn = raw.get("duration_min_minutes")
    mx = raw.get("duration_max_minutes")
    if isinstance(mn, int) and isinstance(mx, int) and mx < mn:
        errors.append(f"duration_max_minutes ({mx}) < duration_min_minutes ({mn})")

    # Phases
    phases = raw.get("phases") or []
    if not isinstance(phases, list):
        errors.append("phases muss eine Liste sein.")
    else:
        primaries = [p for p in phases if isinstance(p, dict) and p.get("is_primary")]
        if len(primaries) > 1:
            errors.append(f"Nur eine Phase darf is_primary=true sein (gefunden: {len(primaries)}).")

    # Settings
    for st in raw.get("settings") or []:
        if not isinstance(st, dict):
            errors.append(f"settings-Eintrag kein Mapping: {st!r}")
            continue
        sui = st.get("suitability", "good")
        if sui not in SUITABILITIES:
            errors.append(f"setting.suitability ungültig: {sui!r} (code={st.get('code')!r})")

    # Synonyms
    for syn in raw.get("synonyms") or []:
        if not isinstance(syn, dict):
            errors.append(f"synonym-Eintrag kein Mapping: {syn!r}")
            continue
        lang = syn.get("language", "de")
        if lang not in SYNONYM_LANGS:
            errors.append(f"synonym.language ungültig: {lang!r}")

    # Sources
    for src in raw.get("sources") or []:
        if not isinstance(src, dict):
            errors.append(f"source-Eintrag kein Mapping: {src!r}")
            continue
        k = src.get("kind")
        if k not in SOURCE_KINDS:
            errors.append(f"source.kind ungültig: {k!r} (title={src.get('title')!r})")
        r = src.get("relation", "reference")
        if r not in SOURCE_RELATIONS:
            errors.append(f"source.relation ungültig: {r!r} (title={src.get('title')!r})")

    # Steps
    steps = raw.get("steps") or []
    if not isinstance(steps, list):
        errors.append("steps muss eine Liste sein.")
        steps = []

    orders: list[int] = []
    total_step_dur = 0
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            errors.append(f"steps[{i}] kein Mapping: {step!r}")
            continue
        o = step.get("order")
        if not isinstance(o, int) or o < 1:
            errors.append(f"steps[{i}].order ungültig: {o!r}")
        else:
            orders.append(o)
        for field in ("label", "trainer_action", "participant_action"):
            if not step.get(field):
                errors.append(f"steps[{i}].{field} fehlt")
        d = step.get("duration_minutes")
        if d is not None and not isinstance(d, int):
            errors.append(f"steps[{i}].duration_minutes kein int: {d!r}")
        if isinstance(d, int):
            total_step_dur += d

    if steps and orders and sorted(orders) != list(range(1, len(steps) + 1)):
        errors.append(f"step.order muss lückenlos 1..{len(steps)} sein, bekam {sorted(orders)}")

    # Total step dur gegen duration_max_minutes * 1.2 (Heuristik)
    if steps and isinstance(mx, int) and total_step_dur > mx * 1.2:
        errors.append(
            f"Σ step.duration_minutes ({total_step_dur}) > duration_max_minutes × 1.2 ({mx * 1.2:.0f})"
        )

    # Σ step dur gegen duration_typical_minutes — harte Konsistenzprüfung.
    # Wenn die beschriebenen Schritte nicht ungefähr so lange dauern wie
    # der behauptete Typical, ist duration_typical als Planungsdatum
    # irreführend. Toleranz ±15 %: fängt Drift wie 12 min vs. 10 min
    # typical (Faktor 1.2), lässt aber kleine Rundungs-Unschärfen durch.
    # Drifts unter ~17 % werden vom Σ-Check nicht gefangen — die deckt
    # das menschliche Review ab.
    tp = raw.get("duration_typical_minutes")
    if steps and isinstance(tp, int) and total_step_dur > 0:
        lower = tp * 0.85
        upper = tp * 1.15
        if not (lower <= total_step_dur <= upper):
            errors.append(
                f"Σ step.duration_minutes ({total_step_dur}) außerhalb "
                f"duration_typical_minutes × [0.85, 1.15] ({lower:.0f}-{upper:.0f})"
            )

    # Variations
    for i, v in enumerate(raw.get("variations") or []):
        if not isinstance(v, dict):
            errors.append(f"variations[{i}] kein Mapping")
            continue
        for field in ("name", "description"):
            if not v.get(field):
                errors.append(f"variations[{i}].{field} fehlt")

    # Pitfalls
    for i, p in enumerate(raw.get("pitfalls") or []):
        if not isinstance(p, dict):
            errors.append(f"pitfalls[{i}] kein Mapping")
            continue
        for field in ("issue", "countermeasure"):
            if not p.get(field):
                errors.append(f"pitfalls[{i}].{field} fehlt")
        sev = p.get("severity", "common")
        if sev not in SEVERITIES:
            errors.append(f"pitfalls[{i}].severity ungültig: {sev!r}")

    # Stage-2-Konsistenz
    if s2 != "none":
        if not steps:
            errors.append(f"stage_2_status={s2!r}, aber steps leer")
        if not raw.get("variations"):
            errors.append(f"stage_2_status={s2!r}, aber variations leer")
        # pitfalls empfohlen, nicht zwingend

    return errors


def main() -> int:
    root = Path(__file__).resolve().parent.parent / "data" / "methods"
    files = sorted(root.glob("*.yaml"))
    print(f"Prüfe {len(files)} YAMLs in {root}")

    total_errors = 0
    total_warnings = 0
    stage2_counts = {k: 0 for k in STAGE2_STATUSES}
    quality_counts = {k: 0 for k in QUALITY_STATUSES}

    for p in files:
        errs = validate(p)
        warns: list[str] = []
        try:
            raw = yaml.safe_load(p.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                stage2_counts[raw.get("stage_2_status", "none")] = (
                    stage2_counts.get(raw.get("stage_2_status", "none"), 0) + 1
                )
                quality_counts[raw.get("quality_status", "ai_draft")] = (
                    quality_counts.get(raw.get("quality_status", "ai_draft"), 0) + 1
                )
                warns = _check_group_size_consistency(raw)
        except Exception:
            pass

        if errs:
            total_errors += len(errs)
            print(f"\n❌ {p.name}")
            for e in errs:
                print(f"   - {e}")
        if warns:
            total_warnings += len(warns)
            print(f"\n⚠️  {p.name}")
            for w in warns:
                print(f"   - {w}")

    print("\n--- Summary ---")
    print(f"Dateien: {len(files)}")
    print(f"Fehler gesamt: {total_errors}")
    print(f"Warnungen (Heuristik): {total_warnings}")
    print(f"Stage-2: {stage2_counts}")
    print(f"Quality: {quality_counts}")

    return 0 if total_errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
