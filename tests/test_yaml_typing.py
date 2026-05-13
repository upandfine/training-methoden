"""Schützt gegen YAML-Auto-Typing-Gotchas in String-Listen-Feldern.

YAML 1.1 (PyYAML-Default) macht automatisch:
  * ``- Optional: Text`` → Dict ``{Optional: "Text"}`` statt Liste-mit-String
  * ``- 1:1`` → int 61 (Sexagesimal)
  * ``- yes`` / ``- no`` → Boolean
  * ``- 2024-04-24`` → date

In den Schema-Validatoren fällt das auf, aber oft erst beim Ingest.
Dieser Test zieht die Erkennung nach vorn.
"""

from __future__ import annotations

from pathlib import Path

import yaml

METHODS_DIR = Path(__file__).resolve().parent.parent / "data" / "methods"

FLAT_STRING_LIST_FIELDS = (
    "materials",
    "tags",
    "group_sizes",
    "target_audiences",
    "method_types",
)


def test_flat_string_list_fields_contain_only_strings() -> None:
    problems: list[str] = []
    for p in sorted(METHODS_DIR.glob("*.yaml")):
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            continue
        for field in FLAT_STRING_LIST_FIELDS:
            v = raw.get(field)
            if v is None:
                continue
            if not isinstance(v, list):
                problems.append(
                    f"{p.name}: {field} ist keine Liste, "
                    f"sondern {type(v).__name__} — vermutlich YAML-Auto-Typing."
                )
                continue
            for i, item in enumerate(v):
                if not isinstance(item, str):
                    problems.append(
                        f"{p.name}: {field}[{i}] = {item!r} "
                        f"(type={type(item).__name__}) — Eintrag quoten."
                    )
        # setting_codes in variations/pitfalls
        for coll_name in ("variations", "pitfalls"):
            for vi, entry in enumerate(raw.get(coll_name) or []):
                if not isinstance(entry, dict):
                    continue
                sc = entry.get("setting_codes")
                if sc is None:
                    continue
                if not isinstance(sc, list):
                    problems.append(
                        f"{p.name}: {coll_name}[{vi}].setting_codes ist keine Liste"
                    )
                    continue
                for i, item in enumerate(sc):
                    if not isinstance(item, str):
                        problems.append(
                            f"{p.name}: {coll_name}[{vi}].setting_codes[{i}] = {item!r}"
                        )

    assert not problems, "YAML-Typing-Probleme:\n" + "\n".join(problems)
