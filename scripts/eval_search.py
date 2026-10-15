"""Manueller Sanity-Check: 15 typische Trainer-Queries gegen die /search-API.

Aufruf:
    ./.venv/bin/python scripts/eval_search.py
    ./.venv/bin/python scripts/eval_search.py --base http://localhost:8000

Was es macht
------------
Für jede Query gegen die laufende API in drei Modi anfragen
(``lexical``, ``semantic``, ``hybrid``) und die Top-5 nebeneinander
ausgeben. Kein Pass/Fail — der menschliche Blick urteilt, ob die
Treffer plausibel sind. Idealerweise sieht ``hybrid`` mindestens so
gut aus wie der bessere von Lexik / Semantik.

Warum nicht im pytest?
----------------------
Die "Wahrheit" über Such-Qualität ist subjektiv und gruppenabhängig
("Konflikt-Methoden" → erwartet jemand Mediation oder eher Eskalations-
Vorgänger?). Automatisierte Tests wären hier verlogen. Deshalb: Eval
als reproduzierbares Ergebnis im Terminal — leicht zu lesen, leicht
zu erweitern.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

# urllib geht sonst über ggf. konfigurierte HTTP-Proxies — und Corporate-/
# PAC-Proxies werfen für localhost gern 401, weil sie für intern keine
# Credentials haben. Wir umgehen das, indem wir localhost und 127.0.0.1
# in NO_PROXY hängen, BEVOR urllib seine Konfig liest.
_no_proxy = os.environ.get("NO_PROXY", "") + ",localhost,127.0.0.1,::1"
os.environ["NO_PROXY"] = _no_proxy.lstrip(",")
os.environ["no_proxy"] = os.environ["NO_PROXY"]
# Einen Opener ohne Proxy registrieren — robusterer Hammer als NO_PROXY allein.
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
urllib.request.install_opener(_opener)

QUERIES: list[tuple[str, str]] = [
    # (query, kurze Begründung was wir erwarten)
    ("Energizer am Morgen", "Aktivierung, Warm-up; nicht: Reflexion"),
    ("Reflexion am Seminarende", "Abschluss-Methoden, Drei-Behälter, Blitzlicht"),
    ("Konflikt zwischen zwei Personen", "Mediation, Tetralemma, Klärungsgespräch"),
    ("Erwartungen klären", "Erwartungsabfrage, Hopes-and-Fears"),
    ("Ideen sammeln in 30 Minuten", "Brainstorming, 6-3-5, World Café Light"),
    ("Feedback an Mitarbeitende", "SBI, WWW, Feedback-Sandwich"),
    ("Online-Workshop für 80 Personen", "Liberating Structures, 1-2-4-All"),
    ("Mathematik-Aufgabe schrittweise erklären", "Worked Example mit Fading"),
    ("Gruppe einander vorstellen", "Speed-Dating, Bingo, Soziogramm"),
    ("Rollenspiel kurz", "Kurzsimulation, Goldfish Bowl"),
    ("Entscheidung im Team treffen", "Konsens, systemisches Konsensieren"),
    ("Stress im Raum, alle reden durcheinander", "Talking Stick, Time-Out"),
    ("Auszubildende motivieren", "Spielifikation, Quiz-Methoden"),
    ("Lerninhalte langfristig verankern", "Spaced Repetition, Lerntagebuch"),
    ("Präsentations-Feedback geben", "Sandwich, SBI, 1-Karte-Methode"),
]


def fetch(base: str, q: str, mode: str, limit: int = 5) -> list[dict]:
    params = urllib.parse.urlencode({"q": q, "mode": mode, "limit": limit})
    url = f"{base.rstrip('/')}/search?{params}"

    # Embedding-Calls über OpenAI können vereinzelt 30-50s dauern, wenn das
    # Modell unter Last steht. 60s Timeout + 1 Retry für genau diesen Fall —
    # zwei aufeinanderfolgende Hänger sind ein echtes Problem, einer nicht.
    last_err: Exception | None = None
    for attempt in range(2):
        try:
            with urllib.request.urlopen(url, timeout=60) as resp:  # noqa: S310
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return [{"error": f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}"}]
        except TimeoutError as e:
            last_err = e
            if attempt == 0:
                print("   ⏱  Timeout, einmal Retry …", flush=True)
                continue
        except Exception as e:  # noqa: BLE001
            return [{"error": f"{e.__class__.__name__}: {e}"}]
    return [{"error": f"Timeout (2x): {last_err}"}]


def fmt_hits(hits: list[dict], width: int = 36) -> str:
    if hits and "error" in hits[0]:
        return f"  ✘ {hits[0]['error']}"
    if not hits:
        return "  (keine Treffer)"
    lines = []
    for i, h in enumerate(hits, 1):
        name = h.get("canonical_name", h.get("slug", "?"))
        if len(name) > width:
            name = name[: width - 1] + "…"
        lines.append(f"  {i}. {name}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base",
        default="http://localhost:8000",
        help="API-Basis-URL (default: http://localhost:8000)",
    )
    parser.add_argument(
        "--limit", type=int, default=5, help="Top-N pro Modus (default: 5)"
    )
    parser.add_argument(
        "--mode",
        choices=["all", "lexical", "semantic", "hybrid"],
        default="all",
        help="Nur einen Modus testen (default: alle drei nebeneinander)",
    )
    args = parser.parse_args()

    # Health-Check zuerst — wenn die API nicht läuft, sofort bescheid sagen.
    try:
        with urllib.request.urlopen(f"{args.base.rstrip('/')}/health", timeout=5) as r:  # noqa: S310
            health = json.loads(r.read())
        print(f"API ok: {health}")
    except Exception as e:  # noqa: BLE001
        print(
            f"❌ API nicht erreichbar unter {args.base}: {e}\n"
            f"   Hast du `make api` gestartet?",
            file=sys.stderr,
        )
        return 2

    modes = [args.mode] if args.mode != "all" else ["lexical", "semantic", "hybrid"]

    for q, hint in QUERIES:
        print()
        print("─" * 80)
        print(f"Query: «{q}»   — Erwartung: {hint}")
        print("─" * 80)
        for mode in modes:
            hits = fetch(args.base, q, mode, args.limit)
            print(f"\n[{mode}]")
            print(fmt_hits(hits))

    print("\n" + "=" * 80)
    print(
        "Lies die Ergebnisse mit folgenden Faustregeln:\n"
        "  • hybrid sollte ≥ lexical UND ≥ semantic abschneiden.\n"
        "  • Falls semantic 'bessere' Treffer hat als hybrid → Gewichte\n"
        "    SEMANTIC_WEIGHT in src/methods/api.py erhöhen.\n"
        "  • Falls lexical exakte Slug-Treffer auf Platz 1 hat, hybrid\n"
        "    sie aber tiefer rankt → LEXICAL_WEIGHT erhöhen oder\n"
        "    Slug-Match als Boost separat modellieren."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
